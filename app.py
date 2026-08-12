
import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import plotly.graph_objects as go

st.set_page_config(page_title="Regime-Aware Swing Scanner", layout="wide")

st.title("Regime-Aware Swing Momentum Scanner")
st.caption(
    "Assesses the U.S. market regime first, then ranks swing-trade candidates by "
    "multi-timeframe momentum, trend, RSI, volume, and regime alignment."
)

# =========================================================
# Indicators / helpers
# =========================================================
def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).fillna(100)

def momentum_score(ret, scale):
    if pd.isna(ret):
        return np.nan
    return float(np.clip(ret * scale, -100, 100))

def classify(score):
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

def direction(score):
    if pd.isna(score):
        return "N/A"
    if score > 15:
        return "UP"
    if score < -15:
        return "DOWN"
    return "FLAT"

@st.cache_data(ttl=120)
def load_symbol(symbol, period="1y"):
    df = yf.download(symbol, period=period, interval="1d", auto_adjust=False, progress=False)
    if df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df = df.reset_index()
    df["Date"] = pd.to_datetime(df["Date"])
    return df

def enrich(df):
    if df.empty or len(df) < 60 or "Close" not in df.columns:
        return df
    df = df.copy()
    df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
    df["EMA200"] = df["Close"].ewm(span=200, adjust=False).mean()
    df["RSI14"] = rsi(df["Close"], 14)
    df["Vol20"] = df["Volume"].rolling(20).mean()
    df["Volume Ratio"] = df["Volume"] / df["Vol20"]
    df["1D %"] = df["Close"].pct_change(1)
    df["1W %"] = df["Close"].pct_change(5)
    df["1M %"] = df["Close"].pct_change(20)
    df["3M %"] = df["Close"].pct_change(60)
    df["Daily Score"] = df["1D %"].apply(lambda x: momentum_score(x, 2500))
    df["Weekly Score"] = df["1W %"].apply(lambda x: momentum_score(x, 700))
    df["Monthly Score"] = df["1M %"].apply(lambda x: momentum_score(x, 350))
    df["Composite"] = (
        df["Daily Score"] * 0.40 +
        df["Weekly Score"] * 0.35 +
        df["Monthly Score"] * 0.25
    )
    return df

def setup_label(row):
    bullish_trend = row["Close"] > row["EMA20"] > row["EMA50"]
    bearish_trend = row["Close"] < row["EMA20"] < row["EMA50"]
    strong_momo = row["Composite"] >= 25
    weak_momo = row["Composite"] <= -25
    vol_confirm = row["Volume Ratio"] >= 1.2
    rsi_ok_long = 50 <= row["RSI14"] <= 75
    rsi_ok_short = 25 <= row["RSI14"] <= 50

    if bullish_trend and strong_momo and vol_confirm and rsi_ok_long:
        return "A+ Long"
    if bullish_trend and strong_momo:
        return "Long Watch"
    if bearish_trend and weak_momo and vol_confirm and rsi_ok_short:
        return "A+ Short"
    if bearish_trend and weak_momo:
        return "Short Watch"
    if abs(row["Composite"]) < 15:
        return "Neutral"
    return "Mixed"

def analyze_symbol(symbol, period="1y"):
    df = enrich(load_symbol(symbol, period))
    if df.empty or len(df) < 60:
        return None, df

    latest = df.iloc[-1]
    prev20_high = df["High"].iloc[-21:-1].max() if len(df) >= 21 else np.nan
    prev20_low = df["Low"].iloc[-21:-1].min() if len(df) >= 21 else np.nan

    rec = {
        "Ticker": symbol,
        "Close": float(latest["Close"]),
        "1D %": float(latest["1D %"]) if pd.notna(latest["1D %"]) else np.nan,
        "1W %": float(latest["1W %"]) if pd.notna(latest["1W %"]) else np.nan,
        "1M %": float(latest["1M %"]) if pd.notna(latest["1M %"]) else np.nan,
        "3M %": float(latest["3M %"]) if pd.notna(latest["3M %"]) else np.nan,
        "Daily": float(latest["Daily Score"]),
        "Weekly": float(latest["Weekly Score"]),
        "Monthly": float(latest["Monthly Score"]),
        "Composite": float(latest["Composite"]),
        "RSI14": float(latest["RSI14"]),
        "EMA20": float(latest["EMA20"]),
        "EMA50": float(latest["EMA50"]),
        "EMA200": float(latest["EMA200"]),
        "Volume Ratio": float(latest["Volume Ratio"]) if pd.notna(latest["Volume Ratio"]) else np.nan,
        "20D High Dist": float(latest["Close"]/prev20_high - 1) if pd.notna(prev20_high) else np.nan,
        "20D Low Dist": float(latest["Close"]/prev20_low - 1) if pd.notna(prev20_low) else np.nan,
        "Regime": classify(latest["Composite"]),
    }
    rec["Setup"] = setup_label(rec)
    return rec, df

# =========================================================
# Market regime
# =========================================================
def assess_market_regime():
    proxies = {}
    for symbol in ["SPY", "QQQ", "IWM", "^VIX"]:
        df = enrich(load_symbol(symbol, "1y"))
        if df.empty or len(df) < 60:
            proxies[symbol] = None
        else:
            proxies[symbol] = df

    score = 0
    details = []

    for symbol, weight in [("SPY", 2), ("QQQ", 2), ("IWM", 1)]:
        df = proxies.get(symbol)
        if df is None:
            continue
        row = df.iloc[-1]
        bullish = row["Close"] > row["EMA20"] > row["EMA50"]
        bearish = row["Close"] < row["EMA20"] < row["EMA50"]
        above_200 = row["Close"] > row["EMA200"]

        if bullish:
            score += weight
            details.append((symbol, "Bullish trend", +weight))
        elif bearish:
            score -= weight
            details.append((symbol, "Bearish trend", -weight))
        else:
            details.append((symbol, "Mixed trend", 0))

        if above_200:
            score += 0.5
        else:
            score -= 0.5

    vix_df = proxies.get("^VIX")
    vix_level = np.nan
    if vix_df is not None:
        vix_level = float(vix_df.iloc[-1]["Close"])
        if vix_level < 18:
            score += 1
            details.append(("VIX", f"Low volatility ({vix_level:.1f})", +1))
        elif vix_level > 25:
            score -= 1.5
            details.append(("VIX", f"High volatility ({vix_level:.1f})", -1.5))
        else:
            details.append(("VIX", f"Moderate volatility ({vix_level:.1f})", 0))

    if score >= 5:
        regime = "RISK-ON"
        bias = "Favor long momentum setups"
    elif score >= 2:
        regime = "BULLISH"
        bias = "Prefer longs; be selective on breakouts"
    elif score > -2:
        regime = "NEUTRAL"
        bias = "Reduce aggression; require stronger confirmation"
    elif score > -5:
        regime = "BEARISH"
        bias = "Favor defensive positioning / selective shorts"
    else:
        regime = "RISK-OFF"
        bias = "Avoid weak longs; favor cash or high-quality shorts"

    return {
        "score": score,
        "regime": regime,
        "bias": bias,
        "vix": vix_level,
        "details": details,
        "proxies": proxies,
    }


def regime_aligned_for(row, market_regime):
    if market_regime in ["RISK-ON", "BULLISH"]:
        return row["Setup"] in ["A+ Long", "Long Watch"]
    if market_regime in ["RISK-OFF", "BEARISH"]:
        return row["Setup"] in ["A+ Short", "Short Watch"]
    return row["Setup"] not in ["Neutral", "Mixed"]

def exclusion_reasons(row, market_regime, min_composite, min_volume_ratio, rsi_min, rsi_max):
    reasons = []
    if pd.isna(row["Volume Ratio"]) or row["Volume Ratio"] < min_volume_ratio:
        reasons.append(f"Volume ratio below {min_volume_ratio:.1f}x")
    if row["RSI14"] < rsi_min or row["RSI14"] > rsi_max:
        reasons.append(f"RSI outside {rsi_min}-{rsi_max}")
    if market_regime in ["RISK-OFF", "BEARISH"]:
        if row["Composite"] > -abs(min_composite):
            reasons.append("Short momentum below threshold")
    else:
        if row["Composite"] < min_composite:
            reasons.append("Composite below threshold")
    if not regime_aligned_for(row, market_regime):
        reasons.append("Not regime-aligned")
    return reasons

def render_price_chart(df, symbol, height=460):
    chart_df = df.tail(120)
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=chart_df["Date"], open=chart_df["Open"], high=chart_df["High"],
        low=chart_df["Low"], close=chart_df["Close"], name="Price"
    ))
    fig.add_trace(go.Scatter(x=chart_df["Date"], y=chart_df["EMA20"], name="EMA20"))
    fig.add_trace(go.Scatter(x=chart_df["Date"], y=chart_df["EMA50"], name="EMA50"))
    fig.add_trace(go.Scatter(x=chart_df["Date"], y=chart_df["EMA200"], name="EMA200"))
    fig.update_layout(
        title=f"{symbol} Price + Trend",
        height=height,
        xaxis_rangeslider_visible=False,
        margin=dict(l=10, r=10, t=45, b=10)
    )
    return fig

# =========================================================
# Sidebar
# =========================================================
with st.sidebar:
    st.header("Scanner Settings")
    watchlist_text = st.text_area(
        "Watchlist",
        value="AAPL, MSFT, NVDA, AMZN, META, GOOGL, TSLA, AMD, AVGO, NFLX, JPM, XOM",
        height=140,
        help="Comma-separated tickers."
    )
    period = st.selectbox("History window", ["6mo", "1y", "2y"], index=1)

    st.subheader("Filters")
    min_composite = st.slider("Minimum composite", -100, 100, 10, 5)
    min_volume_ratio = st.slider("Minimum volume ratio", 0.0, 3.0, 0.8, 0.1)
    rsi_min, rsi_max = st.slider("RSI range", 0, 100, (30, 80), 1)
    regime_aligned_only = st.checkbox("Regime-aligned only", value=True)
    view_mode = st.radio("Scanner view", ["Passing Filters", "Regime-Aligned", "All"], index=0)

    st.subheader("Display")
    show_top = st.slider("Rows to show", 5, 50, 20, 5)
    auto_refresh = st.checkbox("Auto-refresh every 2 minutes", value=False)

if auto_refresh:
    st.markdown("<meta http-equiv='refresh' content='120'>", unsafe_allow_html=True)

symbols = []
for x in watchlist_text.replace("\n", ",").split(","):
    s = x.strip().upper()
    if s and s not in symbols:
        symbols.append(s)

if not symbols:
    st.warning("Enter at least one ticker.")
    st.stop()

# =========================================================
# Regime first
# =========================================================
regime = assess_market_regime()

st.subheader("1. U.S. Market Regime")
r1, r2, r3, r4 = st.columns(4)
r1.metric("Regime", regime["regime"])
r2.metric("Regime Score", f"{regime['score']:.1f}")
r3.metric("VIX", f"{regime['vix']:.1f}" if pd.notna(regime["vix"]) else "N/A")
r4.metric("Trading Bias", regime["bias"])

detail_df = pd.DataFrame(regime["details"], columns=["Proxy", "Signal", "Contribution"])
st.dataframe(detail_df, hide_index=True, use_container_width=True)

# =========================================================
# Direct ticker search — always display analysis
# =========================================================
st.subheader("Search Any Ticker")
search_col, button_col = st.columns([4, 1])
with search_col:
    direct_ticker = st.text_input(
        "Ticker symbol",
        placeholder="e.g. CRDO, CSCO, NVDA",
        label_visibility="collapsed",
    ).strip().upper()
with button_col:
    analyze_clicked = st.button("Analyze", type="primary", use_container_width=True)

if direct_ticker:
    try:
        direct_rec, direct_hist = analyze_symbol(direct_ticker, period)
        if direct_rec is None:
            st.error(f"No usable market data returned for {direct_ticker}. Check the symbol or try again later.")
        else:
            aligned = regime_aligned_for(direct_rec, regime["regime"])
            direct_rec["Regime Aligned"] = aligned
            reasons = exclusion_reasons(
                direct_rec, regime["regime"], min_composite,
                min_volume_ratio, rsi_min, rsi_max
            )

            st.markdown(f"### {direct_ticker} — Direct Analysis")
            q1, q2, q3, q4, q5 = st.columns(5)
            q1.metric("Close", f"${direct_rec['Close']:,.2f}")
            q2.metric("Composite", f"{direct_rec['Composite']:.1f}", direct_rec["Regime"])
            q3.metric("RSI(14)", f"{direct_rec['RSI14']:.1f}")
            q4.metric(
                "Volume Ratio",
                f"{direct_rec['Volume Ratio']:.2f}x"
                if pd.notna(direct_rec["Volume Ratio"]) else "N/A"
            )
            q5.metric("Setup", direct_rec["Setup"])

            if reasons:
                st.warning(
                    "Ticker analyzed successfully. It would be excluded from the current "
                    "scanner filters because: " + "; ".join(reasons)
                )
            else:
                st.success("This ticker passes the current scanner filters and is regime-aligned.")

            st.plotly_chart(
                render_price_chart(direct_hist, direct_ticker),
                use_container_width=True
            )
    except Exception as ex:
        st.error(f"Could not analyze {direct_ticker}: {ex}")

st.divider()

# =========================================================
# Scan watchlist
# =========================================================
st.subheader("2. Swing Candidate Scan")

records = []
history_map = {}
errors = []

progress = st.progress(0, text="Scanning watchlist...")
for i, symbol in enumerate(symbols):
    try:
        rec, hist = analyze_symbol(symbol, period)
        if rec is not None:
            records.append(rec)
            history_map[symbol] = hist
        else:
            errors.append(symbol)
    except Exception:
        errors.append(symbol)
    progress.progress((i + 1) / len(symbols), text=f"Scanning {symbol}...")
progress.empty()

if not records:
    st.error("No valid ticker data was returned.")
    st.stop()

scan = pd.DataFrame(records)

# Watchlist breadth
scan["Above EMA20"] = scan["Close"] > scan["EMA20"]
scan["Above EMA50"] = scan["Close"] > scan["EMA50"]
scan["Above EMA200"] = scan["Close"] > scan["EMA200"]

breadth_20 = scan["Above EMA20"].mean()
breadth_50 = scan["Above EMA50"].mean()
breadth_200 = scan["Above EMA200"].mean()

b1, b2, b3 = st.columns(3)
b1.metric("Watchlist > EMA20", f"{breadth_20:.0%}")
b2.metric("Watchlist > EMA50", f"{breadth_50:.0%}")
b3.metric("Watchlist > EMA200", f"{breadth_200:.0%}")

# Regime alignment
def is_regime_aligned(row):
    return regime_aligned_for(row, regime["regime"])

scan["Regime Aligned"] = scan.apply(is_regime_aligned, axis=1)

# Regime-adjusted rank score
def adjusted_score(row):
    base = row["Composite"]
    alignment_bonus = 15 if row["Regime Aligned"] else -10

    trend_bonus = 0
    if row["Close"] > row["EMA20"] > row["EMA50"] and regime["regime"] in ["RISK-ON", "BULLISH"]:
        trend_bonus += 10
    elif row["Close"] < row["EMA20"] < row["EMA50"] and regime["regime"] in ["RISK-OFF", "BEARISH"]:
        trend_bonus += 10

    vol_bonus = min(max((row["Volume Ratio"] - 1) * 10, -5), 10) if pd.notna(row["Volume Ratio"]) else 0
    return base + alignment_bonus + trend_bonus + vol_bonus

scan["Adjusted Score"] = scan.apply(adjusted_score, axis=1)
scan["Rank"] = scan["Adjusted Score"].rank(method="min", ascending=False).astype(int)
scan["Filter Reasons"] = scan.apply(
    lambda r: "; ".join(exclusion_reasons(
        r, regime["regime"], min_composite, min_volume_ratio, rsi_min, rsi_max
    )),
    axis=1
)
scan["Passes Filters"] = scan.apply(
    lambda r: (
        (pd.notna(r["Volume Ratio"]) and r["Volume Ratio"] >= min_volume_ratio)
        and (r["RSI14"] >= rsi_min and r["RSI14"] <= rsi_max)
        and (
            r["Composite"] <= -abs(min_composite)
            if regime["regime"] in ["RISK-OFF", "BEARISH"]
            else r["Composite"] >= min_composite
        )
    ),
    axis=1
)

passing = scan[scan["Passes Filters"]].copy()

if view_mode == "All":
    filtered = scan.copy()
elif view_mode == "Regime-Aligned":
    filtered = scan[scan["Regime Aligned"]].copy()
else:
    filtered = passing[passing["Regime Aligned"]].copy()

filtered = filtered.sort_values(
    ["Adjusted Score", "Volume Ratio"],
    ascending=[False, False]
).head(show_top)

k1, k2, k3, k4 = st.columns(4)
k1.metric("Stocks Scanned", len(scan))
k2.metric("Passing Filters", len(passing))
k3.metric("Regime-Aligned", int(scan["Regime Aligned"].sum()))
k4.metric("A+ Setups", int(scan["Setup"].isin(["A+ Long", "A+ Short"]).sum()))

display_cols = [
    "Rank", "Ticker", "Setup", "Regime Aligned", "Adjusted Score", "Composite",
    "Daily", "Weekly", "Monthly", "RSI14", "Volume Ratio",
    "1D %", "1W %", "1M %", "20D High Dist", "20D Low Dist", "Filter Reasons"
]

if filtered.empty:
    st.info("No stocks match this view. Switch Scanner view to All to see every ticker and its exclusion reason.")
else:
    st.dataframe(
        filtered[display_cols],
        hide_index=True,
        use_container_width=True,
        column_config={
            "Adjusted Score": st.column_config.NumberColumn(format="%.1f"),
            "Composite": st.column_config.NumberColumn(format="%.1f"),
            "Daily": st.column_config.NumberColumn(format="%.1f"),
            "Weekly": st.column_config.NumberColumn(format="%.1f"),
            "Monthly": st.column_config.NumberColumn(format="%.1f"),
            "RSI14": st.column_config.NumberColumn(format="%.1f"),
            "Volume Ratio": st.column_config.NumberColumn(format="%.2fx"),
            "1D %": st.column_config.NumberColumn(format="%.2%"),
            "1W %": st.column_config.NumberColumn(format="%.2%"),
            "1M %": st.column_config.NumberColumn(format="%.2%"),
            "20D High Dist": st.column_config.NumberColumn(format="%.2%"),
            "20D Low Dist": st.column_config.NumberColumn(format="%.2%"),
        }
    )

    st.download_button(
        "Download regime-aware scan",
        filtered[display_cols].to_csv(index=False).encode("utf-8"),
        file_name="regime_aware_swing_scan.csv",
        mime="text/csv"
    )

st.caption("Tip: choose Scanner view = All to see every watchlist symbol and the exact reason it fails the current filters.")

# =========================================================
# Detail view
# =========================================================
st.divider()
st.subheader("3. Candidate Detail")

default_ticker = (
    filtered["Ticker"].iloc[0]
    if not filtered.empty
    else scan.sort_values("Adjusted Score", ascending=False)["Ticker"].iloc[0]
)

ticker_list = scan["Ticker"].tolist()
selected = st.selectbox("Select ticker", ticker_list, index=ticker_list.index(default_ticker))

row = scan.loc[scan["Ticker"] == selected].iloc[0]
hist = history_map[selected].copy()

if row["Filter Reasons"]:
    st.warning("Scanner diagnostics: " + row["Filter Reasons"])
else:
    st.success("This ticker passes the current numeric filters.")

d1, d2, d3, d4, d5, d6 = st.columns(6)
d1.metric("Adjusted Score", f"{row['Adjusted Score']:.1f}")
d2.metric("Composite", f"{row['Composite']:.1f}", row["Regime"])
d3.metric("RSI(14)", f"{row['RSI14']:.1f}")
d4.metric("Volume Ratio", f"{row['Volume Ratio']:.2f}x")
d5.metric("1-Month", f"{row['1M %']:.2%}")
d6.metric("Setup", row["Setup"])

left, right = st.columns([2, 1])

with left:
    chart_df = hist.tail(120)

    price_fig = go.Figure()
    price_fig.add_trace(go.Candlestick(
        x=chart_df["Date"],
        open=chart_df["Open"],
        high=chart_df["High"],
        low=chart_df["Low"],
        close=chart_df["Close"],
        name="Price"
    ))
    price_fig.add_trace(go.Scatter(x=chart_df["Date"], y=chart_df["EMA20"], name="EMA20"))
    price_fig.add_trace(go.Scatter(x=chart_df["Date"], y=chart_df["EMA50"], name="EMA50"))
    price_fig.add_trace(go.Scatter(x=chart_df["Date"], y=chart_df["EMA200"], name="EMA200"))
    price_fig.update_layout(
        title=f"{selected} Price + Trend",
        height=520,
        xaxis_rangeslider_visible=False
    )
    st.plotly_chart(price_fig, use_container_width=True)

    mom_fig = go.Figure()
    mom_fig.add_trace(go.Scatter(x=chart_df["Date"], y=chart_df["Daily Score"], name="Daily"))
    mom_fig.add_trace(go.Scatter(x=chart_df["Date"], y=chart_df["Weekly Score"], name="Weekly"))
    mom_fig.add_trace(go.Scatter(x=chart_df["Date"], y=chart_df["Monthly Score"], name="Monthly"))
    mom_fig.add_trace(go.Scatter(x=chart_df["Date"], y=chart_df["Composite"], name="Composite"))
    for level in [50, 15, -15, -50]:
        mom_fig.add_hline(y=level, line_dash="dot")
    mom_fig.update_layout(title="Momentum Structure", height=380)
    st.plotly_chart(mom_fig, use_container_width=True)

with right:
    st.markdown("### Regime Fit")
    st.write(f"**Market regime:** {regime['regime']}")
    st.write(f"**Preferred bias:** {regime['bias']}")
    st.write(f"**Candidate aligned:** {'✅ Yes' if row['Regime Aligned'] else '❌ No'}")

    st.markdown("### Setup Checklist")
    long_checklist = {
        "Price > EMA20": row["Close"] > row["EMA20"],
        "EMA20 > EMA50": row["EMA20"] > row["EMA50"],
        "EMA50 > EMA200": row["EMA50"] > row["EMA200"],
        "Composite > 25": row["Composite"] > 25,
        "RSI 50–75": 50 <= row["RSI14"] <= 75,
        "Volume ≥ 1.2x": row["Volume Ratio"] >= 1.2,
        "Near 20D high": row["20D High Dist"] >= -0.03,
    }

    short_checklist = {
        "Price < EMA20": row["Close"] < row["EMA20"],
        "EMA20 < EMA50": row["EMA20"] < row["EMA50"],
        "EMA50 < EMA200": row["EMA50"] < row["EMA200"],
        "Composite < -25": row["Composite"] < -25,
        "RSI 25–50": 25 <= row["RSI14"] <= 50,
        "Volume ≥ 1.2x": row["Volume Ratio"] >= 1.2,
        "Near 20D low": row["20D Low Dist"] <= 0.03,
    }

    checklist = short_checklist if regime["regime"] in ["RISK-OFF", "BEARISH"] else long_checklist

    for item, passed in checklist.items():
        st.write(("✅" if passed else "➖") + " " + item)

    st.metric("Checklist Score", f"{sum(checklist.values())}/{len(checklist)}")

    st.markdown("### Swing Interpretation")
    if row["Regime Aligned"] and row["Setup"] in ["A+ Long", "A+ Short"]:
        st.success("High-conviction regime-aligned momentum setup.")
    elif row["Regime Aligned"]:
        st.info("Regime-aligned, but confirmation is incomplete.")
    else:
        st.warning("Setup conflicts with the current market regime.")

if errors:
    st.caption("No usable data returned for: " + ", ".join(errors))

st.divider()
st.caption(
    "For research and decision support only. Market regime and momentum scores are heuristic, not predictions. "
    "Validate earnings dates, liquidity, risk/reward, and stop placement before trading."
)
