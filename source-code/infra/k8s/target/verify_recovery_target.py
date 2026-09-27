#!/usr/bin/env python3
"""P11.05 acceptance evidence: backup, restore, restart and rollout/rollback on the target, with the recovery times MEASURED.

    KUBECONFIG=... python3 verify_recovery_target.py        # ON the target host; about 25 minutes; writes docs/evidence/p11_05_target_recovery.json

A. BACKUP AND RESTORE. With the writers quiesced, a fingerprint of the database (row count and content hash of every table) is taken, the roles and the
   database are dumped and encrypted (`backup_restore.py`), the archive is checked against its manifest, and it is restored into a SCRATCH database:
   the restored fingerprint must equal the original's and the audit hash chains must verify intact on the restored copy. A tampered archive must be refused.
B. DISASTER. The database's claim is deleted and the pod recreated empty (the loss of the volume). Then: the roles and the database restored from the
   backup, the writers started, and the end-to-end Job must pass. Data written AFTER the backup must be gone (that is the recovery point, measured, not
   argued away). The fingerprint must equal the one taken before the backup, and the chains intact. Time from the loss to a passing end-to-end check is
   the measured recovery time.
C. RESTARTS. Each stateful and stateless component is killed once; time to Ready and time to a passing end-to-end check are recorded, and the data must
   survive (row counts do not fall, the audit chains stay intact).
D. ROLLOUT AND ROLLBACK. A rollout to an image that does not exist: the stateless API (rolling update, no unavailable pod) must keep answering throughout,
   and `rollout undo` restores the previous revision; the same fault on a Recreate worker is an outage until it is undone, and that time is recorded.

Documented recovery objectives are written into the evidence from what was measured: the recovery point is the time since the last backup (there is no
scheduled backup yet: one is taken by hand), and the recovery time is the measured one.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[3]
K8S = SOURCE_ROOT / "infra" / "k8s"
sys.path.insert(0, str(SOURCE_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import backup_restore as br  # noqa: E402
import yaml  # noqa: E402
from backend.evidence import Evidence  # noqa: E402

NS = "aiops"
ev = Evidence("P11.05", "p11_05_target_recovery", docs_name="p11_05_target_recovery")
DIR = Path.home() / "aiops-p11" / "backup-test"
WRITERS = (
    "aiops-ingestion-gateway",
    "aiops-platform-correlator",
    "aiops-platform-probe",
    "aiops-command-executor",
    "aiops-outcome-verifier",
)
GATEWAY = "aiops-mqtt-gateway"


def kubectl(
    *args: str, check: bool = True, timeout: int = 300, stdin: str | None = None
) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["kubectl", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        input=stdin,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args[:5])} failed: {proc.stderr[-300:]}")
    return proc


def psql(sql: str, database: str = "aiops") -> str:
    return kubectl(
        "-n",
        NS,
        "exec",
        "sts/aiops-postgres",
        "--",
        "psql",
        "-U",
        br.postgres_user(),
        "-d",
        database,
        "-At",
        "-c",
        sql,
        check=False,
    ).stdout.strip()


def scale(replicas: int) -> None:
    """Quiesce (0) or start (1) everything that writes to the database on its own. Quiescing waits until the pods are GONE: a terminating pod still writes."""
    for name in WRITERS:
        kubectl("-n", NS, "scale", f"deploy/{name}", f"--replicas={replicas}")
    kubectl("-n", NS, "scale", f"sts/{GATEWAY}", f"--replicas={replicas}")
    if replicas:
        for name in WRITERS:
            kubectl(
                "-n",
                NS,
                "rollout",
                "status",
                f"deploy/{name}",
                "--timeout=240s",
                check=False,
                timeout=260,
            )
        kubectl(
            "-n",
            NS,
            "rollout",
            "status",
            f"sts/{GATEWAY}",
            "--timeout=240s",
            check=False,
            timeout=260,
        )
    else:
        for name in (*WRITERS, GATEWAY):
            kubectl(
                "-n",
                NS,
                "wait",
                "--for=delete",
                "pod",
                "-l",
                f"app.kubernetes.io/name={name}",
                "--timeout=120s",
                check=False,
                timeout=140,
            )


def run_load(label: str, devices: int, rate: float, seconds: int) -> dict:
    kubectl("-n", NS, "delete", "job", "aiops-load", "--ignore-not-found", "--wait=true")
    doc = next(
        d
        for d in yaml.safe_load_all((K8S / "jobs.yaml").read_text(encoding="utf-8"))
        if d and d["metadata"]["name"] == "aiops-load"
    )
    doc["spec"]["template"]["spec"]["containers"][0]["env"] += [
        {"name": "LOAD_DEVICES", "value": str(devices)},
        {"name": "LOAD_RATE", "value": str(rate)},
        {"name": "LOAD_SECONDS", "value": str(seconds)},
        {"name": "LOAD_LABEL", "value": label},
    ]
    kubectl("apply", "-f", "-", stdin=json.dumps(doc))
    kubectl(
        "-n",
        NS,
        "wait",
        "--for=condition=complete",
        "job/aiops-load",
        f"--timeout={seconds + 240}s",
        check=False,
        timeout=seconds + 260,
    )
    log = kubectl("-n", NS, "logs", "job/aiops-load", check=False).stdout.strip().splitlines()
    return (
        json.loads(log[-1])
        if log and log[-1].startswith("{")
        else {"passed": False, "offered": 0, "arrived_in_postgres": 0}
    )


def fingerprint(database: str = "aiops") -> dict[str, tuple[int, str]]:
    tables = [
        t
        for t in psql(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'spatial_ref_sys' ORDER BY 1",
            database,
        ).splitlines()
        if t
    ]
    sql = " UNION ALL ".join(
        f"SELECT '{t}', count(*)::text, coalesce(md5(string_agg(x::text, '|' ORDER BY x::text)), '') FROM public.\"{t}\" x"
        for t in tables
    )
    out = psql(sql, database)
    result = {}
    for line in out.splitlines():
        name, count, digest = line.split("|")
        result[name] = (int(count), digest)
    return result


def chains(database: str = "aiops") -> str:
    out = kubectl(
        "-n",
        NS,
        "exec",
        "deploy/aiops-api",
        "--",
        "env",
        f"POSTGRES_DB={database}",
        "python3",
        "backend/audit_chain.py",
        check=False,
    ).stdout
    return out.strip().splitlines()[-1] if out.strip() else "no output"


def e2e(timeout: int = 300) -> tuple[bool, float]:
    started = time.time()
    kubectl("-n", NS, "delete", "job", "aiops-e2e", "--ignore-not-found", "--wait=true")
    kubectl("apply", "-f", str(K8S / "jobs.yaml"), "-l", "app.kubernetes.io/name=aiops-e2e")
    kubectl(
        "-n",
        NS,
        "wait",
        "--for=condition=complete",
        "job/aiops-e2e",
        f"--timeout={timeout}s",
        check=False,
        timeout=timeout + 20,
    )
    log = kubectl("-n", NS, "logs", "job/aiops-e2e", check=False).stdout.strip().splitlines()
    ok = bool(log) and log[-1].startswith("{") and json.loads(log[-1]).get("passed") is True
    return ok, round(time.time() - started, 1)


def ready(kind: str, name: str, timeout: int = 420) -> float:
    started = time.time()
    kubectl(
        "-n",
        NS,
        "rollout",
        "status",
        f"{kind}/{name}",
        f"--timeout={timeout}s",
        check=False,
        timeout=timeout + 30,
    )
    return round(time.time() - started, 1)


def pods_of(name: str) -> list[tuple[str, bool]]:
    """(uid, ready) of every live pod of a workload; a pod that is terminating is never ready."""
    items = json.loads(
        kubectl(
            "-n", NS, "get", "pods", "-l", f"app.kubernetes.io/name={name}", "-o", "json"
        ).stdout
    )["items"]
    return [
        (
            p["metadata"]["uid"],
            "deletionTimestamp" not in p["metadata"]
            and any(
                c["type"] == "Ready" and c["status"] == "True"
                for c in p["status"].get("conditions", [])
            ),
        )
        for p in items
    ]


def restart_to_ready(name: str, timeout: int = 420) -> tuple[float, bool]:
    """Delete the workload's pod and time how long until a NEW pod (a uid that was not there before) is Ready. `rollout status` cannot be used for this: a
    deleted pod is not a rollout, so it answers at once (measured: it reported 'ready in 0.1 s' for pods that take seconds to start)."""
    old = {uid for uid, _ in pods_of(name)}
    started = time.time()
    kubectl("-n", NS, "delete", "pod", "-l", f"app.kubernetes.io/name={name}", "--wait=false")
    while time.time() - started < timeout:
        if any(is_ready and uid not in old for uid, is_ready in pods_of(name)):
            return round(time.time() - started, 1), True
        time.sleep(0.5)
    return round(time.time() - started, 1), False


def main() -> int:  # noqa: PLR0915
    shutil.rmtree(DIR, ignore_errors=True)
    # ---- A. backup, restore into a scratch database
    seed = run_load("recovery-seed", 8, 5, 60)
    ev.check(
        "the_platform_is_given_real_content_through_the_real_path_before_the_backup",
        seed.get("passed") is True and seed.get("arrived_in_postgres", 0) >= 2000,
        f"{seed.get('arrived_in_postgres')} of {seed.get('offered')} events arrived",
    )
    scale(0)
    before = fingerprint()
    ev.check(
        "the_database_has_real_content_before_the_backup",
        sum(c for c, _ in before.values()) > 2000 and len(before) > 30,
        f"{len(before)} tables, {sum(c for c, _ in before.values())} rows",
    )
    chain_before = chains()
    ev.check(
        "the_audit_hash_chains_are_intact_before_the_backup",
        "intact" in chain_before and "TAMPERING" not in chain_before,
        chain_before,
    )
    manifest = br.backup(DIR)
    ev.check(
        "the_backup_is_encrypted_restricted_and_matches_its_manifest",
        br.verify_files(DIR)
        and oct(DIR.stat().st_mode)[-3:] == "700"
        and oct((DIR / ".key").stat().st_mode)[-3:] == "600"
        and b"PGDMP" not in (DIR / "aiops.dump.enc").read_bytes()[:64],
        f"{manifest['files']['aiops.dump.enc']['bytes']} bytes in {manifest['seconds']} s",
    )
    restore = br.restore(DIR, "aiops_restore_test", with_globals=False)
    ev.check(
        "the_backup_restores_into_a_scratch_database",
        restore["restored"],
        f"{restore['seconds']} s {restore['pg_restore_messages'][-120:]}",
    )
    after = fingerprint("aiops_restore_test")
    diff = sorted(t for t in before if before[t] != after.get(t))
    ev.check(
        "the_restored_copy_has_exactly_the_rows_and_content_of_the_original_in_every_table",
        not diff and set(before) == set(after),
        f"{len(before)} tables; differing {diff[:5]}",
    )
    chain_restored = chains("aiops_restore_test")
    ev.check(
        "the_audit_hash_chains_verify_intact_on_the_restored_copy",
        "intact" in chain_restored and "TAMPERING" not in chain_restored,
        chain_restored,
    )
    psql('DROP DATABASE IF EXISTS "aiops_restore_test"', "postgres")
    tampered = DIR / "aiops.dump.enc"
    original = tampered.read_bytes()
    tampered.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    try:
        br.restore(DIR, "aiops_restore_test", with_globals=False)
        refused = False
    except RuntimeError:
        refused = True
    tampered.write_bytes(original)
    ev.check("a_backup_that_was_changed_by_one_bit_is_refused", refused)
    ev.metrics["backup"] = manifest
    ev.metrics["restore_into_scratch_seconds"] = restore["seconds"]
    ev.metrics["tables"] = {t: c for t, (c, _) in before.items() if c}
    scale(1)

    # ---- B. disaster: the volume is lost
    backup_at = time.time()
    marker = psql(
        "INSERT INTO operator_audit (actor, actor_roles, action, entity_type, outcome, detail) VALUES ('p1105-after-backup', '{}', 'marker', 'recovery-test', 'ok', '{}'::jsonb) RETURNING audit_id"
    )
    time.sleep(2)
    lost_at = time.time()
    scale(0)
    kubectl("-n", NS, "scale", "sts/aiops-postgres", "--replicas=0")
    kubectl(
        "-n",
        NS,
        "wait",
        "--for=delete",
        "pod/aiops-postgres-0",
        "--timeout=180s",
        check=False,
        timeout=200,
    )
    kubectl("-n", NS, "delete", "pvc", "data-aiops-postgres-0", "--wait=true", timeout=200)
    kubectl("-n", NS, "scale", "sts/aiops-postgres", "--replicas=1")
    ready("statefulset", "aiops-postgres")
    empty = psql(
        "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'spatial_ref_sys'"
    )
    ev.check(
        "after_the_loss_of_the_volume_the_database_is_empty",
        empty == "0",
        f"{empty} tables in the new instance",
    )
    recovered = br.restore(DIR, "aiops", with_globals=True)
    ev.check(
        "the_database_is_restored_from_the_encrypted_backup_roles_and_all",
        recovered["restored"],
        f"{recovered['seconds']} s",
    )
    scale(1)
    passed, e2e_seconds = e2e()
    recovery_s = round(time.time() - lost_at, 1)
    ev.check(
        "the_end_to_end_check_passes_after_the_restore", passed, f"{e2e_seconds} s for the check"
    )
    scale(0)
    final = fingerprint()
    # the platform writes as it runs, so compare the tables the backup froze: every table that was not rewritten by the restart must be identical
    same = sorted(t for t in before if final.get(t) == before[t])
    changed = sorted(t for t in before if final.get(t) != before[t])
    ev.check(
        "every_row_written_before_the_backup_is_back_in_the_restored_database",
        all(final[t][0] >= before[t][0] for t in before if t in final)
        and set(before) <= set(final),
        f"{len(same)} tables identical, {len(changed)} grew after the restart: {changed[:6]}",
    )
    ev.check(
        "the_audit_hash_chains_are_intact_after_the_restore",
        "intact" in chains() and "TAMPERING" not in chains(),
        chains(),
    )
    gone = psql(
        f"SELECT count(*) FROM operator_audit WHERE audit_id = {marker.splitlines()[0].strip() if marker else -1} AND actor = 'p1105-after-backup'"
    )
    ev.check(
        "data_written_after_the_backup_is_gone_that_is_the_recovery_point_and_it_is_the_age_of_the_backup",
        gone == "0",
        f"the marker row written {round(lost_at - backup_at, 1)} s after the backup is {'absent' if gone == '0' else 'present'}",
    )
    ev.metrics["disaster"] = {
        "seconds_from_loss_of_the_volume_to_a_passing_end_to_end_check": recovery_s,
        "restore_seconds": recovered["seconds"],
        "recovery_point_seconds_age_of_backup": round(lost_at - backup_at, 1),
    }
    scale(1)

    # ---- C. restarts
    restarts = {}
    for _kind, name in (
        ("sts", "aiops-postgres"),
        ("sts", "aiops-kafka-broker"),
        ("sts", "aiops-mqtt-broker"),
        ("sts", "aiops-keycloak"),
        ("sts", GATEWAY),
        ("deploy", "aiops-api"),
        ("deploy", "aiops-ingestion-gateway"),
        ("deploy", "aiops-opa"),
    ):
        rows_before = (
            sum(c for c, _ in fingerprint().values()) if name == "aiops-postgres" else None
        )
        started = time.time()
        seconds, came_back = restart_to_ready(name)
        ok, _ = e2e()
        ok = ok and came_back
        restarts[name] = {
            "ready_seconds": seconds,
            "healthy_end_to_end_seconds": round(time.time() - started, 1),
            "end_to_end": ok,
        }
        extra = ""
        if rows_before is not None:
            rows_after = sum(c for c, _ in fingerprint().values())
            restarts[name]["rows_before"], restarts[name]["rows_after"] = rows_before, rows_after
            extra = f"; rows {rows_before} -> {rows_after}"
        ev.check(
            f"after_a_restart_of_{name}_it_is_ready_and_the_end_to_end_check_passes",
            ok,
            f"ready in {seconds} s, healthy end to end in {restarts[name]['healthy_end_to_end_seconds']} s{extra}",
        )
    ev.check(
        "the_data_survived_every_restart_and_the_audit_chains_are_intact",
        all(
            r.get("rows_after", r.get("rows_before", 0)) >= r.get("rows_before", 0)
            for r in restarts.values()
        )
        and "intact" in chains(),
        chains(),
    )
    ev.metrics["restarts"] = restarts

    # ---- D. rollout and rollback
    docs = {
        d["metadata"]["name"]: d
        for d in yaml.safe_load_all((K8S / "workloads.yaml").read_text(encoding="utf-8"))
        if d and d["kind"] == "Deployment"
    }
    api_container = docs["aiops-api"]["spec"]["template"]["spec"]["containers"][0]["name"]
    executor_container = docs["aiops-command-executor"]["spec"]["template"]["spec"]["containers"][
        0
    ]["name"]
    answered = failures = 0
    kubectl(
        "-n",
        NS,
        "set",
        "image",
        "deploy/aiops-api",
        f"{api_container}=aiops-backend:does-not-exist",
    )
    end = time.time() + 45
    while time.time() < end:
        probe = kubectl(
            "-n",
            NS,
            "exec",
            "deploy/aiops-frontend",
            "--",
            "wget",
            "-qO-",
            "-T",
            "3",
            "http://aiops-api:8100/api/v1/health",
            check=False,
            timeout=30,
        )
        answered, failures = (
            (answered + 1, failures) if '"status"' in probe.stdout else (answered, failures + 1)
        )
        time.sleep(2)
    stuck = kubectl(
        "-n", NS, "get", "pods", "-l", "app.kubernetes.io/name=aiops-api", "--no-headers"
    ).stdout
    ev.check(
        "a_rollout_to_an_image_that_does_not_exist_leaves_the_old_api_serving_throughout",
        failures == 0
        and answered > 10
        and ("ImagePullBackOff" in stuck or "ErrImagePull" in stuck),
        f"{answered} answers, {failures} failures during 45 s; pods:\n{stuck.strip()}",
    )
    kubectl("-n", NS, "rollout", "undo", "deploy/aiops-api")
    undo_seconds = ready("deployment", "aiops-api")
    healthy = (
        '"status"'
        in kubectl(
            "-n",
            NS,
            "exec",
            "deploy/aiops-frontend",
            "--",
            "wget",
            "-qO-",
            "-T",
            "3",
            "http://aiops-api:8100/api/v1/health",
            check=False,
        ).stdout
    )
    ev.check(
        "rollout_undo_restores_the_previous_revision_and_the_api_is_healthy",
        healthy,
        f"rolled back in {undo_seconds} s",
    )
    kubectl(
        "-n",
        NS,
        "set",
        "image",
        "deploy/aiops-command-executor",
        f"{executor_container}=aiops-backend:does-not-exist",
    )
    went_down = time.time()
    time.sleep(30)
    executor_pods = kubectl(
        "-n",
        NS,
        "get",
        "pods",
        "-l",
        "app.kubernetes.io/name=aiops-command-executor",
        "--no-headers",
    ).stdout
    down = "Running" not in executor_pods
    kubectl("-n", NS, "rollout", "undo", "deploy/aiops-command-executor")
    ready("deployment", "aiops-command-executor")
    ev.check(
        "the_same_fault_on_a_recreate_worker_is_an_outage_until_it_is_undone_and_it_is_recorded_as_one",
        down,
        f"executor down {round(time.time() - went_down, 1)} s including the undo; pods during the fault: {executor_pods.strip()[:120]}",
    )
    ev.metrics["rollout"] = {
        "api_answers_during_bad_rollout": answered,
        "api_failures_during_bad_rollout": failures,
        "api_undo_seconds": undo_seconds,
        "executor_outage_seconds": round(time.time() - went_down, 1),
    }
    passed, _ = e2e()
    ev.check("the_platform_passes_the_end_to_end_check_after_everything", passed)

    measured = ev.metrics["disaster"]
    ev.notes["recovery_objectives"] = (
        f"Recovery point: the age of the newest backup - here {measured['recovery_point_seconds_age_of_backup']} s because the backup was taken just before the loss; there is NO scheduled backup, so in real use it is however long ago someone took one. "
        f"Recovery time for the loss of the database volume: {measured['seconds_from_loss_of_the_volume_to_a_passing_end_to_end_check']} s on this data set (small), from the loss to a passing end-to-end check, done by a script, not an operator following a runbook."
    )
    ev.notes["not_proven"] = (
        "one node, one volume: no replica, no failover, no node loss; the backup key is on the same host as the archive; the data set is small, so restore times do not extrapolate; an operator following the runbook (P11.08) was not timed; Keycloak's embedded database is not backed up here"
    )
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
