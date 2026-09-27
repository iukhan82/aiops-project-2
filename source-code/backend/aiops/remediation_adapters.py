"""P10.08: what a remediation action actually does. One adapter per kind of control point.

An adapter is the last line of defence, not the first: the policy engine has already decided the action, target and
parameters are allowed. Each adapter still refuses a target the registry does not list for it and a parameter outside its
bounds, so a bug in the worker cannot turn into an action on something unregistered.

An adapter's `ok` says only that the ACTION was carried out. Whether the platform recovered is decided elsewhere, from
Prometheus, independently of anything an adapter reports.

- `DockerAdapter`         restart one allowlisted container and wait for it to report running (and healthy, if it has a
                          health check).
- `ModelRegistryAdapter`  roll the edge model registry back to the previous verified version (`edge.activation`, which
                          re-verifies the target before switching) and restart the runtime that serves it.
- `TraceSamplingAdapter`  write the bounded, self-expiring sampling override (`backend.trace_sampling`).
- `PlanOnlyAdapter`       never executes. It describes what a person would have to do, for actions this single-host
                          repository cannot perform (scale out, fail over, quarantine a device).
"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from backend import trace_sampling
from backend.aiops.remediation import ACTIONS, PLATFORM_ACTOR

SOURCE_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_ROOT = SOURCE_ROOT / "models" / "registry"
RUNTIME_CONTAINER = {"edge-runtime": "aiops-edge-runtime"}


class NotExecutable(Exception):
    """The action is registered but software may not carry it out (plan-only)."""


@dataclass
class Outcome:
    ok: bool
    detail: dict = field(default_factory=dict)


def allowed_targets(adapter: str) -> set[str]:
    return {
        t.id for a in ACTIONS.values() if a.adapter == adapter for t in a.targets if not t.pattern
    }


def docker(*args: str, timeout: float = 120) -> subprocess.CompletedProcess:
    argv = ["docker", *args] if shutil.which("docker") else ["wsl", "-e", "docker", *args]
    return subprocess.run(  # noqa: S603 - fixed argv, target validated against the registry first
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


class DockerAdapter:
    name = "docker"

    def __init__(self, run=docker, sleep=time.sleep, clock=time.monotonic) -> None:  # noqa: ANN001
        self._run, self._sleep, self._clock = run, sleep, clock

    def plan(self, target: str, params: dict) -> dict:
        return {
            "do": f"docker restart {target}",
            "then": "wait until the container reports running and healthy",
        }

    def _state(self, target: str) -> dict | None:
        out = self._run(
            "inspect",
            "-f",
            "{{.State.Status}}|{{.State.StartedAt}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}",
            target,
            timeout=30,
        )
        if out.returncode != 0:
            return None
        status, started, health = (out.stdout.strip().split("|") + ["", ""])[:3]
        return {"status": status, "started_at": started, "health": health}

    def execute(self, target: str, params: dict, timeout_s: float = 90) -> Outcome:
        if target not in allowed_targets(self.name):
            return Outcome(False, {"error": "target is not registered for this adapter"})
        before = self._state(target)
        if before is None:
            return Outcome(False, {"error": "no such container"})
        started = self._clock()
        restart = self._run("restart", "-t", "10", target, timeout=timeout_s)
        if restart.returncode != 0:
            return Outcome(
                False, {"error": "docker restart failed", "stderr": restart.stderr.strip()[-300:]}
            )
        while self._clock() - started < timeout_s:
            now = self._state(target)
            if (
                now
                and now["status"] == "running"
                and now["started_at"] != before["started_at"]
                and now["health"] in ("", "healthy")
            ):
                return Outcome(
                    True,
                    {
                        "before": before,
                        "after": now,
                        "seconds": round(self._clock() - started, 1),
                    },
                )
            self._sleep(1)
        return Outcome(
            False,
            {"error": "container did not report running and healthy in time", "before": before},
        )


class ModelRegistryAdapter:
    name = "model_registry"

    def __init__(
        self, registry_root: Path = REGISTRY_ROOT, restarter: DockerAdapter | None = None
    ) -> None:
        self._root = registry_root
        self._restarter = restarter or DockerAdapter()

    def plan(self, target: str, params: dict) -> dict:
        service, _, model_id = target.partition(":")
        return {
            "do": f"roll {model_id} back to its previous verified version, then restart {RUNTIME_CONTAINER.get(service)}",
        }

    def execute(self, target: str, params: dict, timeout_s: float = 120) -> Outcome:
        from edge.activation import ActivationError, ModelActivator
        from edge.features import FEATURE_VERSION
        from edge.model_runtime import ModelError

        if target not in allowed_targets(self.name):
            return Outcome(False, {"error": "target is not registered for this adapter"})
        service, _, model_id = target.partition(":")
        activator = ModelActivator(self._root / model_id, FEATURE_VERSION)
        before = activator.active_version()
        history = activator.history()
        if history and history[-1].action == "rollback":
            return Outcome(
                False,
                {
                    "error": "already rolled back; another rollback would only return to the version just left",
                    "active": before,
                },
            )
        try:
            activator.rollback(reason="aiops remediation: model failed to serve")
        except (ActivationError, ModelError) as exc:
            return Outcome(
                False,
                {
                    "error": "rollback refused",
                    "code": getattr(exc, "code", "error"),
                    "active": before,
                },
            )
        detail = {"from": before, "to": activator.active_version()}
        restart = self._restarter.execute(RUNTIME_CONTAINER[service], {}, timeout_s=timeout_s)
        return Outcome(restart.ok, {**detail, "restart": restart.detail})


class TraceSamplingAdapter:
    name = "trace_sampling"

    def __init__(self, path: Path = trace_sampling.OVERRIDE_PATH) -> None:
        self._path = path

    def plan(self, target: str, params: dict) -> dict:
        return {
            "do": f"keep {params.get('ratio')} of new traces for {params.get('ttl_s')} s, then return to 100%"
        }

    def execute(self, target: str, params: dict, timeout_s: float = 10) -> Outcome:
        if target not in allowed_targets(self.name):
            return Outcome(False, {"error": "target is not registered for this adapter"})
        try:
            doc = trace_sampling.write_override(
                float(params["ratio"]), float(params["ttl_s"]), PLATFORM_ACTOR, self._path
            )
        except (KeyError, ValueError) as exc:
            return Outcome(False, {"error": f"parameter refused: {exc}"})
        return Outcome(True, {"ratio": doc["ratio"], "expires_at": doc["expires_at"]})

    def undo(self, target: str, detail: dict) -> Outcome:
        trace_sampling.clear_override(self._path)
        return Outcome(True, {"cleared": True})


class PlanOnlyAdapter:
    name = "plan_only"

    def plan(self, target: str, params: dict) -> dict:
        return {
            "do": "nothing is executed by software",
            "for_a_person": "carry this out by hand or extend the platform with a real replica or enforcement point",
            "target": target,
            "params": params,
        }

    def execute(self, target: str, params: dict, timeout_s: float = 0) -> Outcome:
        raise NotExecutable("plan-only action: software must not execute it")


def default_adapters() -> dict:
    return {
        a.name: a
        for a in (
            DockerAdapter(),
            ModelRegistryAdapter(),
            TraceSamplingAdapter(),
            PlanOnlyAdapter(),
        )
    }
