"""P11.06: the ingestion consumer writes in batches, and the guarantee it had per event still holds: offsets move only after the database committed."""

import json

import psycopg
import pytest
from backend.ingestion import ingest


class Message:
    def __init__(self, value: dict | bytes, error=None):
        self._value = value if isinstance(value, bytes) else json.dumps(value).encode()
        self._error = error

    def value(self):
        return self._value

    def error(self):
        return self._error


class Connection:
    def __init__(self, log):
        self.log = log

    def commit(self):
        self.log.append("db commit")

    def rollback(self):
        self.log.append("db rollback")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Consumer:
    def __init__(self, batches, log):
        self.batches, self.log = list(batches), log

    def subscribe(self, _topics):
        pass

    def consume(self, num_messages, timeout):
        return self.batches.pop(0) if self.batches else []

    def commit(self, asynchronous=False):
        self.log.append("offset commit")

    def close(self):
        pass


@pytest.fixture
def rig(monkeypatch):
    log: list = []

    def build(batches, ingest_one):
        monkeypatch.setattr(ingest, "Consumer", lambda _config: Consumer(batches, log))
        monkeypatch.setattr(ingest.psycopg, "connect", lambda _dsn: Connection(log))
        monkeypatch.setattr(ingest, "dsn_from_env", lambda: "")
        monkeypatch.setattr(ingest, "configure", lambda _name: None)
        monkeypatch.setattr(ingest, "load_schema", lambda: {})
        monkeypatch.setattr(ingest, "ingest_one", ingest_one)
        return log

    return build


def outcome(name="inserted"):
    return ingest.IngestResult(ingest.Outcome(name), "id")


def test_a_batch_is_one_database_commit_and_the_offsets_move_only_after_it(rig):
    calls = []

    def fake(conn, event, schema, commit=True):
        calls.append(commit)
        return outcome()

    log = rig([[Message({"n": i}) for i in range(5)]], fake)
    counts = ingest.run("kafka:9092", max_messages=5)
    assert counts == {"inserted": 5}
    assert calls == [False] * 5  # no event commits on its own
    assert log == ["db commit", "offset commit"]  # the database first, the offsets after


def test_a_batch_that_cannot_be_written_is_rolled_back_and_written_one_event_at_a_time(rig):
    state = {"failed": False}

    def fake(conn, event, schema, commit=True):
        if not commit and event["n"] == 2 and not state["failed"]:
            state["failed"] = True
            raise psycopg.errors.DeadlockDetected("boom")
        return outcome()

    log = rig([[Message({"n": i}) for i in range(4)]], fake)
    counts = ingest.run("kafka:9092", max_messages=4)
    assert counts == {"inserted": 4}  # nothing lost
    assert log[0] == "db rollback"  # the failed batch was undone
    assert log[-1] == "offset commit"  # and only then the offsets moved


def test_an_undecodable_message_does_not_hold_back_the_good_ones(rig):
    def fake(conn, event, schema, commit=True):
        return outcome()

    log = rig([[Message({"n": 1}), Message(b"not json"), Message({"n": 3})]], fake)
    counts = ingest.run("kafka:9092", max_messages=3)
    assert counts == {"inserted": 2}
    assert log[-1] == "offset commit"


def test_a_kafka_error_message_is_skipped_not_written(rig):
    seen = []

    def fake(conn, event, schema, commit=True):
        seen.append(event)
        return outcome()

    log = rig([[Message({"n": 1}), Message({"n": 2}, error="broker down")]], fake)
    ingest.run("kafka:9092", max_messages=2)
    assert seen == [{"n": 1}]
    assert "offset commit" in log
