"""
Chartink 4-table V-shape RSI scanner -> Telegram alert
Reuses the exact base conditions from the user's own dashboard tables,
plus the V-turn / inverted-V-turn confirmation from their own
"PYRAMID" scans (dip-then-turn-up / rise-then-turn-down on RSI).
"""

import os
import re
import requests

CHARTINK_SCAN_URL = "https://chartink.com/screener/process"
CHARTINK_DASHBOARD_URL = "https://chartink.com/dashboard/433470"

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# V-turn confirmation add-ons (reused verbatim from the user's own
# "-1D | M60 | W50 & BULLISH/BEARISH PYRAMID" scans)
V_UP = "and 1 day ago rsi( 14 ) > 2 days ago rsi( 14 ) and 2 days ago rsi( 14 ) < 3 days ago rsi( 14 )"
V_DOWN = "and 1 day ago rsi( 14 ) < 2 days ago rsi( 14 ) and 2 days ago rsi( 14 ) > 3 days ago rsi( 14 )"

SCANS = {
    "NSE BUY": f"""
        ( {{33489}} (
            monthly rsi( 14 ) > 60 and
            weekly rsi( 14 ) > 60 and
            daily rsi( 14 ) > 1 day ago rsi( 14 )
            {V_UP}
        ) )
    """,
    "NSE SELL": f"""
        ( {{33489}} (
            monthly rsi( 14 ) < 40 and
            weekly rsi( 14 ) < 40 and
            daily rsi( 14 ) < 1 day ago rsi( 14 )
            {V_DOWN}
        ) )
    """,
    "BSE BUY": f"""
        ( {{cash}} (
            monthly rsi( 14 ) > 60 and
            weekly rsi( 14 ) > 60 and
            daily rsi( 14 ) > 1 day ago rsi( 14 )
            {V_UP}
        ) )
    """,
    "BSE SELL": f"""
        ( {{cash}} (
            monthly rsi( 14 ) < 40 and
            weekly rsi( 14 ) < 40 and
            daily rsi( 14 ) < 1 day ago rsi( 14 )
            {V_DOWN}
        ) )
    """,
}


def get_session():
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0"})
    r = s.get(CHARTINK_DASHBOARD_URL)
    m = re.search(r'name="csrf-token" content="(.+?)"', r.text)
    if not m:
        raise RuntimeError("Could not find csrf token - chartink page structure may have changed")
    s.headers.update({"x-csrf-token": m.group(1)})
    return s


def run_scan(session, condition):
    resp = session.post(CHARTINK_SCAN_URL, data={"scan_clause": condition})
    resp.raise_for_status()
    data = resp.json()
    return [row["nsecode"] for row in data.get("data", [])]


TELEGRAM_LIMIT = 4000


def send_telegram(message):
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
    session = get_session()
    results = {}

    for name, clause in SCANS.items():
        try:
            results[name] = run_scan(session, clause)
        except Exception as e:
            results[name] = []
            print(f"{name} scan failed:", e)

    lines = ["*Morning V-Shape Scan (AT 03:15PM style)*"]
    for name, stocks in results.items():
        emoji = "\U0001F7E2" if "BUY" in name else "\U0001F534"
        lines.append(f"\n{emoji} *{name}* [{len(stocks)} found]:")
        lines.append(", ".join(stocks) if stocks else "None today")

    message = "\n".join(lines)
    print(message)
    send_telegram(message)


if __name__ == "__main__":
    main()
