"""Secret-safe trader readiness shared by the web API and worker."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import WorkerHeartbeat


TRADER_ROLE = "trader-worker"
HEARTBEAT_STALE_SECONDS = 20
CREDENTIAL_KEYS = (
    "POLY_PRIVATE_KEY",
    "POLY_API_KEY",
    "POLY_API_SECRET",
    "POLY_API_PASSPHRASE",
    "POLY_FUNDER_ADDRESS",
)


def credential_presence() -> dict[str, bool]:
    """Return booleans only; secret values never leave the worker process."""
    return {name: bool(os.getenv(name)) for name in CREDENTIAL_KEYS}


def credentials_complete(presence: dict[str, bool], signature_type: int) -> bool:
    if signature_type not in (0, 3):
        return False
    required = [
        "POLY_PRIVATE_KEY", "POLY_API_KEY", "POLY_API_SECRET",
        "POLY_API_PASSPHRASE",
    ]
    if signature_type == 3:
        required.append("POLY_FUNDER_ADDRESS")
    return all(presence.get(name, False) for name in required)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def trader_readiness(db: Session) -> dict:
    """Read readiness published by trader-worker without inspecting web env."""
    worker = db.get(WorkerHeartbeat, TRADER_ROLE)
    if worker is None:
        return {
            "worker_online": False,
            "worker_stale": True,
            "worker_status": "missing",
            "last_seen": None,
            "credentials": {name: False for name in CREDENTIAL_KEYS},
            "credentials_complete": False,
            "api_valid": False,
            "balance_check_ok": False,
            "usdc_balance": None,
            "live_trading_enabled": False,
            "signature_type": None,
            "preflight_at": None,
            "can_start_live": False,
        }

    now = datetime.now(timezone.utc)
    stale = (now - _aware(worker.last_seen)).total_seconds() > HEARTBEAT_STALE_SECONDS
    published = dict((worker.detail or {}).get("readiness") or {})
    credentials = {
        name: bool((published.get("credentials") or {}).get(name, False))
        for name in CREDENTIAL_KEYS
    }
    result = {
        "worker_online": not stale,
        "worker_stale": stale,
        "worker_status": worker.status,
        "last_seen": worker.last_seen.isoformat(),
        "credentials": credentials,
        "credentials_complete": bool(published.get("credentials_complete")),
        "api_valid": bool(published.get("api_valid")),
        "balance_check_ok": bool(published.get("balance_check_ok")),
        "usdc_balance": published.get("usdc_balance"),
        "live_trading_enabled": bool(published.get("live_trading_enabled")),
        "signature_type": published.get("signature_type"),
        "preflight_at": published.get("preflight_at"),
    }
    balance = result["usdc_balance"]
    balance_ready = isinstance(balance, (int, float)) and balance > 0
    result["can_start_live"] = all((
        result["worker_online"],
        result["credentials_complete"],
        result["api_valid"],
        result["live_trading_enabled"],
        balance_ready,
    ))
    return result
