"""
NSE IPO Scanner — Deep Debug
Finds exact Dhan symbol names and tests OHLC API with correct parameters.
"""

import requests
import os
import time
import io
import json
import pandas as pd
from datetime import datetime, timedelta

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
DHAN_CLIENT_ID     = os.environ.get("DHAN_CLIENT_ID", "")
DHAN_ACCESS_TOKEN  = os.environ.get("DHAN_ACCESS_TOKEN", "")

DHAN_BASE         = "https://api.dhan.co"
DHAN_SCRIP_MASTER = "https://images.dhan.co/api-data/api-scrip-master.csv"

DHAN_HEADERS = {
    "Content-Type": "application/json",
    "Accept":       "application/json",
    "client-id":    DHAN_CLIENT_ID,
    "access-token": DHAN_ACCESS_TOKEN,
}

# Just 5 stocks to debug deeply
TEST_SYMBOLS = ["VIDYAWIRES", "BAJAJHFL", "SWIGGY", "NETWEB", "HYUNDAI"]


def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(msg)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for chunk in [msg[i:i+4000] for i in range(0, len(msg), 4000)]:
        requests.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": chunk,
            "parse_mode": "Markdown",
        })


def load_scrip_master():
    print("Downloading scrip master...")
    r = requests.get(DHAN_SCRIP_MASTER, timeout=30)
    df = pd.read_csv(io.StringIO(r.text), low_memory=False)
    print(f"Total rows: {len(df)}")
    print(f"Columns: {list(df.columns)}")
    # Show sample row
    print(f"Sample row:\n{df.iloc[0].to_dict()}")
    return df


def search_symbol_in_master(df, symbol):
    """Try multiple column matches to find the symbol."""
    sym = symbol.upper()
    results = []

    # Check all string columns
    for col in df.select_dtypes(include="object").columns:
        matches = df[df[col].str.upper().str.strip() == sym]
        if not matches.empty:
            for _, row in matches.iterrows():
                results.append({
                    "matched_col": col,
                    "SEM_SMST_SECURITY_ID": row.get("SEM_SMST_SECURITY_ID", "?"),
                    "SM_SYMBOL_NAME": row.get("SM_SYMBOL_NAME", "?"),
                    "SEM_TRADING_SYMBOL": row.get("SEM_TRADING_SYMBOL", "?"),
                    "SEM_CUSTOM_SYMBOL": row.get("SEM_CUSTOM_SYMBOL", "?"),
                    "SEM_EXM_EXCH_ID": row.get("SEM_EXM_EXCH_ID", "?"),
                    "SEM_SEGMENT": row.get("SEM_SEGMENT", "?"),
                    "SEM_INSTRUMENT_NAME": row.get("SEM_INSTRUMENT_NAME", "?"),
                })

    # Also try partial match
    if not results:
        for col in ["SM_SYMBOL_NAME", "SEM_TRADING_SYMBOL", "SEM_CUSTOM_SYMBOL"]:
            if col in df.columns:
                matches = df[df[col].str.upper().str.contains(sym, na=False)]
                if not matches.empty:
                    for _, row in matches.head(3).iterrows():
                        results.append({
                            "matched_col": f"{col} (partial)",
                            "SEM_SMST_SECURITY_ID": row.get("SEM_SMST_SECURITY_ID", "?"),
                            "SM_SYMBOL_NAME": row.get("SM_SYMBOL_NAME", "?"),
                            "SEM_TRADING_SYMBOL": row.get("SEM_TRADING_SYMBOL", "?"),
                            "SEM_CUSTOM_SYMBOL": row.get("SEM_CUSTOM_SYMBOL", "?"),
                            "SEM_EXM_EXCH_ID": row.get("SEM_EXM_EXCH_ID", "?"),
                            "SEM_SEGMENT": row.get("SEM_SEGMENT", "?"),
                        })

    return results


def test_ohlc(security_id, exchange_seg="NSE_EQ", instrument="EQUITY"):
    """Test OHLC fetch and return candle count or error."""
    to_date   = datetime.now().date()
    from_date = to_date - timedelta(days=30)  # just 30 days for debug

    body = {
        "securityId":      str(security_id),
        "exchangeSegment": exchange_seg,
        "instrument":      instrument,
        "expiryCode":      0,
        "oi":              False,
        "fromDate":        from_date.strftime("%Y-%m-%d"),
        "toDate":          to_date.strftime("%Y-%m-%d"),
    }

    r = requests.post(
        f"{DHAN_BASE}/v2/charts/historical",
        headers=DHAN_HEADERS,
        json=body,
        timeout=15,
    )

    if r.status_code != 200:
        return f"HTTP {r.status_code}: {r.text[:80]}"

    data = r.json()
    closes = data.get("close", [])
    if closes:
        return f"OK — {len(closes)} candles (latest close: {closes[-1]})"
    else:
        return f"Empty response: {str(data)[:100]}"


if __name__ == "__main__":
    print(f"Client ID set: {'YES' if DHAN_CLIENT_ID else 'NO'}")
    print(f"Token set: {'YES' if DHAN_ACCESS_TOKEN else 'NO'}")

    # Load scrip master
    try:
        master = load_scrip_master()
    except Exception as e:
        print(f"Failed to load scrip master: {e}")
        send_telegram(f"Failed to load scrip master: {e}")
        exit(1)

    now = datetime.now().strftime("%d %b %Y %I:%M %p")
    report = f"*NSE Scanner — Deep Debug*\n*{now}*\n{'─'*30}\n\n"

    # Show column names
    cols = list(master.columns)
    report += f"*Scrip master columns:*\n`{', '.join(cols[:10])}`\n\n"

    for sym in TEST_SYMBOLS:
        print(f"\n{'='*40}\nSearching: {sym}")
        report += f"*{sym}*\n"

        matches = search_symbol_in_master(master, sym)

        if not matches:
            report += f"  Not found in scrip master at all\n\n"
            print(f"  NOT FOUND")
            continue

        # Show all matches
        for m in matches[:3]:
            report += (f"  Found via [{m['matched_col']}]:\n"
                       f"  ID={m['SEM_SMST_SECURITY_ID']} | "
                       f"sym={m['SM_SYMBOL_NAME']} | "
                       f"trading={m['SEM_TRADING_SYMBOL']} | "
                       f"exch={m['SEM_EXM_EXCH_ID']} | "
                       f"seg={m['SEM_SEGMENT']}\n")
            print(f"  Match: {m}")

            # Test OHLC for NSE equity matches
            if m['SEM_EXM_EXCH_ID'] == 'NSE' and m['SEM_SEGMENT'] == 'E':
                sec_id = str(m['SEM_SMST_SECURITY_ID'])
                ohlc_result = test_ohlc(sec_id, "NSE_EQ", "EQUITY")
                report += f"  OHLC test: {ohlc_result}\n"
                print(f"  OHLC: {ohlc_result}")

        report += "\n"
        time.sleep(0.5)

    print("\nSending to Telegram...")
    send_telegram(report)
    print("Done!")
