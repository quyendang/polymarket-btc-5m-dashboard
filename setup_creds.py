"""One-time setup: derive Polymarket L2 API credentials from your private key.

Run this once, then paste the printed values into your .env:

    python setup_creds.py

It never writes secrets to disk itself — you copy them into .env manually.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    key = os.getenv("POLY_PRIVATE_KEY")
    if not key or key.startswith("0x...") or key == "":
        raise SystemExit("Set POLY_PRIVATE_KEY in your .env first.")

    host = os.getenv("CLOB_HOST", "https://clob.polymarket.com")
    from py_clob_client_v2 import ClobClient, SignatureTypeV2

    try:
        sig_type = SignatureTypeV2(int(os.getenv("POLY_SIGNATURE_TYPE", "3")))
    except (TypeError, ValueError):
        raise SystemExit("POLY_SIGNATURE_TYPE must be 0 (EOA) or 3 (Deposit Wallet).")
    funder = os.getenv("POLY_FUNDER_ADDRESS") or None
    if sig_type not in (SignatureTypeV2.EOA, SignatureTypeV2.POLY_1271):
        raise SystemExit(
            "CLOB V2 no longer accepts legacy proxy/Safe makers. "
            "Use POLY_SIGNATURE_TYPE=3 with your Deposit Wallet address."
        )
    if sig_type == SignatureTypeV2.POLY_1271 and not funder:
        raise SystemExit(
            "Set POLY_FUNDER_ADDRESS to your Deposit Wallet address first."
        )
    client_funder = funder if sig_type == SignatureTypeV2.POLY_1271 else None
    chain_id = 137  # Polygon mainnet

    client = ClobClient(
        host=host,
        key=key,
        chain_id=chain_id,
        signature_type=sig_type,
        funder=client_funder,
    )

    print("Deriving API credentials from private key...")
    creds = client.create_or_derive_api_key()

    print("\nPaste these into your .env:\n")
    print(f"POLY_API_KEY={creds.api_key}")
    print(f"POLY_API_SECRET={creds.api_secret}")
    print(f"POLY_API_PASSPHRASE={creds.api_passphrase}")
    print("\nDone. Keep these secret.")


if __name__ == "__main__":
    main()
