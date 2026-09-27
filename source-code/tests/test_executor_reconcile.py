"""REC-02: the command executor closes what a dead executor left `executing` before it carries on (`executor_worker.reconcile`).

The database is stood in for: what matters is which commands are judged orphans (all of them at start-up, only the old ones while running) and what is done to them (failed, not retried, saying the
outcome is unknown, never `executed`). `acceptance/lab_executor_restart.py` does it against the real database with a real killed process.
"""

from backend.control import executor_worker
from backend.roles import EXECUTOR


class Cursor:
    def __init__(self, conn) -> None:
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        return None

    def execute(self, sql: str, params: tuple) -> None:
        self.conn.queries.append((sql, params))

    def fetchall(self):
        return [(cid,) for cid in self.conn.orphans]


class Connection:
    def __init__(self, orphans: list[str]) -> None:
        self.orphans = orphans
        self.queries: list[tuple[str, tuple]] = []
        self.commits = 0

    def cursor(self) -> Cursor:
        return Cursor(self)

    def commit(self) -> None:
        self.commits += 1


def reconcile(
    monkeypatch, orphans: list[str], at_start: bool
) -> tuple[list[str], list[tuple], Connection]:
    moved: list[tuple] = []
    monkeypatch.setattr(
        executor_worker.command_repo,
        "transition_command",
        lambda conn, command_id, status, actor, note, **kw: moved.append(
            (command_id, status, actor, note, kw)
        ),
    )
    conn = Connection(orphans)
    return executor_worker.reconcile(conn, at_start=at_start), moved, conn


def test_at_start_up_every_executing_command_is_an_orphan_and_is_failed_not_executed(monkeypatch):
    found, moved, conn = reconcile(monkeypatch, ["a", "b"], at_start=True)
    assert found == ["a", "b"]
    assert (
        conn.queries[0][1][0] is True
    )  # the age condition is off at start-up: there is one executor and it was not running
    assert [(m[0], m[1], m[2]) for m in moved] == [
        ("a", "failed", EXECUTOR),
        ("b", "failed", EXECUTOR),
    ]


def test_an_orphan_is_failed_with_the_outcome_said_to_be_unknown_and_is_not_retryable(monkeypatch):
    _, moved, _ = reconcile(monkeypatch, ["a"], at_start=True)
    (_command, _status, _actor, note, kwargs) = moved[0]
    assert "not known" in note
    assert kwargs["error_code"] == "internal" and kwargs["error_retryable"] is False
    assert "unknown" in kwargs["error_message"] and "not repeated" in kwargs["error_message"]


def test_while_running_only_a_command_older_than_the_adapters_time_limit_is_judged_an_orphan(
    monkeypatch,
):
    found, _moved, conn = reconcile(monkeypatch, [], at_start=False)
    assert found == []
    sql, params = conn.queries[0]
    assert params == (False, executor_worker.ORPHAN_AFTER_S) and "updated_at < now()" in sql


def test_the_orphan_limit_is_longer_than_the_adapters_own_time_limit():
    from backend.control.simulator_adapters import EXECUTION_TIMEOUT_S

    assert executor_worker.ORPHAN_AFTER_S > EXECUTION_TIMEOUT_S
