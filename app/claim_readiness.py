"""Secret-safe claim-worker readiness published through PostgreSQL."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import WorkerHeartbeat


CLAIM_ROLE = "claim-worker"
CLAIM_HEARTBEAT_STALE_SECONDS = 30


def claim_readiness(db: Session) -> dict:
    worker = db.get(WorkerHeartbeat, CLAIM_ROLE)
    if worker is None:
        return {
            "worker_online": False,
            "worker_stale": True,
            "worker_status": "missing",
            "last_seen": None,
            "auto_claim_enabled": False,
            "credentials": {},
            "credentials_complete": False,
            "sdk_ready": False,
            "auth_mode": None,
            "clob_auth_mode": None,
            "wallet": None,
            "pending_claims": 0,
            "failed_claims": 0,
            "last_error": None,
            "can_auto_claim": False,
        }

    last_seen = worker.last_seen
    aware = last_seen if last_seen.tzinfo else last_seen.replace(tzinfo=timezone.utc)
    stale = (
        datetime.now(timezone.utc) - aware
    ).total_seconds() > CLAIM_HEARTBEAT_STALE_SECONDS
    detail = dict(worker.detail or {})
    result = {
        "worker_online": not stale,
        "worker_stale": stale,
        "worker_status": worker.status,
        "last_seen": last_seen.isoformat(),
        "auto_claim_enabled": bool(detail.get("auto_claim_enabled")),
        "credentials": {
            name: bool(value)
            for name, value in (detail.get("credentials") or {}).items()
        },
        "credentials_complete": bool(detail.get("credentials_complete")),
        "sdk_ready": bool(detail.get("sdk_ready")),
        "auth_mode": detail.get("auth_mode"),
        "clob_auth_mode": detail.get("clob_auth_mode"),
        "wallet": detail.get("wallet"),
        "pending_claims": int(detail.get("pending_claims") or 0),
        "failed_claims": int(detail.get("failed_claims") or 0),
        "last_error": detail.get("last_error"),
    }
    result["can_auto_claim"] = all((
        result["worker_online"],
        result["auto_claim_enabled"],
        result["credentials_complete"],
        result["sdk_ready"],
    ))
    return result
