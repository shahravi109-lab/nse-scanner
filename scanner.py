"""
NSE IPO Scanner — DEBUG VERSION
Sends detailed rejection reason for every stock to Telegram.
Use this once to diagnose why stocks are being filtered out.
"""

import requests
import os
import time
import pandas as pd
from datetime import datetime, timedelta, timezone

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
DHAN_CLIENT_ID     = os.environ.get("DHAN_CLIENT_ID", "")
DHAN_ACCESS_TOKEN  = os.environ.get("DHAN_ACCESS_TOKEN", "")

MAX_PCT_BELOW_HIGH = 6.0
MIN_MARKET_CAP_CR  = 1_000
MAX_MARKET_CAP_CR  = 50_000
MAX_LISTING_DAYS   = 365

DHAN_BASE    = "https://api.dhan.co"
DHAN_HEADERS = {
    "Content-Type": "application/json",
    "Accept":       "application/json",
    "client-id":    DHAN_CLIENT_ID,
    "access-token": DHAN_ACCESS_TOKEN,
}

# Test with just 10 stocks first
TEST_SYMBOLS = [
    "VIDYAWIRES", "SEDMAC", "BAJAJHFL", "SWIGGY",
    "HYUNDAI", "NTPCGREEN", "NETWEB", "WAAREEENER",
    "SAGILITY", "AFCONS"
]


def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(msg)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for chunk in [msg[i:i+4000] for i in range(0, len(msg), 4000)]:
        requests.post(url, data={
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       chunk,
            "parse_mode": "Markdown",
        })


def dhan_search(symbol):
    try:
        r = requests.post(
            f"{DHAN_BASE}/v2/instruments/search",
            headers=DHAN_HEADERS,
            json={"searchString": symbol},
            timeout=10,
        )
        print(f"  Search status: {r.status_code}")
        if r.status_code != 200:
            return None, f"Search API error {r.status_code}: {r.text[:100]}"

        items = r.json()
        if isinstance(items, dict):
            items = items.get("data", [])

        print(f"  Search results: {len(items)} items")
        for item in items:
            print(f"    -> {item.get('tradingSymbol')} | {item.get('exchangeSegment')} | id={item.get('securityId')}")

        # Find NSE equity match
        for item in items:
            if (item.get("tradingSymbol","").upper() == symbol.upper() and
                    item.get("exchangeSegment") in ("NSE_EQ","NSE")):
                return item, ""
        # fallback: first NSE result
        for item in items:
            if item.get("exchangeSegment") in ("NSE_EQ","NSE"):
                return item, f"No exact match, using {item.get('tradingSymbol')}"

        return None, f"Not found in NSE. Results: {[i.get('tradingSymbol') for i in items[:5]]}"
    except Exception as e:
        return None, f"Search exception: {e}"


def dhan_ohlc(security_id, exchange_seg="NSE_EQ"):
    try:
        to_date   = datetime.now().date()
        from_date = to_date - timedelta(days=400)
        body = {
            "securityId":      security_id,
            "exchangeSegment": exchange_seg,
            "instrument":      "EQUITY",
            "expiryCode":      0,
            "oi_flag":         "N",
            "fromDate":        from_date.strftime("%Y-%m-%d"),
            "toDate":          to_date.strftime("%Y-%m-%d"),
        }
        r = requests.post(
            f"{DHAN_BASE}/v2/charts/historical",
            headers=DHAN_HEADERS,
            json=body,
            timeout=15,
        )
        print(f"  OHLC status: {r.status_code}")
        if r.status_code != 200:
            return None, f"OHLC API error {r.status_code}: {r.text[:150]}"

        data = r.json()
        closes     = data.get("close",     [])
        highs      = data.get("high",      [])
        lows       = data.get("low",       [])
        opens      = data.get("open",      [])
        timestamps = data.get("timestamp", [])

        print(f"  OHLC candles received: {len(closes)}")
        if len(closes) < 2:
            return None, f"Too few candles: {len(closes)}"

        df = pd.DataFrame({
            "Open": opens, "High": highs,
            "Low":  lows,  "Close": closes,
            "Date": pd.to_datetime(timestamps, unit="s"),
        }).dropna().sort_values("Date").reset_index(drop=True)

        return df, ""
    except Exception as e:
        return None, f"OHLC exception: {e}"


def debug_stock(symbol):
    print(f"\n{'─'*50}")
    print(f"Checking: {symbol}")
    result = {"symbol": symbol, "steps": []}

    # Step 1: Search
    scrip, err = dhan_search(symbol)
    if not scrip:
        result["verdict"] = f"FAIL: {err}"
        return result
    result["steps"].append(f"Found: id={scrip.get('securityId')} seg={scrip.get('exchangeSegment')}")

    security_id  = str(scrip.get("securityId",""))
    exchange_seg = scrip.get("exchangeSegment","NSE_EQ")

    # Step 2: OHLC
    df, err = dhan_ohlc(security_id, exchange_seg)
    if df is None:
        result["verdict"] = f"FAIL: {err}"
        return result
    result["steps"].append(f"OHLC: {len(df)} candles | {df['Date'].iloc[0].date()} to {df['Date'].iloc[-1].date()}")

    # Step 3: Listing date
    ipo_date    = df["Date"].iloc[0].date()
    days_listed = (datetime.now().date() - ipo_date).days
    result["steps"].append(f"IPO date: {ipo_date} ({days_listed}d ago)")
    if days_listed > MAX_LISTING_DAYS:
        result["verdict"] = f"FAIL Filter1: Listed {days_listed}d ago > {MAX_LISTING_DAYS}d"
        return result

    # Step 4: Market cap
    mkt_cap_crore = (scrip.get("marketCap") or 0) / 1e7
    result["steps"].append(f"Mkt Cap: Rs.{mkt_cap_crore:,.0f} Cr")
    if mkt_cap_crore > 0:
        if mkt_cap_crore < MIN_MARKET_CAP_CR:
            result["verdict"] = f"FAIL Filter2: Mkt cap Rs.{mkt_cap_crore:,.0f}Cr < Rs.{MIN_MARKET_CAP_CR:,}Cr"
            return result
        if mkt_cap_crore > MAX_MARKET_CAP_CR:
            result["verdict"] = f"FAIL Filter2: Mkt cap Rs.{mkt_cap_crore:,.0f}Cr > Rs.{MAX_MARKET_CAP_CR:,}Cr"
            return result

    # Step 5: Price vs 52W high
    current = float(df["Close"].iloc[-1])
    high52  = float(df["High"].tail(252).max())
    pct     = ((high52 - current) / high52) * 100
    result["steps"].append(f"Price: Rs.{current:.2f} | 52W High: Rs.{high52:.2f} | {pct:.1f}% below")
    if pct > MAX_PCT_BELOW_HIGH:
        result["verdict"] = f"FAIL Filter3: {pct:.1f}% below 52W high > {MAX_PCT_BELOW_HIGH}%"
        return result
    if pct < 0:
        result["verdict"] = f"FAIL Filter3: Price above 52W high (data issue)"
        return result

    # Step 6: Insider bar check
    recent = df.tail(6).reset_index(drop=True)
    ib_found = False
    for i in range(1, len(recent)):
        c = recent.iloc[i]
        p = recent.iloc[i-1]
        if float(c["High"]) <= float(p["High"]) and float(c["Low"]) >= float(p["Low"]):
            ib_found = True
            result["steps"].append(
                f"IB on {c['Date'].strftime('%d-%b')}: "
                f"H={c['High']:.2f} L={c['Low']:.2f} inside "
                f"Mother H={p['High']:.2f} L={p['Low']:.2f}"
            )

    result["verdict"] = "✅ PASS ALL FILTERS" + (" + INSIDER BAR!" if ib_found else " | No insider bar")
    return result


if __name__ == "__main__":
    print(f"DHAN_CLIENT_ID set: {'YES' if DHAN_CLIENT_ID else 'NO'}")
    print(f"DHAN_ACCESS_TOKEN set: {'YES (len='+str(len(DHAN_ACCESS_TOKEN))+')' if DHAN_ACCESS_TOKEN else 'NO'}")

    all_results = []
    for sym in TEST_SYMBOLS:
        r = debug_stock(sym)
        all_results.append(r)
        time.sleep(0.5)

    # Build Telegram message
    now = datetime.now().strftime("%d %b %Y %I:%M %p")
    msg = f"*NSE Scanner — Debug Report*\n*{now}*\n{'─'*30}\n\n"

    for r in all_results:
        msg += f"*{r['symbol']}*\n"
        for step in r.get("steps", []):
            msg += f"  {step}\n"
        msg += f"  ➜ {r.get('verdict','?')}\n\n"

    print("\n" + "="*50)
    print(msg)
    send_telegram(msg)
    print("Done!")
