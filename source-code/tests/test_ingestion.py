"""P05.05: ingestion pure-logic unit tests (content hashing). Real-database
behavior (schema/identity/content conflict rejection, idempotent replay
against the real Kafka+Postgres stack) needs the real P05.01/P05.04
infrastructure and is proven by
source-code/backend/ingestion/verify_ingest.py, not here - see
docs/evidence/p05_05_ingestion.json.
"""

from backend.ingestion.ingest import content_hash


def test_content_hash_is_deterministic() -> None:
    event = {"event_id": "x", "device_id": "y", "measurements": [{"name": "a", "value": 1}]}
    assert content_hash(event) == content_hash(dict(event))


def test_content_hash_is_key_order_independent() -> None:
    a = {"event_id": "x", "device_id": "y"}
    b = {"device_id": "y", "event_id": "x"}
    assert content_hash(a) == content_hash(b)


def test_content_hash_changes_with_content() -> None:
    a = {"event_id": "x", "measurements": [{"value": 1}]}
    b = {"event_id": "x", "measurements": [{"value": 2}]}
    assert content_hash(a) != content_hash(b)
