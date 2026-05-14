"""
NSE IPO Daily Scanner — Exact TradingView Criteria
====================================================
Filters:
  1. Listed in last 365 days (recent IPOs)
  2. Price within 0-6% of 52W high
  3. Market cap Rs.1,000 Cr to Rs.50,000 Cr
  4. Insider bar on daily chart (mother candle)

Trade Setup (if insider bar found):
  Entry     = Mother candle High + Rs.0.10
  Stop Loss = Mother candle Low  - Rs.0.10
  Target    = Entry + 2 x Risk  (1:2 R:R)

Runs daily at 10 PM IST via GitHub Actions -> sends to Telegram
"""

import yfinance as yf
import pandas as pd
import requests
import os
from datetime import datetime, timezone

# ---------------------------------------------------------
# CONFIG — set in GitHub Secrets
# ---------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

# ---------------------------------------------------------
# CRITERIA SETTINGS — edit these to adjust your filters
# ---------------------------------------------------------
MAX_PCT_BELOW_HIGH = 6.0       # % below 52W high
MIN_MARKET_CAP_CR  = 1_000     # Rs.1,000 Crore
MAX_MARKET_CAP_CR  = 50_000    # Rs.50,000 Crore
MAX_LISTING_DAYS   = 365       # Listed within last 365 days
ENTRY_BUFFER       = 0.10      # Rs.0.10 above mother candle high
SL_BUFFER          = 0.10      # Rs.0.10 below mother candle low
RR_RATIO           = 2.0       # 1:2 risk reward

# ---------------------------------------------------------
# NSE IPO WATCHLIST — recent IPOs listed on NSE (2024-2025)
# Add new IPOs here as they list
# ---------------------------------------------------------
RECENT_IPOS = [
    "BAJAJHFL",   "WAAREEENER",  "SWIGGY",      "HYUNDAI",    "NTPCGREEN",
    "AFCONS",     "SAGILITY",    "DEEPINDS",    "VIBHOR",     "STALLION",
    "INOXINDIA",  "YATHARTH",    "SENCO",       "NETWEB",     "UTKARSH",
    "RATNAVEER",  "ELIN",        "IDEAFORGE",   "CYIENTDL",   "CREDO",
    "ENVIRO",     "GARUDA",      "MOBIKWIK",    "DIFFUSION",  "BLACKBUCK",
    "NIVA",       "GODREJIND",   "PNGIPL",      "LANDMARK",   "SIGNORIA",
    "BHARTIHEXA", "UPDATER",     "UNIMECH",     "INVENTURUS", "MAMATA",
    "ZINKA",      "ACME",        "GODAVARI",    "RAPIDFLUX",  "HEXAWARE",
    "DENTA",      "RIKHAV",      "LAXMIDENTAL", "KAYNES",     "TRACXN",
    "SULA",       "DHARMAJ",     "BIKAJI",      "UNIPARTS",   "RAINBOW",
    "FUSION",     "MEDPLUS",     "DELHIVERY",   "ETHOS",      "ARCHEAN",
]


def inr_to_crore(value_inr):
    return value_inr / 1e7


def fetch_stock(symbol):
    """Fetch stock data and check first 3 filter criteria."""
    try:
        ticker = yf.Ticker(f"{symbol}.NS")
        info   = ticker.info

        # -- Get 1 year daily OHLC --
        df = ticker.history(period="1y", interval="1d").dropna()
        if df.empty or len(df) < 5:
            print(f"  x {symbol}: no data")
            return None

        # -- FILTER 1: Listed within 365 days --
        # Try IPO date from info fields first
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

        # Fallback: first candle date as proxy
        if ipo_date is None:
            ipo_date = df.index[0].date()

        days_listed = (datetime.now().date() - ipo_date).days
        if days_listed > MAX_LISTING_DAYS:
            print(f"  - {symbol}: listed {days_listed}d ago (>{MAX_LISTING_DAYS}d, skip)")
            return None

        # -- FILTER 2: Market cap --
        mkt_cap_inr   = info.get("marketCap", 0)
        mkt_cap_crore = inr_to_crore(mkt_cap_inr)
        if not (MIN_MARKET_CAP_CR <= mkt_cap_crore <= MAX_MARKET_CAP_CR):
            print(f"  - {symbol}: mktcap Rs.{mkt_cap_crore:,.0f}Cr (out of range)")
            return None

        # -- FILTER 3: Price vs 52W high --
        current_price = float(df["Close"].iloc[-1])
        high_52w      = float(df["High"].max())
        pct_below     = ((high_52w - current_price) / high_52w) * 100
        if not (0 <= pct_below <= MAX_PCT_BELOW_HIGH):
            print(f"  - {symbol}: {pct_below:.1f}% below 52W high (out of range)")
            return None

        name = info.get("shortName") or info.get("longName") or symbol
        print(f"  PASS {symbol}: {pct_below:.1f}% below high | "
              f"Rs.{mkt_cap_crore:,.0f}Cr | listed {days_listed}d ago")

        return {
            "symbol":       symbol,
            "name":         name,
            "current_price": round(current_price, 2),
            "high_52w":     round(high_52w, 2),
            "pct_below":    round(pct_below, 2),
            "mkt_cap_crore": round(mkt_cap_crore, 0),
            "days_listed":  days_listed,
            "ipo_date":     ipo_date.strftime("%d-%b-%Y"),
            "df":           df,
        }

    except Exception as e:
        print(f"  x {symbol}: {e}")
        return None


def find_insider_bar(df):
    """
    Scans last 5 daily candles for an insider bar.
    Insider bar = current candle's high & low both inside mother candle.

    GTT setup uses MOTHER candle (not insider candle):
      Entry  = mother high + Rs.0.10
      SL     = mother low  - Rs.0.10
      Target = Entry + 2 x Risk
    """
    recent = df.tail(6).reset_index()
    last_insider = None

    for i in range(1, len(recent)):
        insider = recent.iloc[i]
        mother  = recent.iloc[i - 1]

        ins_high = float(insider["High"])
        ins_low  = float(insider["Low"])
        mom_high = float(mother["High"])
        mom_low  = float(mother["Low"])

        # Core insider bar rule
        if ins_high <= mom_high and ins_low >= mom_low:
            entry  = round(mom_high + ENTRY_BUFFER, 2)
            sl     = round(mom_low  - SL_BUFFER,    2)
            risk   = round(entry - sl, 2)
            target = round(entry + (RR_RATIO * risk), 2)

            def fmt_date(row):
                try:
                    return pd.Timestamp(row["Date"]).strftime("%d-%b")
                except Exception:
                    try:
                        return pd.Timestamp(row.name).strftime("%d-%b")
                    except Exception:
                        return "—"

            last_insider = {
                "found":        True,
                "mother_date":  fmt_date(mother),
                "mother_high":  round(mom_high, 2),
                "mother_low":   round(mom_low, 2),
                "insider_date": fmt_date(insider),
                "insider_high": round(ins_high, 2),
                "insider_low":  round(ins_low, 2),
                "entry":        entry,
                "stop_loss":    sl,
                "target":       target,
                "risk":         risk,
                "reward":       round(target - entry, 2),
            }

    return last_insider or {"found": False}


def scan_all():
    print(f"\n{'='*56}")
    print(f"  NSE IPO SCANNER  |  {datetime.now().strftime('%d %b %Y %I:%M %p')}")
    print(f"{'='*56}")
    print(f"  Filters:")
    print(f"    1. Listed <= {MAX_LISTING_DAYS} days ago")
    print(f"    2. Market cap Rs.{MIN_MARKET_CAP_CR:,} – {MAX_MARKET_CAP_CR:,} Cr")
    print(f"    3. Price 0–{MAX_PCT_BELOW_HIGH}% below 52W High")
    print(f"    4. Insider bar on daily chart")
    print(f"  GTT: entry=mother H+0.10 | SL=mother L-0.10 | 1:2 Target")
    print(f"{'='*56}\n")

    matches       = []
    passed_3      = 0

    for i, sym in enumerate(RECENT_IPOS, 1):
        print(f"[{i:02d}/{len(RECENT_IPOS)}] {sym}")
        data = fetch_stock(sym)
        if not data:
            continue

        passed_3 += 1
        insider = find_insider_bar(data["df"])

        if not insider["found"]:
            print(f"       -> No insider bar in last 5 candles\n")
            continue

        print(f"       INSIDER BAR FOUND!")
        print(f"         Mother  [{insider['mother_date']}]: "
              f"H {insider['mother_high']}  L {insider['mother_low']}")
        print(f"         Insider [{insider['insider_date']}]: "
              f"H {insider['insider_high']}  L {insider['insider_low']}")
        print(f"         Entry {insider['entry']} | SL {insider['stop_loss']} | "
              f"Target {insider['target']}\n")

        matches.append({**{k: v for k, v in data.items() if k != "df"}, **insider})

    return matches, passed_3


def format_message(matches, total, passed_3):
    now = datetime.now().strftime("%d %b %Y  %I:%M %p")

    msg  = f"*NSE IPO Daily Scanner*\n"
    msg += f"*{now}*\n"
    msg += f"{'─'*30}\n"
    msg += f"Checked : {total} stocks\n"
    msg += f"Passed filters 1-3 : {passed_3}\n"
    msg += f"Insider bars found : *{len(matches)}*\n"
    msg += f"{'─'*30}\n\n"

    if not matches:
        msg += "*No insider bar setups today.*\n\n"
        msg += "_No action needed. Protect capital._\n"
        msg += "_Better setups tomorrow!_"
        return msg

    for r in matches:
        msg += f"*{r['symbol']}*  |  {r['name']}\n"
        msg += f"Listed : {r['ipo_date']}  ({r['days_listed']}d ago)\n"
        msg += f"Mkt Cap : Rs.{r['mkt_cap_crore']:,.0f} Cr\n"
        msg += f"Price : Rs.{r['current_price']:,}  |  "
        msg += f"52W High : Rs.{r['high_52w']:,}  ({r['pct_below']}% below)\n\n"
        msg += f"Candles:\n"
        msg += f"  Mother  [{r['mother_date']}]  H: {r['mother_high']}  L: {r['mother_low']}\n"
        msg += f"  Insider [{r['insider_date']}]  H: {r['insider_high']}  L: {r['insider_low']}\n\n"
        msg += f"GTT Order:\n"
        msg += f"  Entry      : Rs.{r['entry']}   (mother H + 0.10)\n"
        msg += f"  Stop Loss  : Rs.{r['stop_loss']}   (mother L - 0.10)\n"
        msg += f"  Target     : Rs.{r['target']}   (1:2 R:R)\n"
        msg += f"  Risk/share : Rs.{r['risk']}\n"
        msg += f"{'─'*30}\n\n"

    msg += "_Educational use only. Verify on TradingView before GTT._"
    return msg


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("\nTELEGRAM NOT SET — preview:\n")
        print(message)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    chunks = [message[i:i+4000] for i in range(0, len(message), 4000)]
    for chunk in chunks:
        r = requests.post(url, data={
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       chunk,
            "parse_mode": "Markdown",
        })
        status = "OK" if r.status_code == 200 else f"ERROR {r.status_code}: {r.text[:100]}"
        print(f"  Telegram: {status}")


if __name__ == "__main__":
    matches, passed_3 = scan_all()

    print(f"\n{'='*56}")
    print(f"  DONE: {len(matches)} insider bar setup(s) found")
    print(f"{'='*56}\n")

    msg = format_message(matches, len(RECENT_IPOS), passed_3)
    print("Sending to Telegram...")
    send_telegram(msg)
    print("Complete!")
