"""
Chartink 4-table V-shape RSI scanner + squeeze momentum filter -> Telegram alert
Reuses the exact base conditions from the user's own dashboard tables,
plus the V-turn / inverted-V-turn confirmation from their own
"PYRAMID" scans (dip-then-turn-up / rise-then-turn-down on RSI).
NSE tables get one more check: LazyBear squeeze must have fired on todays live
daily bar (yesterday squeeze on, today off) with momentum pointing the trade's way.
"""

import os
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pyotp
import requests
from growwapi import GrowwAPI

CHARTINK_SCAN_URL = "https://chartink.com/screener/process"
CHARTINK_DASHBOARD_URL = "https://chartink.com/dashboard/433470"

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# totp flow on purpose - the key+secret flow needs a manual approval every day, usless for a robot
GROWW_TOTP_TOKEN = os.environ["GROWW_TOTP_TOKEN"]
GROWW_TOTP_SECRET = os.environ["GROWW_TOTP_SECRET"]

IST = ZoneInfo("Asia/Kolkata")
TIME_FMT = "%Y-%m-%d %H:%M:%S"

# squeeze settings, LazyBear defaults
SQZ_LEN = 20
KC_MULT = 1.5
# lazybear's code actualy uses the keltner multiplier for the bollinger bands too
# (the 2.0 in his settings box is never used), so 1.5 is what matches the chart dots
BB_MULT = 1.5
HISTORY_DAYS = 90  # ~60 trading days. plan caps history at 3 months, we need atleast 40 bars

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


def groww_login():
    totp = pyotp.TOTP(GROWW_TOTP_SECRET).now()
    token = GrowwAPI.get_access_token(api_key=GROWW_TOTP_TOKEN, totp=totp)
    return GrowwAPI(token)


def live_bars(groww, symbols):
    # todays bar as it stands right now. groww takes max 50 symbols per call
    bars = {}
    for i in range(0, len(symbols), 50):
        keys = tuple(f"NSE_{s}" for s in symbols[i:i + 50])
        ohlc = groww.get_ohlc(segment=groww.SEGMENT_CASH, exchange_trading_symbols=keys)
        ltp = groww.get_ltp(segment=groww.SEGMENT_CASH, exchange_trading_symbols=keys)
        for k in keys:
            if k in ohlc and k in ltp:
                o = ohlc[k]
                bars[k[4:]] = {"open": o["open"], "high": o["high"], "low": o["low"], "close": ltp[k]}
    return bars


def daily_bars(groww, symbol, now, today_bar):
    resp = groww.get_historical_candles(
        exchange=groww.EXCHANGE_NSE,
        segment=groww.SEGMENT_CASH,
        groww_symbol=f"NSE-{symbol}",
        start_time=(now - timedelta(days=HISTORY_DAYS)).strftime(TIME_FMT),
        end_time=now.strftime(TIME_FMT),
        candle_interval=groww.CANDLE_INTERVAL_DAY,
    )
    df = pd.DataFrame([c[:5] for c in resp["candles"]], columns=["ts", "open", "high", "low", "close"])
    # throw away todays candle if groww already sends a partial one, wich we replace with the live bar
    df = df[pd.to_datetime(df["ts"]).dt.date < now.date()]
    return pd.concat([df, pd.DataFrame([today_bar])], ignore_index=True)


def squeeze(df):
    c, h, l = df["close"], df["high"], df["low"]
    basis = c.rolling(SQZ_LEN).mean()
    bb_dev = BB_MULT * c.rolling(SQZ_LEN).std(ddof=0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    kc_dev = KC_MULT * tr.rolling(SQZ_LEN).mean()
    # both channels share the same centre line, so "bollinger inside keltner" is just a width check
    sqz_on = bb_dev < kc_dev

    mid = ((h.rolling(SQZ_LEN).max() + l.rolling(SQZ_LEN).min()) / 2 + basis) / 2
    x = np.arange(SQZ_LEN)
    mom = (c - mid).rolling(SQZ_LEN).apply(lambda y: np.polyval(np.polyfit(x, y, 1), SQZ_LEN - 1), raw=True)
    return sqz_on, mom


def squeeze_fired(df, side):
    sqz_on, mom = squeeze(df)
    if len(df) < 2 * SQZ_LEN or pd.isna(mom.iloc[-2]):
        return False
    fired = sqz_on.iloc[-2] and not sqz_on.iloc[-1]
    if side == "BUY":
        return fired and mom.iloc[-1] > 0 and mom.iloc[-1] > mom.iloc[-2]
    return fired and mom.iloc[-1] < 0 and mom.iloc[-1] < mom.iloc[-2]


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
    now = datetime.now(IST)
    try:
        groww = groww_login()
    except Exception as e:
        groww = None
        print("Groww login failed:", e)

    results = {}
    failed = []

    for name, clause in SCANS.items():
        try:
            stocks = run_scan(session, clause)
        except Exception as e:
            stocks = []
            print(f"{name} scan failed:", e)

        if not name.startswith("NSE"):
            # bse/cash tables go out unfiltered for now - no stock options on most of them anyway
            results[name] = (stocks, None)
            continue

        side = "BUY" if "BUY" in name else "SELL"
        try:
            live = live_bars(groww, stocks) if stocks else {}
        except Exception as e:
            live = {}
            print(f"{name} live quotes failed:", e)

        kept = []
        for sym in stocks:
            if sym not in live:
                failed.append(f"{sym} (no live quote)")
                continue
            try:
                if squeeze_fired(daily_bars(groww, sym, now, live[sym]), side):
                    kept.append(sym)
            except Exception as e:
                failed.append(f"{sym} ({e})")
            time.sleep(0.2)  # historical calls arent in groww's rate table so go easy on them
        results[name] = (stocks, kept)

    if failed:
        print("Groww fetch failed:", ", ".join(failed))

    lines = ["*V-Shape + Squeeze Scan*"]
    for name, (stocks, kept) in results.items():
        emoji = "\U0001F7E2" if "BUY" in name else "\U0001F534"
        if kept is None:
            lines.append(f"\n{emoji} *{name}* [{len(stocks)} found, no squeeze filter]:")
            lines.append(", ".join(stocks) if stocks else "None today")
        else:
            lines.append(f"\n{emoji} *{name}* [{len(kept)} of {len(stocks)} fired]:")
            lines.append(", ".join(kept) if kept else "None today")
    if failed:
        lines.append(f"\n\u26A0\uFE0F Groww could not check {len(failed)} symbols - see the Actions log")

    message = "\n".join(lines)
    print(message)
    send_telegram(message)


if __name__ == "__main__":
    main()
