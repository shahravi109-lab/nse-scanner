"""
NSE IPO Daily Scanner
=====================
Stock list source (in priority order):
  1. Your Google Sheet (you paste Nifty IPO symbols — update anytime)
  2. GitHub repo file symbols.txt (commit to update)
  3. Built-in fallback list

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

import yfinance as yf
import pandas as pd
import requests
import os
from datetime import datetime, timezone

# ── CONFIG ────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

# Your Google Sheet published CSV link (set via env or hardcode)
# See README for how to set this up (2 mins)
GOOGLE_SHEET_CSV   = os.environ.get("GOOGLE_SHEET_CSV", "")

# ── CRITERIA ──────────────────────────────────────────────
MAX_PCT_BELOW_HIGH = 6.0
MIN_MARKET_CAP_CR  = 1_000
MAX_MARKET_CAP_CR  = 50_000
MAX_LISTING_DAYS   = 365
ENTRY_BUFFER       = 0.10
SL_BUFFER          = 0.10
RR_RATIO           = 2.0

# ── FALLBACK SYMBOL LIST ──────────────────────────────────
# Edit this list to add new IPOs when they list
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


# ── FETCH SYMBOL LIST ─────────────────────────────────────
def get_symbols() -> tuple[list[str], str]:
    """
    Returns (symbols_list, source_description)
    Priority: Google Sheet → symbols.txt → fallback
    """

    # 1. Try Google Sheet (live, you control it)
    if GOOGLE_SHEET_CSV:
        try:
            print("📡 Fetching symbols from Google Sheet...")
            r = requests.get(GOOGLE_SHEET_CSV, timeout=10)
            if r.status_code == 200:
                lines = r.text.strip().splitlines()
                symbols = []
                for line in lines:
                    # Handle CSV with possible header
                    for cell in line.split(","):
                        sym = cell.strip().upper().replace('"', '')
                        if sym and sym not in ("SYMBOL", "NSE SYMBOL", ""):
                            symbols.append(sym)
                if len(symbols) >= 5:
                    print(f"  ✅ Got {len(symbols)} symbols from Google Sheet")
                    return symbols, f"Google Sheet ({len(symbols)} stocks)"
        except Exception as e:
            print(f"  ⚠️  Google Sheet fetch failed: {e}")

    # 2. Try symbols.txt in the repo
    if os.path.exists("symbols.txt"):
        try:
            with open("symbols.txt") as f:
                symbols = [
                    line.strip().upper()
                    for line in f.readlines()
                    if line.strip() and not line.startswith("#")
                ]
            if len(symbols) >= 5:
                print(f"  ✅ Got {len(symbols)} symbols from symbols.txt")
                return symbols, f"symbols.txt ({len(symbols)} stocks)"
        except Exception as e:
            print(f"  ⚠️  symbols.txt read failed: {e}")

    # 3. Use fallback
    print(f"  Using built-in fallback list ({len(FALLBACK_SYMBOLS)} stocks)")
    return FALLBACK_SYMBOLS, f"Built-in list ({len(FALLBACK_SYMBOLS)} stocks)"


# ── FETCH STOCK DATA ──────────────────────────────────────
def inr_to_crore(v):
    return v / 1e7


def fetch_stock(symbol: str) -> dict | None:
    try:
        ticker = yf.Ticker(f"{symbol}.NS")
        info   = ticker.info

        df = ticker.history(period="1y", interval="1d").dropna()
        if df.empty or len(df) < 5:
            print(f"    x no data")
            return None

        # Filter 1: listing date
        ipo_date = None
        for field in ["firstTradeDateEpochUtc", "ipoExpectedDate"]:
            val = info.get(field)
            if val:
                if isinstance(val, int):
                    ipo_date = datetime.fromtimestamp(val, tz=timezone.utc).date()
                else:
                    try:
                        ipo_date = datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
                    except Exception:
                        pass
                if ipo_date:
                    break
        if ipo_date is None:
            ipo_date = df.index[0].date()

        days_listed = (datetime.now().date() - ipo_date).days
        if days_listed > MAX_LISTING_DAYS:
            print(f"    - listed {days_listed}d ago (>{MAX_LISTING_DAYS}d, skip)")
            return None

        # Filter 2: market cap
        mkt_cap_crore = inr_to_crore(info.get("marketCap", 0))
        if not (MIN_MARKET_CAP_CR <= mkt_cap_crore <= MAX_MARKET_CAP_CR):
            print(f"    - mktcap Rs.{mkt_cap_crore:,.0f}Cr (out of range, skip)")
            return None

        # Filter 3: price vs 52W high
        current_price = float(df["Close"].iloc[-1])
        high_52w      = float(df["High"].max())
        pct_below     = ((high_52w - current_price) / high_52w) * 100
        if not (0 <= pct_below <= MAX_PCT_BELOW_HIGH):
            print(f"    - {pct_below:.1f}% below 52W high (out of range, skip)")
            return None

        name = info.get("shortName") or info.get("longName") or symbol
        print(f"    PASS: {pct_below:.1f}% below high | "
              f"Rs.{mkt_cap_crore:,.0f}Cr | {days_listed}d listed")

        return {
            "symbol":        symbol,
            "name":          name,
            "current_price": round(current_price, 2),
            "high_52w":      round(high_52w, 2),
            "pct_below":     round(pct_below, 2),
            "mkt_cap_crore": round(mkt_cap_crore, 0),
            "days_listed":   days_listed,
            "ipo_date":      ipo_date.strftime("%d-%b-%Y"),
            "df":            df,
        }

    except Exception as e:
        print(f"    x error: {e}")
        return None


# ── INSIDER BAR ───────────────────────────────────────────
def find_insider_bar(df: pd.DataFrame) -> dict:
    recent = df.tail(6).reset_index()
    last_insider = None

    for i in range(1, len(recent)):
        insider = recent.iloc[i]
        mother  = recent.iloc[i - 1]

        ins_high = float(insider["High"])
        ins_low  = float(insider["Low"])
        mom_high = float(mother["High"])
        mom_low  = float(mother["Low"])

        if ins_high <= mom_high and ins_low >= mom_low:
            entry  = round(mom_high + ENTRY_BUFFER, 2)
            sl     = round(mom_low  - SL_BUFFER,    2)
            risk   = round(entry - sl, 2)
            target = round(entry + (RR_RATIO * risk), 2)

            def fmt(row):
                try:
                    return pd.Timestamp(row["Date"]).strftime("%d-%b")
                except Exception:
                    try:
                        return pd.Timestamp(row.name).strftime("%d-%b")
                    except Exception:
                        return "—"

            last_insider = {
                "found":        True,
                "mother_date":  fmt(mother),
                "mother_high":  round(mom_high, 2),
                "mother_low":   round(mom_low,  2),
                "insider_date": fmt(insider),
                "insider_high": round(ins_high, 2),
                "insider_low":  round(ins_low,  2),
                "entry":        entry,
                "stop_loss":    sl,
                "target":       target,
                "risk":         risk,
                "reward":       round(target - entry, 2),
            }

    return last_insider or {"found": False}


# ── MAIN SCAN ─────────────────────────────────────────────
def scan_all(symbols):
    print(f"\n{'='*56}")
    print(f"  NSE IPO SCANNER | {datetime.now().strftime('%d %b %Y %I:%M %p')}")
    print(f"{'='*56}\n")

    matches  = []
    passed_3 = 0

    for i, sym in enumerate(symbols, 1):
        print(f"[{i:02d}/{len(symbols)}] {sym}")
        data = fetch_stock(sym)
        if not data:
            continue

        passed_3 += 1
        insider = find_insider_bar(data["df"])

        if not insider["found"]:
            print(f"    -> No insider bar in last 5 candles\n")
            continue

        print(f"    *** INSIDER BAR FOUND! ***")
        print(f"    Mother  [{insider['mother_date']}] "
              f"H={insider['mother_high']} L={insider['mother_low']}")
        print(f"    Insider [{insider['insider_date']}] "
              f"H={insider['insider_high']} L={insider['insider_low']}")
        print(f"    Entry={insider['entry']} SL={insider['stop_loss']} "
              f"Target={insider['target']}\n")

        matches.append({**{k: v for k, v in data.items() if k != "df"},
                        **insider})
    return matches, passed_3


# ── TELEGRAM MESSAGE ──────────────────────────────────────
def format_message(matches, total, passed_3, source):
    now = datetime.now().strftime("%d %b %Y  %I:%M %p")
    msg  = f"*NSE IPO Daily Scanner*\n"
    msg += f"*{now}*\n"
    msg += f"_Source: {source}_\n"
    msg += f"{'─'*30}\n"
    msg += f"Stocks checked     : {total}\n"
    msg += f"Passed 3 filters   : {passed_3}\n"
    msg += f"Insider bars found : *{len(matches)}*\n"
    msg += f"{'─'*30}\n\n"

    if not matches:
        msg += "*No insider bar setups today.*\n\n"
        msg += "_No action. Protect capital._\n"
        msg += "_Better setups tomorrow!_ 💪"
        return msg

    for r in matches:
        msg += f"*{r['symbol']}*  |  {r['name']}\n"
        msg += f"Listed   : {r['ipo_date']} ({r['days_listed']}d ago)\n"
        msg += f"Mkt Cap  : Rs.{r['mkt_cap_crore']:,.0f} Cr\n"
        msg += (f"Price    : Rs.{r['current_price']:,}  |  "
                f"52W High: Rs.{r['high_52w']:,} ({r['pct_below']}% below)\n\n")
        msg += f"*Candles:*\n"
        msg += (f"  Mother  [{r['mother_date']}] "
                f"H:{r['mother_high']}  L:{r['mother_low']}\n")
        msg += (f"  Insider [{r['insider_date']}] "
                f"H:{r['insider_high']}  L:{r['insider_low']}\n\n")
        msg += f"*GTT Order:*\n"
        msg += f"  Entry     : Rs.{r['entry']}\n"
        msg += f"  Stop Loss : Rs.{r['stop_loss']}\n"
        msg += f"  Target    : Rs.{r['target']}  (1:2)\n"
        msg += f"  Risk/share: Rs.{r['risk']}\n"
        msg += f"{'─'*30}\n\n"

    msg += "_Verify on TradingView before placing GTT._"
    return msg


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("\n--- TELEGRAM PREVIEW ---")
        print(message)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for chunk in [message[i:i+4000] for i in range(0, len(message), 4000)]:
        r = requests.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": chunk,
            "parse_mode": "Markdown",
        })
        print(f"  Telegram: {'OK' if r.status_code==200 else r.status_code}")


# ── ENTRY ─────────────────────────────────────────────────
if __name__ == "__main__":
    symbols, source = get_symbols()
    matches, passed_3 = scan_all(symbols)
    print(f"\nDONE: {len(matches)} match(es) found\n")
    msg = format_message(matches, len(symbols), passed_3, source)
    send_telegram(msg)
    print("Complete!")
