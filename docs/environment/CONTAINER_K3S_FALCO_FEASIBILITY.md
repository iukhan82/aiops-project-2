# Container, K3s and Falco feasibility evidence

Captured 2026-09-18 on the measured WSL2/Docker development route
(`docs/environment/WORKSTATION_INVENTORY.md`), and 2026-09-19 on the
target-native Ubuntu LTS host (`docs/environment/TOPOLOGY.md`). The WSL2
route proves container/K3s workload scheduling and characterizes exactly why
Falco's live syscall capture did not run there. The Ubuntu LTS host
(192.168.11.108) closes that gap: **live syscall capture is now proven on a
real, non-WSL2, non-container-virtualized kernel** - see "Target-native
result" below. P01.07 is no longer blocked on host availability.

## Container and K3s: proven on the development route

- Runtime: Docker 29.7.2 inside Ubuntu 26.04 WSL2 (matches
  `docs/environment/WORKSTATION_INVENTORY.md`).
- Cluster: K3s v1.35.5+k3s1, containerd 2.2.3-k3s1, image
  `rancher/k3s:v1.35.5-k3s1` (digest
  `sha256:2074403abe1bded11ef3dde09d457e13be8e0b64c218b1c4f8269b4565cfbc65`),
  run via k3d (K3s-in-Docker) as containers `k3d-traffic-p01-server-0` and
  `k3d-traffic-p01-serverlb`, API exposed on host port 34367.
- Node reaches `Ready`; `coredns`, `local-path-provisioner` and
  `metrics-server` reach `Running` in `kube-system`.
- A scoped, least-privilege test workload was deployed and reached `Ready`:
  namespace `p01-feasibility`, one `Deployment` (`feasibility-echo`,
  `registry.k8s.io/e2e-test-images/agnhost:2.53`) with `runAsNonRoot`,
  `runAsUser: 65532`, `allowPrivilegeEscalation: false`, `capabilities.drop:
  [ALL]`, and bounded resource requests/limits (25m/32Mi request,
  100m/64Mi limit). Rollout: `deployment "feasibility-echo" successfully
  rolled out`; pod `feasibility-echo-86485d9cc5-92vct` reached `1/1 Running`.
  The namespace was deleted after capturing this evidence; it is disposable
  and not part of the platform's own workload set.

### Reproduction

```bash
# extract a working kubeconfig from the running k3d server container
docker exec k3d-traffic-p01-server-0 cat /output/kubeconfig.yaml > /tmp/k3d-traffic-p01.yaml
sed -i 's#https://127.0.0.1:6443#https://127.0.0.1:34367#' /tmp/k3d-traffic-p01.yaml
KUBECONFIG=/tmp/k3d-traffic-p01.yaml kubectl get nodes -o wide
KUBECONFIG=/tmp/k3d-traffic-p01.yaml kubectl apply -f <manifest>
KUBECONFIG=/tmp/k3d-traffic-p01.yaml kubectl -n <ns> rollout status deployment/<name>
```

## Falco: daemon runs, live capture blocked on WSL2's kernel

Image: `falcosecurity/falco-no-driver:latest` (digest
`sha256:ba05dfb209cc1c2e912ce116d230b5f59c4542e7286f05b663c499066d5d6a42`),
Falco 0.39.2. Container run with `--privileged --pid=host` and read-only
bind mounts of `/proc`, `/boot`, `/lib/modules`, `/usr`, `/etc`,
`/sys/kernel/debug` and the Docker socket, matching the project's documented
Falco deployment shape.

### Blocker 1 (fixed): host `inotify` instance ceiling

First failure: `Error: could not initialize inotify handler`, reproduced
identically with `--privileged`, `--security-opt seccomp=unconfined
--security-opt apparmor=unconfined --cap-add ALL` (ruling out a container
capability/confinement cause). Direct syscall probe confirmed the cause:

```
$ docker run --rm --privileged --pid=host python:3.12-alpine python3 -c \
  "import ctypes,os; libc=ctypes.CDLL('libc.so.6',use_errno=True); \
   fd=libc.inotify_init1(0); \
   print(os.strerror(ctypes.get_errno())) if fd<0 else print('ok')"
No file descriptors available   # EMFILE
```

`inotify_init1()` returns `EMFILE` specifically when the *host* limit
`fs.inotify.max_user_instances` (default 128) is exhausted for the calling
UID. This WSL2 VM runs many unrelated shared root-owned processes/containers
(`docs/environment/WORKSTATION_INVENTORY.md` records seventeen pre-existing
containers), which had exhausted the default ceiling for UID 0 before Falco
ever started - not a Falco or K3s defect. Fix, applied additively (raises the
ceiling only, evicts nothing, reversible on VM restart):

```bash
docker run --rm --privileged --pid=host -v /proc:/host/proc python:3.12-alpine \
  sh -c 'echo 1024 > /host/proc/sys/fs/inotify/max_user_instances'
```

Confirmed on the host: `sysctl fs.inotify.max_user_instances` reads `1024`
after the change. This is a real capacity-planning finding, independent of
Falco: a target host must budget `fs.inotify.max_user_instances` for the
combined Falco/kubelet/containerd/model-watcher process set, not assume the
Linux default.

### Blocker 2 (not fixed, environment-level): no working driver on the WSL2 kernel

With the `inotify` ceiling raised, all three Falco driver types were tried in
turn against kernel `6.18.33.2-microsoft-standard-WSL2`:

| Driver | Result |
|---|---|
| `modern_ebpf` (`-o engine.kind=modern_ebpf`) | Rules and config load; health webserver starts; then `Opening 'syscall' source with modern BPF probe` is immediately followed by `An error occurred in an event source, forcing termination... Error: Initialization issues during scap_init`, with no further detail and no matching `dmesg` entry. `CONFIG_BPF=y`, `CONFIG_DEBUG_INFO_BTF=y` and `/sys/kernel/btf/vmlinux` are all present, so this is a CO-RE/BPF-loader failure specific to Microsoft's non-upstream WSL2 kernel build, not a missing kernel feature flag. |
| `ebpf` (legacy probe, `-o engine.kind=ebpf`) | `Error: can't open BPF probe '/root/.falco/falco-bpf.o'`. The legacy driver needs a prebuilt `.o` matched to the exact kernel version string by Falco's driver registry; no such artifact exists for a `-microsoft-standard-WSL2` kernel string. |
| Kernel module | Not attempted: `/lib/modules/$(uname -r)/build` does not exist in this WSL2 distribution, so there are no matching headers to build a DKMS module against. Same root cause as the `ebpf` row (non-standard kernel identity), so a distinct attempt would not add information. |

### Result

Container and K3s feasibility is proven on the supported WSL2 development
route with inspectable evidence above. Falco's own process, configuration,
rule loading and health endpoint are proven functional, and one real,
previously-undocumented environment defect (the shared VM's `inotify`
ceiling) was found and fixed. Live syscall capture itself is blocked by
WSL2's non-standard kernel build across all three Falco driver types, which
confirms - with a specific, reproducible mechanism rather than an assumption
- the existing caveat in `docs/environment/WORKSTATION_INVENTORY.md`: "WSL2
does not count as final K3s/Falco or production-level target evidence."
Target-native acceptance for the Falco half of P01.07 required the dedicated
Ubuntu LTS host defined in `docs/environment/TOPOLOGY.md`. That host became
available 2026-09-19; results below.

## Target-native result (2026-09-19, real Ubuntu LTS host)

Host: `192.168.11.108` (hostname `monitoring`), Ubuntu 24.04.4 LTS, kernel
`7.0.0-31-generic` (real, non-WSL2; `systemd-detect-virt` reports `kvm` -
a standard virtualized server, not a container-in-container or WSL2
translation layer), 8 vCPU / 23 GiB RAM, Docker 29.1.3. Host already ran a
pre-existing K3s v1.36.4+k3s1 cluster (namespaces `aiops`,
`interview-platform`, `kube-system`, `monitoring` and others) with active
workloads - not created by this session, not disturbed by it.

### kind cluster: proven, isolated from the pre-existing K3s cluster

Installed `kind` v0.33.0 (checksum-verified binary) and created cluster
`aiops-test`: node `aiops-test-control-plane` reached `Ready`
(`v1.31.2`, kernel `7.0.0-31-generic`, containerd 1.7.18), fully isolated
from the pre-existing K3s cluster (separate `docker network`, separate
kubeconfig). `kind`'s own kubeconfig auto-merge into
`/home/aiops/.kube/config` silently no-ops because that directory is
`root:root`-owned (`drwxr-xr-x`) and `aiops` lacks write access to it - a
real host finding, not a `kind` defect; worked around with
`kind get kubeconfig --name aiops-test > /tmp/kind-aiops-test.kubeconfig`
and `KUBECONFIG=` passed explicitly.

### Falco: live syscall capture confirmed working

Falco 0.39.2 (the version pinned throughout this project's WSL2 evidence,
`falcosecurity/falco-no-driver@sha256:ba05...c6a42`) was retried first,
identically to the WSL2 procedure. Result: **not** the same failure mode -
`modern_ebpf` still failed silently (`Initialization issues during
scap_init`, no `dmesg` trace); `ebpf` (legacy) still had no prebuilt probe
for this kernel string (expected, consistent with WSL2). `kmod` failed with
a genuine, specific, reproducible cause distinct from WSL2's "no headers"
case: DKMS attempted a real compile against this kernel's headers and
failed with `error: too many arguments to function 'class_create'` - Linux
6.4+ removed `class_create()`'s first (`struct module *`) argument, and
Falco 0.39.2's bundled kmod driver source (`7.3.0+driver`) still calls the
old two-argument form. This is a Falco-driver/kernel-version incompatibility
in the specific pinned release, not an environment defect.

Upgrading to Falco 0.44.1 (latest upstream at time of test;
`falcosecurity/falco:0.44.1` - the `-no-driver` variant does not exist for
this tag) with `-e FALCO_DRIVER_LOADER_OPTIONS=modern_ebpf` resolved it:
Falco initialized, loaded rules, started its health webserver
(`{"status": "ok"}` on `:8765/healthz`), opened the modern BPF probe, and
began emitting real events within seconds - including its own default
`Sensitive file opened for reading by non-trusted program` rule firing on
genuine host activity from `systemd-executor`. Capture was then verified as
live (not a one-time replay) with a deliberate trigger:

```
$ echo '<password>' | sudo -S -p '' cat /etc/shadow >/dev/null 2>&1
```

produced, within 3 seconds, a matching Falco event carrying the exact
process ancestry of that command:

```
Warning Sensitive file opened for reading by non-trusted program |
file=/etc/shadow evt_type=openat user=root user_uid=0 user_loginuid=1001
process=cat proc_exepath=/usr/bin/cat parent=sudo command=cat /etc/shadow
gparent=bash ggparent=sshd gggparent=sshd container_id=host ...
```

`process=cat parent=sudo gparent=bash ggparent=sshd` is the exact call
chain of the SSH session that ran the trigger - direct, reproducible proof
of live kernel-level syscall capture, not a cached/replayed log line.

### Conclusion

P01.07's Falco acceptance criterion is met on target-native hardware,
**conditional on using Falco >=0.44.1's `modern_ebpf` driver** rather than
the 0.39.2 pin used elsewhere in this project's evidence trail - 0.39.2's
kmod driver is incompatible with kernel 6.4+'s `class_create()` signature,
and its modern_ebpf path does not initialize on this kernel for reasons not
further isolated (untried: verbose/debug libbpf output, since the working
0.44.1 path made further isolation unnecessary for the acceptance decision).
Any production Falco deployment target for this project should pin
`>=0.44.1`, not 0.39.2.

Test artifacts (the `falco-feasibility` container) were removed after
capture. The `aiops-test` `kind` cluster was left running on the host as
the requested deliverable; it does not conflict with the pre-existing K3s
cluster.

**Operational note (not a technical finding):** the SSH credential used for
this test was shared in plaintext chat; rotate it.
