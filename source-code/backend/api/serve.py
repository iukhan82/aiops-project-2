"""Run the operator API for the browser stack (Vite proxies /api to it):

    python source-code/backend/api/serve.py            # 127.0.0.1:8100, database aiops_demo
    python source-code/backend/api/serve.py --port N --database NAME

The demo database (built and fed by backend/demo/feeder.py) is separate from the
`aiops` one the verification scripts write to and clean, so a browser session never
sees a half-finished test.

Database credentials come from the environment or, for any key not set there,
from the git-ignored infra/platform/.env. Authentication is always on: this
entry point refuses to start with AIOPS_AUTH_MODE=off, which exists only for the
data-verification scripts.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))


def load_platform_env() -> None:
    env_file = SOURCE_ROOT / "infra" / "platform" / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--database", default="aiops_demo")
    args = parser.parse_args()
    if os.environ.get("AIOPS_AUTH_MODE", "oidc").lower() == "off":
        print("Refusing to serve the browser stack with authentication off.", file=sys.stderr)
        return 2
    load_platform_env()
    os.environ["POSTGRES_DB"] = args.database
    import uvicorn

    from backend.api.app import app
    from backend.redaction import install_log_redaction

    install_log_redaction()  # CTL-16: a WebSocket's access token in the query string must not reach uvicorn's access log
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
