"""Isolated Deposit Wallet redemption worker using Polymarket's relayer SDK."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import timedelta
from typing import Callable

from sqlalchemy import func, or_, select, text

import markets
from app.config import settings
from app.database import SessionLocal, engine, init_db
from app.models import Trade, WorkerHeartbeat, utcnow
from claiming import (
    ClaimExecutor,
    ClaimResult,
    ClaimSubmission,
    ClaimSubmissionUnknownError,
    ClaimTerminalError,
    credential_state,
    safe_claim_error,
)


ROLE = "claim-worker"
ADVISORY_LOCK_KEY = 5_300_202_609
QUEUE_STATUSES = ("pending", "awaiting_resolution", "failed")
WaitHeartbeat = Callable[[], None]


def acquire_process_lock():
    connection = engine.connect()
    if engine.dialect.name == "postgresql":
        acquired = bool(connection.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": ADVISORY_LOCK_KEY}
        ))
        if not acquired:
            connection.close()
            return None
    return connection


def queue_counts() -> tuple[int, int]:
    with SessionLocal() as db:
        pending = db.scalar(select(func.count(Trade.id)).where(
            Trade.claim_required.is_(True),
            Trade.claim_status.in_((
                "pending", "awaiting_resolution", "checking", "submitting", "submitted",
            )),
        )) or 0
        failed = db.scalar(select(func.count(Trade.id)).where(
            Trade.claim_required.is_(True),
            Trade.claim_status.in_(("failed", "manual_required")),
        )) or 0
        return int(pending), int(failed)


def heartbeat(status: str, *, sdk_ready: bool = False, wallet: str | None = None,
              last_error: str | None = None, detail: dict | None = None) -> None:
    state = credential_state()
    pending, failed = queue_counts()
    payload = {
        **state,
        "auto_claim_enabled": settings.auto_claim_enabled,
        "sdk_ready": sdk_ready,
        "wallet": wallet,
        "pending_claims": pending,
        "failed_claims": failed,
        "last_error": last_error,
        **(detail or {}),
    }
    with SessionLocal() as db:
        item = db.get(WorkerHeartbeat, ROLE)
        if item is None:
            item = WorkerHeartbeat(role=ROLE)
            db.add(item)
        item.status = status
        item.detail = payload
        item.last_seen = utcnow()
        db.commit()


def claim_next() -> str | None:
    now = utcnow()
    with SessionLocal() as db:
        statement = (
            select(Trade)
            .where(
                Trade.claim_required.is_(True),
                Trade.won.is_(True),
                Trade.claim_status.in_(QUEUE_STATUSES),
                Trade.claim_attempts < settings.claim_max_attempts,
                or_(
                    Trade.claim_next_attempt_at.is_(None),
                    Trade.claim_next_attempt_at <= now,
                ),
            )
            .order_by(Trade.created_at)
        )
        if engine.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        item = db.scalar(statement)
        if item is None:
            return None
        item.claim_status = "checking"
        item.claim_updated_at = now
        db.commit()
        return item.id


def _schedule(trade_id: str, status: str, seconds: int, error: str | None = None) -> None:
    with SessionLocal() as db:
        item = db.get(Trade, trade_id)
        if item is None:
            return
        item.claim_status = status
        item.claim_error = error
        item.claim_next_attempt_at = utcnow() + timedelta(seconds=max(seconds, 1))
        item.claim_updated_at = utcnow()
        db.commit()


def _condition_for(trade_id: str) -> str | None:
    with SessionLocal() as db:
        item = db.get(Trade, trade_id)
        if item is None:
            return None
        if item.condition_id:
            return item.condition_id
        window_ts = item.window_ts

    try:
        condition_id = markets.fetch_market(window_ts).condition_id
    except Exception as exc:
        _schedule(
            trade_id,
            "awaiting_resolution",
            settings.claim_resolution_retry_seconds,
            safe_claim_error(exc),
        )
        return None
    if not condition_id:
        _schedule(
            trade_id,
            "awaiting_resolution",
            settings.claim_resolution_retry_seconds,
            "Condition ID chưa khả dụng trên Gamma.",
        )
        return None
    with SessionLocal() as db:
        item = db.get(Trade, trade_id)
        if item:
            item.condition_id = condition_id
            item.claim_updated_at = utcnow()
            db.commit()
    return condition_id


def _start_attempt(trade_id: str) -> int:
    with SessionLocal() as db:
        item = db.get(Trade, trade_id)
        if item is None:
            return 0
        item.claim_attempts += 1
        item.claim_status = "submitting"
        item.claim_error = None
        item.claim_next_attempt_at = None
        item.claim_updated_at = utcnow()
        attempts = item.claim_attempts
        db.commit()
        return attempts


def _record_failure(trade_id: str, error: Exception, *, attempt_started: bool) -> None:
    safe_error = safe_claim_error(error)
    with SessionLocal() as db:
        item = db.get(Trade, trade_id)
        if item is None:
            return
        if not attempt_started:
            item.claim_attempts += 1
        attempts = item.claim_attempts
        item.claim_status = (
            "manual_required" if attempts >= settings.claim_max_attempts else "failed"
        )
        item.claim_error = safe_error
        item.claim_updated_at = utcnow()
        if item.claim_status == "failed":
            delay = min(
                settings.claim_retry_base_seconds * (2 ** max(attempts - 1, 0)),
                900,
            )
            item.claim_next_attempt_at = utcnow() + timedelta(seconds=delay)
        else:
            item.claim_next_attempt_at = None
        db.commit()


def _store_submission(trade_id: str, transaction_id: str | None,
                      transaction_hash: str | None) -> None:
    with SessionLocal() as db:
        item = db.get(Trade, trade_id)
        if item is None:
            return
        item.claim_status = "submitted"
        item.claim_transaction_id = transaction_id
        item.claim_transaction_hash = transaction_hash
        item.claim_error = None
        item.claim_next_attempt_at = None
        item.claim_updated_at = utcnow()
        db.commit()


def _mark_claimed(condition_id: str, transaction_id: str | None,
                  transaction_hash: str | None) -> None:
    now = utcnow()
    with SessionLocal() as db:
        items = db.scalars(select(Trade).where(
            Trade.condition_id == condition_id,
            Trade.claim_required.is_(True),
            Trade.won.is_(True),
            Trade.claim_status != "acknowledged",
        )).all()
        for item in items:
            item.claim_status = "claimed"
            item.claim_transaction_id = transaction_id or item.claim_transaction_id
            item.claim_transaction_hash = transaction_hash or item.claim_transaction_hash
            item.claim_error = None
            item.claim_next_attempt_at = None
            item.claim_updated_at = now
            item.claimed_at = now
        db.commit()


def _wait_for_confirmation(executor: ClaimExecutor, submission: ClaimSubmission,
                           on_wait: WaitHeartbeat | None = None) -> ClaimResult:
    if on_wait is None:
        return executor.wait(submission)
    interval = max(min(settings.claim_poll_seconds, 10), 1)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(executor.wait, submission)
        while True:
            try:
                return future.result(timeout=interval)
            except FutureTimeoutError:
                on_wait()


def _defer_submitted(trade_id: str, error: Exception | str) -> None:
    _schedule(
        trade_id,
        "submitted",
        settings.claim_reconcile_seconds,
        safe_claim_error(error),
    )


def _reconcile_unknown_submission(trade_id: str) -> None:
    with SessionLocal() as db:
        item = db.get(Trade, trade_id)
        if item is None:
            return
        item.claim_attempts += 1
        item.claim_updated_at = utcnow()
        item.claim_error = (
            "Submission outcome is unknown and no relayer transactionID was found."
        )
        if item.claim_attempts >= settings.claim_max_attempts:
            item.claim_status = "manual_required"
            item.claim_next_attempt_at = None
        else:
            item.claim_status = "submitted"
            item.claim_next_attempt_at = utcnow() + timedelta(
                seconds=max(settings.claim_reconcile_seconds, 1)
            )
        db.commit()


def process_trade(trade_id: str, executor: ClaimExecutor,
                  on_wait: WaitHeartbeat | None = None) -> None:
    condition_id = _condition_for(trade_id)
    if not condition_id:
        return
    try:
        if not executor.is_redeemable(condition_id):
            _schedule(
                trade_id,
                "awaiting_resolution",
                settings.claim_resolution_retry_seconds,
            )
            return
    except Exception as exc:
        _schedule(
            trade_id,
            "failed",
            settings.claim_retry_base_seconds,
            safe_claim_error(exc),
        )
        return

    _start_attempt(trade_id)
    try:
        submission = executor.submit(condition_id)
    except ClaimSubmissionUnknownError as exc:
        _store_submission(trade_id, None, None)
        _defer_submitted(trade_id, exc)
        return
    except Exception as exc:
        _record_failure(trade_id, exc, attempt_started=True)
        return

    _store_submission(
        trade_id,
        submission.transaction_id,
        submission.transaction_hash,
    )
    try:
        result = _wait_for_confirmation(executor, submission, on_wait)
    except ClaimTerminalError as exc:
        _record_failure(trade_id, exc, attempt_started=True)
    except Exception as exc:
        _defer_submitted(trade_id, exc)
    else:
        _mark_claimed(
            condition_id,
            result.transaction_id,
            result.transaction_hash,
        )


def claim_next_submitted() -> str | None:
    now = utcnow()
    with SessionLocal() as db:
        statement = (
            select(Trade)
            .where(
                Trade.claim_required.is_(True),
                Trade.won.is_(True),
                Trade.claim_status == "submitted",
                or_(
                    Trade.claim_next_attempt_at.is_(None),
                    Trade.claim_next_attempt_at <= now,
                ),
            )
            .order_by(Trade.claim_updated_at, Trade.created_at)
        )
        if engine.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        item = db.scalar(statement)
        if item is None:
            return None
        item.claim_next_attempt_at = now + timedelta(
            seconds=max(settings.claim_reconcile_seconds, 1)
        )
        item.claim_updated_at = now
        db.commit()
        return item.id


def process_submitted(trade_id: str, executor: ClaimExecutor,
                      on_wait: WaitHeartbeat | None = None) -> None:
    with SessionLocal() as db:
        item = db.get(Trade, trade_id)
        if item is None or not item.condition_id:
            return
        condition_id = item.condition_id
        transaction_id = item.claim_transaction_id
        transaction_hash = item.claim_transaction_hash

    if transaction_id:
        try:
            submission = executor.resume(transaction_id, transaction_hash)
            result = _wait_for_confirmation(executor, submission, on_wait)
        except ClaimTerminalError as exc:
            _record_failure(trade_id, exc, attempt_started=True)
        except Exception as exc:
            _defer_submitted(trade_id, exc)
        else:
            _mark_claimed(
                condition_id,
                result.transaction_id,
                result.transaction_hash,
            )
        return

    try:
        recovered = executor.find_recent_submission(condition_id)
    except Exception as exc:
        _defer_submitted(trade_id, exc)
        return
    if recovered is not None:
        _store_submission(
            trade_id,
            recovered.transaction_id,
            recovered.transaction_hash,
        )
        try:
            result = _wait_for_confirmation(executor, recovered, on_wait)
        except ClaimTerminalError as exc:
            _record_failure(trade_id, exc, attempt_started=True)
        except Exception as exc:
            _defer_submitted(trade_id, exc)
        else:
            _mark_claimed(
                condition_id,
                result.transaction_id,
                result.transaction_hash,
            )
        return

    # A missing position is not enough evidence that redemption succeeded: the
    # Data API can lag or be temporarily incomplete. Only a confirmed relayer
    # transaction may mark the claim complete.
    _reconcile_unknown_submission(trade_id)


def recover_inflight() -> None:
    now = utcnow()
    with SessionLocal() as db:
        checking = db.scalars(select(Trade).where(
            Trade.claim_status == "checking",
        )).all()
        for item in checking:
            item.claim_status = "failed"
            item.claim_error = "Claim worker restarted before submission began."
            item.claim_next_attempt_at = now
            item.claim_updated_at = now
        submitting = db.scalars(select(Trade).where(
            Trade.claim_status == "submitting",
        )).all()
        for item in submitting:
            item.claim_status = "submitted"
            item.claim_error = (
                "Claim worker restarted while submission outcome was unknown."
            )
            item.claim_next_attempt_at = now + timedelta(
                seconds=max(settings.claim_reconcile_seconds, 1)
            )
            item.claim_updated_at = now
        submitted = db.scalars(select(Trade).where(
            Trade.claim_status == "submitted",
            Trade.claim_next_attempt_at.is_(None),
        )).all()
        for item in submitted:
            item.claim_next_attempt_at = now
        db.commit()

def main() -> None:
    init_db()
    process_lock = acquire_process_lock()
    if process_lock is None:
        while True:
            heartbeat("standby", last_error="Another claim-worker owns the advisory lock.")
            time.sleep(max(settings.claim_poll_seconds, 1))

    executor: ClaimExecutor | None = None
    try:
        while True:
            state = credential_state()
            if not settings.auto_claim_enabled:
                heartbeat("disabled")
                time.sleep(max(settings.claim_poll_seconds, 1))
                continue
            if not state["credentials_complete"]:
                heartbeat("blocked", last_error="Claim credentials are incomplete.")
                time.sleep(max(settings.claim_poll_seconds, 1))
                continue
            if executor is None:
                try:
                    executor = ClaimExecutor()
                    recover_inflight()
                except Exception as exc:
                    heartbeat("blocked", last_error=safe_claim_error(exc))
                    time.sleep(max(settings.claim_poll_seconds, 1))
                    continue

            trade_id = claim_next()
            if trade_id:
                heartbeat("claiming", sdk_ready=True, wallet=executor.wallet,
                          detail={"trade_id": trade_id})
                process_trade(
                    trade_id,
                    executor,
                    on_wait=lambda: heartbeat(
                        "confirming",
                        sdk_ready=True,
                        wallet=executor.wallet,
                        detail={"trade_id": trade_id},
                    ),
                )
            else:
                submitted_id = claim_next_submitted()
                if submitted_id:
                    heartbeat(
                        "reconciling",
                        sdk_ready=True,
                        wallet=executor.wallet,
                        detail={"trade_id": submitted_id},
                    )
                    process_submitted(
                        submitted_id,
                        executor,
                        on_wait=lambda: heartbeat(
                            "reconciling",
                            sdk_ready=True,
                            wallet=executor.wallet,
                            detail={"trade_id": submitted_id},
                        ),
                    )
                else:
                    heartbeat("idle", sdk_ready=True, wallet=executor.wallet)
                    time.sleep(max(settings.claim_poll_seconds, 1))
    finally:
        if executor is not None:
            executor.close()
        process_lock.close()


if __name__ == "__main__":
    main()
