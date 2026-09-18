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
# Buy = V-shape bounce at RSI 40/50/60 zone
BUY_CONDITION = """
( {cash} ( latest rsi(14) > 1 candle ago rsi(14) )
and ( 1 candle ago rsi(14) < 2 candles ago rsi(14) )
and ( 1 candle ago rsi(14) > 40 and 1 candle ago rsi(14) < 60 )
and ( latest close > 1 candle ago close ) )
"""

# Sell = Inverted-V at RSI 40/50/60 zone
SELL_CONDITION = """
( {cash} ( latest rsi(14) < 1 candle ago rsi(14) )
and ( 1 candle ago rsi(14) > 2 candles ago rsi(14) )
and ( 1 candle ago rsi(14) > 40 and 1 candle ago rsi(14) < 60 )
and ( latest close < 1 candle ago close ) )
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


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, data={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    })


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

    lines = ["*Morning V-Shape Scan*"]
    lines.append("\n*BUY (V-shape bounce):*")
    lines.append(", ".join(buys) if buys else "None today")
    lines.append("\n*SELL (Inverted-V):*")
    lines.append(", ".join(sells) if sells else "None today")

    message = "\n".join(lines)
    print(message)
    send_telegram(message)


if __name__ == "__main__":
    main()
