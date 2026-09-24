"""Run the scenario-control API for the browser stack (Vite proxies /scenario-control to it):

    python source-code/backend/scenario_control/serve.py            # 127.0.0.1:8101, database aiops_demo
    python source-code/backend/scenario_control/serve.py --port N --database NAME

A separate process from the operator API on purpose: the demo controls have their own routes, audit trail and role. Database
credentials come from the environment or, for any key not set there, from the git-ignored infra/platform/.env.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8101)
    parser.add_argument("--database", default="aiops_demo")
    args = parser.parse_args()
    from backend.demo import world

    world.load_platform_env()
    world.use_database(args.database)
    import uvicorn

    from backend.redaction import install_log_redaction
    from backend.scenario_control.app import app

    install_log_redaction()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
