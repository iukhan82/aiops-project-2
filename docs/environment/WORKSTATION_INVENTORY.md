# Development and target environment inventory

Captured 2026-09-18. Values are measured unless marked unknown or proposed.
Hostname and dynamic IP addresses are intentionally omitted for publication safety.

## Windows development workstation

| Item | Measured value |
|---|---|
| OS | Windows 11 Pro 64-bit, version 10.0.22621, build 22621 |
| CPU | Intel Core i5-1145G7, 4 physical cores / 8 logical processors |
| RAM | 15.69 GiB |
| Graphics | Intel Iris Xe, reported 2 GiB adapter memory; no discrete GPU detected |
| Project drive | D:, 109.92 GiB free at capture |
| System drive | C:, 73.50 GiB free at capture |
| Hypervisor | Present |
| Python | 3.12.3 |
| Node.js / npm | Node 24.19.0 / npm 11.17.0 |
| Git | 2.49.0.windows.1 |
| WSL | 2.7.14.0; default distribution Ubuntu; WSL version 2 |
| Windows-native container tools | Docker, Podman, kubectl, Helm, K3s, Falco and Trivy not on PATH |
| Traffic simulator | SUMO, SUMO GUI and netconvert not on PATH |
| Relevant ports | No listeners observed on 3000, 5432, 6379, 8080, 8081, 8181, 8883, 9090, 9092, 30080-30083 |

## WSL2 development runtime

| Item | Measured value |
|---|---|
| Distribution | Ubuntu 26.04 LTS, x86_64 |
| Kernel | 6.18.33.2-microsoft-standard-WSL2 |
| Init / cgroups | systemd; cgroup v2 (`cgroup2fs`) |
| Allocated compute | 4 logical CPUs, 11 GiB RAM |
| Root filesystem | 1007 GiB virtual size, 898 GiB reported free |
| Python | 3.14.4; `pip3` absent |
| Node.js / npm | Node 22.22.1 / npm 9.2.0 |
| Git | 2.53.0 |
| Docker | Client/server 29.7.2, API 1.55, overlayfs, systemd cgroup driver, cgroup v2 |
| Docker capacity | 4 CPUs, 12,543,696,896 bytes memory; service active |
| Kubernetes client | kubectl v1.36.3 |
| Present security/network tools | OpenSSL present |
| Missing tools | SUMO, netconvert, K3s, Helm, Falco, Trivy, Podman and Java |
| Network | DNS/internet resolution succeeded during capture |

Seventeen containers and nineteen images already existed in the shared WSL Docker
engine. Their ownership was not assumed and they were not changed during inventory.

## Target acceptance environment

No Project 2 target host has been supplied or authorized. The following facts
remain unknown and must be captured before target deployment:

- Provider/location, OS/version/kernel and CPU architecture.
- CPU, RAM, free storage and GPU/accelerator availability.
- Network address/access method, DNS, proxy/firewall and required exposed ports.
- Docker/containerd, K3s/MicroK8s, storage class and ingress availability.
- Falco probe compatibility and required privileges.
- Backup destination, TLS/domain ownership and external cloud/account constraints.

WSL2 is suitable for development and disposable integration. WSL2 does not count
as final K3s/Falco or production-like target evidence unless the assessment owner
explicitly accepts it.

## Commands used

- PowerShell CIM queries for OS, CPU, memory, GPU and filesystem capacity.
- `Get-Command` and tool `--version` checks.
- `wsl --status`, `wsl --version`, and `wsl -l -v`.
- WSL `uname`, `free`, `df`, `systemctl`, `stat`, `docker version`, `docker info`,
  and `kubectl version --client`.
- Windows `Get-NetTCPConnection` restricted to planned project ports.

## Constraints and implications

- Use WSL Docker Engine, not a Windows-native Docker CLI, for development.
- Preserve capacity for unrelated existing containers; use project labels/names and
  bounded resources. Never prune shared Docker state.
- Prefer WSL-native working/runtime directories for database and event-broker I/O;
  Windows-mounted source may remain convenient for editing.
- Choose CPU-compatible models and measure inference without assuming a discrete
  accelerator.
- Install SUMO and project dependencies through a documented, pinned setup task.
- Obtain a separate supported Linux target before final K3s/runtime-security
  acceptance.
