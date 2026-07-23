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
    sig_type = int(os.getenv("POLY_SIGNATURE_TYPE", "1"))
    funder = os.getenv("POLY_FUNDER_ADDRESS") or None
    chain_id = 137  # Polygon mainnet

    from py_clob_client_v2 import ClobClient

    client = ClobClient(
        host=host,
        key=key,
        chain_id=chain_id,
        signature_type=sig_type,
        funder=funder,
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
