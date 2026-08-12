
import io
import time
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
import yfinance as yf

st.set_page_config(
    page_title="Regime-Aware Swing Scanner",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------
# UI / constants
# ---------------------------
st.title("Regime-Aware Swing Momentum Scanner")
st.caption(
    "Fast ticker lookup + cached market-universe scanning. "
    "Market regime is assessed first, then candidates are ranked by momentum, trend, RSI and volume."
)

YAHOO_BATCH_SIZE = 120
SCAN_CACHE_TTL = 15 * 60
UNIVERSE_CACHE_TTL = 6 * 60 * 60
DIRECTORY_CACHE_TTL = 24 * 60 * 60

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
IWM_HOLDINGS_URL = "https://www.ishares.com/us/products/239710/ishares-russell-2000-etf/latest-holdings.csv"
SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NASDAQ100_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"

DEFAULT_WATCHLIST = "AAPL, MSFT, NVDA, AMZN, META, GOOGL, TSLA, AMD, AVGO, NFLX, JPM, XOM"

# Session-persisted scan results keep UI changes fast.
if "scan_df" not in st.session_state:
    st.session_state.scan_df = pd.DataFrame()
if "scan_universe_name" not in st.session_state:
    st.session_state.scan_universe_name = ""
if "scan_timestamp" not in st.session_state:
    st.session_state.scan_timestamp = None
if "scan_errors" not in st.session_state:
    st.session_state.scan_errors = []
if "provider_health" not in st.session_state:
    st.session_state.provider_health = {
        "status": "UNKNOWN",
        "provider": "Yahoo",
        "coverage": np.nan,
        "message": "No market scan has run yet.",
    }
if "last_good_scans" not in st.session_state:
    st.session_state.last_good_scans = {}
if "show_a_plus" not in st.session_state:
    st.session_state.show_a_plus = False
if "a_plus_selected_ticker" not in st.session_state:
    st.session_state.a_plus_selected_ticker = None

# ---------------------------
# Helpers
# ---------------------------
def yahoo_symbol(symbol: str) -> str:
    """Normalize common US share-class notation to Yahoo Finance format."""
    s = str(symbol).strip().upper()
    return s.replace(".", "-")

def clean_symbol(symbol: str) -> str:
    s = str(symbol).strip().upper()
    if not s or s in {"-", "NAN", "CASH", "USD"}:
        return ""
    return yahoo_symbol(s)

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100)

def momentum_score(value, scale):
    if pd.isna(value):
        return np.nan
    return float(np.clip(value * scale, -100, 100))

def regime_label(score):
    if pd.isna(score):
        return "N/A"
    if score >= 50:
        return "STRONG BULL"
    if score >= 15:
        return "BULL"
    if score > -15:
        return "NEUTRAL"
    if score > -50:
        return "BEAR"
    return "STRONG BEAR"

def setup_label(row):
    """Technical setup only. Final Quality Grade is assigned after RS is calculated."""
    bull = row["Close"] > row["EMA20"] > row["EMA50"]
    bear = row["Close"] < row["EMA20"] < row["EMA50"]
    volume_ok = pd.notna(row["Volume Ratio"]) and row["Volume Ratio"] >= 1.2

    if bull and row["Momentum Score"] >= 25 and volume_ok and 50 <= row["RSI14"] <= 75:
        return "Technical Long"
    if bull and row["Momentum Score"] >= 25:
        return "Long Watch"
    if bear and row["Momentum Score"] <= -25 and volume_ok and 25 <= row["RSI14"] <= 50:
        return "Technical Short"
    if bear and row["Momentum Score"] <= -25:
        return "Short Watch"
    if abs(row["Momentum Score"]) < 15:
        return "Neutral"
    return "Mixed"

def safe_headers():
    # SEC asks automated clients to identify themselves.
    return {
        "User-Agent": "SwingMomentumScanner/1.0 research-dashboard contact@example.com",
        "Accept-Encoding": "gzip, deflate",
    }

# ---------------------------
# Company directory / autocomplete
# ---------------------------
@st.cache_data(ttl=DIRECTORY_CACHE_TTL, show_spinner=False)
def load_company_directory():
    rows = []
    try:
        response = requests.get(SEC_TICKERS_URL, headers=safe_headers(), timeout=12)
        response.raise_for_status()
        payload = response.json()
        for item in payload.values():
            ticker = clean_symbol(item.get("ticker", ""))
            title = str(item.get("title", "")).strip()
            if ticker and title:
                rows.append((ticker, title))
    except Exception:
        # Small fallback so the search control still renders if SEC is temporarily unavailable.
        rows = [
            ("AAPL", "Apple Inc."),
            ("MSFT", "Microsoft Corporation"),
            ("NVDA", "NVIDIA Corporation"),
            ("CSCO", "Cisco Systems, Inc."),
            ("CRDO", "Credo Technology Group Holding Ltd"),
        ]

    df = pd.DataFrame(rows, columns=["Ticker", "Company"]).drop_duplicates("Ticker")
    df = df.sort_values(["Ticker", "Company"]).reset_index(drop=True)
    df["Label"] = df["Ticker"] + " — " + df["Company"]
    return df

# ---------------------------
# Universe loaders
# ---------------------------
@st.cache_data(ttl=UNIVERSE_CACHE_TTL, show_spinner=False)
def load_sp500():
    """Load S&P 500 constituents with multiple fallbacks."""
    try:
        response = requests.get(
            SP500_URL, timeout=15, headers={"User-Agent": "Mozilla/5.0"}
        )
        response.raise_for_status()
        tables = pd.read_html(io.StringIO(response.text))
        for table in tables:
            table.columns = [str(c).strip() for c in table.columns]
            if "Symbol" in table.columns and "Security" in table.columns and len(table) >= 450:
                out = pd.DataFrame({
                    "Ticker": table["Symbol"].map(clean_symbol),
                    "Company": table["Security"].astype(str),
                    "Sector": table["GICS Sector"].astype(str) if "GICS Sector" in table.columns else "",
                })
                out = out[out["Ticker"] != ""].drop_duplicates("Ticker")
                if len(out) >= 450:
                    return out.reset_index(drop=True)
    except Exception:
        pass

    try:
        url = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
        response = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        table = pd.read_csv(io.StringIO(response.text))
        if "Symbol" in table.columns and len(table) >= 450:
            company_col = "Security" if "Security" in table.columns else ("Name" if "Name" in table.columns else "Symbol")
            sector_col = "GICS Sector" if "GICS Sector" in table.columns else ("Sector" if "Sector" in table.columns else None)
            out = pd.DataFrame({
                "Ticker": table["Symbol"].map(clean_symbol),
                "Company": table[company_col].astype(str),
                "Sector": table[sector_col].astype(str) if sector_col else "",
            })
            out = out[out["Ticker"] != ""].drop_duplicates("Ticker")
            if len(out) >= 450:
                return out.reset_index(drop=True)
    except Exception:
        pass

    return pd.DataFrame(columns=["Ticker", "Company", "Sector"])


@st.cache_data(ttl=UNIVERSE_CACHE_TTL, show_spinner=False)
def load_nasdaq100():
    """Load Nasdaq-100 constituents with robust parsing and a fallback symbol set."""
    try:
        response = requests.get(
            NASDAQ100_URL,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()
        tables = pd.read_html(io.StringIO(response.text))

        for table in tables:
            table = table.copy()
            # Flatten MultiIndex headers if present.
            if isinstance(table.columns, pd.MultiIndex):
                table.columns = [
                    " ".join([str(v) for v in c if str(v) != "nan"]).strip()
                    for c in table.columns
                ]
            else:
                table.columns = [str(c).strip() for c in table.columns]

            ticker_col = next(
                (c for c in table.columns if c.lower() in {"ticker", "symbol", "ticker symbol"}),
                None,
            )
            company_col = next(
                (
                    c for c in table.columns
                    if c.lower() in {"company", "company name", "security"}
                    or "company" in c.lower()
                ),
                None,
            )
            if ticker_col and company_col and len(table) >= 90:
                sector_col = next(
                    (c for c in table.columns if "sector" in c.lower() or "industry" in c.lower()),
                    None,
                )
                out = pd.DataFrame({
                    "Ticker": table[ticker_col].map(clean_symbol),
                    "Company": table[company_col].astype(str),
                    "Sector": table[sector_col].astype(str) if sector_col else "",
                })
                out = out[out["Ticker"] != ""].drop_duplicates("Ticker")
                if len(out) >= 90:
                    return out.reset_index(drop=True)
    except Exception:
        pass

    # Fallback: current-style Nasdaq-100 symbol basket. Company names are filled
    # from the SEC company directory, so the scanner remains usable even if
    # Wikipedia changes its table markup temporarily.
    fallback_symbols = """
    AAPL ABNB ADBE ADI ADP ADSK AEP AMAT AMD AMGN AMZN APP ARM ASML AVGO AXON
    BKNG BKR CCEP CDNS CEG CHTR CMCSA COST CPRT CRWD CSCO CSGP CSX CTAS CTSH
    DASH DDOG DXCM EA EXC FANG FAST FTNT GFS GILD GOOG GOOGL HON IDXX INTC INTU
    ISRG KDP KHC KLAC LIN LRCX MAR MCHP MDB MDLZ MELI META MNST MRVL MSFT MSTR
    MU NFLX NVDA NXPI ODFL ON ORLY PANW PAYX PCAR PDD PEP PLTR PYPL QCOM REGN
    ROP ROST SBUX SNPS TEAM TMUS TSLA TTWO TXN VRSK VRTX WBD WDAY XEL ZS
    """.split()

    directory = load_company_directory().set_index("Ticker")
    rows = []
    for ticker in fallback_symbols:
        company = ticker
        if ticker in directory.index:
            company = directory.loc[ticker, "Company"]
            if isinstance(company, pd.Series):
                company = company.iloc[0]
        rows.append((ticker, str(company), ""))
    return pd.DataFrame(rows, columns=["Ticker", "Company", "Sector"]).drop_duplicates("Ticker")


@st.cache_data(ttl=UNIVERSE_CACHE_TTL, show_spinner=False)
def load_iwm():
    """Load IWM holdings from iShares' downloadable holdings CSV."""
    csv_urls = [
        "https://www.ishares.com/us/products/239710/ishares-russell-2000-etf/1467271812596.ajax?fileType=csv&fileName=IWM_holdings&dataType=fund",
        IWM_HOLDINGS_URL,
    ]
    for url in csv_urls:
        try:
            response = requests.get(url, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
            response.raise_for_status()
            raw = response.content.decode("utf-8-sig", errors="ignore")

            # iShares CSV files contain several metadata lines before the header.
            lines = raw.splitlines()
            header_idx = next(
                (
                    i for i, line in enumerate(lines[:30])
                    if "Ticker" in line and ("Name" in line or "Sector" in line)
                ),
                None,
            )
            if header_idx is None:
                continue

            holdings = pd.read_csv(io.StringIO("\n".join(lines[header_idx:])))
            if "Ticker" not in holdings.columns:
                continue

            if "Asset Class" in holdings.columns:
                equity_mask = holdings["Asset Class"].astype(str).str.lower().eq("equity")
                if equity_mask.any():
                    holdings = holdings[equity_mask]

            out = pd.DataFrame({
                "Ticker": holdings["Ticker"].map(clean_symbol),
                "Company": holdings["Name"].astype(str) if "Name" in holdings.columns else "",
                "Sector": holdings["Sector"].astype(str) if "Sector" in holdings.columns else "",
            })
            out = out[out["Ticker"] != ""].drop_duplicates("Ticker")
            if len(out) > 1000:
                return out.reset_index(drop=True)
        except Exception:
            continue

    return pd.DataFrame(columns=["Ticker", "Company", "Sector"])


def parse_watchlist(text):
    tickers = []
    for part in str(text).replace("\n", ",").split(","):
        ticker = clean_symbol(part)
        if ticker and ticker not in tickers:
            tickers.append(ticker)
    directory = load_company_directory().set_index("Ticker")
    rows = []
    for ticker in tickers:
        company = directory.loc[ticker, "Company"] if ticker in directory.index else ticker
        if isinstance(company, pd.Series):
            company = company.iloc[0]
        rows.append((ticker, str(company), ""))
    return pd.DataFrame(rows, columns=["Ticker", "Company", "Sector"])

def get_universe(name, watchlist_text):
    if name == "My Watchlist":
        return parse_watchlist(watchlist_text)
    if name == "S&P 500":
        return load_sp500()
    if name == "Nasdaq-100":
        return load_nasdaq100()
    if name == "Russell 2000 / IWM":
        return load_iwm()
    if name == "Combined US":
        frames = [load_sp500(), load_nasdaq100(), load_iwm()]
        frames = [f for f in frames if not f.empty]
        if not frames:
            return pd.DataFrame(columns=["Ticker", "Company", "Sector"])
        out = pd.concat(frames, ignore_index=True)
        return out.drop_duplicates("Ticker").reset_index(drop=True)
    return pd.DataFrame(columns=["Ticker", "Company", "Sector"])

# ---------------------------
# Yahoo downloads
# ---------------------------

@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def get_company_snapshot(symbol):
    """Lightweight cached metadata for direct ticker diagnostics."""
    snapshot = {
        "sector": "",
        "industry": "",
        "market_cap": np.nan,
        "trailing_pe": np.nan,
        "forward_pe": np.nan,
        "earnings_date": None,
    }
    try:
        ticker = yf.Ticker(symbol)
        try:
            fast = ticker.fast_info
            if fast:
                snapshot["market_cap"] = fast.get("market_cap", np.nan)
        except Exception:
            pass

        try:
            info = ticker.info or {}
            snapshot["sector"] = str(info.get("sector", "") or "")
            snapshot["industry"] = str(info.get("industry", "") or "")
            snapshot["trailing_pe"] = info.get("trailingPE", np.nan)
            snapshot["forward_pe"] = info.get("forwardPE", np.nan)
            if pd.isna(snapshot["market_cap"]):
                snapshot["market_cap"] = info.get("marketCap", np.nan)
        except Exception:
            pass

        try:
            cal = ticker.calendar
            if isinstance(cal, dict):
                value = cal.get("Earnings Date")
                if isinstance(value, (list, tuple)) and value:
                    snapshot["earnings_date"] = pd.to_datetime(value[0], errors="coerce")
                elif value is not None:
                    snapshot["earnings_date"] = pd.to_datetime(value, errors="coerce")
            elif isinstance(cal, pd.DataFrame) and not cal.empty:
                if "Earnings Date" in cal.index:
                    value = cal.loc["Earnings Date"].iloc[0]
                    snapshot["earnings_date"] = pd.to_datetime(value, errors="coerce")
        except Exception:
            pass
    except Exception:
        pass
    return snapshot



def sanitize_ohlcv(df):
    """Return clean OHLCV rows or an empty frame if the structure is unusable."""
    if df is None or df.empty:
        return pd.DataFrame()

    x = df.copy()
    required = ["Open", "High", "Low", "Close", "Volume"]
    missing = [c for c in required if c not in x.columns]
    if missing:
        return pd.DataFrame()

    for col in required:
        x[col] = pd.to_numeric(x[col], errors="coerce")

    x[required] = x[required].replace([np.inf, -np.inf], np.nan)

    if "Date" in x.columns:
        x["Date"] = pd.to_datetime(x["Date"], errors="coerce")
        x = x.dropna(subset=["Date"])

    # OHLC must be valid; zero/negative prices are unusable.
    x = x.dropna(subset=["Open", "High", "Low", "Close"])
    x = x[
        (x["Open"] > 0)
        & (x["High"] > 0)
        & (x["Low"] > 0)
        & (x["Close"] > 0)
    ]

    # Volume may occasionally be absent; keep the row but normalize to NaN.
    x.loc[x["Volume"] < 0, "Volume"] = np.nan

    if x.empty:
        return pd.DataFrame()

    # Remove obviously corrupt bars.
    valid_range = (x["High"] >= x[["Open", "Close", "Low"]].max(axis=1)) & (
        x["Low"] <= x[["Open", "Close", "High"]].min(axis=1)
    )
    x = x[valid_range].copy()

    return x.reset_index(drop=True)


def price_data_status(df, min_rows=80):
    """Return (is_valid, reason) for diagnostic/scanner calculations."""
    x = sanitize_ohlcv(df)
    if x.empty:
        return False, "No valid OHLC price rows were returned."
    if len(x) < min_rows:
        return False, f"Only {len(x)} valid trading days were returned; at least {min_rows} are required."

    latest = x.iloc[-1]
    for col in ["Open", "High", "Low", "Close"]:
        if pd.isna(latest[col]) or not np.isfinite(latest[col]) or latest[col] <= 0:
            return False, f"Latest {col} value is invalid."

    return True, ""



def expected_latest_completed_us_session(now=None):
    """
    Best-effort freshness guard for US daily bars.
    Uses weekdays (not the full exchange holiday calendar) and a conservative
    18:00 New York cutoff so Yahoo has time to publish the completed bar.
    """
    try:
        now_ny = pd.Timestamp.now(tz="America/New_York") if now is None else pd.Timestamp(now).tz_convert("America/New_York")
    except Exception:
        now_ny = pd.Timestamp.now(tz="America/New_York")

    d = now_ny.normalize()
    if now_ny.weekday() < 5 and now_ny.hour >= 18:
        expected = d
    else:
        expected = d - pd.Timedelta(days=1)

    while expected.weekday() >= 5:
        expected -= pd.Timedelta(days=1)
    return expected.tz_localize(None).normalize()


def market_data_freshness(df):
    x = sanitize_ohlcv(df)
    if x.empty or "Date" not in x.columns:
        return False, pd.NaT, pd.NaT, "No valid dated OHLCV rows."

    latest = pd.to_datetime(x["Date"], errors="coerce").dropna().max()
    if pd.isna(latest):
        return False, pd.NaT, pd.NaT, "Latest market-data date is unavailable."

    latest = pd.Timestamp(latest).tz_localize(None).normalize()
    expected = expected_latest_completed_us_session()
    fresh = latest >= expected
    reason = (
        f"Latest completed daily bar: {latest:%d-%b-%Y}. Expected latest session: {expected:%d-%b-%Y}."
        if fresh else
        f"Latest completed daily bar: {latest:%d-%b-%Y}; expected at least {expected:%d-%b-%Y}."
    )
    return fresh, latest, expected, reason



def expected_recent_us_sessions(lookback_days=14):
    """
    Best-effort recent US business-day calendar.
    This intentionally uses weekdays only; exchange holidays are tolerated
    during continuity checks if neither provider supplies a bar.
    """
    end = expected_latest_completed_us_session()
    start = end - pd.Timedelta(days=lookback_days * 2)
    days = pd.date_range(start=start, end=end, freq="B")
    return pd.DatetimeIndex(days[-lookback_days:])


def missing_recent_sessions(df, lookback_days=14):
    x = sanitize_ohlcv(df)
    if x.empty or "Date" not in x.columns:
        return list(expected_recent_us_sessions(lookback_days))

    present = pd.to_datetime(x["Date"], errors="coerce").dropna()
    present = pd.DatetimeIndex([pd.Timestamp(d).normalize() for d in present])

    expected = expected_recent_us_sessions(lookback_days)
    return [d for d in expected if d not in present]


def merge_price_sources(primary_df, fallback_df):
    """
    Merge OHLCV by date, preferring primary rows and filling only missing
    sessions from fallback.
    """
    p = normalize_single_history(primary_df)
    f = normalize_single_history(fallback_df)

    if p.empty and f.empty:
        return pd.DataFrame()
    if p.empty:
        out = f.copy()
        out.attrs["provider"] = f.attrs.get("provider", "Fallback")
        out.attrs["download_route"] = f.attrs.get("download_route", "fallback only")
        return out
    if f.empty:
        return p.copy()

    p = p.copy()
    f = f.copy()
    p["Date"] = pd.to_datetime(p["Date"]).dt.normalize()
    f["Date"] = pd.to_datetime(f["Date"]).dt.normalize()

    p = p.drop_duplicates("Date", keep="last").set_index("Date")
    f = f.drop_duplicates("Date", keep="last").set_index("Date")

    combined = p.combine_first(f).reset_index().sort_values("Date")
    combined = sanitize_ohlcv(combined)

    # Preserve source metadata.
    combined.attrs["provider"] = "Yahoo + Stooq"
    combined.attrs["download_route"] = "Yahoo primary + Stooq gap fill"
    return combined


def continuity_status(df, lookback_days=14):
    """
    Return (ok, missing_dates, message) for recent-session continuity.
    Weekend/holiday false positives are minimized by looking only at weekdays;
    unresolved dates are reported explicitly.
    """
    missing = missing_recent_sessions(df, lookback_days=lookback_days)
    if not missing:
        return True, [], f"No missing weekday sessions in the last {lookback_days} expected sessions."

    formatted = ", ".join(pd.Timestamp(d).strftime("%d-%b-%Y") for d in missing)
    return False, missing, f"Missing recent session(s): {formatted}."



def data_confidence(df, recent_lookback=14):
    """
    Practical data-confidence model.

    HIGH:
      - latest completed session is current
      - no recent weekday gaps

    MEDIUM:
      - latest session is current
      - exactly one recent missing weekday session
      - sufficient valid history remains

    LOW:
      - stale latest bar
      - multiple recent gaps
      - insufficient valid history / invalid OHLC

    LOW blocks actionable trade plans.
    MEDIUM keeps calculations available but displays a caution flag.
    """
    valid, reason = price_data_status(df, min_rows=126)
    if not valid:
        return {
            "level": "LOW",
            "score": 0,
            "block": True,
            "message": f"Invalid/insufficient price data: {reason}",
            "missing": [],
        }

    fresh, latest, expected, fresh_reason = market_data_freshness(df)
    missing = missing_recent_sessions(df, lookback_days=recent_lookback)

    if not fresh:
        return {
            "level": "LOW",
            "score": 20,
            "block": True,
            "message": fresh_reason,
            "missing": missing,
        }

    if len(missing) == 0:
        return {
            "level": "HIGH",
            "score": 100,
            "block": False,
            "message": "Latest session is current and no recent weekday gaps were detected.",
            "missing": [],
        }

    if len(missing) == 1:
        d = pd.Timestamp(missing[0]).strftime("%d-%b-%Y")
        return {
            "level": "MEDIUM",
            "score": 70,
            "block": False,
            "message": (
                f"Latest session is current, but one isolated recent data gap was detected: {d}. "
                "Signals remain usable with reduced confidence."
            ),
            "missing": missing,
        }

    formatted = ", ".join(pd.Timestamp(d).strftime("%d-%b-%Y") for d in missing[:5])
    return {
        "level": "LOW",
        "score": 35,
        "block": True,
        "message": (
            f"Multiple recent data gaps were detected ({len(missing)}): {formatted}. "
            "Actionable signals are blocked until data quality improves."
        ),
        "missing": missing,
    }


def chart_frame_for_timeframe(base_df, timeframe):
    base = sanitize_ohlcv(base_df).copy()
    if base.empty:
        return base

    base["Date"] = pd.to_datetime(base["Date"], errors="coerce")
    base = base.dropna(subset=["Date"]).sort_values("Date")

    if timeframe == "Daily":
        chart = base.tail(90).copy()
    elif timeframe == "Weekly":
        chart = (
            base.set_index("Date")
            .resample("W-FRI")
            .agg({"Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"})
            .dropna(subset=["Open","High","Low","Close"])
            .reset_index()
            .tail(104)
        )
    elif timeframe == "Monthly":
        chart = (
            base.set_index("Date")
            .resample("ME")
            .agg({"Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"})
            .dropna(subset=["Open","High","Low","Close"])
            .reset_index()
            .tail(60)
        )
    elif timeframe == "YTD":
        current_year = pd.Timestamp.today().year
        chart = base[base["Date"].dt.year == current_year].copy()
    else:
        chart = base.copy()

    chart["EMA20"] = chart["Close"].ewm(span=20, adjust=False).mean()
    chart["EMA50"] = chart["Close"].ewm(span=50, adjust=False).mean()
    chart["EMA200"] = chart["Close"].ewm(span=200, adjust=False).mean()
    return chart

def finite_or_nan(value):
    """Convert to float only when finite; otherwise return NaN."""
    try:
        v = float(value)
        return v if np.isfinite(v) else np.nan
    except Exception:
        return np.nan



def period_start_date(period):
    today = pd.Timestamp.today().normalize()
    mapping = {
        "1mo": pd.DateOffset(months=2),
        "3mo": pd.DateOffset(months=4),
        "6mo": pd.DateOffset(months=7),
        "1y": pd.DateOffset(years=1, months=1),
        "2y": pd.DateOffset(years=2, months=1),
        "5y": pd.DateOffset(years=5, months=1),
        "10y": pd.DateOffset(years=10, months=1),
        "max": pd.DateOffset(years=20),
    }
    if period == "ytd":
        return pd.Timestamp(year=today.year, month=1, day=1)
    return today - mapping.get(period, pd.DateOffset(years=5, months=1))


def normalize_single_history(df):
    if df is None or df.empty:
        return pd.DataFrame()

    x = df.copy()
    if isinstance(x.columns, pd.MultiIndex):
        x.columns = [c[0] for c in x.columns]

    if "Date" not in x.columns:
        x = x.reset_index()
    if "Datetime" in x.columns and "Date" not in x.columns:
        x = x.rename(columns={"Datetime": "Date"})

    if "Date" in x.columns:
        x["Date"] = pd.to_datetime(x["Date"], errors="coerce")
        try:
            if x["Date"].dt.tz is not None:
                x["Date"] = x["Date"].dt.tz_localize(None)
        except Exception:
            pass

    return sanitize_ohlcv(x)


def stooq_symbol(symbol):
    """Map common US ticker punctuation to Stooq's symbol convention."""
    s = clean_symbol(symbol).lower().replace("-", ".")
    if s.startswith("^"):
        return None
    return f"{s}.us"


@st.cache_data(ttl=5 * 60, show_spinner=False)
def download_stooq(symbol, period="1y"):
    """Independent fallback provider for individual US equities."""
    mapped = stooq_symbol(symbol)
    if not mapped:
        return pd.DataFrame()

    try:
        start_date = period_start_date(period)
        end_date = pd.Timestamp.today().normalize() + pd.Timedelta(days=1)
        url = "https://stooq.com/q/d/l/"
        response = requests.get(
            url,
            params={
                "s": mapped,
                "d1": start_date.strftime("%Y%m%d"),
                "d2": end_date.strftime("%Y%m%d"),
                "i": "d",
            },
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()
        raw = response.text.strip()
        if not raw or raw.lower().startswith("no data"):
            return pd.DataFrame()

        df = pd.read_csv(io.StringIO(raw))
        clean = normalize_single_history(df)
        if not clean.empty:
            clean.attrs["download_route"] = "Stooq fallback"
            clean.attrs["provider"] = "Stooq"
        return clean
    except Exception:
        return pd.DataFrame()


def choose_freshest_history(candidates):
    valid = []
    for provider, route, df in candidates:
        clean = normalize_single_history(df)
        if clean.empty or "Date" not in clean.columns:
            continue
        latest = pd.to_datetime(clean["Date"], errors="coerce").max()
        if pd.isna(latest):
            continue
        ts = pd.Timestamp(latest)
        try:
            if ts.tzinfo is not None:
                ts = ts.tz_localize(None)
        except Exception:
            pass
        valid.append((ts, len(clean), provider, route, clean))

    if not valid:
        return pd.DataFrame()

    valid.sort(key=lambda item: (item[0], item[1]), reverse=True)
    _, _, provider, route, best = valid[0]
    best = best.copy()
    best.attrs["download_route"] = route
    best.attrs["provider"] = provider
    return best


@st.cache_data(ttl=60, show_spinner=False)
def download_one(symbol, period="1y"):
    """
    Individual download with continuity repair:
    Yahoo primary, Stooq fallback, then date-by-date merge when Yahoo is stale
    or has recent session gaps.
    """
    yahoo = pd.DataFrame()
    try:
        yahoo = yf.download(
            symbol,
            period=period,
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=False,
            timeout=12,
        )
        yahoo = normalize_single_history(yahoo)
        if not yahoo.empty:
            yahoo.attrs["provider"] = "Yahoo"
            yahoo.attrs["download_route"] = "Yahoo / yfinance"
    except Exception:
        yahoo = pd.DataFrame()

    yahoo_fresh = False
    yahoo_continuous = False
    if not yahoo.empty:
        yahoo_fresh, _, _, _ = market_data_freshness(yahoo)
        yahoo_continuous, _, _ = continuity_status(yahoo, lookback_days=14)

    # If Yahoo is both current and continuous, avoid extra network load.
    if yahoo_fresh and yahoo_continuous:
        return yahoo

    stooq = download_stooq(symbol, period)

    # Merge by date to fill specific missing sessions.
    merged = merge_price_sources(yahoo, stooq)
    if not merged.empty:
        return merged

    return yahoo if not yahoo.empty else stooq


@st.cache_data(ttl=SCAN_CACHE_TTL, show_spinner=False)
def download_batch_cached(symbols_tuple, conservative=False):
    symbols = list(symbols_tuple)
    if not symbols:
        return pd.DataFrame()

    try:
        return yf.download(
            tickers=symbols,
            period="1y",
            interval="1d",
            auto_adjust=True,
            group_by="ticker",
            progress=False,
            threads=False if conservative else True,
            timeout=25,
        )
    except Exception:
        return pd.DataFrame()



def split_batch_result(data, symbol):
    if data is None or data.empty:
        return pd.DataFrame()

    # Multi-symbol download normally returns (Ticker, OHLCV) columns with group_by="ticker".
    if isinstance(data.columns, pd.MultiIndex):
        level0 = set(map(str, data.columns.get_level_values(0)))
        level1 = set(map(str, data.columns.get_level_values(1)))
        try:
            if symbol in level0:
                df = data[symbol].copy()
            elif symbol in level1:
                df = data.xs(symbol, axis=1, level=1).copy()
            else:
                return pd.DataFrame()
        except Exception:
            return pd.DataFrame()
    else:
        # One-ticker edge case.
        df = data.copy()

    df = df.dropna(how="all")
    if df.empty or "Close" not in df.columns:
        return pd.DataFrame()

    df = df.reset_index()
    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    return sanitize_ohlcv(df)

def compute_record(symbol, company, sector, df):
    valid, _ = price_data_status(df, min_rows=126)
    if not valid:
        return None

    x = sanitize_ohlcv(df)
    x["EMA20"] = x["Close"].ewm(span=20, adjust=False).mean()
    x["EMA50"] = x["Close"].ewm(span=50, adjust=False).mean()
    x["EMA200"] = x["Close"].ewm(span=200, adjust=False).mean()
    x["RSI14"] = rsi(x["Close"], 14)
    x["Vol20"] = x["Volume"].rolling(20).mean()
    x["Volume Ratio"] = x["Volume"] / x["Vol20"]
    x["1D %"] = x["Close"].pct_change(1)
    x["1W %"] = x["Close"].pct_change(5)
    x["1M %"] = x["Close"].pct_change(20)
    x["3M %"] = x["Close"].pct_change(60)
    x["6M %"] = x["Close"].pct_change(126)

    # Professional screening metrics.
    prev_close = x["Close"].shift(1)
    true_range = pd.concat(
        [
            x["High"] - x["Low"],
            (x["High"] - prev_close).abs(),
            (x["Low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    x["ATR14"] = true_range.rolling(14).mean()
    x["ATR50"] = true_range.rolling(50).mean()
    x["Dollar Volume"] = x["Close"] * x["Volume"]
    x["Avg Dollar Volume 20"] = x["Dollar Volume"].rolling(20).mean()
    x["High20"] = x["High"].rolling(20).max()
    x["High252"] = x["High"].rolling(252, min_periods=126).max()
    x["Low20"] = x["Low"].rolling(20).min()
    x["Low252"] = x["Low"].rolling(252, min_periods=126).min()
    x["Volatility20"] = x["Close"].pct_change().rolling(20).std() * np.sqrt(252)

    row = x.iloc[-1]
    record = {
        "Ticker": symbol,
        "Company": company,
        "Sector": sector,
        "Close": finite_or_nan(row["Close"]),
        "EMA20": finite_or_nan(row["EMA20"]),
        "EMA50": finite_or_nan(row["EMA50"]),
        "EMA200": finite_or_nan(row["EMA200"]),
        "RSI14": finite_or_nan(row["RSI14"]),
        "Volume Ratio": float(row["Volume Ratio"]) if pd.notna(row["Volume Ratio"]) else np.nan,
        "1D %": float(row["1D %"]) if pd.notna(row["1D %"]) else np.nan,
        "1W %": float(row["1W %"]) if pd.notna(row["1W %"]) else np.nan,
        "1M %": float(row["1M %"]) if pd.notna(row["1M %"]) else np.nan,
        "3M %": float(row["3M %"]) if pd.notna(row["3M %"]) else np.nan,
        "6M %": float(row["6M %"]) if pd.notna(row["6M %"]) else np.nan,
        "ATR14": float(row["ATR14"]) if pd.notna(row["ATR14"]) else np.nan,
        "ATR50": float(row["ATR50"]) if pd.notna(row["ATR50"]) else np.nan,
        "Avg Dollar Volume 20": float(row["Avg Dollar Volume 20"]) if pd.notna(row["Avg Dollar Volume 20"]) else np.nan,
        "High20": float(row["High20"]) if pd.notna(row["High20"]) else np.nan,
        "High252": float(row["High252"]) if pd.notna(row["High252"]) else np.nan,
        "Low20": float(row["Low20"]) if pd.notna(row["Low20"]) else np.nan,
        "Low252": float(row["Low252"]) if pd.notna(row["Low252"]) else np.nan,
        "Volatility20": float(row["Volatility20"]) if pd.notna(row["Volatility20"]) else np.nan,
        "History Days": int(len(x)),
    }
    record["Daily"] = momentum_score(record["1D %"], 2500)
    record["Weekly"] = momentum_score(record["1W %"], 700)
    record["Monthly"] = momentum_score(record["1M %"], 350)
    record["Momentum Score"] = (
        record["Daily"] * 0.40
        + record["Weekly"] * 0.35
        + record["Monthly"] * 0.25
    )
    record["Regime"] = regime_label(record["Momentum Score"])
    record["Setup"] = setup_label(record)
    return record

def run_market_scan(universe_df, progress_bar, status_box):
    """
    Bulk scan with provider-health detection.

    A provider outage is NOT treated as hundreds of failed stocks.
    If Yahoo coverage collapses, abort the scan and preserve the last good result.
    """
    if universe_df.empty:
        return pd.DataFrame(), [], {
            "status": "NO_UNIVERSE",
            "provider": "Yahoo",
            "coverage": 0.0,
            "message": "Universe is empty.",
        }

    universe_df = universe_df.drop_duplicates("Ticker").reset_index(drop=True)
    metadata = universe_df.set_index("Ticker").to_dict("index")
    tickers = universe_df["Ticker"].tolist()

    records = []
    failures = []
    provider_batches_failed = 0
    total_batches = max(1, int(np.ceil(len(tickers) / YAHOO_BATCH_SIZE)))

    for batch_no, start_i in enumerate(range(0, len(tickers), YAHOO_BATCH_SIZE), start=1):
        batch = tickers[start_i : start_i + YAHOO_BATCH_SIZE]
        status_box.info(
            f"Yahoo bulk download {batch_no}/{total_batches} "
            f"({start_i + 1}-{min(start_i + len(batch), len(tickers))} of {len(tickers)})"
        )

        raw = download_batch_cached(tuple(batch), conservative=False)

        # Provider-level retry: one conservative retry, not per-stock hammering.
        valid_in_batch = sum(
            1 for symbol in batch
            if price_data_status(split_batch_result(raw, symbol), min_rows=126)[0]
        )
        if valid_in_batch < max(2, int(len(batch) * 0.20)):
            raw_retry = download_batch_cached(tuple(batch), conservative=True)
            retry_valid = sum(
                1 for symbol in batch
                if price_data_status(split_batch_result(raw_retry, symbol), min_rows=126)[0]
            )
            if retry_valid > valid_in_batch:
                raw = raw_retry
                valid_in_batch = retry_valid

        if valid_in_batch < max(2, int(len(batch) * 0.20)):
            provider_batches_failed += 1

        for symbol in batch:
            df = split_batch_result(raw, symbol)
            meta = metadata.get(symbol, {})
            rec = compute_record(
                symbol,
                str(meta.get("Company", symbol)),
                str(meta.get("Sector", "")),
                df,
            )
            if rec is not None:
                records.append(rec)
            else:
                failures.append(symbol)

        progress_bar.progress(min(batch_no / total_batches, 1.0))

        # Fail fast when the first two large batches are essentially empty.
        if batch_no >= 2 and provider_batches_failed == batch_no and len(records) < 5:
            break

    coverage = len(records) / max(len(tickers), 1)

    if coverage < 0.20:
        health = {
            "status": "PROVIDER_FAILURE",
            "provider": "Yahoo",
            "coverage": coverage,
            "message": (
                f"Yahoo bulk coverage collapsed to {len(records)}/{len(tickers)} "
                f"({coverage:.0%}). Scan aborted to protect data integrity."
            ),
        }
    elif coverage < 0.80:
        health = {
            "status": "DEGRADED",
            "provider": "Yahoo",
            "coverage": coverage,
            "message": f"Yahoo coverage is degraded: {len(records)}/{len(tickers)} ({coverage:.0%}).",
        }
    else:
        health = {
            "status": "HEALTHY",
            "provider": "Yahoo",
            "coverage": coverage,
            "message": f"Yahoo coverage healthy: {len(records)}/{len(tickers)} ({coverage:.0%}).",
        }

    return pd.DataFrame(records), failures, health



# ---------------------------
# Market regime
# ---------------------------
@st.cache_data(ttl=300, show_spinner=False)
def market_regime():
    score = 0.0
    rows = []

    for symbol, weight in [("SPY", 2), ("QQQ", 2), ("IWM", 1)]:
        df = download_one(symbol, "1y")
        valid, _ = price_data_status(df, min_rows=126)
        if not valid:
            rows.append((symbol, "Unavailable"))
            continue

        close = df["Close"]
        ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
        ema200 = close.ewm(span=200, adjust=False).mean().iloc[-1]
        last = float(close.iloc[-1])

        if last > ema20 > ema50:
            score += weight
            signal = "Bullish trend"
        elif last < ema20 < ema50:
            score -= weight
            signal = "Bearish trend"
        else:
            signal = "Mixed trend"

        score += 0.5 if last > ema200 else -0.5
        rows.append((symbol, signal))

    vix_df = download_one("^VIX", "6mo")
    vix = np.nan
    if not vix_df.empty:
        vix = float(vix_df["Close"].iloc[-1])
        if vix < 18:
            score += 1
            vix_signal = "Low volatility"
        elif vix > 25:
            score -= 1.5
            vix_signal = "High volatility"
        else:
            vix_signal = "Moderate volatility"
        rows.append(("VIX", f"{vix_signal} ({vix:.1f})"))

    if score >= 5:
        label, bias = "RISK-ON", "Favor long momentum"
    elif score >= 2:
        label, bias = "BULLISH", "Prefer longs"
    elif score > -2:
        label, bias = "NEUTRAL", "Demand stronger confirmation"
    elif score > -5:
        label, bias = "BEARISH", "Selective shorts / defensive"
    else:
        label, bias = "RISK-OFF", "Favor cash / quality shorts"

    return {"score": score, "label": label, "bias": bias, "vix": vix, "rows": rows}

regime = market_regime()

def is_regime_aligned(row):
    setup = str(row.get("Setup", ""))
    if regime["label"] in {"RISK-ON", "BULLISH"}:
        return setup in {"A+ Long", "A Long", "B+ Long", "Long Watch", "Technical Long"}
    if regime["label"] in {"RISK-OFF", "BEARISH"}:
        return setup in {"A+ Short", "A Short", "B+ Short", "Short Watch", "Technical Short"}
    return setup not in {"Neutral", "Mixed"}

def add_ranking_fields(df, min_composite, min_volume, rsi_low, rsi_high):
    """Professional swing-candidate ranking engine.

    Design:
    1) Tradeability gates remove low-quality / hard-to-trade names.
    2) Relative strength is measured versus SPY and within the selected universe.
    3) Quality Score (0-100) ranks trend, momentum, RS, liquidity/volume,
       volatility/risk, entry location, and market-regime fit.
    4) Setup Type describes how the stock may be tradable: breakout, pullback,
       continuation, or watch.
    """
    if df.empty:
        return df

    x = df.copy()
    numeric_cols = [
        "Close", "EMA20", "EMA50", "EMA200", "RSI14", "Volume Ratio",
        "1D %", "1W %", "1M %", "3M %", "6M %",
        "Daily", "Weekly", "Monthly", "Momentum Score",
        "ATR14", "ATR50", "Avg Dollar Volume 20", "High20", "High252",
        "Low20", "Low252", "Volatility20", "History Days",
    ]
    for col in numeric_cols:
        if col in x.columns:
            x[col] = pd.to_numeric(x[col], errors="coerce")

    # ---------------------------
    # Relative Strength vs SPY
    # ---------------------------
    spy_df = download_one("SPY", "1y")
    if not spy_df.empty and len(spy_df) >= 127:
        spy_1m = float(spy_df["Close"].pct_change(20).iloc[-1])
        spy_3m = float(spy_df["Close"].pct_change(60).iloc[-1])
        spy_6m = float(spy_df["Close"].pct_change(126).iloc[-1])
    else:
        spy_1m = spy_3m = spy_6m = np.nan

    x["RS 1M vs SPY"] = (x["1M %"] - spy_1m) * 100.0
    x["RS 3M vs SPY"] = (x["3M %"] - spy_3m) * 100.0
    x["RS 6M vs SPY"] = (x["6M %"] - spy_6m) * 100.0

    # Dynamic weighting prevents newer stocks with missing 6M history from
    # being falsely treated as neutral. Available periods are reweighted.
    def rs_composite(row):
        values = [
            (row.get("RS 1M vs SPY"), 0.20),
            (row.get("RS 3M vs SPY"), 0.35),
            (row.get("RS 6M vs SPY"), 0.45),
        ]
        valid = [(v, w) for v, w in values if pd.notna(v)]
        if not valid:
            return np.nan
        total_w = sum(w for _, w in valid)
        return sum(v * w for v, w in valid) / total_w

    x["RS Composite"] = x.apply(rs_composite, axis=1)
    x["RS Rating"] = (
        x["RS Composite"]
        .rank(pct=True, method="average", na_option="bottom")
        .mul(99)
        .add(1)
        .round()
        .clip(1, 100)
        .astype("Int64")
    )

    # ---------------------------
    # Derived tradeability / location metrics
    # ---------------------------
    x["ATR %"] = np.where(
        x["Close"].gt(0) & x["ATR14"].notna(),
        x["ATR14"] / x["Close"] * 100.0,
        np.nan,
    )
    x["EMA20 Distance %"] = np.where(
        x["EMA20"].gt(0),
        (x["Close"] / x["EMA20"] - 1.0) * 100.0,
        np.nan,
    )
    x["20D High Distance %"] = np.where(
        x["High20"].gt(0),
        (x["Close"] / x["High20"] - 1.0) * 100.0,
        np.nan,
    )
    x["52W High Distance %"] = np.where(
        x["High252"].gt(0),
        (x["Close"] / x["High252"] - 1.0) * 100.0,
        np.nan,
    )

    # ---------------------------
    # Hard tradeability gates
    # ---------------------------
    price_ok = x["Close"].fillna(0) >= 10.0
    liquidity_ok = x["Avg Dollar Volume 20"].fillna(0) >= 20_000_000
    history_ok = x["History Days"].fillna(0) >= 126
    atr_ok = x["ATR %"].between(1.0, 8.0, inclusive="both")
    data_ok = x[["Close", "EMA20", "EMA50", "RSI14", "Momentum Score"]].notna().all(axis=1)

    x["Tradeable"] = price_ok & liquidity_ok & history_ok & atr_ok & data_ok

    def gate_reason(row):
        reasons = []
        if pd.isna(row.get("Close")) or row["Close"] < 10:
            reasons.append("price < $10")
        if pd.isna(row.get("Avg Dollar Volume 20")) or row["Avg Dollar Volume 20"] < 20_000_000:
            reasons.append("20D dollar volume < $20M")
        if pd.isna(row.get("History Days")) or row["History Days"] < 126:
            reasons.append("insufficient price history")
        if pd.isna(row.get("ATR %")):
            reasons.append("ATR unavailable")
        elif not (1.0 <= row["ATR %"] <= 8.0):
            reasons.append("ATR% outside 1-8%")
        return "; ".join(reasons)

    x["Gate Note"] = x.apply(gate_reason, axis=1)

    # ---------------------------
    # Direction / market regime
    # ---------------------------
    long_direction = x["Momentum Score"] >= 15
    short_direction = x["Momentum Score"] <= -15

    long_trend = (
        (x["Close"] > x["EMA20"])
        & (x["EMA20"] > x["EMA50"])
        & (x["EMA50"] > x["EMA200"])
        & (x["Close"] > x["EMA200"])
    )
    short_trend = (
        (x["Close"] < x["EMA20"])
        & (x["EMA20"] < x["EMA50"])
        & (x["EMA50"] < x["EMA200"])
        & (x["Close"] < x["EMA200"])
    )

    long_rs = (x["RS Rating"] >= 70) & (x["RS Composite"] > 0)
    short_rs = (x["RS Rating"] <= 31) & (x["RS Composite"] < 0)

    long_location = (
        x["EMA20 Distance %"].between(-2.5, 8.0, inclusive="both")
        & (x["RSI14"] < 75)
    )
    short_location = (
        x["EMA20 Distance %"].between(-8.0, 2.5, inclusive="both")
        & (x["RSI14"] > 25)
    )

    # Volume confirmation: allow ordinary volume for pullbacks, stronger volume
    # is rewarded separately rather than required for every good setup.
    volume_healthy = x["Volume Ratio"].fillna(0) >= 0.75
    volume_strong = x["Volume Ratio"].fillna(0) >= 1.20

    # Momentum persistence across 1M/3M/6M.
    long_persistence = (
        (x["1M %"].fillna(-999) > 0)
        & (x["3M %"].fillna(-999) > 0)
        & (x["6M %"].fillna(-999) > 0)
    )
    short_persistence = (
        (x["1M %"].fillna(999) < 0)
        & (x["3M %"].fillna(999) < 0)
        & (x["6M %"].fillna(999) < 0)
    )

    if regime["label"] in {"RISK-ON", "BULLISH"}:
        long_regime = pd.Series(True, index=x.index)
        short_regime = pd.Series(False, index=x.index)
    elif regime["label"] in {"RISK-OFF", "BEARISH"}:
        long_regime = pd.Series(False, index=x.index)
        short_regime = pd.Series(True, index=x.index)
    else:
        long_regime = pd.Series(True, index=x.index)
        short_regime = pd.Series(True, index=x.index)

    # ---------------------------
    # 100-point Professional Quality Score
    # ---------------------------
    # Trend 25, RS 20, momentum 15, liquidity 10, volume 10,
    # entry location 10, risk/ATR 5, regime fit 5.
    long_score = (
        np.where(long_trend, 25, np.where((x["Close"] > x["EMA50"]) & (x["Close"] > x["EMA200"]), 15, 0))
        + np.where(long_rs, 20, np.where((x["RS Rating"] >= 55) & (x["RS Composite"] > 0), 10, 0))
        + np.where((x["Momentum Score"] >= 40) & long_persistence, 15,
                   np.where(x["Momentum Score"] >= 25, 10, np.where(x["Momentum Score"] >= 15, 5, 0)))
        + np.where(liquidity_ok, 10, 0)
        + np.where(volume_strong, 10, np.where(volume_healthy, 6, 0))
        + np.where(long_location, 10, 0)
        + np.where(atr_ok, 5, 0)
        + np.where(long_regime, 5, 0)
    )

    short_score = (
        np.where(short_trend, 25, np.where((x["Close"] < x["EMA50"]) & (x["Close"] < x["EMA200"]), 15, 0))
        + np.where(short_rs, 20, np.where((x["RS Rating"] <= 45) & (x["RS Composite"] < 0), 10, 0))
        + np.where((x["Momentum Score"] <= -40) & short_persistence, 15,
                   np.where(x["Momentum Score"] <= -25, 10, np.where(x["Momentum Score"] <= -15, 5, 0)))
        + np.where(liquidity_ok, 10, 0)
        + np.where(volume_strong, 10, np.where(volume_healthy, 6, 0))
        + np.where(short_location, 10, 0)
        + np.where(atr_ok, 5, 0)
        + np.where(short_regime, 5, 0)
    )

    x["Quality Score"] = np.where(
        long_direction,
        long_score,
        np.where(short_direction, short_score, 0),
    ).astype(float)

    # Hard-gate failures cannot be quality candidates.
    x.loc[~x["Tradeable"], "Quality Score"] = np.minimum(
        x.loc[~x["Tradeable"], "Quality Score"], 49
    )

    # ---------------------------
    # Setup classification
    # ---------------------------
    near_20d_high = x["20D High Distance %"].between(-3.0, 0.5, inclusive="both")
    near_52w_high = x["52W High Distance %"].between(-8.0, 0.5, inclusive="both")
    long_pullback = (
        long_trend
        & x["EMA20 Distance %"].between(-2.0, 3.0, inclusive="both")
        & x["RSI14"].between(45, 65, inclusive="both")
    )
    short_pullback = (
        short_trend
        & x["EMA20 Distance %"].between(-3.0, 2.0, inclusive="both")
        & x["RSI14"].between(35, 55, inclusive="both")
    )
    long_breakout = long_trend & near_20d_high & near_52w_high & volume_strong & long_rs
    short_breakdown = short_trend & (x["Close"] <= x["Low20"] * 1.03) & volume_strong & short_rs

    x["Setup Type"] = "Watch"
    x.loc[long_direction & long_trend, "Setup Type"] = "Trend Continuation"
    x.loc[short_direction & short_trend, "Setup Type"] = "Trend Continuation Short"
    x.loc[long_pullback, "Setup Type"] = "Pullback to Trend"
    x.loc[short_pullback, "Setup Type"] = "Rally into Resistance"
    x.loc[long_breakout, "Setup Type"] = "Breakout"
    x.loc[short_breakdown, "Setup Type"] = "Breakdown"

    # ---------------------------
    # Professional grade
    # ---------------------------
    x["Setup"] = "Avoid"
    x.loc[x["Tradeable"] & long_direction & (x["Quality Score"] >= 90), "Setup"] = "A+ Long"
    x.loc[x["Tradeable"] & long_direction & x["Quality Score"].between(82, 89.999), "Setup"] = "A Long"
    x.loc[x["Tradeable"] & long_direction & x["Quality Score"].between(74, 81.999), "Setup"] = "B+ Long"
    x.loc[x["Tradeable"] & long_direction & x["Quality Score"].between(65, 73.999), "Setup"] = "Long Watch"

    x.loc[x["Tradeable"] & short_direction & (x["Quality Score"] >= 90), "Setup"] = "A+ Short"
    x.loc[x["Tradeable"] & short_direction & x["Quality Score"].between(82, 89.999), "Setup"] = "A Short"
    x.loc[x["Tradeable"] & short_direction & x["Quality Score"].between(74, 81.999), "Setup"] = "B+ Short"
    x.loc[x["Tradeable"] & short_direction & x["Quality Score"].between(65, 73.999), "Setup"] = "Short Watch"

    x.loc[x["Momentum Score"].abs() < 15, "Setup"] = "Neutral"

    # Explainability columns.
    x["Trend Check"] = np.where(long_trend | short_trend, "✅", "❌")
    x["Momentum Check"] = np.where(
        ((long_direction & (x["Momentum Score"] >= 25)) | (short_direction & (x["Momentum Score"] <= -25))),
        "✅", "❌"
    )
    x["RS Check"] = np.where(long_rs | short_rs, "✅", "❌")
    x["Volume Check"] = np.where(volume_healthy, "✅", "❌")
    x["Extension Check"] = np.where(long_location | short_location, "✅", "❌")
    x["Liquidity Check"] = np.where(liquidity_ok, "✅", "❌")
    x["Regime Check"] = np.where(
        (long_direction & long_regime) | (short_direction & short_regime), "✅", "❌"
    )

    def quality_note(row):
        if not bool(row.get("Tradeable", False)):
            return "Fails tradeability gate: " + (row.get("Gate Note") or "data/liquidity requirement")
        if abs(row.get("Momentum Score", 0)) < 15:
            return "Neutral momentum; no directional edge."
        strengths = []
        weaknesses = []
        checks = [
            ("trend", row.get("Trend Check")),
            ("relative strength", row.get("RS Check")),
            ("volume", row.get("Volume Check")),
            ("entry location", row.get("Extension Check")),
            ("liquidity", row.get("Liquidity Check")),
            ("regime", row.get("Regime Check")),
        ]
        for name, check in checks:
            (strengths if check == "✅" else weaknesses).append(name)
        note = f"{int(row['Quality Score'])}/100 quality score."
        if strengths:
            note += " Strengths: " + ", ".join(strengths) + "."
        if weaknesses:
            note += " Improve/monitor: " + ", ".join(weaknesses) + "."
        return note

    x["Setup Note"] = x.apply(quality_note, axis=1)

    # Regime alignment.
    x["Regime Aligned"] = x.apply(is_regime_aligned, axis=1)

    # User filter reasons remain separate from the professional grade.
    def reasons(row):
        out = []
        if not bool(row.get("Tradeable", False)):
            out.append(row.get("Gate Note") or "tradeability gate failed")
        if pd.isna(row.get("Volume Ratio")) or row["Volume Ratio"] < min_volume:
            out.append(f"Vol<{min_volume:.1f}x")
        if pd.isna(row.get("RSI14")):
            out.append("RSI unavailable")
        elif row["RSI14"] < rsi_low or row["RSI14"] > rsi_high:
            out.append(f"RSI outside {rsi_low}-{rsi_high}")
        if regime["label"] in {"RISK-OFF", "BEARISH"}:
            if pd.notna(row.get("Momentum Score")) and row["Momentum Score"] > -abs(min_composite):
                out.append("short momentum below user threshold")
        else:
            if pd.notna(row.get("Momentum Score")) and row["Momentum Score"] < min_composite:
                out.append("momentum below user threshold")
        if not row.get("Regime Aligned", False):
            out.append("not regime-aligned")
        return "; ".join(dict.fromkeys([v for v in out if v]))

    x["Filter Reasons"] = x.apply(reasons, axis=1)
    x["Passes Filters"] = x["Filter Reasons"].eq("")

    # Ranking is now dominated by the professional Quality Score.
    # RS and volume are tie-breakers only.
    x["Adjusted Score"] = (
        x["Quality Score"]
        + x["RS Composite"].fillna(0).clip(-10, 10) * 0.20
        + (x["Volume Ratio"].fillna(1) - 1).clip(-0.5, 1.5) * 2.0
    )
    x["Adjusted Score"] = x["Adjusted Score"].replace([np.inf, -np.inf], np.nan).fillna(-9999.0)
    ranks = x["Adjusted Score"].rank(method="min", ascending=False, na_option="bottom")
    x["Rank"] = ranks.fillna(len(x) + 1).round().astype("Int64")
    return x



def compute_search_diagnostic(symbol, company, df, metadata):
    """Decision-oriented swing diagnostic from validated daily price history."""
    valid, reason = price_data_status(df, min_rows=126)
    if not valid:
        return {"Data Valid": False, "Data Error": reason}

    x = sanitize_ohlcv(df)
    x["EMA20"] = x["Close"].ewm(span=20, adjust=False).mean()
    x["EMA50"] = x["Close"].ewm(span=50, adjust=False).mean()
    x["EMA200"] = x["Close"].ewm(span=200, adjust=False).mean()

    true_range = pd.concat(
        [
            x["High"] - x["Low"],
            (x["High"] - x["Close"].shift(1)).abs(),
            (x["Low"] - x["Close"].shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    x["ATR14"] = true_range.rolling(14).mean()
    x["RSI14"] = rsi(x["Close"], 14)
    x["Vol20"] = x["Volume"].rolling(20).mean()
    x["Volume Ratio"] = x["Volume"] / x["Vol20"]

    x["1D %"] = x["Close"].pct_change(1)
    x["1W %"] = x["Close"].pct_change(5)
    x["1M %"] = x["Close"].pct_change(20)
    x["3M %"] = x["Close"].pct_change(60)
    x["6M %"] = x["Close"].pct_change(126)

    latest = x.iloc[-1]
    prev = x.iloc[-6] if len(x) >= 6 else latest

    daily = momentum_score(latest["1D %"], 2500)
    weekly = momentum_score(latest["1W %"], 700)
    monthly = momentum_score(latest["1M %"], 350)
    composite = daily * 0.40 + weekly * 0.35 + monthly * 0.25

    if not all(np.isfinite(v) for v in [daily, weekly, monthly, composite]):
        return {
            "Data Valid": False,
            "Data Error": "Momentum inputs are incomplete or non-finite, so no trading assessment was generated.",
        }

    prev_daily = momentum_score(prev["1D %"], 2500) if pd.notna(prev["1D %"]) else np.nan
    prev_weekly = momentum_score(prev["1W %"], 700) if pd.notna(prev["1W %"]) else np.nan
    prev_monthly = momentum_score(prev["1M %"], 350) if pd.notna(prev["1M %"]) else np.nan

    acceleration_delta = np.nan
    if all(pd.notna(v) for v in [prev_daily, prev_weekly, prev_monthly]):
        prev_composite = prev_daily * 0.40 + prev_weekly * 0.35 + prev_monthly * 0.25
        acceleration_delta = composite - prev_composite
        if acceleration_delta >= 10:
            acceleration = "Accelerating ↑"
        elif acceleration_delta <= -10:
            acceleration = "Weakening ↓"
        else:
            acceleration = "Stable →"
    else:
        acceleration = "N/A"

    close = finite_or_nan(latest["Close"])
    ema20 = finite_or_nan(latest["EMA20"])
    ema50 = finite_or_nan(latest["EMA50"])
    ema200 = finite_or_nan(latest["EMA200"])

    if not all(np.isfinite(v) and v > 0 for v in [close, ema20, ema50, ema200]):
        return {
            "Data Valid": False,
            "Data Error": "Latest price/trend values are incomplete, so no trading assessment was generated.",
        }
    atr = float(latest["ATR14"]) if pd.notna(latest["ATR14"]) else close * 0.025
    rsi14 = finite_or_nan(latest["RSI14"])
    if not np.isfinite(rsi14):
        return {
            "Data Valid": False,
            "Data Error": "RSI could not be calculated from the available data.",
        }
    vol_ratio = float(latest["Volume Ratio"]) if pd.notna(latest["Volume Ratio"]) else np.nan

    bull_stack = close > ema20 > ema50 > ema200
    bull_mid = close > ema20 > ema50
    bear_stack = close < ema20 < ema50 < ema200
    bear_mid = close < ema20 < ema50

    if bull_stack:
        trend = "Strong bullish"
    elif bull_mid:
        trend = "Bullish"
    elif bear_stack:
        trend = "Strong bearish"
    elif bear_mid:
        trend = "Bearish"
    else:
        trend = "Mixed"

    dist_ema20 = close / ema20 - 1 if ema20 else np.nan
    if dist_ema20 > 0.08 or rsi14 >= 75:
        extension = "Extended"
    elif dist_ema20 < -0.08 or rsi14 <= 25:
        extension = "Oversold"
    else:
        extension = "Normal"

    rs_text = "N/A"
    rs_score = np.nan
    rs_1m = rs_3m = rs_6m = np.nan
    spy = download_one("SPY", "1y")
    if not spy.empty and len(spy) >= 127:
        spy_1m = float(spy["Close"].pct_change(20).iloc[-1])
        spy_3m = float(spy["Close"].pct_change(60).iloc[-1])
        spy_6m = float(spy["Close"].pct_change(126).iloc[-1])

        stock_1m = float(latest["1M %"])
        stock_3m = float(latest["3M %"])
        stock_6m = float(latest["6M %"])

        if all(np.isfinite(v) for v in [stock_1m, stock_3m, stock_6m, spy_1m, spy_3m, spy_6m]):
            rs_1m = (stock_1m - spy_1m) * 100.0
            rs_3m = (stock_3m - spy_3m) * 100.0
            rs_6m = (stock_6m - spy_6m) * 100.0
            rs_score = rs_1m * 0.20 + rs_3m * 0.35 + rs_6m * 0.45

        if np.isfinite(rs_score) and rs_score >= 10:
            rs_text = "Strongly outperforming SPY"
        elif np.isfinite(rs_score) and rs_score >= 3:
            rs_text = "Outperforming SPY"
        elif np.isfinite(rs_score) and rs_score <= -10:
            rs_text = "Strongly underperforming SPY"
        elif np.isfinite(rs_score) and rs_score <= -3:
            rs_text = "Underperforming SPY"
        elif np.isfinite(rs_score):
            rs_text = "In line with SPY"

    sector = metadata.get("sector", "") or "N/A"
    industry = metadata.get("industry", "") or ""

    earnings_date = metadata.get("earnings_date")
    earnings_text = "No date available"
    earnings_risk = False
    if earnings_date is not None and not pd.isna(earnings_date):
        days = (pd.Timestamp(earnings_date).normalize() - pd.Timestamp.today().normalize()).days
        if days >= 0:
            earnings_risk = days <= 14
            earnings_text = f"{days} days away" if days > 0 else "Today"
        else:
            earnings_text = "Recently reported"

    high20 = float(x["High"].iloc[-21:-1].max()) if len(x) >= 21 else close
    low20 = float(x["Low"].iloc[-21:-1].min()) if len(x) >= 21 else close

    long_bias = composite >= 15 and close > ema20
    short_bias = composite <= -15 and close < ema20

    # v6.7 Tradeability & Risk Engine
    # Candidate quality and current entry quality are separate.
    extended_long = (dist_ema20 > 0.08) or (rsi14 >= 75)
    extended_short = (dist_ema20 < -0.08) or (rsi14 <= 25)

    trade_state = "NO TRADE"
    trade_block_reason = ""
    stop_pct = np.nan
    entry_mid = np.nan

    if long_bias and extended_long:
        entry_low = entry_high = stop = target1 = target2 = rr = np.nan
        bias = "WAIT"
        trade_state = "WAIT FOR PULLBACK"
        trade_block_reason = (
            f"Extended: price is {dist_ema20:+.1%} vs EMA20 and RSI is {rsi14:.1f}. "
            "Do not chase. Wait for a pullback, consolidation, or new base before recalculating entry."
        )
    elif short_bias and extended_short:
        entry_low = entry_high = stop = target1 = target2 = rr = np.nan
        bias = "WAIT"
        trade_state = "WAIT FOR BOUNCE"
        trade_block_reason = (
            f"Oversold: price is {dist_ema20:+.1%} vs EMA20 and RSI is {rsi14:.1f}. "
            "Do not chase weakness. Wait for a bounce/rejection before recalculating entry."
        )
    elif long_bias:
        entry_low = max(ema20, close - 0.35 * atr)
        entry_high = close + 0.10 * atr
        entry_mid = (entry_low + entry_high) / 2.0

        stop = min(ema50, entry_low - 1.10 * atr)
        stop_pct = (entry_mid - stop) / entry_mid if entry_mid > 0 else np.nan

        if not np.isfinite(stop_pct):
            bias = "WAIT"
            trade_state = "NO TRADE"
            trade_block_reason = "Stop distance could not be validated."
            entry_low = entry_high = stop = target1 = target2 = rr = np.nan
        elif stop_pct > 0.10:
            bias = "WAIT"
            trade_state = "WAIT FOR BETTER ENTRY"
            trade_block_reason = (
                f"Required stop is {stop_pct:.1%} from the proposed entry, above the 10% hard cap. "
                "Wait for price to pull back or form a tighter structure."
            )
            entry_low = entry_high = stop = target1 = target2 = rr = np.nan
        elif stop_pct < 0.02:
            bias = "WAIT"
            trade_state = "WAIT FOR STRUCTURE"
            trade_block_reason = (
                f"Required stop is only {stop_pct:.1%}, too tight for a normal swing setup. "
                "Wait for a clearer structure."
            )
            entry_low = entry_high = stop = target1 = target2 = rr = np.nan
        else:
            risk = entry_mid - stop
            target1 = max(high20, entry_mid + 1.5 * risk)
            target2 = entry_mid + 2.5 * risk
            rr = (target2 - entry_mid) / max(risk, 0.01)
            bias = "LONG"
            trade_state = "ACTIONABLE"

    elif short_bias:
        entry_high = min(ema20, close + 0.35 * atr)
        entry_low = close - 0.10 * atr
        entry_mid = (entry_low + entry_high) / 2.0

        stop = max(ema50, entry_high + 1.10 * atr)
        stop_pct = (stop - entry_mid) / entry_mid if entry_mid > 0 else np.nan

        if not np.isfinite(stop_pct):
            bias = "WAIT"
            trade_state = "NO TRADE"
            trade_block_reason = "Stop distance could not be validated."
            entry_low = entry_high = stop = target1 = target2 = rr = np.nan
        elif stop_pct > 0.10:
            bias = "WAIT"
            trade_state = "WAIT FOR BETTER ENTRY"
            trade_block_reason = (
                f"Required stop is {stop_pct:.1%} from the proposed entry, above the 10% hard cap. "
                "Wait for a rally/rejection or tighter structure."
            )
            entry_low = entry_high = stop = target1 = target2 = rr = np.nan
        elif stop_pct < 0.02:
            bias = "WAIT"
            trade_state = "WAIT FOR STRUCTURE"
            trade_block_reason = (
                f"Required stop is only {stop_pct:.1%}, too tight for a normal swing setup. "
                "Wait for clearer structure."
            )
            entry_low = entry_high = stop = target1 = target2 = rr = np.nan
        else:
            risk = stop - entry_mid
            target1 = min(low20, entry_mid - 1.5 * risk)
            target2 = entry_mid - 2.5 * risk
            rr = (entry_mid - target2) / max(risk, 0.01)
            bias = "SHORT"
            trade_state = "ACTIONABLE"
    else:
        entry_low = entry_high = stop = target1 = target2 = rr = np.nan
        bias = "WAIT"
        trade_state = "NO TRADE"
        trade_block_reason = "Momentum/trend alignment is mixed."

    points = 0
    points += 2 if bull_stack else 1 if bull_mid else -2 if bear_stack else -1 if bear_mid else 0
    points += 2 if composite >= 40 else 1 if composite >= 20 else -2 if composite <= -40 else -1 if composite <= -20 else 0
    points += 1 if np.isfinite(rs_score) and rs_score >= 1 else -1 if np.isfinite(rs_score) and rs_score <= -1 else 0
    points += 1 if pd.notna(vol_ratio) and vol_ratio >= 1.2 else 0
    points -= 1 if extension == "Extended" else 0
    points -= 2 if earnings_risk else 0

    if bias == "LONG":
        verdict = "A — ACTIONABLE LONG" if points >= 5 else "B+ — LONG WATCH" if points >= 3 else "B — CONDITIONAL LONG" if points >= 1 else "C — WAIT"
    elif bias == "SHORT":
        verdict = "A — ACTIONABLE SHORT" if points <= -5 else "B+ — SHORT WATCH" if points <= -3 else "B — CONDITIONAL SHORT" if points <= -1 else "C — WAIT"
    else:
        verdict = "C — WAIT / MIXED"

    # Plain-English interpretation for the UI.
    if composite >= 70:
        momentum_grade = "Very strong"
    elif composite >= 40:
        momentum_grade = "Strong"
    elif composite >= 15:
        momentum_grade = "Moderate bullish"
    elif composite <= -70:
        momentum_grade = "Very weak"
    elif composite <= -40:
        momentum_grade = "Weak"
    elif composite <= -15:
        momentum_grade = "Moderate bearish"
    else:
        momentum_grade = "Neutral"

    chase_risk = "Normal"
    trade_comment = ""
    if long_bias and extension == "Extended":
        chase_risk = "Elevated"
        trade_comment = (
            "Strong momentum, but chase risk is elevated. "
            "Prefer a pullback toward EMA20 / support or fresh confirmation rather than chasing."
        )
    elif short_bias and extension == "Oversold":
        chase_risk = "Elevated"
        trade_comment = (
            "Bearish momentum is strong, but the stock is stretched lower. "
            "Prefer a bounce/rejection setup rather than chasing weakness."
        )
    elif bias == "LONG":
        trade_comment = "Momentum and trend are constructive; focus on entry quality and risk/reward."
    elif bias == "SHORT":
        trade_comment = "Bearish momentum and trend are constructive for shorts; focus on entry quality."
    else:
        trade_comment = "Momentum/trend alignment is mixed. Waiting for confirmation is preferable."

    return {
        "Data Valid": True,
        "Data Error": "",
        "Ticker": symbol, "Company": company, "Close": close,
        "Daily": daily, "Weekly": weekly, "Monthly": monthly, "Momentum Score": composite,
        "Momentum Grade": momentum_grade,
        "Acceleration": acceleration, "Acceleration Delta": acceleration_delta,
        "Trend": trend, "RSI14": rsi14,
        "Volume Ratio": vol_ratio, "Extension": extension,
        "Distance EMA20": dist_ema20,
        "RS vs SPY": rs_text,
        "RS 1M vs SPY": rs_1m,
        "RS 3M vs SPY": rs_3m,
        "RS 6M vs SPY": rs_6m,
        "RS Composite": rs_score,
        "Chase Risk": chase_risk,
        "Trade Comment": trade_comment,
        "Sector": sector, "Industry": industry, "Earnings": earnings_text,
        "Earnings Risk": earnings_risk, "Bias": bias, "Verdict": verdict,
        "Entry Low": entry_low, "Entry High": entry_high, "Stop": stop,
        "Target 1": target1, "Target 2": target2, "RR": rr,
        "Trade State": trade_state,
        "Trade Block Reason": trade_block_reason,
        "Stop %": stop_pct,
        "EMA20": ema20, "EMA50": ema50, "EMA200": ema200,
        "Market Cap": metadata.get("market_cap", np.nan),
        "Trailing PE": metadata.get("trailing_pe", np.nan),
        "Forward PE": metadata.get("forward_pe", np.nan),
    }


def fmt_price(value):
    return "N/A" if pd.isna(value) else f"${value:,.2f}"


def fmt_ratio(value):
    return "N/A" if pd.isna(value) else f"{value:.1f}R"


# ---------------------------
# Compact market-regime header
# ---------------------------
st.subheader("Market Regime")
m1, m2, m3, m4 = st.columns(4)
m1.metric("Regime", regime["label"])
m2.metric("Score", f"{regime['score']:.1f}")
m3.metric("VIX", f"{regime['vix']:.1f}" if pd.notna(regime["vix"]) else "N/A")
m4.metric("Bias", regime["bias"])

with st.expander("Regime details", expanded=False):
    st.dataframe(
        pd.DataFrame(regime["rows"], columns=["Proxy", "Signal"]),
        hide_index=True,
        use_container_width=True,
    )

# ---------------------------
# Tabs
# ---------------------------
ticker_tab, scanner_tab = st.tabs(["🔎 Ticker Search", "📊 Market Scanner"])

# ---------------------------
# Ticker autocomplete
# ---------------------------
with ticker_tab:
    st.subheader("Instant Swing-Trade Diagnostic")
    st.caption(
        "Data Quality Gate: invalid or incomplete market data produces **no score, no grade, and no trade plan**."
    )
    st.caption("Choose any ticker/company and get a fast decision-oriented readout focused on quality, entry risk, and action.")

    company_dir = load_company_directory()
    labels = company_dir["Label"].tolist()

    chosen = st.selectbox(
        "Ticker / company",
        labels,
        index=None,
        placeholder="Start typing CRDO, Cisco, NVIDIA...",
        help="Type a ticker or company name. The list filters immediately; no Enter key is required.",
    )

    if chosen:
        selected_ticker = chosen.split(" — ", 1)[0]
        selected_company = chosen.split(" — ", 1)[1] if " — " in chosen else selected_ticker

        with st.spinner(f"Analyzing {selected_ticker}..."):
            df = download_one(selected_ticker, "5y")
            metadata = get_company_snapshot(selected_ticker)
            diag = compute_search_diagnostic(selected_ticker, selected_company, df, metadata)

        if diag is None or not diag.get("Data Valid", False):
            reason = "No usable market data returned." if diag is None else diag.get("Data Error", "Market data validation failed.")
            st.error(
                f"⚠️ **Market data unavailable for {selected_ticker}.** {reason} "
                "No Momentum Score, Relative Strength, grade, or Trade Plan will be generated from invalid data."
            )
            st.info("Yahoo primary and an independent Stooq fallback are used for individual tickers. Retry shortly if both are unavailable.")
        else:
            confidence = data_confidence(df, recent_lookback=14)
            download_route = df.attrs.get("download_route", "validated market-data route")
            data_provider = df.attrs.get("provider", "Yahoo")

            if confidence["level"] == "HIGH":
                st.success(
                    f"🟢 **Data Confidence: HIGH** — {confidence['message']} "
                    f"Provider: **{data_provider}** • Retrieval: **{download_route}**."
                )
            elif confidence["level"] == "MEDIUM":
                st.warning(
                    f"🟠 **Data Confidence: MEDIUM** — {confidence['message']} "
                    f"Provider: **{data_provider}** • Retrieval: **{download_route}**."
                )
            else:
                st.error(
                    f"🔴 **Data Confidence: LOW — DO NOT ACT.** {confidence['message']} "
                    f"Provider: **{data_provider}** • Retrieval: **{download_route}**."
                )
                diag["Trade State"] = "DATA ISSUE"
                diag["Trade Block Reason"] = confidence["message"]
                diag["Bias"] = "WAIT"
                diag["Entry Low"] = diag["Entry High"] = diag["Stop"] = np.nan
                diag["Target 1"] = diag["Target 2"] = diag["RR"] = np.nan
                diag["Stop %"] = np.nan

            st.markdown(f"### {selected_ticker} — {selected_company}")
            st.markdown(f"## {diag['Verdict']}")

            # v6.7: separate stock quality from entry quality and current action.
            candidate_points = 0
            candidate_points += 2 if diag["Trend"] in ("Strong bullish", "Strong bearish") else (1 if diag["Trend"] in ("Bullish", "Bearish") else 0)
            candidate_points += 2 if abs(diag["Momentum Score"]) >= 70 else (1 if abs(diag["Momentum Score"]) >= 40 else 0)
            candidate_points += 2 if pd.notna(diag["RS Composite"]) and abs(diag["RS Composite"]) >= 15 else (1 if pd.notna(diag["RS Composite"]) and abs(diag["RS Composite"]) >= 5 else 0)
            candidate_points += 1 if pd.notna(diag["Volume Ratio"]) and diag["Volume Ratio"] >= 1.2 else 0

            if candidate_points >= 6:
                candidate_quality = "A+"
            elif candidate_points >= 5:
                candidate_quality = "A"
            elif candidate_points >= 4:
                candidate_quality = "B+"
            elif candidate_points >= 3:
                candidate_quality = "B"
            else:
                candidate_quality = "C"

            if confidence["block"]:
                entry_quality = "N/A — DATA ISSUE"
                action_label = "DO NOT ACT"
            elif diag["Trade State"] == "ACTIONABLE":
                sp = diag.get("Stop %", np.nan)
                if pd.notna(sp) and sp <= 0.06 and pd.notna(diag["RR"]) and diag["RR"] >= 2:
                    entry_quality = "A"
                elif pd.notna(sp) and sp <= 0.08:
                    entry_quality = "B+"
                else:
                    entry_quality = "B"

                if confidence["level"] == "MEDIUM":
                    entry_quality = {"A": "B+", "B+": "B", "B": "C"}.get(entry_quality, entry_quality)
                    action_label = f"{diag['Bias']} — REDUCED CONFIDENCE"
                else:
                    action_label = diag["Bias"]
            elif diag["Extension"] in ("Extended", "Oversold"):
                entry_quality = "D — EXTENDED" if diag["Extension"] == "Extended" else "D — OVERSOLD"
                action_label = diag["Trade State"]
            else:
                entry_quality = "C — NOT READY"
                action_label = diag["Trade State"]

            cq1, cq2, cq3, cq4 = st.columns(4)
            cq1.metric("Candidate Quality", candidate_quality)
            cq2.metric("Entry Quality", entry_quality)
            cq3.metric("Action", action_label)
            cq4.metric("Data Confidence", confidence["level"])
            st.caption(
                "Quality Engine: **Candidate Quality** asks whether the stock is worth watching; "
                "**Entry Quality** asks whether today's location/risk is acceptable; "
                "**Action** is the current decision. A strong candidate can correctly remain WAIT."
            )

            v1, v2, v3, v4 = st.columns(4)
            v1.metric("Close", fmt_price(diag["Close"]))
            v2.metric("Momentum Score", f"{diag['Momentum Score']:.1f}")
            if diag["Acceleration"] == "Accelerating ↑":
                st.success("Momentum: Accelerating ↑")
            elif diag["Acceleration"] == "Weakening ↓":
                st.warning("Momentum: Weakening ↓")
            else:
                st.info(f"Momentum: {diag['Acceleration']}")
            v3.metric("Trend", diag["Trend"])
            v4.metric("RS vs SPY", diag["RS vs SPY"])

            st.caption(
                "Relative Strength = the stock's return minus SPY's return. "
                "Positive = outperforming SPY; negative = underperforming."
            )
            rs1, rs2, rs3, rs4 = st.columns(4)
            rs1.metric("RS 1M", "N/A" if pd.isna(diag["RS 1M vs SPY"]) else f"{diag['RS 1M vs SPY']:+.1f} pp")
            rs2.metric("RS 3M", "N/A" if pd.isna(diag["RS 3M vs SPY"]) else f"{diag['RS 3M vs SPY']:+.1f} pp")
            rs3.metric("RS 6M", "N/A" if pd.isna(diag["RS 6M vs SPY"]) else f"{diag['RS 6M vs SPY']:+.1f} pp")
            rs4.metric("RS Composite", "N/A" if pd.isna(diag["RS Composite"]) else f"{diag['RS Composite']:+.1f}")

            st.caption(
                "RS Composite = 20% × RS 1M + 35% × RS 3M + 45% × RS 6M. "
                "Values are percentage points of outperformance/underperformance vs SPY."
            )

            # Explicit calculation note directly under headline metrics.
            st.info(
                "**Momentum Score range: -100 to +100.** Formula = 40% Daily + 35% Weekly + 25% Monthly. "
                "Daily uses 1 trading day, Weekly 5 trading days, Monthly 20 trading days. "
                "Each component is scaled/capped to a -100 to +100 score, so the composite is a momentum score, not a % return."
            )

            m1, m2, m3 = st.columns(3)
            m1.metric("Daily score", f"{diag['Daily']:.1f}")
            m2.metric("Weekly score", f"{diag['Weekly']:.1f}")
            m3.metric("Monthly score", f"{diag['Monthly']:.1f}")

            accel_delta = diag["Acceleration Delta"]
            if pd.notna(accel_delta):
                if accel_delta >= 10:
                    accel_rule = "Accelerating because momentum score improved by at least +10 points vs 5 trading days ago."
                elif accel_delta <= -10:
                    accel_rule = "Weakening because momentum score fell by at least -10 points vs 5 trading days ago."
                else:
                    accel_rule = "Stable because the momentum score changed by less than 10 points vs 5 trading days ago."
                st.caption(
                    f"Momentum quality: **{diag['Momentum Grade']}** • "
                    f"5-day momentum change: **{accel_delta:+.1f} pts** • {accel_rule}"
                )
            else:
                st.caption(f"Momentum quality: **{diag['Momentum Grade']}** • 5-day acceleration comparison unavailable.")

            q1, q2, q3, q4 = st.columns(4)
            q1.metric("RSI(14)", f"{diag['RSI14']:.1f}")
            q2.metric(
                "Volume Ratio",
                f"{diag['Volume Ratio']:.2f}x" if pd.notna(diag["Volume Ratio"]) else "N/A",
            )
            q3.metric("Extension", diag["Extension"])
            q4.metric("Sector", diag["Sector"])

            ema20_dist = diag["Distance EMA20"]
            extension_note = (
                f"Price vs EMA20: **{ema20_dist:+.1%}** • RSI(14): **{diag['RSI14']:.1f}**. "
                "Rule: **Extended** if price is >8% above EMA20 OR RSI ≥75. "
                "**Oversold** if price is >8% below EMA20 OR RSI ≤25."
                if pd.notna(ema20_dist)
                else "Extension rule: Extended if >8% above EMA20 or RSI ≥75; Oversold if >8% below EMA20 or RSI ≤25."
            )
            st.caption(extension_note)

            if diag["Industry"]:
                st.caption(f"Industry: {diag['Industry']}")

            if diag["Chase Risk"] == "Elevated":
                st.warning(f"⚠️ **Chase risk: Elevated.** {diag['Trade Comment']}")
            else:
                st.success(f"**Trade interpretation:** {diag['Trade Comment']}")

            # Explain missing metadata rather than implying N/A is a company characteristic.
            missing_meta = []
            if diag["Sector"] == "N/A":
                missing_meta.append("sector")
            if pd.isna(diag["Market Cap"]):
                missing_meta.append("market cap")
            if pd.isna(diag["Trailing PE"]):
                missing_meta.append("trailing P/E")
            if pd.isna(diag["Forward PE"]):
                missing_meta.append("forward P/E")
            if diag["Earnings"] == "No date available":
                missing_meta.append("earnings date")
            if missing_meta:
                st.warning(
                    "Some fundamental metadata could not be retrieved from the current data provider: "
                    + ", ".join(missing_meta)
                    + ". This is a data-availability issue, not an indication that the company has no such data."
                )

            if diag["Earnings Risk"]:
                st.warning(f"⚠️ Earnings risk: {diag['Earnings']}. Consider reducing size or waiting.")
            else:
                st.info(f"Earnings: {diag['Earnings']}")

            st.markdown("### Decision Summary")
            d1, d2 = st.columns(2)

            with d1:
                st.markdown("**Why it qualifies / strengths**")
                strengths = []
                if diag["Trend"] in ("Strong bullish", "Strong bearish"):
                    strengths.append(f"Strong trend: {diag['Trend']}")
                elif diag["Trend"] in ("Bullish", "Bearish"):
                    strengths.append(f"Directional trend: {diag['Trend']}")
                if pd.notna(diag["RS Composite"]) and abs(diag["RS Composite"]) >= 5:
                    strengths.append(f"Relative strength edge vs SPY: {diag['RS Composite']:+.1f} pp")
                if abs(diag["Momentum Score"]) >= 40:
                    strengths.append(f"Momentum Score: {diag['Momentum Score']:.1f}")
                if pd.notna(diag["Volume Ratio"]) and diag["Volume Ratio"] >= 1.2:
                    strengths.append(f"Volume confirmation: {diag['Volume Ratio']:.2f}x")
                if strengths:
                    for item in strengths:
                        st.write(f"• {item}")
                else:
                    st.write("• No major quality edge is currently confirmed.")

            with d2:
                st.markdown("**Why to wait / key risks**")
                risks = []
                if diag["Extension"] in ("Extended", "Oversold"):
                    risks.append(f"Entry location: {diag['Extension']}")
                if diag["Acceleration"] == "Weakening ↓":
                    risks.append("Momentum is weakening")
                if pd.notna(diag["Volume Ratio"]) and diag["Volume Ratio"] < 0.75:
                    risks.append(f"Volume is weak: {diag['Volume Ratio']:.2f}x")
                if confidence["level"] != "HIGH":
                    risks.append(f"Data Confidence is {confidence['level']}")
                if diag["Trade State"] != "ACTIONABLE":
                    risks.append(diag.get("Trade Block Reason", diag["Trade State"]))
                if risks:
                    for item in risks:
                        st.write(f"• {item}")
                else:
                    st.write("• No major blocking risk identified.")

            st.markdown("**What would improve the setup?**")
            improvement = []
            if diag["Extension"] == "Extended":
                improvement.append("Pullback/consolidation closer to EMA20 with RSI cooling below 75")
            elif diag["Extension"] == "Oversold":
                improvement.append("Bounce/rejection structure before considering a short")
            if diag["Acceleration"] == "Weakening ↓":
                improvement.append("Momentum stabilization or renewed acceleration")
            if pd.notna(diag["Volume Ratio"]) and diag["Volume Ratio"] < 1.0:
                improvement.append("Stronger volume confirmation")
            if confidence["level"] == "MEDIUM":
                improvement.append("Cleaner data feed / repaired isolated gap for higher confidence")
            if not improvement:
                improvement.append("Maintain current trend/RS while preserving acceptable entry risk")
            for item in improvement:
                st.write(f"• {item}")

            st.markdown("### Trade Plan")
            st.caption(
                "Trade plan is shown only when entry quality passes the risk gate. "
                "Hard stop-distance cap: **10%**. Extended/oversold names are blocked from actionable entries."
            )

            if diag["Trade State"] != "ACTIONABLE":
                st.warning(
                    f"**{diag['Trade State']}** — {diag.get('Trade Block Reason', '')}"
                )
                st.info(
                    "The stock can still be a high-quality candidate, but the current entry is not acceptable. "
                    "Candidate quality and entry quality are intentionally scored separately."
                )
            else:
                p1, p2, p3, p4, p5 = st.columns(5)
                p1.metric("Bias", diag["Bias"])
                p2.metric(
                    "Entry Zone",
                    f"{fmt_price(diag['Entry Low'])} – {fmt_price(diag['Entry High'])}"
                )
                p3.metric("Stop", fmt_price(diag["Stop"]))
                p4.metric(
                    "Stop Distance",
                    "N/A" if pd.isna(diag["Stop %"]) else f"{diag['Stop %']:.1%}"
                )
                p5.metric("R:R", fmt_ratio(diag["RR"]))

                t1, t2 = st.columns(2)
                t1.metric("Target 1", fmt_price(diag["Target 1"]))
                t2.metric("Target 2", fmt_price(diag["Target 2"]))

                if pd.notna(diag["Stop %"]) and diag["Stop %"] > 0.08:
                    st.warning(
                        "Stop distance is above 8%. This is still inside the 10% hard cap, "
                        "but risk is on the wide side for a typical swing trade."
                    )
                elif diag["RR"] >= 2:
                    st.success("Entry quality and risk/reward both pass the current swing-trade gate.")
                elif pd.notna(diag["RR"]):
                    st.warning("Risk/reward is below 2R. Entry quality may need improvement.")

            with st.expander("How each signal is calculated", expanded=False):
                rows = [
                    ("Momentum Score", "40% Daily + 35% Weekly + 25% Monthly; each component scaled to -100…+100"),
                    ("Daily / Weekly / Monthly", "1D / 5D / 20D price change, normalized to score"),
                    ("Acceleration", "Current composite vs composite 5 trading days ago; ±10 points triggers Accelerating/Weakening"),
                    ("Trend", "Price/EMA20/EMA50/EMA200 stacking"),
                    ("RS vs SPY", "1M / 3M / 6M excess return vs SPY; composite weights 20% / 35% / 45%"),
                    ("Volume Ratio", "Current volume ÷ 20-day average volume"),
                    ("Extension", ">8% above EMA20 or RSI≥75 = Extended; >8% below EMA20 or RSI≤25 = Oversold"),
                    ("Trade Plan", "ATR(14), EMA structure and 20-day highs/lows; blocked if extended/oversold or stop distance exceeds 10%"),
                ]
                st.dataframe(pd.DataFrame(rows, columns=["Signal", "Rule"]), hide_index=True, use_container_width=True)

            with st.expander("Fundamental snapshot", expanded=False):
                f1, f2, f3 = st.columns(3)
                mc = diag["Market Cap"]
                f1.metric("Market Cap", "N/A" if pd.isna(mc) else f"${mc/1e9:,.1f}B")
                f2.metric("Trailing P/E", "N/A" if pd.isna(diag["Trailing PE"]) else f"{diag['Trailing PE']:.1f}")
                f3.metric("Forward P/E", "N/A" if pd.isna(diag["Forward PE"]) else f"{diag['Forward PE']:.1f}")


            st.caption(
                "Trade plan levels are heuristic decision-support estimates based on ATR, trend and recent highs/lows. "
                "They are not personalized investment advice or guaranteed execution levels."
            )


# ---------------------------
# Universe scanner
# ---------------------------
with scanner_tab:
    st.subheader("Choose scan universe")

    health = st.session_state.provider_health
    h_status = health.get("status", "UNKNOWN")
    if h_status == "HEALTHY":
        st.success(f"🟢 **Data Health: HEALTHY** — {health.get('message', '')}")
    elif h_status == "DEGRADED":
        st.warning(f"🟠 **Data Health: DEGRADED** — {health.get('message', '')}")
    elif h_status == "PROVIDER_FAILURE":
        st.error(f"🔴 **Data Health: PROVIDER FAILURE** — {health.get('message', '')}")
    else:
        st.info("🔵 **Data Health:** Run a scan to test current bulk-provider coverage.")

    c1, c2 = st.columns([1, 2])
    with c1:
        universe_name = st.selectbox(
            "Universe",
            ["My Watchlist", "S&P 500", "Nasdaq-100", "Russell 2000 / IWM", "Combined US"],
            index=1,
        )
    with c2:
        watchlist_text = st.text_area(
            "My Watchlist",
            value=DEFAULT_WATCHLIST,
            height=90,
            disabled=universe_name != "My Watchlist",
        )

    universe_df = get_universe(universe_name, watchlist_text)
    if universe_df.empty:
        st.warning(
            f"{universe_name} constituent source did not load on this request. "
            "The Run Scan button remains available and will retry the source."
        )
    else:
        st.caption(f"Universe contains **{len(universe_df):,}** unique symbols.")

    st.info(
        "**Professional Quality Engine:** first applies hard tradeability gates "
        "(price, liquidity, history, ATR), then scores each stock from **0–100**. "
        "A+ ≥90, A ≥82, B+ ≥74. Ranking emphasizes trend quality, relative strength, "
        "persistent momentum, liquidity/volume, entry location, volatility, and market-regime fit."
    )

    st.caption(
        "Hard tradeability gates: price ≥ $10 • 20-day average dollar volume ≥ $20M "
        "• at least 126 trading days of history • ATR% between 1% and 8%."
    )

    with st.expander("Scanner filters", expanded=False):
        f1, f2, f3 = st.columns(3)
        with f1:
            min_composite = st.slider("Minimum Momentum Score", 0, 60, 10, 5)
        with f2:
            min_volume = st.slider("Min volume ratio", 0.0, 3.0, 0.8, 0.1)
        with f3:
            rsi_low, rsi_high = st.slider("RSI range", 0, 100, (30, 80), 1)

    run_col, clear_col = st.columns([2, 1])
    with run_col:
        run_scan = st.button(
            f"Run {universe_name} scan",
            type="primary",
            use_container_width=True,
        )
    with clear_col:
        clear_scan = st.button("Clear results", use_container_width=True)

    if clear_scan:
        st.session_state.scan_df = pd.DataFrame()
        st.session_state.scan_universe_name = ""
        st.session_state.scan_timestamp = None
        st.session_state.scan_errors = []
        st.rerun()

    if run_scan:
        if universe_df.empty:
            st.cache_data.clear()
            universe_df = get_universe(universe_name, watchlist_text)
        if universe_df.empty:
            st.error(
                f"Could not load the {universe_name} constituent list. "
                "Please retry once. If it still fails, use My Watchlist while the source is unavailable."
            )
            st.stop()

        progress = st.progress(0.0)
        status = st.empty()
        started = time.time()

        results, failures, health = run_market_scan(universe_df, progress, status)
        progress.empty()
        status.empty()

        st.session_state.provider_health = health
        elapsed = time.time() - started

        if health["status"] == "PROVIDER_FAILURE":
            previous = st.session_state.last_good_scans.get(universe_name)
            st.error(
                f"🔴 **DATA PROVIDER FAILURE** — {health['message']} "
                "The failed scan has NOT replaced your last valid results."
            )
            if previous is not None and not previous.get("df", pd.DataFrame()).empty:
                st.session_state.scan_df = previous["df"].copy()
                st.session_state.scan_universe_name = universe_name
                st.session_state.scan_timestamp = previous["timestamp"]
                st.session_state.scan_errors = previous.get("failures", [])
                st.warning(
                    f"Showing the last good {universe_name} scan from "
                    f"{previous['timestamp'].strftime('%Y-%m-%d %H:%M:%S')} instead."
                )
            else:
                st.warning(
                    "No last-good scan is available in this session. "
                    "Please retry later rather than using an empty/partial scan."
                )
        else:
            st.session_state.scan_df = results
            st.session_state.scan_universe_name = universe_name
            st.session_state.scan_timestamp = datetime.now()
            st.session_state.scan_errors = failures

            if health["status"] == "HEALTHY":
                st.session_state.last_good_scans[universe_name] = {
                    "df": results.copy(),
                    "timestamp": st.session_state.scan_timestamp,
                    "failures": list(failures),
                }
                st.success(
                    f"Scan complete: {len(results):,}/{len(universe_df):,} symbols analyzed "
                    f"in {elapsed:.0f}s."
                )
            else:
                st.warning(
                    f"⚠️ Scan completed with degraded coverage: "
                    f"{len(results):,}/{len(universe_df):,} in {elapsed:.0f}s."
                )

    scan_df = st.session_state.scan_df
    if not scan_df.empty:
        ranked = add_ranking_fields(scan_df, min_composite, min_volume, rsi_low, rsi_high)

        scan_time = st.session_state.scan_timestamp
        when = scan_time.strftime("%H:%M:%S") if scan_time else ""
        st.caption(
            f"Showing cached session scan: **{st.session_state.scan_universe_name}** • "
            f"{len(ranked):,} stocks analyzed • {when}"
        )

        a_plus_mask = ranked["Setup"].isin(["A+ Long", "A+ Short"])
        a_plus_count = int(a_plus_mask.sum())
        st.caption("A+ = professional Quality Score ≥90 after tradeability gates. High score alone is not enough if liquidity/history/ATR gates fail.")
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Stocks analyzed", f"{len(ranked):,}")
        k2.metric("Quality candidates", f"{int(ranked['Setup'].isin(['A+ Long','A Long','B+ Long','A+ Short','A Short','B+ Short']).sum()):,}")
        k3.metric("Regime-aligned", f"{int(ranked['Regime Aligned'].sum()):,}")

        with k4:
            st.caption("A+ setups")
            if st.button(
                f"{a_plus_count:,}",
                key="a_plus_count_button",
                use_container_width=True,
                help="Tap to show the exact A+ setup list.",
            ):
                st.session_state.show_a_plus = not st.session_state.show_a_plus

        # A+ setup drilldown
        if st.session_state.show_a_plus:
            st.markdown("### ⭐ A+ Setups")
            aplus = ranked[a_plus_mask].copy().sort_values(
                ["Adjusted Score", "Volume Ratio"],
                ascending=[False, False],
            )

            if aplus.empty:
                st.info("There are currently no A+ setups in this scan.")
            else:
                st.caption(
                    f"Exact A+ count: **{len(aplus):,}**. "
                    "Select a ticker below to open its chart."
                )

                aplus_cols = [
                    "Ticker", "Company", "Sector", "Setup", "Setup Type", "Quality Score",
                    "RS Rating", "RS Composite", "Momentum Score", "RSI14",
                    "Volume Ratio", "ATR %", "EMA20 Distance %", "20D High Distance %",
                    "Avg Dollar Volume 20", "Setup Note",
                ]
                aplus_cols = [c for c in aplus_cols if c in aplus.columns]

                st.dataframe(
                    aplus[aplus_cols],
                    hide_index=True,
                    use_container_width=True,
                    height=min(420, 45 + 36 * len(aplus)),
                    column_config={
                        "Quality Score": st.column_config.NumberColumn(format="%.0f"),
                        "RS Rating": st.column_config.NumberColumn(format="%d"),
                        "RS Composite": st.column_config.NumberColumn(format="%+.1f"),
                        "Momentum Score": st.column_config.NumberColumn(format="%.1f"),
                        "ATR %": st.column_config.NumberColumn(format="%.1f%%"),
                        "EMA20 Distance %": st.column_config.NumberColumn(format="%+.1f%%"),
                        "20D High Distance %": st.column_config.NumberColumn(format="%+.1f%%"),
                        "Avg Dollar Volume 20": st.column_config.NumberColumn(format="$%.0f"),
                        "RSI14": st.column_config.NumberColumn(format="%.1f"),
                        "Volume Ratio": st.column_config.NumberColumn(format="%.2fx"),
                        "1D %": st.column_config.NumberColumn(format="%.2%"),
                        "1W %": st.column_config.NumberColumn(format="%.2%"),
                        "1M %": st.column_config.NumberColumn(format="%.2%"),
                    },
                )

                ticker_options = aplus["Ticker"].tolist()
                labels = {
                    row["Ticker"]: (
                        f"{row['Ticker']} — {row.get('Company', row['Ticker'])} "
                        f"({row['Setup']})"
                    )
                    for _, row in aplus.iterrows()
                }

                selected_aplus = st.selectbox(
                    "Open A+ stock chart",
                    ticker_options,
                    format_func=lambda t: labels.get(t, t),
                    key="a_plus_ticker_select",
                )
                st.session_state.a_plus_selected_ticker = selected_aplus

                selected_row = aplus.loc[aplus["Ticker"] == selected_aplus].iloc[0]

                with st.spinner(f"Loading {selected_aplus} chart..."):
                    detail_df = download_one(selected_aplus, "5y")

                if detail_df.empty:
                    st.warning(f"No chart data is available for {selected_aplus} right now.")
                else:
                    aplus_fresh, _, _, aplus_fresh_reason = market_data_freshness(detail_df)
                    aplus_continuous, _, aplus_cont_reason = continuity_status(detail_df, lookback_days=14)
                    if aplus_fresh and aplus_continuous:
                        st.success(f"🟢 Chart data current and continuous. {aplus_fresh_reason}")
                    else:
                        st.warning(
                            f"⚠️ Chart data issue: {aplus_fresh_reason} {aplus_cont_reason} "
                            "Treat setup status as non-actionable until data is complete."
                        )
                    d = detail_df.tail(120).copy()
                    d["EMA20"] = d["Close"].ewm(span=20, adjust=False).mean()
                    d["EMA50"] = d["Close"].ewm(span=50, adjust=False).mean()
                    d["EMA200"] = d["Close"].ewm(span=200, adjust=False).mean()

                    c1, c2, c3, c4, c5 = st.columns(5)
                    c1.metric("Grade", selected_row["Setup"])
                    c2.metric("Quality Score", f"{selected_row['Quality Score']:.0f}/100")
                    c3.metric("Setup Type", selected_row.get("Setup Type", "N/A"))
                    c4.metric("RS Rating", f"{int(selected_row['RS Rating'])}" if pd.notna(selected_row.get("RS Rating")) else "N/A")
                    c5.metric("Momentum Score", f"{selected_row['Momentum Score']:.1f}")

                    st.caption(selected_row.get("Setup Note", ""))
                    st.markdown(
                        f"**Professional Checklist:** "
                        f"Trend {selected_row.get('Trend Check','')}  •  "
                        f"Momentum {selected_row.get('Momentum Check','')}  •  "
                        f"RS {selected_row.get('RS Check','')}  •  "
                        f"Volume {selected_row.get('Volume Check','')}  •  "
                        f"Entry {selected_row.get('Extension Check','')}  •  "
                        f"Liquidity {selected_row.get('Liquidity Check','')}  •  "
                        f"Regime {selected_row.get('Regime Check','')}"
                    )

                    timeframe = st.segmented_control(
                        "Chart timeframe",
                        ["Daily", "Weekly", "Monthly", "YTD", "All"],
                        default="Daily",
                        key=f"aplus_timeframe_{selected_aplus}",
                    )

                    chart_df = chart_frame_for_timeframe(detail_df, timeframe)

                    fig_aplus = go.Figure()
                    fig_aplus.add_trace(go.Candlestick(
                        x=chart_df["Date"],
                        open=chart_df["Open"],
                        high=chart_df["High"],
                        low=chart_df["Low"],
                        close=chart_df["Close"],
                        name="Price",
                    ))
                    fig_aplus.add_trace(go.Scatter(
                        x=chart_df["Date"], y=chart_df["EMA20"], name="EMA20"
                    ))
                    fig_aplus.add_trace(go.Scatter(
                        x=chart_df["Date"], y=chart_df["EMA50"], name="EMA50"
                    ))
                    fig_aplus.add_trace(go.Scatter(
                        x=chart_df["Date"], y=chart_df["EMA200"], name="EMA200"
                    ))
                    fig_aplus.update_layout(
                        title=f"{selected_aplus} — A+ Setup Chart ({timeframe})",
                        height=480,
                        xaxis_rangeslider_visible=False,
                        margin=dict(l=5, r=5, t=45, b=5),
                    )
                    st.plotly_chart(fig_aplus, use_container_width=True)

        view_mode = st.segmented_control(
            "View",
            ["Quality Candidates", "Passing Filters", "Regime-Aligned", "All"],
            default="Quality Candidates",
        )
        if view_mode == "Quality Candidates":
            shown = ranked[
                ranked["Tradeable"]
                & ranked["Setup"].isin(["A+ Long", "A Long", "B+ Long", "A+ Short", "A Short", "B+ Short"])
            ].copy()
        elif view_mode == "Passing Filters":
            shown = ranked[ranked["Passes Filters"]].copy()
        elif view_mode == "Regime-Aligned":
            shown = ranked[ranked["Regime Aligned"]].copy()
        else:
            shown = ranked.copy()

        shown = shown.sort_values(["Adjusted Score", "Volume Ratio"], ascending=[False, False])

        top_n = st.slider("Rows to display", 10, 100, 30, 10)
        shown = shown.head(top_n)

        display_cols = [
            "Rank", "Ticker", "Company", "Sector", "Setup", "Setup Type", "Quality Score",
            "Tradeable", "Regime Aligned", "RS Rating", "RS Composite",
            "Momentum Score", "RSI14", "Volume Ratio", "ATR %",
            "EMA20 Distance %", "20D High Distance %", "Avg Dollar Volume 20",
            "Setup Note", "Filter Reasons",
        ]
        existing_cols = [c for c in display_cols if c in shown.columns]

        st.dataframe(
            shown[existing_cols],
            hide_index=True,
            use_container_width=True,
            height=520,
            column_config={
                "Quality Score": st.column_config.NumberColumn(format="%.0f"),
                "RS Rating": st.column_config.NumberColumn(format="%d"),
                "RS Composite": st.column_config.NumberColumn(format="%+.1f"),
                "Momentum Score": st.column_config.NumberColumn(format="%.1f"),
                "ATR %": st.column_config.NumberColumn(format="%.1f%%"),
                "EMA20 Distance %": st.column_config.NumberColumn(format="%+.1f%%"),
                "20D High Distance %": st.column_config.NumberColumn(format="%+.1f%%"),
                "Avg Dollar Volume 20": st.column_config.NumberColumn(format="$%.0f"),
                "RSI14": st.column_config.NumberColumn(format="%.1f"),
                "Volume Ratio": st.column_config.NumberColumn(format="%.2fx"),
                "1D %": st.column_config.NumberColumn(format="%.2%"),
                "1W %": st.column_config.NumberColumn(format="%.2%"),
                "1M %": st.column_config.NumberColumn(format="%.2%"),
            },
        )

        csv = ranked.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Download full scan CSV",
            csv,
            file_name=f"{st.session_state.scan_universe_name.lower().replace(' ', '_')}_scan.csv",
            mime="text/csv",
        )

        if st.session_state.scan_errors:
            with st.expander(f"Symbols without usable data ({len(st.session_state.scan_errors)})"):
                st.write(", ".join(st.session_state.scan_errors[:300]))

    else:
        st.info(
            "Choose a universe and press **Run scan**. "
            "Large-universe results are cached for 15 minutes and also kept in your current session, "
            "so changing filters or viewing candidates does not re-download the market."
        )

st.divider()
st.caption(
    "Research / decision-support tool only. Yahoo Finance data is accessed through yfinance. "
    "Constituent lists are cached to reduce network load and improve responsiveness."
)
