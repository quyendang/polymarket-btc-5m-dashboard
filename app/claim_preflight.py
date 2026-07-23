"""Offline claim-worker configuration checks with no relayer submission."""

from __future__ import annotations

import json
import os

from eth_account import Account

from claiming import credential_state, safe_claim_error


def inspect_claim_environment() -> dict:
    state = credential_state()
    report = {
        **state,
        "signer": None,
        "wallet": os.getenv("POLY_FUNDER_ADDRESS") or None,
        "wallet_type": None,
        "relayer_address_matches_signer": None,
        "errors": [],
        "ready": False,
    }

    private_key = os.getenv("POLY_PRIVATE_KEY")
    wallet = os.getenv("POLY_FUNDER_ADDRESS")
    if not private_key or not wallet:
        report["errors"].append("POLY_PRIVATE_KEY or POLY_FUNDER_ADDRESS is missing.")
        return report

    try:
        signer = Account.from_key(private_key).address
        report["signer"] = signer
        from polymarket import PRODUCTION
        from polymarket._internal.wallet import classify_wallet_type

        report["wallet_type"] = classify_wallet_type(
            signer=signer,
            wallet=wallet,
            config=PRODUCTION.wallet_derivation,
        )
    except Exception as exc:
        report["errors"].append(safe_claim_error(exc))
        return report

    if state["auth_mode"] == "relayer":
        relayer_address = os.getenv("POLY_RELAYER_ADDRESS", "")
        matches = relayer_address.lower() == signer.lower()
        report["relayer_address_matches_signer"] = matches
        if not matches:
            report["errors"].append(
                "POLY_RELAYER_ADDRESS must equal the signer address shown with the key."
            )

    if report["wallet_type"] != "DEPOSIT_WALLET":
        report["errors"].append("Claim worker requires a Deposit Wallet (type 3).")

    report["ready"] = bool(
        state["credentials_complete"]
        and report["wallet_type"] == "DEPOSIT_WALLET"
        and report["relayer_address_matches_signer"] is not False
        and not report["errors"]
    )
    return report


def main() -> None:
    report = inspect_claim_environment()
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if report["ready"] else 1)


if __name__ == "__main__":
    main()
