
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


@st.cache_data(ttl=120, show_spinner=False)
def download_one(symbol, period="1y"):
    try:
        df = yf.download(
            symbol,
            period=period,
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=False,
            timeout=12,
        )
        if df.empty:
            return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        df = df.reset_index()
        df["Date"] = pd.to_datetime(df["Date"])
        return df
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=SCAN_CACHE_TTL, show_spinner=False)
def download_batch_cached(symbols_tuple):
    symbols = list(symbols_tuple)
    if not symbols:
        return pd.DataFrame()

    try:
        data = yf.download(
            tickers=symbols,
            period="1y",
            interval="1d",
            auto_adjust=True,
            group_by="ticker",
            progress=False,
            threads=True,
            timeout=20,
        )
        return data
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
    return df

def compute_record(symbol, company, sector, df):
    if df.empty or len(df) < 60:
        return None

    x = df.copy()
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

    row = x.iloc[-1]
    record = {
        "Ticker": symbol,
        "Company": company,
        "Sector": sector,
        "Close": float(row["Close"]),
        "EMA20": float(row["EMA20"]),
        "EMA50": float(row["EMA50"]),
        "EMA200": float(row["EMA200"]),
        "RSI14": float(row["RSI14"]),
        "Volume Ratio": float(row["Volume Ratio"]) if pd.notna(row["Volume Ratio"]) else np.nan,
        "1D %": float(row["1D %"]) if pd.notna(row["1D %"]) else np.nan,
        "1W %": float(row["1W %"]) if pd.notna(row["1W %"]) else np.nan,
        "1M %": float(row["1M %"]) if pd.notna(row["1M %"]) else np.nan,
        "3M %": float(row["3M %"]) if pd.notna(row["3M %"]) else np.nan,
        "6M %": float(row["6M %"]) if pd.notna(row["6M %"]) else np.nan,
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
    if universe_df.empty:
        return pd.DataFrame(), []

    universe_df = universe_df.drop_duplicates("Ticker").reset_index(drop=True)
    metadata = universe_df.set_index("Ticker").to_dict("index")
    tickers = universe_df["Ticker"].tolist()

    records = []
    failures = []
    total_batches = max(1, int(np.ceil(len(tickers) / YAHOO_BATCH_SIZE)))

    for batch_no, start in enumerate(range(0, len(tickers), YAHOO_BATCH_SIZE), start=1):
        batch = tickers[start : start + YAHOO_BATCH_SIZE]
        status_box.info(
            f"Downloading batch {batch_no}/{total_batches} "
            f"({start + 1}-{min(start + len(batch), len(tickers))} of {len(tickers)})"
        )
        raw = download_batch_cached(tuple(batch))

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

    return pd.DataFrame(records), failures

# ---------------------------
# Market regime
# ---------------------------
@st.cache_data(ttl=300, show_spinner=False)
def market_regime():
    score = 0.0
    rows = []

    for symbol, weight in [("SPY", 2), ("QQQ", 2), ("IWM", 1)]:
        df = download_one(symbol, "1y")
        if df.empty or len(df) < 60:
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
    """Add filters/ranking safely even when Yahoo returns partial rows."""
    if df.empty:
        return df

    x = df.copy()
    numeric_cols = [
        "Close", "EMA20", "EMA50", "EMA200", "RSI14", "Volume Ratio",
        "1D %", "1W %", "1M %", "3M %", "6M %", "Daily", "Weekly", "Monthly", "Momentum Score",
    ]
    for col in numeric_cols:
        if col in x.columns:
            x[col] = pd.to_numeric(x[col], errors="coerce")

    x["Regime Aligned"] = x.apply(is_regime_aligned, axis=1)

    # Relative Strength vs SPY.
    # RS Composite = 20% × 1M excess return + 35% × 3M excess return
    #              + 45% × 6M excess return (percentage points).
    spy_df = download_one("SPY", "1y")
    if not spy_df.empty and len(spy_df) >= 127:
        spy_1m = float(spy_df["Close"].pct_change(20).iloc[-1])
        spy_3m = float(spy_df["Close"].pct_change(60).iloc[-1])
        spy_6m = float(spy_df["Close"].pct_change(126).iloc[-1])
    else:
        spy_1m = spy_3m = spy_6m = 0.0

    x["RS 1M vs SPY"] = (x["1M %"] - spy_1m) * 100.0
    x["RS 3M vs SPY"] = (x["3M %"] - spy_3m) * 100.0
    x["RS 6M vs SPY"] = (x["6M %"] - spy_6m) * 100.0
    x["RS Composite"] = (
        x["RS 1M vs SPY"].fillna(0.0) * 0.20
        + x["RS 3M vs SPY"].fillna(0.0) * 0.35
        + x["RS 6M vs SPY"].fillna(0.0) * 0.45
    )

    # 1-100 percentile rank inside the selected universe.
    x["RS Rating"] = (
        x["RS Composite"]
        .rank(pct=True, method="average")
        .mul(99)
        .add(1)
        .round()
        .clip(1, 100)
        .astype("Int64")
    )

    # ---------------------------
    # v5 Quality Engine
    # ---------------------------
    # Distance from EMA20 measures extension/chase risk.
    x["EMA20 Distance %"] = np.where(
        x["EMA20"].notna() & x["EMA20"].ne(0),
        (x["Close"] / x["EMA20"] - 1.0) * 100.0,
        np.nan,
    )

    long_trend = (
        (x["Close"] > x["EMA20"])
        & (x["EMA20"] > x["EMA50"])
        & (x["Close"] > x["EMA200"])
    )
    short_trend = (
        (x["Close"] < x["EMA20"])
        & (x["EMA20"] < x["EMA50"])
        & (x["Close"] < x["EMA200"])
    )

    long_momentum = x["Momentum Score"] >= 25
    short_momentum = x["Momentum Score"] <= -25
    volume_confirm = x["Volume Ratio"].fillna(0) >= 1.2
    long_rsi = x["RSI14"].between(50, 75, inclusive="both")
    short_rsi = x["RSI14"].between(25, 50, inclusive="both")

    long_rs = (x["RS Rating"] >= 70) & (x["RS Composite"] > 0)
    short_rs = (x["RS Rating"] <= 31) & (x["RS Composite"] < 0)

    not_long_extended = (
        x["EMA20 Distance %"].fillna(999) <= 8.0
    ) & (x["RSI14"].fillna(999) < 75)
    not_short_extended = (
        x["EMA20 Distance %"].fillna(-999) >= -8.0
    ) & (x["RSI14"].fillna(-999) > 25)

    # Quality points: 6 checks. A+ requires all 6.
    x["Trend Check"] = np.where(long_trend | short_trend, "✅", "❌")
    x["Momentum Check"] = np.where(long_momentum | short_momentum, "✅", "❌")
    x["RS Check"] = np.where(long_rs | short_rs, "✅", "❌")
    x["Volume Check"] = np.where(volume_confirm, "✅", "❌")
    x["RSI Check"] = np.where(long_rsi | short_rsi, "✅", "❌")
    x["Extension Check"] = np.where(not_long_extended | not_short_extended, "✅", "❌")

    long_points = (
        long_trend.astype(int)
        + long_momentum.astype(int)
        + long_rs.astype(int)
        + volume_confirm.astype(int)
        + long_rsi.astype(int)
        + not_long_extended.astype(int)
    )
    short_points = (
        short_trend.astype(int)
        + short_momentum.astype(int)
        + short_rs.astype(int)
        + volume_confirm.astype(int)
        + short_rsi.astype(int)
        + not_short_extended.astype(int)
    )

    long_candidate = x["Momentum Score"] >= 15
    short_candidate = x["Momentum Score"] <= -15

    x["Quality Score"] = np.where(long_candidate, long_points, np.where(short_candidate, short_points, 0))

    # Grade hierarchy:
    # A+ = all 6 checks.
    # A  = 5/6 checks.
    # B+ = 4/6 checks.
    # Watch = directional but <4 checks.
    x["Setup"] = "Mixed"

    x.loc[long_candidate & (long_points >= 6), "Setup"] = "A+ Long"
    x.loc[long_candidate & (long_points == 5), "Setup"] = "A Long"
    x.loc[long_candidate & (long_points == 4), "Setup"] = "B+ Long"
    x.loc[long_candidate & (long_points < 4), "Setup"] = "Long Watch"

    x.loc[short_candidate & (short_points >= 6), "Setup"] = "A+ Short"
    x.loc[short_candidate & (short_points == 5), "Setup"] = "A Short"
    x.loc[short_candidate & (short_points == 4), "Setup"] = "B+ Short"
    x.loc[short_candidate & (short_points < 4), "Setup"] = "Short Watch"

    x.loc[x["Momentum Score"].abs() < 15, "Setup"] = "Neutral"

    def quality_note(row):
        direction = "Long" if row["Momentum Score"] >= 15 else "Short" if row["Momentum Score"] <= -15 else "Neutral"
        if direction == "Neutral":
            return "Momentum Score is inside the neutral zone (-15 to +15)."

        failed = []
        if row["Trend Check"] != "✅":
            failed.append("trend")
        if row["Momentum Check"] != "✅":
            failed.append("momentum")
        if row["RS Check"] != "✅":
            failed.append("relative strength")
        if row["Volume Check"] != "✅":
            failed.append("volume")
        if row["RSI Check"] != "✅":
            failed.append("RSI")
        if row["Extension Check"] != "✅":
            failed.append("extension")

        if not failed:
            return "A+ confirmed: all six quality checks passed."
        return f"{int(row['Quality Score'])}/6 quality checks passed; missing: " + ", ".join(failed) + "."

    x["Setup Note"] = x.apply(quality_note, axis=1)

    # Re-evaluate regime alignment after Quality Grade is assigned.
    x["Regime Aligned"] = x.apply(is_regime_aligned, axis=1)

    def reasons(row):
        out = []
        if pd.isna(row.get("Momentum Score")):
            out.append("Incomplete momentum data")
        if pd.isna(row.get("Volume Ratio")) or row["Volume Ratio"] < min_volume:
            out.append(f"Vol<{min_volume:.1f}x")
        if pd.isna(row.get("RSI14")):
            out.append("RSI unavailable")
        elif row["RSI14"] < rsi_low or row["RSI14"] > rsi_high:
            out.append(f"RSI outside {rsi_low}-{rsi_high}")

        if pd.notna(row.get("Momentum Score")):
            if regime["label"] in {"RISK-OFF", "BEARISH"}:
                if row["Momentum Score"] > -abs(min_composite):
                    out.append("Short momentum weak")
            elif row["Momentum Score"] < min_composite:
                out.append("Momentum weak")

        if not row["Regime Aligned"]:
            out.append("Not regime-aligned")
        return "; ".join(dict.fromkeys(out))

    x["Filter Reasons"] = x.apply(reasons, axis=1)
    x["Passes Filters"] = x["Filter Reasons"].eq("")

    trend_bonus = np.zeros(len(x), dtype=float)
    if regime["label"] in {"RISK-ON", "BULLISH"}:
        trend_bonus = np.where(
            (x["Close"] > x["EMA20"]) & (x["EMA20"] > x["EMA50"]), 10.0, 0.0
        )
    elif regime["label"] in {"RISK-OFF", "BEARISH"}:
        trend_bonus = np.where(
            (x["Close"] < x["EMA20"]) & (x["EMA20"] < x["EMA50"]), 10.0, 0.0
        )

    vol_bonus = ((x["Volume Ratio"].fillna(1.0) - 1.0) * 10.0).clip(-5.0, 10.0)
    align_bonus = np.where(x["Regime Aligned"], 15.0, -10.0)

    composite_safe = x["Momentum Score"].fillna(0.0)
    raw_momentum = -composite_safe if regime["label"] in {"RISK-OFF", "BEARISH"} else composite_safe

    rs_bonus = x["RS Composite"].fillna(0.0).clip(-20.0, 20.0) * 0.50

    x["Adjusted Score"] = (
        pd.Series(raw_momentum, index=x.index, dtype="float64")
        + pd.Series(align_bonus, index=x.index, dtype="float64")
        + pd.Series(trend_bonus, index=x.index, dtype="float64")
        + pd.Series(vol_bonus, index=x.index, dtype="float64")
        + pd.Series(rs_bonus, index=x.index, dtype="float64")
    )
    x["Adjusted Score"] = x["Adjusted Score"].replace([np.inf, -np.inf], np.nan).fillna(-9999.0)

    ranks = x["Adjusted Score"].rank(method="min", ascending=False, na_option="bottom")
    x["Rank"] = ranks.fillna(len(x) + 1).round().astype("Int64")
    return x



def compute_search_diagnostic(symbol, company, df, metadata):
    """Decision-oriented swing diagnostic from cached daily price history."""
    if df.empty or len(df) < 80:
        return None

    x = df.copy()
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

    close = float(latest["Close"])
    ema20 = float(latest["EMA20"])
    ema50 = float(latest["EMA50"])
    ema200 = float(latest["EMA200"])
    atr = float(latest["ATR14"]) if pd.notna(latest["ATR14"]) else close * 0.025
    rsi14 = float(latest["RSI14"])
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
    rs_score = 0.0
    rs_1m = rs_3m = rs_6m = np.nan
    spy = download_one("SPY", "1y")
    if not spy.empty and len(spy) >= 127:
        spy_1m = float(spy["Close"].pct_change(20).iloc[-1])
        spy_3m = float(spy["Close"].pct_change(60).iloc[-1])
        spy_6m = float(spy["Close"].pct_change(126).iloc[-1])

        stock_1m = float(latest["1M %"])
        stock_3m = float(latest["3M %"])
        stock_6m = float(latest["6M %"])

        rs_1m = (stock_1m - spy_1m) * 100.0
        rs_3m = (stock_3m - spy_3m) * 100.0
        rs_6m = (stock_6m - spy_6m) * 100.0
        rs_score = rs_1m * 0.20 + rs_3m * 0.35 + rs_6m * 0.45

        if rs_score >= 10:
            rs_text = "Strongly outperforming SPY"
        elif rs_score >= 3:
            rs_text = "Outperforming SPY"
        elif rs_score <= -10:
            rs_text = "Strongly underperforming SPY"
        elif rs_score <= -3:
            rs_text = "Underperforming SPY"
        else:
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

    if long_bias:
        entry_low = max(ema20, close - 0.50 * atr)
        entry_high = close + 0.15 * atr
        stop = min(ema50, entry_low - 1.35 * atr)
        risk = max(entry_low - stop, atr * 0.6)
        target1 = max(high20, entry_high + 1.5 * risk)
        target2 = entry_high + 2.5 * risk
        rr = (target2 - entry_high) / max(entry_high - stop, 0.01)
        bias = "LONG"
    elif short_bias:
        entry_high = min(ema20, close + 0.50 * atr)
        entry_low = close - 0.15 * atr
        stop = max(ema50, entry_high + 1.35 * atr)
        risk = max(stop - entry_high, atr * 0.6)
        target1 = min(low20, entry_low - 1.5 * risk)
        target2 = entry_low - 2.5 * risk
        rr = (entry_low - target2) / max(stop - entry_low, 0.01)
        bias = "SHORT"
    else:
        entry_low = entry_high = stop = target1 = target2 = rr = np.nan
        bias = "WAIT"

    points = 0
    points += 2 if bull_stack else 1 if bull_mid else -2 if bear_stack else -1 if bear_mid else 0
    points += 2 if composite >= 40 else 1 if composite >= 20 else -2 if composite <= -40 else -1 if composite <= -20 else 0
    points += 1 if rs_score >= 1 else -1 if rs_score <= -1 else 0
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
    st.caption("Choose any ticker/company and get a fast decision-oriented readout with the calculation logic shown.")

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
            df = download_one(selected_ticker, "1y")
            metadata = get_company_snapshot(selected_ticker)
            diag = compute_search_diagnostic(selected_ticker, selected_company, df, metadata)

        if diag is None:
            st.error(f"No usable market data returned for {selected_ticker}.")
        else:
            st.markdown(f"### {selected_ticker} — {selected_company}")
            st.markdown(f"## {diag['Verdict']}")

            v1, v2, v3, v4 = st.columns(4)
            v1.metric("Close", fmt_price(diag["Close"]))
            v2.metric("Momentum Score", f"{diag['Momentum Score']:.1f}", diag["Acceleration"])
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
            rs4.metric("RS Composite", f"{diag['RS Composite']:+.1f}")

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

            st.markdown("### Trade Plan")
            if diag["Bias"] == "WAIT":
                st.warning(
                    "No clean entry plan right now. Momentum/trend alignment is mixed, "
                    "so the better action is to wait for confirmation."
                )
            else:
                p1, p2, p3, p4 = st.columns(4)
                p1.metric("Bias", diag["Bias"])
                p2.metric(
                    "Entry Zone",
                    f"{fmt_price(diag['Entry Low'])} – {fmt_price(diag['Entry High'])}"
                )
                p3.metric("Stop", fmt_price(diag["Stop"]))
                p4.metric("R:R", fmt_ratio(diag["RR"]))

                t1, t2 = st.columns(2)
                t1.metric("Target 1", fmt_price(diag["Target 1"]))
                t2.metric("Target 2", fmt_price(diag["Target 2"]))

                if diag["RR"] >= 2:
                    st.success("Risk/reward is attractive on the heuristic plan.")
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
                    ("Trade Plan", "ATR(14), EMA structure and recent 20-day highs/lows"),
                ]
                st.dataframe(pd.DataFrame(rows, columns=["Signal", "Rule"]), hide_index=True, use_container_width=True)

            with st.expander("Fundamental snapshot", expanded=False):
                f1, f2, f3 = st.columns(3)
                mc = diag["Market Cap"]
                f1.metric("Market Cap", "N/A" if pd.isna(mc) else f"${mc/1e9:,.1f}B")
                f2.metric("Trailing P/E", "N/A" if pd.isna(diag["Trailing PE"]) else f"{diag['Trailing PE']:.1f}")
                f3.metric("Forward P/E", "N/A" if pd.isna(diag["Forward PE"]) else f"{diag['Forward PE']:.1f}")

            if not df.empty:
                d = df.tail(120).copy()
                d["EMA20"] = d["Close"].ewm(span=20, adjust=False).mean()
                d["EMA50"] = d["Close"].ewm(span=50, adjust=False).mean()
                d["EMA200"] = d["Close"].ewm(span=200, adjust=False).mean()

                fig = go.Figure()
                fig.add_trace(go.Candlestick(
                    x=d["Date"], open=d["Open"], high=d["High"], low=d["Low"], close=d["Close"], name="Price"
                ))
                fig.add_trace(go.Scatter(x=d["Date"], y=d["EMA20"], name="EMA20"))
                fig.add_trace(go.Scatter(x=d["Date"], y=d["EMA50"], name="EMA50"))
                fig.add_trace(go.Scatter(x=d["Date"], y=d["EMA200"], name="EMA200"))
                fig.update_layout(
                    height=460,
                    xaxis_rangeslider_visible=False,
                    margin=dict(l=5, r=5, t=20, b=5),
                )
                st.plotly_chart(fig, use_container_width=True)

            st.caption(
                "Trade plan levels are heuristic decision-support estimates based on ATR, trend and recent highs/lows. "
                "They are not personalized investment advice or guaranteed execution levels."
            )


# ---------------------------
# Universe scanner
# ---------------------------
with scanner_tab:
    st.subheader("Choose scan universe")

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
        "**v5 Quality Engine:** A+ requires all 6 checks: "
        "Trend + Momentum + Relative Strength + Volume + RSI + Not Extended. "
        "A = 5/6, B+ = 4/6, Watch = fewer than 4."
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

        results, failures = run_market_scan(universe_df, progress, status)
        progress.empty()
        status.empty()

        st.session_state.scan_df = results
        st.session_state.scan_universe_name = universe_name
        st.session_state.scan_timestamp = datetime.now()
        st.session_state.scan_errors = failures

        elapsed = time.time() - started
        st.success(
            f"Scan complete: {len(results):,}/{len(universe_df):,} symbols analyzed "
            f"in {elapsed:.0f}s. Results stay loaded while you use the dashboard."
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
        st.caption("A+ Quality Engine: all 6 checks must pass — Trend, Momentum, Relative Strength, Volume, RSI, and Extension.")
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Stocks analyzed", f"{len(ranked):,}")
        k2.metric("Passing filters", f"{int(ranked['Passes Filters'].sum()):,}")
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
                    "Ticker", "Company", "Sector", "Setup", "Quality Score",
                    "RS Rating", "RS Composite",
                    "Adjusted Score", "Momentum Score", "RSI14",
                    "Volume Ratio", "1D %", "1W %", "1M %",
                ]
                aplus_cols = [c for c in aplus_cols if c in aplus.columns]

                st.dataframe(
                    aplus[aplus_cols],
                    hide_index=True,
                    use_container_width=True,
                    height=min(420, 45 + 36 * len(aplus)),
                    column_config={
                        "RS Rating": st.column_config.NumberColumn(format="%d"),
                        "RS Composite": st.column_config.NumberColumn(format="%+.1f"),
                        "Adjusted Score": st.column_config.NumberColumn(format="%.1f"),
                        "Momentum Score": st.column_config.NumberColumn(format="%.1f"),
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
                    d = detail_df.tail(120).copy()
                    d["EMA20"] = d["Close"].ewm(span=20, adjust=False).mean()
                    d["EMA50"] = d["Close"].ewm(span=50, adjust=False).mean()
                    d["EMA200"] = d["Close"].ewm(span=200, adjust=False).mean()

                    c1, c2, c3, c4, c5 = st.columns(5)
                    c1.metric("Quality Grade", selected_row["Setup"])
                    c2.metric("Quality Score", f"{int(selected_row['Quality Score'])}/6")
                    c3.metric("Momentum Score", f"{selected_row['Momentum Score']:.1f}")
                    c4.metric("RS Rating", f"{int(selected_row['RS Rating'])}" if pd.notna(selected_row.get("RS Rating")) else "N/A")
                    c5.metric("RSI(14)", f"{selected_row['RSI14']:.1f}")

                    st.caption(selected_row.get("Setup Note", ""))
                    st.markdown(
                        f"**Quality Checklist:** "
                        f"Trend {selected_row.get('Trend Check','')}  •  "
                        f"Momentum {selected_row.get('Momentum Check','')}  •  "
                        f"RS {selected_row.get('RS Check','')}  •  "
                        f"Volume {selected_row.get('Volume Check','')}  •  "
                        f"RSI {selected_row.get('RSI Check','')}  •  "
                        f"Extension {selected_row.get('Extension Check','')}"
                    )

                    timeframe = st.segmented_control(
                        "Chart timeframe",
                        ["Daily", "Weekly", "Monthly", "YTD", "All"],
                        default="Daily",
                        key=f"aplus_timeframe_{selected_aplus}",
                    )

                    base_chart = detail_df.copy()
                    base_chart["Date"] = pd.to_datetime(base_chart["Date"])

                    if timeframe == "Daily":
                        chart_df = base_chart.tail(90).copy()
                    elif timeframe == "Weekly":
                        chart_df = (
                            base_chart.set_index("Date")
                            .resample("W-FRI")
                            .agg({
                                "Open": "first",
                                "High": "max",
                                "Low": "min",
                                "Close": "last",
                                "Volume": "sum",
                            })
                            .dropna(subset=["Open", "High", "Low", "Close"])
                            .reset_index()
                            .tail(104)
                        )
                    elif timeframe == "Monthly":
                        chart_df = (
                            base_chart.set_index("Date")
                            .resample("ME")
                            .agg({
                                "Open": "first",
                                "High": "max",
                                "Low": "min",
                                "Close": "last",
                                "Volume": "sum",
                            })
                            .dropna(subset=["Open", "High", "Low", "Close"])
                            .reset_index()
                            .tail(60)
                        )
                    elif timeframe == "YTD":
                        current_year = pd.Timestamp.today().year
                        chart_df = base_chart[base_chart["Date"].dt.year == current_year].copy()
                    else:
                        chart_df = base_chart.copy()

                    chart_df["EMA20"] = chart_df["Close"].ewm(span=20, adjust=False).mean()
                    chart_df["EMA50"] = chart_df["Close"].ewm(span=50, adjust=False).mean()
                    chart_df["EMA200"] = chart_df["Close"].ewm(span=200, adjust=False).mean()

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
            ["Passing Filters", "Regime-Aligned", "All"],
            default="Passing Filters",
        )
        if view_mode == "Passing Filters":
            shown = ranked[ranked["Passes Filters"]].copy()
        elif view_mode == "Regime-Aligned":
            shown = ranked[ranked["Regime Aligned"]].copy()
        else:
            shown = ranked.copy()

        shown = shown.sort_values(["Adjusted Score", "Volume Ratio"], ascending=[False, False])

        top_n = st.slider("Rows to display", 10, 100, 30, 10)
        shown = shown.head(top_n)

        display_cols = [
            "Rank", "Ticker", "Company", "Sector", "Setup", "Quality Score", "Regime Aligned",
            "RS Rating", "RS Composite",
            "Adjusted Score", "Momentum Score", "RSI14", "Volume Ratio",
            "1D %", "1W %", "1M %", "Setup Note", "Filter Reasons",
        ]
        existing_cols = [c for c in display_cols if c in shown.columns]

        st.dataframe(
            shown[existing_cols],
            hide_index=True,
            use_container_width=True,
            height=520,
            column_config={
                "RS Rating": st.column_config.NumberColumn(format="%d"),
                "RS Composite": st.column_config.NumberColumn(format="%+.1f"),
                "Adjusted Score": st.column_config.NumberColumn(format="%.1f"),
                "Momentum Score": st.column_config.NumberColumn(format="%.1f"),
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
