"""Background auto-claimer for resolved winning positions (Playwright).

When a 5-min market resolves, winnings sit as redeemable positions until you
claim them. This script logs into Polymarket in a real browser context and
clicks the claim/redeem controls on a loop.

    playwright install chromium      # one-time
    python auto_claim.py             # runs until stopped

IMPORTANT — this is a scaffold. Polymarket's DOM and login flow change over
time, so the SELECTORS below must be verified against the live site and updated.
Run once with HEADLESS=0 to watch it and adjust selectors. As an alternative to
UI automation, positions can also be redeemed on-chain via the CTF contract
using the conditionId — more robust but out of scope here.

Auth: the cleanest approach is to reuse a logged-in browser profile. Set
CLAIM_USER_DATA_DIR to a Chrome/Chromium profile dir where you've already
logged into polymarket.com; the script launches a persistent context from it.
"""

from __future__ import annotations

import os
import time

from dotenv import load_dotenv

load_dotenv()

PORTFOLIO_URL = "https://polymarket.com/portfolio"
POLL_SECONDS = int(os.getenv("CLAIM_POLL_SECONDS", "120"))
HEADLESS = os.getenv("HEADLESS", "1") != "0"
USER_DATA_DIR = os.getenv("CLAIM_USER_DATA_DIR", "")

# --- Selectors to VERIFY against the live site -----------------------------
# Buttons on Polymarket that trigger a redeem/claim. Update as needed.
CLAIM_BUTTON_SELECTORS = [
    "button:has-text('Claim')",
    "button:has-text('Redeem')",
    "button:has-text('Claim all')",
]


def _claim_once(page) -> int:
    """Click every visible claim/redeem button on the portfolio. Returns count."""
    claimed = 0
    page.goto(PORTFOLIO_URL, wait_until="networkidle")
    time.sleep(3)
    for selector in CLAIM_BUTTON_SELECTORS:
        buttons = page.query_selector_all(selector)
        for btn in buttons:
            try:
                if btn.is_visible() and btn.is_enabled():
                    btn.click()
                    claimed += 1
                    time.sleep(2)  # let any confirmation modal appear
                    # Confirm dialog, if any.
                    for confirm in ("button:has-text('Confirm')",
                                    "button:has-text('Approve')"):
                        c = page.query_selector(confirm)
                        if c and c.is_visible():
                            c.click()
                            time.sleep(2)
            except Exception as e:
                print(f"  [claim] click failed: {e}")
    return claimed


def run() -> None:
    from playwright.sync_api import sync_playwright

    if not USER_DATA_DIR:
        print("Set CLAIM_USER_DATA_DIR to a browser profile already logged in "
              "to polymarket.com (see module docstring).")
        return

    print(f"Auto-claimer starting (headless={HEADLESS}, every {POLL_SECONDS}s). "
          "Ctrl-C to stop.")
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            USER_DATA_DIR, headless=HEADLESS)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            while True:
                try:
                    n = _claim_once(page)
                    ts = int(time.time())
                    print(f"  [{ts}] claimed {n} position(s)")
                except Exception as e:
                    print(f"  [claim] cycle error: {e}")
                time.sleep(POLL_SECONDS)
        except KeyboardInterrupt:
            print("Stopping auto-claimer.")
        finally:
            ctx.close()


if __name__ == "__main__":
    run()
