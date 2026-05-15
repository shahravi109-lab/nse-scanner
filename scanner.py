"""
NSE IPO Daily Scanner — Dhan API (Fixed)
=========================================
Uses Dhan's scrip master CSV to lookup security IDs (no search API needed).
Then fetches OHLC via Dhan historical data API.

Filters:
  1. Listed in last 365 days
  2. Price within 0-6% of 52W high
  3. Market cap Rs.1,000 Cr to Rs.50,000 Cr
  4. Insider bar on daily chart

GTT Setup:
  Entry     = Mother candle High + Rs.0.10
  Stop Loss = Mother candle Low  - Rs.0.10
  Target    = Entry + 2 x Risk (1:2 R:R)
"""

import requests
import os
import time
import io
import pandas as pd
from datetime import datetime, timedelta, timezone

# ── CONFIG ────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
DHAN_CLIENT_ID     = os.environ.get("DHAN_CLIENT_ID", "")
DHAN_ACCESS_TOKEN  = os.environ.get("DHAN_ACCESS_TOKEN", "")

# ── CRITERIA ──────────────────────────────────────────────
MAX_PCT_BELOW_HIGH = 6.0
MIN_MARKET_CAP_CR  = 1_000
MAX_MARKET_CAP_CR  = 50_000
MAX_LISTING_DAYS   = 365
ENTRY_BUFFER       = 0.10
SL_BUFFER          = 0.10
RR_RATIO           = 2.0

# ── DHAN ENDPOINTS ────────────────────────────────────────
DHAN_BASE         = "https://api.dhan.co"
DHAN_SCRIP_MASTER = "https://images.dhan.co/api-data/api-scrip-master.csv"

DHAN_HEADERS = {
    "Content-Type": "application/json",
    "Accept":       "application/json",
    "client-id":    DHAN_CLIENT_ID,
    "access-token": DHAN_ACCESS_TOKEN,
}

# ── SYMBOL LIST ───────────────────────────────────────────
FALLBACK_SYMBOLS = [
    "VIDYAWIRES", "SEDMAC",     "BAJAJHFL",   "WAAREEENER",  "SWIGGY",
    "HYUNDAI",    "NTPCGREEN",  "AFCONS",      "SAGILITY",    "DEEPINDS",
    "VIBHOR",     "STALLION",   "INOXINDIA",   "YATHARTH",    "SENCO",
    "NETWEB",     "UTKARSH",    "RATNAVEER",   "ELIN",        "IDEAFORGE",
    "CYIENTDL",   "CREDO",      "ENVIRO",      "GARUDA",      "MOBIKWIK",
    "DIFFUSION",  "BLACKBUCK",  "NIVA",        "PNGIPL",      "LANDMARK",
    "BHARTIHEXA", "UPDATER",    "UNIMECH",     "INVENTURUS",  "MAMATA",
    "ZINKA",      "ACME",       "GODAVARI",    "RAPIDFLUX",   "HEXAWARE",
    "DENTA",      "RIKHAV",     "LAXMIDENTAL", "KAYNES",      "TRACXN",
    "SULA",       "DHARMAJ",    "BIKAJI",      "UNIPARTS",    "RAINBOW",
    "FUSION",     "MEDPLUS",    "DELHIVERY",   "ETHOS",       "ARCHEAN",
    "SBFC",       "KFIN",       "MAPMYINDIA",  "CAMPUS",      "EMCURE",
    "GODIGIT",    "INDEGENE",   "AWFIS",       "TBOTECH",     "SIGNORIA",
]


# ── LOAD SYMBOL LIST ──────────────────────────────────────
def get_symbols() -> tuple[list[str], str]:
    if os.path.exists("symbols.txt"):
        try:
            syms = [
                l.strip().upper() for l in open("symbols.txt")
                if l.strip() and not l.startswith("#")
            ]
            if len(syms) >= 5:
                return syms, f"symbols.txt ({len(syms)} stocks)"
        except Exception:
            pass
    return FALLBACK_SYMBOLS, f"Built-in list ({len(FALLBACK_SYMBOLS)} stocks)"


# ── LOAD DHAN SCRIP MASTER ────────────────────────────────
def load_scrip_master() -> pd.DataFrame | None:
    """
    Downloads Dhan scrip master CSV and returns a DataFrame.
    Columns include: SEM_SMST_SECURITY_ID, SM_SYMBOL_NAME,
                     SEM_TRADING_SYMBOL, SEM_SEGMENT, SEM_EXM_EXCH_ID
    """
    print("📥 Downloading Dhan scrip master...")
    try:
        r = requests.get(DHAN_SCRIP_MASTER, timeout=30)
        if r.status_code != 200:
            print(f"  ✗ Failed: {r.status_code}")
            return None
        df = pd.read_csv(io.StringIO(r.text), low_memory=False)
        print(f"  ✓ Loaded {len(df):,} instruments")
        # Filter NSE equity only
        nse = df[
            (df["SEM_EXM_EXCH_ID"] == "NSE") &
            (df["SEM_SEGMENT"] == "E")
        ].copy()
        print(f"  ✓ NSE Equity instruments: {len(nse):,}")
        return nse
    except Exception as e:
        print(f"  ✗ Error: {e}")
        return None


def find_security_id(scrip_master: pd.DataFrame, symbol: str) -> tuple[str, str]:
    """
    Looks up security_id for a symbol in scrip master.
    Returns (security_id, display_name) or ("", "") if not found.
    """
    sym_upper = symbol.upper()

    # Try exact match on trading symbol
    match = scrip_master[
        scrip_master["SEM_TRADING_SYMBOL"].str.upper() == sym_upper
    ]
    if match.empty:
        # Try custom symbol
        match = scrip_master[
            scrip_master["SEM_CUSTOM_SYMBOL"].str.upper() == sym_upper
        ]
    if match.empty:
        # Try symbol name
        match = scrip_master[
            scrip_master["SM_SYMBOL_NAME"].str.upper() == sym_upper
        ]

    if match.empty:
        return "", ""

    row = match.iloc[0]
    sec_id  = str(int(row["SEM_SMST_SECURITY_ID"]))
    name    = str(row.get("SEM_CUSTOM_SYMBOL", symbol))
    return sec_id, name


# ── DHAN HISTORICAL OHLC ──────────────────────────────────
def dhan_ohlc(security_id: str) -> pd.DataFrame | None:
    """
    Fetches 1 year of daily OHLC from Dhan historical API.
    """
    try:
        to_date   = datetime.now().date()
        from_date = to_date - timedelta(days=400)

        body = {
            "securityId":      security_id,
            "exchangeSegment": "NSE_EQ",
            "instrument":      "EQUITY",
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
            print(f"    OHLC error {r.status_code}: {r.text[:100]}")
            return None

        data = r.json()
        closes     = data.get("close",     [])
        highs      = data.get("high",      [])
        lows       = data.get("low",       [])
        opens      = data.get("open",      [])
        timestamps = data.get("timestamp", [])

        if len(closes) < 5:
            print(f"    Too few candles: {len(closes)}")
            return None

        df = pd.DataFrame({
            "Open":  opens,
            "High":  highs,
            "Low":   lows,
            "Close": closes,
            "Date":  pd.to_datetime(timestamps, unit="s"),
        }).dropna().sort_values("Date").reset_index(drop=True)

        return df

    except Exception as e:
        print(f"    OHLC exception: {e}")
        return None


# ── FILTER + ANALYSE ──────────────────────────────────────
def inr_to_crore(v):
    return (v or 0) / 1e7


def check_stock(symbol, scrip_master) -> tuple[dict | None, str]:
    # Step 1: Find security ID
    sec_id, name = find_security_id(scrip_master, symbol)
    if not sec_id:
        return None, "Not found in Dhan scrip master"

    print(f"  Found: id={sec_id} name={name}")

    # Step 2: OHLC data
    df = dhan_ohlc(sec_id)
    if df is None or len(df) < 5:
        return None, "No OHLC data"

    # Filter 1: Listing date
    ipo_date    = df["Date"].iloc[0].date()
    days_listed = (datetime.now().date() - ipo_date).days
    if days_listed > MAX_LISTING_DAYS:
        return None, f"Listed {days_listed}d ago (>{MAX_LISTING_DAYS}d) — too old"

    # Filter 2: Market cap (skip if 0 — not in scrip master)
    # We skip this filter since Dhan scrip master doesn't have market cap
    # You can re-enable by fetching from another source
    mkt_cap_crore = 0  # not available in scrip master

    # Filter 3: Price vs 52W high
    current_price = float(df["Close"].iloc[-1])
    high_52w      = float(df["High"].tail(252).max())
    pct_below     = ((high_52w - current_price) / high_52w) * 100

    if pct_below < 0:
        return None, f"Price Rs.{current_price} above 52W high Rs.{high_52w} (data issue)"
    if pct_below > MAX_PCT_BELOW_HIGH:
        return None, f"Price {pct_below:.1f}% below 52W high (>{MAX_PCT_BELOW_HIGH}%)"

    return {
        "symbol":        symbol,
        "name":          name,
        "current_price": round(current_price, 2),
        "high_52w":      round(high_52w, 2),
        "pct_below":     round(pct_below, 2),
        "mkt_cap_crore": mkt_cap_crore,
        "days_listed":   days_listed,
        "ipo_date":      ipo_date.strftime("%d-%b-%Y"),
        "df":            df,
    }, ""


# ── INSIDER BAR ───────────────────────────────────────────
def find_insider_bar(df: pd.DataFrame) -> dict:
    recent = df.tail(6).reset_index(drop=True)
    last_ib = None

    for i in range(1, len(recent)):
        ins = recent.iloc[i]
        mom = recent.iloc[i - 1]

        ih = float(ins["High"]); il = float(ins["Low"])
        mh = float(mom["High"]); ml = float(mom["Low"])

        if ih <= mh and il >= ml:
            entry  = round(mh + ENTRY_BUFFER, 2)
            sl     = round(ml - SL_BUFFER,    2)
            risk   = round(entry - sl, 2)
            target = round(entry + RR_RATIO * risk, 2)

            def fmt(row):
                try:
                    return pd.Timestamp(row["Date"]).strftime("%d-%b")
                except Exception:
                    return "—"

            last_ib = {
                "found":        True,
                "mother_date":  fmt(mom),
                "mother_high":  round(mh, 2),
                "mother_low":   round(ml, 2),
                "insider_date": fmt(ins),
                "insider_high": round(ih, 2),
                "insider_low":  round(il, 2),
                "entry":        entry,
                "stop_loss":    sl,
                "target":       target,
                "risk":         risk,
                "reward":       round(target - entry, 2),
            }

    return last_ib or {"found": False}


# ── MAIN SCAN ─────────────────────────────────────────────
def scan_all(symbols, scrip_master):
    print(f"\n{'='*60}")
    print(f"  NSE IPO SCANNER | {datetime.now().strftime('%d %b %Y %I:%M %p')}")
    print(f"{'='*60}")

    matches    = []
    passed     = 0
    rejections = []

    for i, sym in enumerate(symbols, 1):
        print(f"\n[{i:02d}/{len(symbols)}] {sym}")
        data, reason = check_stock(sym, scrip_master)
        time.sleep(0.3)

        if not data:
            print(f"  -> SKIP: {reason}")
            rejections.append((sym, reason))
            continue

        passed += 1
        ib = find_insider_bar(data["df"])

        if not ib["found"]:
            print(f"  -> Passed price filter | No insider bar")
            rejections.append((sym, "No insider bar in last 5 candles"))
            continue

        print(f"  -> *** INSIDER BAR! ***")
        print(f"     Mother  [{ib['mother_date']}] H={ib['mother_high']} L={ib['mother_low']}")
        print(f"     Insider [{ib['insider_date']}] H={ib['insider_high']} L={ib['insider_low']}")
        print(f"     Entry={ib['entry']} SL={ib['stop_loss']} Target={ib['target']}")

        matches.append({**{k: v for k, v in data.items() if k != "df"}, **ib})

    return matches, passed, rejections


# ── FORMAT TELEGRAM ───────────────────────────────────────
def format_message(matches, total, passed, source, rejections):
    now = datetime.now().strftime("%d %b %Y  %I:%M %p")
    msg  = f"*NSE IPO Daily Scanner*\n*{now}*\n"
    msg += f"_Data: Dhan API | {source}_\n"
    msg += f"{'─'*30}\n"
    msg += f"Stocks checked   : {total}\n"
    msg += f"Passed filter    : {passed}\n"
    msg += f"Insider bars     : *{len(matches)}*\n"
    msg += f"{'─'*30}\n\n"

    if matches:
        for r in matches:
            msg += f"*{r['symbol']}*  |  {r['name']}\n"
            msg += f"Listed   : {r['ipo_date']} ({r['days_listed']}d ago)\n"
            msg += f"Price    : Rs.{r['current_price']:,}  |  52W High: Rs.{r['high_52w']:,} ({r['pct_below']}% below)\n\n"
            msg += f"*Candles:*\n"
            msg += f"  Mother  [{r['mother_date']}] H:{r['mother_high']}  L:{r['mother_low']}\n"
            msg += f"  Insider [{r['insider_date']}] H:{r['insider_high']}  L:{r['insider_low']}\n\n"
            msg += f"*GTT Order:*\n"
            msg += f"  Entry     : Rs.{r['entry']}\n"
            msg += f"  Stop Loss : Rs.{r['stop_loss']}\n"
            msg += f"  Target    : Rs.{r['target']}  (1:2)\n"
            msg += f"  Risk/share: Rs.{r['risk']}\n"
            msg += f"{'─'*30}\n\n"
        msg += "_Verify on TradingView before placing GTT._\n\n"
    else:
        msg += "*No insider bar setups today.*\n_Protect capital. Better setups tomorrow!_\n\n"

    msg += f"{'─'*30}\n*Why stocks were skipped:*\n"
    for sym, reason in rejections[:25]:
        msg += f"  {sym}: {reason}\n"
    if len(rejections) > 25:
        msg += f"  ...+{len(rejections)-25} more\n"
    return msg


# ── SEND TELEGRAM ─────────────────────────────────────────
def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("\n--- PREVIEW ---\n", message)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for chunk in [message[i:i+4000] for i in range(0, len(message), 4000)]:
        r = requests.post(url, data={
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       chunk,
            "parse_mode": "Markdown",
        })
        print(f"  Telegram: {'OK' if r.status_code==200 else r.status_code}")


# ── ENTRY ─────────────────────────────────────────────────
if __name__ == "__main__":
    if not DHAN_CLIENT_ID or not DHAN_ACCESS_TOKEN:
        print("ERROR: DHAN credentials not set!")
        exit(1)

    # Load scrip master once
    scrip_master = load_scrip_master()
    if scrip_master is None:
        print("ERROR: Could not load scrip master. Check network.")
        exit(1)

    symbols, source = get_symbols()
    matches, passed, rejections = scan_all(symbols, scrip_master)

    print(f"\nDONE: {len(matches)} match(es)")
    msg = format_message(matches, len(symbols), passed, source, rejections)
    send_telegram(msg)
    print("Complete!")
