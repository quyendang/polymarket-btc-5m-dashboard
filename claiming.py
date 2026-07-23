"""Browser-free redemption through Polymarket's official Deposit Wallet SDK."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Any


SIGNER_CREDENTIAL_KEYS = (
    "POLY_PRIVATE_KEY",
    "POLY_FUNDER_ADDRESS",
)
CLOB_CREDENTIAL_KEYS = (
    "POLY_API_KEY",
    "POLY_API_SECRET",
    "POLY_API_PASSPHRASE",
)
BUILDER_CREDENTIAL_KEYS = (
    "POLY_BUILDER_API_KEY",
    "POLY_BUILDER_API_SECRET",
    "POLY_BUILDER_API_PASSPHRASE",
)
RELAYER_CREDENTIAL_KEYS = (
    "POLY_RELAYER_API_KEY",
    "POLY_RELAYER_ADDRESS",
)


@dataclass
class ClaimSubmission:
    transaction_id: str | None
    transaction_hash: str | None
    handle: Any


@dataclass(frozen=True)
class ClaimResult:
    transaction_id: str | None
    transaction_hash: str | None


class ClaimTerminalError(RuntimeError):
    """The relayer explicitly rejected or failed a submitted transaction."""


class ClaimSubmissionUnknownError(RuntimeError):
    """The submit request may have reached the relayer but returned no ID."""


def claim_metadata(condition_id: str) -> str:
    return f"BTC 5m auto-claim {condition_id}"


def credential_state() -> dict[str, Any]:
    presence = {
        name: bool(os.getenv(name))
        for name in (
            SIGNER_CREDENTIAL_KEYS
            + CLOB_CREDENTIAL_KEYS
            + BUILDER_CREDENTIAL_KEYS
            + RELAYER_CREDENTIAL_KEYS
        )
    }
    builder_complete = all(presence[name] for name in BUILDER_CREDENTIAL_KEYS)
    relayer_complete = all(presence[name] for name in RELAYER_CREDENTIAL_KEYS)
    signer_complete = all(presence[name] for name in SIGNER_CREDENTIAL_KEYS)
    clob_any = any(presence[name] for name in CLOB_CREDENTIAL_KEYS)
    clob_complete = all(presence[name] for name in CLOB_CREDENTIAL_KEYS)
    try:
        signature_type = int(os.getenv("POLY_SIGNATURE_TYPE", "3"))
    except ValueError:
        signature_type = -1
    auth_mode = "relayer" if relayer_complete else "builder" if builder_complete else None
    return {
        "credentials": presence,
        "credentials_complete": bool(
            signer_complete
            and auth_mode
            and signature_type == 3
            and (not clob_any or clob_complete)
        ),
        "auth_mode": auth_mode,
        "clob_auth_mode": (
            "provided" if clob_complete else "incomplete" if clob_any else "derived"
        ),
        "signature_type": signature_type if signature_type in (0, 1, 2, 3) else None,
    }


def safe_claim_error(value: Any) -> str:
    text = str(value)
    for name in (
        SIGNER_CREDENTIAL_KEYS
        + CLOB_CREDENTIAL_KEYS
        + BUILDER_CREDENTIAL_KEYS
        + RELAYER_CREDENTIAL_KEYS
    ):
        secret = os.getenv(name)
        if secret and len(secret) >= 6:
            text = text.replace(secret, "<redacted>")
    return text[:1200]


class ClaimExecutor:
    def __init__(self, client=None):
        self._client = client or self._build_client()

    @staticmethod
    def _build_client():
        from polymarket import (
            PRODUCTION,
            ApiKeyCreds,
            BuilderApiKey,
            RelayerApiKey,
            SecureClient,
        )

        state = credential_state()
        if not state["credentials_complete"]:
            raise RuntimeError(
                "Claim credentials incomplete: Deposit Wallet type 3 and relayer auth required"
            )

        if state["auth_mode"] == "relayer":
            relayer_key = RelayerApiKey(
                key=os.environ["POLY_RELAYER_API_KEY"],
                address=os.environ["POLY_RELAYER_ADDRESS"],
            )
        else:
            relayer_key = BuilderApiKey(
                key=os.environ["POLY_BUILDER_API_KEY"],
                secret=os.environ["POLY_BUILDER_API_SECRET"],
                passphrase=os.environ["POLY_BUILDER_API_PASSPHRASE"],
            )

        credentials = None
        if state["clob_auth_mode"] == "provided":
            credentials = ApiKeyCreds(
                apiKey=os.environ["POLY_API_KEY"],
                secret=os.environ["POLY_API_SECRET"],
                passphrase=os.environ["POLY_API_PASSPHRASE"],
            )
        environment = replace(
            PRODUCTION,
            clob_url=os.getenv("CLOB_HOST", PRODUCTION.clob_url),
            gamma_url=os.getenv("GAMMA_HOST", PRODUCTION.gamma_url),
            data_url=os.getenv("POLY_DATA_HOST", PRODUCTION.data_url),
            relayer_url=os.getenv("POLY_RELAYER_URL", PRODUCTION.relayer_url),
            rpc_url=os.getenv("POLYGON_RPC_URL", PRODUCTION.rpc_url),
        )
        client = SecureClient.create(
            private_key=os.environ["POLY_PRIVATE_KEY"],
            wallet=os.environ["POLY_FUNDER_ADDRESS"],
            environment=environment,
            credentials=credentials,
            api_key=relayer_key,
        )
        if client.wallet_type != "DEPOSIT_WALLET":
            wallet_type = client.wallet_type
            client.close()
            raise RuntimeError(
                "Claim worker requires a Deposit Wallet; "
                f"SDK detected {wallet_type}"
            )
        return client

    @property
    def wallet(self) -> str:
        return str(self._client.wallet)

    def is_redeemable(self, condition_id: str) -> bool:
        page = self._client.list_positions(
            market=[condition_id],
            redeemable=True,
            page_size=20,
        ).first_page()
        condition = condition_id.lower()
        return any(
            str(item.condition_id).lower() == condition
            and bool(item.redeemable)
            and (item.size is None or item.size > 0)
            for item in page.items
        )

    def submit(self, condition_id: str) -> ClaimSubmission:
        try:
            handle = self._client.redeem_positions(
                condition_id=condition_id,
                metadata=claim_metadata(condition_id),
            )
        except Exception as exc:
            if self._submission_outcome_unknown(exc):
                raise ClaimSubmissionUnknownError(str(exc)) from exc
            raise
        transaction_id = getattr(handle, "transaction_id", None)
        if not transaction_id:
            raise ClaimSubmissionUnknownError(
                "Relayer submission returned without transactionID"
            )
        return ClaimSubmission(
            transaction_id=str(transaction_id),
            transaction_hash=(
                str(handle.transaction_hash)
                if getattr(handle, "transaction_hash", None) else None
            ),
            handle=handle,
        )

    @staticmethod
    def _submission_outcome_unknown(error: Exception) -> bool:
        from polymarket.errors import (
            RequestRejectedError,
            TransportError,
            UnexpectedResponseError,
        )

        if isinstance(error, (TransportError, UnexpectedResponseError)):
            return True
        if isinstance(error, RequestRejectedError):
            return error.status >= 500
        return error.__class__.__module__.startswith("pydantic")

    def find_recent_submission(self, condition_id: str) -> ClaimSubmission | None:
        """Recover a transaction ID when the original submit response was lost."""
        response = self._client._ctx.relayer.get_json("/transactions")
        if isinstance(response, dict):
            rows = response.get("items") or response.get("transactions") or []
        else:
            rows = response
        if not isinstance(rows, list):
            return None

        expected_metadata = claim_metadata(condition_id)
        signer = str(self._client._ctx.signer.address).lower()
        wallet = self.wallet.lower()
        for row in rows:
            if not isinstance(row, dict) or row.get("metadata") != expected_metadata:
                continue
            owner = row.get("from") or row.get("owner")
            account_wallet = row.get("proxyAddress") or row.get("depositWallet")
            if owner and str(owner).lower() != signer:
                continue
            if account_wallet and str(account_wallet).lower() != wallet:
                continue
            if not owner and not account_wallet:
                continue
            transaction_id = row.get("transactionID") or row.get("transaction_id")
            if not transaction_id:
                continue
            transaction_hash = row.get("transactionHash") or row.get("transaction_hash")
            return self.resume(
                str(transaction_id),
                str(transaction_hash) if transaction_hash else None,
            )
        return None

    def resume(self, transaction_id: str,
               transaction_hash: str | None = None) -> ClaimSubmission:
        """Reattach to a persisted relayer transaction after a worker restart."""
        from polymarket.transactions import SyncGaslessTransactionHandle

        environment = self._client.environment
        handle = SyncGaslessTransactionHandle(
            transaction_id=transaction_id,
            transaction_hash=transaction_hash,
            _relayer=self._client._ctx.relayer,
            _max_polls=environment.relayer_max_polls,
            _poll_delay_s=environment.relayer_poll_frequency_ms / 1000,
        )
        return ClaimSubmission(
            transaction_id=transaction_id,
            transaction_hash=transaction_hash,
            handle=handle,
        )

    def wait(self, submission: ClaimSubmission) -> ClaimResult:
        from polymarket.errors import TransactionFailedError

        try:
            outcome = submission.handle.wait()
        except TransactionFailedError as exc:
            raise ClaimTerminalError(str(exc)) from exc
        return ClaimResult(
            transaction_id=(
                getattr(outcome, "transaction_id", None) or submission.transaction_id
            ),
            transaction_hash=(
                str(getattr(outcome, "transaction_hash", None))
                if getattr(outcome, "transaction_hash", None)
                else submission.transaction_hash
            ),
        )

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if close:
            close()
