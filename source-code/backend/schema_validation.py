"""P11.06: validate an event against a JSON Schema without paying for the schema again on every event.

`jsonschema.validate(instance, schema)` checks the SCHEMA and builds a validator on every call: 8.8 ms an event for the observation envelope, against 0.13 ms
for a validator built once (measured, 70 times). On the target that was the ceiling of both the gateway and ingestion (an event cost more CPU to validate than
to store). A validator is built once per schema object and reused; the answer is the same: `best_match` of the errors, exactly what `jsonschema.validate` raises.
"""

from __future__ import annotations

from jsonschema import exceptions, validators

_cache: dict[int, tuple[dict, object]] = {}


def first_error(schema: dict, instance: object) -> exceptions.ValidationError | None:
    """The error `jsonschema.validate` would raise, or None when the instance is valid. The schema is checked once, when its validator is built."""
    entry = _cache.get(id(schema))
    if entry is None or entry[0] is not schema:
        cls = validators.validator_for(schema)
        cls.check_schema(schema)
        entry = _cache[id(schema)] = (
            schema,
            cls(schema),
        )  # the schema is kept alive here, so its id cannot be reused
    return exceptions.best_match(entry[1].iter_errors(instance))
