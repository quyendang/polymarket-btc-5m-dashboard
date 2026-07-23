"""One Railway image, selected at runtime by SERVICE_ROLE."""

from __future__ import annotations

import os
import subprocess
import sys

from sqlalchemy import text

from app.database import engine


MIGRATION_LOCK_KEY = 5_300_202_608


def migrate() -> None:
    connection = engine.connect()
    try:
        if engine.dialect.name == "postgresql":
            connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": MIGRATION_LOCK_KEY})
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
    finally:
        if engine.dialect.name == "postgresql":
            try:
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK_KEY})
            except Exception:
                pass
        connection.close()


def main() -> None:
    role = os.getenv("SERVICE_ROLE", "web")
    migrate()
    if role == "web":
        import uvicorn

        uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
        return
    if role == "trader-worker":
        from app.trader_worker import main as worker_main
        from app.worker_health import start_health_server

        start_health_server(role)
        worker_main()
        return
    if role == "backtest-worker":
        from app.backtest_worker import main as worker_main
        from app.worker_health import start_health_server

        start_health_server(role)
        worker_main()
        return
    if role == "claim-worker":
        from app.claim_worker import main as worker_main
        from app.worker_health import start_health_server

        start_health_server(role)
        worker_main()
        return
    raise SystemExit(f"Unknown SERVICE_ROLE: {role}")


if __name__ == "__main__":
    main()
