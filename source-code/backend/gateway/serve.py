"""P11.03: run the MQTT-to-Kafka gateway as a service.

    python backend/gateway/serve.py       # MQTT_HOST, MQTT_PORT, MQTT_CAFILE, MQTT_CERTFILE, MQTT_KEYFILE, KAFKA_BOOTSTRAP, GATEWAY_OUTBOX

`gateway.Gateway` is a library (the verify scripts drive it); this is the entry point a workload runs. Everything it needs comes from the
environment, so the same image runs anywhere: the broker's address, the gateway's own client certificate (its CN is `gateway`, the one identity
the broker's ACL lets read every device's telemetry), Kafka, and the path of the durable outbox (a volume, so a restart loses nothing accepted
off MQTT and not yet confirmed in Kafka). SIGTERM stops it cleanly: the worker drains, the producer flushes, the outbox closes.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.gateway.gateway import Gateway, GatewayConfig  # noqa: E402

log = logging.getLogger("gateway")
REPORT_EVERY_S = 60


def config_from_env() -> GatewayConfig:
    return GatewayConfig(
        mqtt_host=os.environ["MQTT_HOST"],
        mqtt_port=int(os.environ.get("MQTT_PORT", "8883")),
        mqtt_cafile=Path(os.environ["MQTT_CAFILE"]),
        mqtt_certfile=Path(os.environ["MQTT_CERTFILE"]),
        mqtt_keyfile=Path(os.environ["MQTT_KEYFILE"]),
        kafka_bootstrap=os.environ["KAFKA_BOOTSTRAP"],
        outbox_path=Path(os.environ["GATEWAY_OUTBOX"]),
    )


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}',
    )
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    gateway = Gateway(config_from_env())
    gateway.start()
    log.info("gateway started")
    try:
        while not stop.wait(REPORT_EVERY_S):
            log.info("stats %s", gateway.stats)
    finally:
        gateway.stop()
        log.info("gateway stopped, final stats %s", gateway.stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
