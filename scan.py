"""
Chartink V-shape / Inverted-V RSI scanner -> Telegram alert
Runs once per execution (scheduled via GitHub Actions cron).
"""

import os
import re
import requests

CHARTINK_SCAN_URL = "https://chartink.com/screener/process"
CHARTINK_DASHBOARD_URL = "https://chartink.com/dashboard/433470"

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# ---- EDIT THESE conditions to match your 4 tables' underlying scans ----
# Chartink history syntax: ( latest rsi(14) ) means today's value.
# ( 1 rsi(14) ) means value 1 bar ago (their "days ago" shorthand), NOT "1 candle ago rsi(14)".
# Buy = V-shape bounce: RSI fell for 2 bars then turned up, inside the 40-60 zone
BUY_CONDITION = """
( {cash} ( 1 day ago rsi(14) < 2 days ago rsi(14) )
and ( latest rsi(14) > 1 day ago rsi(14) )
and ( 1 day ago rsi(14) >= 40 and 1 day ago rsi(14) <= 60 )
and ( latest close > 1 day ago close ) )
"""

# Sell = Inverted-V: RSI rose for 2 bars then turned down, inside the 40-60 zone
SELL_CONDITION = """
( {cash} ( 1 day ago rsi(14) > 2 days ago rsi(14) )
and ( latest rsi(14) < 1 day ago rsi(14) )
and ( 1 day ago rsi(14) >= 40 and 1 day ago rsi(14) <= 60 )
and ( latest close < 1 day ago close ) )
"""


def get_csrf_and_session():
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0"})
    r = s.get(CHARTINK_DASHBOARD_URL)
    m = re.search(r'name="csrf-token" content="(.+?)"', r.text)
    if not m:
        raise RuntimeError("Could not find csrf token - chartink page structure may have changed")
    token = m.group(1)
    s.headers.update({"x-csrf-token": token})
    return s


def run_scan(session, condition):
    resp = session.post(CHARTINK_SCAN_URL, data={"scan_clause": condition})
    resp.raise_for_status()
    data = resp.json()
    return [row["nsecode"] for row in data.get("data", [])]


TELEGRAM_LIMIT = 4000  # stay under Telegram's 4096 char hard cap


def send_telegram(message):
    """Split into chunks so long lists don't get silently rejected."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    chunks = [message[i:i + TELEGRAM_LIMIT] for i in range(0, len(message), TELEGRAM_LIMIT)] or [message]
    for chunk in chunks:
        resp = requests.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": chunk,
            "parse_mode": "Markdown"
        })
        print("Telegram status:", resp.status_code, resp.text[:300])


def main():
    session = get_csrf_and_session()

    try:
        buys = run_scan(session, BUY_CONDITION)
    except Exception as e:
        buys = []
        print("Buy scan failed:", e)

    try:
        sells = run_scan(session, SELL_CONDITION)
    except Exception as e:
        sells = []
        print("Sell scan failed:", e)

    MAX_SHOW = 60  # safety cap - if a condition is too loose this stops giant messages

    lines = ["*Morning V-Shape Scan*"]
    lines.append(f"\n*BUY (V-shape bounce)* [{len(buys)} found]:")
    lines.append(", ".join(buys[:MAX_SHOW]) if buys else "None today")
    if len(buys) > MAX_SHOW:
        lines.append(f"...and {len(buys) - MAX_SHOW} more (tighten BUY_CONDITION)")

    lines.append(f"\n*SELL (Inverted-V)* [{len(sells)} found]:")
    lines.append(", ".join(sells[:MAX_SHOW]) if sells else "None today")
    if len(sells) > MAX_SHOW:
        lines.append(f"...and {len(sells) - MAX_SHOW} more (tighten SELL_CONDITION)")

    message = "\n".join(lines)
    print(message)
    send_telegram(message)


if __name__ == "__main__":
    main()
