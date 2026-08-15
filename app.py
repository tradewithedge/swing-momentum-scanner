# Quality Engine v7.4.4 — Phase 2C CONTROLLED FALLBACK TEST v3 (Independent Nasdaq + SEC Tertiary Fallback)

import base64
import hashlib
import html as html_lib
import io
import json
import os
import re
import pickle
import sqlite3
import time
import zlib
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
import yfinance as yf

try:
    import exchange_calendars as xcals
except Exception:
    xcals = None

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
SCAN_RESULT_REUSE_TTL = 15 * 60
UNIVERSE_CACHE_TTL = 6 * 60 * 60
DIRECTORY_CACHE_TTL = 24 * 60 * 60
ENGINE_VERSION = "v7.4.4-P2C-CONTROLLED-FALLBACK-TEST-V3"
MIN_ACTIONABLE_CANDIDATE_GRADES = {"A+", "A", "B+"}
NEAR_EXTENSION_CAUTION_PCT = 6.0
NEAR_STOP_CAUTION_PCT = 0.07
EARNINGS_HARD_BLOCK_DAYS = 3
EARNINGS_CAUTION_DAYS = 14
RECENT_EARNINGS_GRACE_DAYS = 5
COMPANY_SNAPSHOT_TTL = 30 * 60
NASDAQ_EARNINGS_CACHE_TTL = 6 * 60 * 60
SEC_REFERENCE_CACHE_TTL = 24 * 60 * 60
METADATA_HTTP_TIMEOUT = 12

# Phase 2A market-session / targeted-recovery controls.
NYSE_CALENDAR_NAME = "XNYS"
MARKET_DATA_PUBLICATION_BUFFER_MINUTES = 120
SCAN_TARGETED_RETRY_MAX_FRACTION = 0.25
SCAN_TARGETED_RETRY_MAX_SYMBOLS = 30
SCAN_INDIVIDUAL_REPAIR_MAX_PER_BATCH = 6
SCAN_INDIVIDUAL_REPAIR_MAX_TOTAL = 24

# Phase 2B recovery snapshot / diagnostics controls.
# Layer 1: local SQLite is fast but best-effort on Streamlit Community Cloud.
# Layer 2 (Phase 2B.2): an optional GitHub Contents durable store survives container
# replacement/reboot. Credentials are read only from Streamlit secrets.
STATE_DB_DIR = Path(os.getenv("SWING_SCANNER_STATE_DIR", ".scanner_state"))
STATE_DB_PATH = STATE_DB_DIR / "phase2b_scanner_state.sqlite3"
PERSIST_MIN_USABLE_COVERAGE = 0.95
PERSISTENT_RUN_LOG_LIMIT = 50
DURABLE_SNAPSHOT_SCHEMA = 1
DURABLE_DEFAULT_BRANCH = "scanner-state"
DURABLE_DEFAULT_SOURCE_BRANCH = "main"
DURABLE_DEFAULT_PREFIX = "scanner_snapshots"
DURABLE_REQUEST_TIMEOUT = 15

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"
NASDAQ_EARNINGS_API = "https://api.nasdaq.com/api/calendar/earnings"
NASDAQ_QUOTE_SUMMARY_API = "https://api.nasdaq.com/api/quote/{symbol}/summary"
YAHOO_QUOTE_API = "https://query1.finance.yahoo.com/v7/finance/quote"
YAHOO_QUOTE_SUMMARY_API = "https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
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
if "scan_ranked_df" not in st.session_state:
    st.session_state.scan_ranked_df = pd.DataFrame()
if "scan_ranked_regime_label" not in st.session_state:
    st.session_state.scan_ranked_regime_label = ""
if "scan_ranked_engine_version" not in st.session_state:
    st.session_state.scan_ranked_engine_version = ""
if "scan_universe_signature" not in st.session_state:
    st.session_state.scan_universe_signature = ""
if "scan_signal_session" not in st.session_state:
    st.session_state.scan_signal_session = None
if "scan_data_engine_version" not in st.session_state:
    st.session_state.scan_data_engine_version = ""
if "scan_diagnostics" not in st.session_state:
    st.session_state.scan_diagnostics = []
if "persistence_restore_checked" not in st.session_state:
    st.session_state.persistence_restore_checked = {}
if "persistent_restore_notice" not in st.session_state:
    st.session_state.persistent_restore_notice = ""
if "last_scan_execution_seconds" not in st.session_state:
    st.session_state.last_scan_execution_seconds = np.nan
if "persistence_last_message" not in st.session_state:
    st.session_state.persistence_last_message = ""
if "durable_persistence_last_message" not in st.session_state:
    st.session_state.durable_persistence_last_message = ""
if "persistence_restore_source" not in st.session_state:
    st.session_state.persistence_restore_source = ""

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

def _json_default(value):
    if isinstance(value, (pd.Timestamp, datetime)):
        return pd.Timestamp(value).isoformat()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    return str(value)


def durable_store_config():
    """Read optional GitHub durable-store configuration from Streamlit secrets.

    Expected Community Cloud secret block:

    [durable_store]
    provider = "github"
    repository = "OWNER/REPO"
    branch = "scanner-state"
    source_branch = "main"
    path_prefix = "scanner_snapshots"
    token = "github_pat_..."
    allow_watchlist = false

    The token is never written to logs, snapshots, or the UI.
    """
    try:
        raw = st.secrets.get("durable_store", {})
        cfg = dict(raw) if raw is not None else {}
    except Exception:
        cfg = {}

    provider = str(cfg.get("provider", "github")).strip().lower()
    repository = str(cfg.get("repository", "")).strip()
    token = str(cfg.get("token", "")).strip()
    branch = str(cfg.get("branch", DURABLE_DEFAULT_BRANCH)).strip() or DURABLE_DEFAULT_BRANCH
    source_branch = str(cfg.get("source_branch", DURABLE_DEFAULT_SOURCE_BRANCH)).strip() or DURABLE_DEFAULT_SOURCE_BRANCH
    path_prefix = str(cfg.get("path_prefix", DURABLE_DEFAULT_PREFIX)).strip().strip("/") or DURABLE_DEFAULT_PREFIX
    allow_watchlist = bool(cfg.get("allow_watchlist", False))

    return {
        "configured": provider == "github" and bool(repository) and bool(token),
        "provider": provider,
        "repository": repository,
        "branch": branch,
        "source_branch": source_branch,
        "path_prefix": path_prefix,
        "token": token,
        "allow_watchlist": allow_watchlist,
    }


def durable_store_status():
    cfg = durable_store_config()
    if not cfg["configured"]:
        return False, (
            "Durable recovery is not configured. Add a [durable_store] block in Streamlit Secrets "
            "to enable reboot-safe GitHub snapshot recovery."
        )
    watchlist_note = "" if cfg["allow_watchlist"] else " Custom watchlist snapshots are disabled by default."
    return True, (
        f"GitHub durable recovery configured: {cfg['repository']}@{cfg['branch']} / {cfg['path_prefix']}."
        + watchlist_note
    )


def _durable_universe_allowed(universe_name):
    cfg = durable_store_config()
    if universe_name == "My Watchlist" and not cfg.get("allow_watchlist", False):
        return False, (
            "Durable save skipped for My Watchlist because allow_watchlist=false. "
            "This prevents a personal watchlist from being written to a repository unintentionally."
        )
    return True, ""


def _github_headers(token):
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "SwingMomentumScanner/Phase2B2",
    }


def _github_repo_parts(repository):
    parts = str(repository).strip().split("/", 1)
    if len(parts) != 2 or not all(parts):
        raise ValueError("durable_store.repository must be in OWNER/REPO format")
    return parts[0], parts[1]


def _github_api_base(cfg):
    owner, repo = _github_repo_parts(cfg["repository"])
    return f"https://api.github.com/repos/{owner}/{repo}"


def _github_ensure_state_branch(cfg):
    """Ensure the configured state branch exists, creating it from source_branch."""
    base = _github_api_base(cfg)
    headers = _github_headers(cfg["token"])
    branch = cfg["branch"]
    check = requests.get(
        f"{base}/git/ref/heads/{requests.utils.quote(branch, safe='')}",
        headers=headers,
        timeout=DURABLE_REQUEST_TIMEOUT,
    )
    if check.status_code == 200:
        return True, ""
    if check.status_code != 404:
        return False, f"GitHub branch check failed ({check.status_code})."

    source = cfg["source_branch"]
    source_resp = requests.get(
        f"{base}/git/ref/heads/{requests.utils.quote(source, safe='')}",
        headers=headers,
        timeout=DURABLE_REQUEST_TIMEOUT,
    )
    if source_resp.status_code != 200:
        return False, f"GitHub source branch '{source}' could not be read ({source_resp.status_code})."
    source_sha = ((source_resp.json().get("object") or {}).get("sha"))
    if not source_sha:
        return False, "GitHub source branch SHA was unavailable."

    create = requests.post(
        f"{base}/git/refs",
        headers=headers,
        json={"ref": f"refs/heads/{branch}", "sha": source_sha},
        timeout=DURABLE_REQUEST_TIMEOUT,
    )
    if create.status_code in {200, 201}:
        return True, ""
    # Another process may have created it between GET and POST.
    if create.status_code == 422:
        recheck = requests.get(
            f"{base}/git/ref/heads/{requests.utils.quote(branch, safe='')}",
            headers=headers,
            timeout=DURABLE_REQUEST_TIMEOUT,
        )
        if recheck.status_code == 200:
            return True, ""
    return False, f"GitHub state branch creation failed ({create.status_code})."


def _durable_snapshot_path(universe_name, cfg=None):
    cfg = cfg or durable_store_config()
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(universe_name)).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    digest = hashlib.sha1(str(universe_name).encode("utf-8")).hexdigest()[:8]
    return f"{cfg['path_prefix']}/{slug}-{digest}.json"


def _encode_durable_dataframe(df):
    # Network-restored data is JSON, not pickle, so remote tampering cannot trigger
    # arbitrary Python object deserialization.
    table_json = df.to_json(orient="table", date_format="iso", double_precision=12)
    compressed = zlib.compress(table_json.encode("utf-8"), level=9)
    return base64.b64encode(compressed).decode("ascii"), hashlib.sha256(compressed).hexdigest()


def _decode_durable_dataframe(data_blob, expected_sha256):
    compressed = base64.b64decode(str(data_blob).encode("ascii"), validate=True)
    actual = hashlib.sha256(compressed).hexdigest()
    if expected_sha256 and actual != expected_sha256:
        raise ValueError("Durable snapshot checksum mismatch")
    table_json = zlib.decompress(compressed).decode("utf-8")
    return pd.read_json(io.StringIO(table_json), orient="table")


def _build_durable_snapshot_envelope(universe_name, results, timestamp, failures,
                                     universe_signature, signal_session, engine_version,
                                     health, diagnostics):
    data_blob, data_sha = _encode_durable_dataframe(results)
    return {
        "schema": DURABLE_SNAPSHOT_SCHEMA,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "universe_name": universe_name,
        "scan_timestamp": pd.Timestamp(timestamp).isoformat(),
        "signal_session": pd.Timestamp(signal_session).isoformat() if signal_session is not None and not pd.isna(signal_session) else None,
        "universe_signature": universe_signature,
        "engine_version": engine_version,
        "provider_health": health,
        "failures": list(failures or []),
        "diagnostics": list(diagnostics or []),
        "row_count": int(len(results)),
        "data_encoding": "pandas-table-json+zlib+base64",
        "data_sha256": data_sha,
        "data_blob": data_blob,
    }


def _github_read_snapshot_file(cfg, universe_name):
    branch_ok, branch_message = _github_ensure_state_branch(cfg)
    if not branch_ok:
        return None, None, branch_message
    base = _github_api_base(cfg)
    path = _durable_snapshot_path(universe_name, cfg)
    encoded_path = requests.utils.quote(path, safe="/")
    resp = requests.get(
        f"{base}/contents/{encoded_path}",
        headers=_github_headers(cfg["token"]),
        params={"ref": cfg["branch"]},
        timeout=DURABLE_REQUEST_TIMEOUT,
    )
    if resp.status_code == 404:
        return None, None, "No durable snapshot exists for this universe yet."
    if resp.status_code != 200:
        return None, None, f"GitHub durable read failed ({resp.status_code})."
    payload = resp.json()
    content = str(payload.get("content", "")).replace("\n", "")
    if not content:
        return None, payload.get("sha"), "GitHub durable snapshot content was empty."
    try:
        text = base64.b64decode(content).decode("utf-8")
        return json.loads(text), payload.get("sha"), ""
    except Exception as exc:
        return None, payload.get("sha"), f"GitHub durable snapshot decode failed: {type(exc).__name__}."


def save_durable_snapshot(universe_name, results, timestamp, failures, universe_signature,
                          signal_session, engine_version, health, diagnostics):
    """Save a reboot-safe snapshot to a GitHub state branch when configured."""
    cfg = durable_store_config()
    if not cfg["configured"]:
        return False, "Durable recovery not configured; local SQLite snapshot only."
    allowed, reason = _durable_universe_allowed(universe_name)
    if not allowed:
        return False, reason
    if not persistable_last_good(results, health):
        return False, "Scan did not meet the durable last-good quality threshold."

    try:
        branch_ok, branch_message = _github_ensure_state_branch(cfg)
        if not branch_ok:
            return False, branch_message

        envelope = _build_durable_snapshot_envelope(
            universe_name, results, timestamp, failures, universe_signature,
            signal_session, engine_version, health, diagnostics,
        )
        existing, existing_sha, read_message = _github_read_snapshot_file(cfg, universe_name)
        if existing is not None and existing.get("data_sha256") == envelope.get("data_sha256"):
            # Avoid a no-op Git commit for repeated same-session scans.
            return True, "Durable GitHub snapshot already matches current scan; no new commit was needed."

        text = json.dumps(envelope, default=_json_default, separators=(",", ":"))
        body = {
            "message": f"Update scanner recovery snapshot: {universe_name}",
            "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
            "branch": cfg["branch"],
        }
        if existing_sha:
            body["sha"] = existing_sha

        base = _github_api_base(cfg)
        path = _durable_snapshot_path(universe_name, cfg)
        encoded_path = requests.utils.quote(path, safe="/")
        resp = requests.put(
            f"{base}/contents/{encoded_path}",
            headers=_github_headers(cfg["token"]),
            json=body,
            timeout=DURABLE_REQUEST_TIMEOUT,
        )
        if resp.status_code in {200, 201}:
            return True, f"Saved {len(results):,}-row durable GitHub recovery snapshot."
        if resp.status_code == 409:
            # Optimistic-concurrency retry: fetch the newest blob SHA and try once.
            latest, latest_sha, _ = _github_read_snapshot_file(cfg, universe_name)
            if latest is not None and latest.get("data_sha256") == envelope.get("data_sha256"):
                return True, "Durable GitHub snapshot was concurrently updated to the same data."
            if latest_sha:
                body["sha"] = latest_sha
                retry = requests.put(
                    f"{base}/contents/{encoded_path}",
                    headers=_github_headers(cfg["token"]),
                    json=body,
                    timeout=DURABLE_REQUEST_TIMEOUT,
                )
                if retry.status_code in {200, 201}:
                    return True, f"Saved {len(results):,}-row durable GitHub recovery snapshot after retry."
                return False, f"Durable GitHub save retry failed ({retry.status_code})."
        return False, f"Durable GitHub save failed ({resp.status_code}). Check token Contents: write permission."
    except Exception as exc:
        return False, f"Durable GitHub save failed: {type(exc).__name__}: {exc}"


def load_durable_snapshot(universe_name):
    cfg = durable_store_config()
    if not cfg["configured"]:
        return None, "Durable recovery not configured."
    allowed, reason = _durable_universe_allowed(universe_name)
    if not allowed:
        return None, reason
    try:
        envelope, _, message = _github_read_snapshot_file(cfg, universe_name)
        if envelope is None:
            return None, message
        if int(envelope.get("schema", -1)) != DURABLE_SNAPSHOT_SCHEMA:
            return None, "Durable snapshot schema is incompatible with this app version."
        if envelope.get("universe_name") != universe_name:
            return None, "Durable snapshot universe name mismatch."
        df = _decode_durable_dataframe(envelope.get("data_blob", ""), envelope.get("data_sha256", ""))
        if df is None or df.empty:
            return None, "Durable snapshot contained no scan rows."
        row_count = int(envelope.get("row_count", len(df)))
        if row_count != len(df):
            return None, "Durable snapshot row-count verification failed."
        snapshot = {
            "df": df,
            "timestamp": pd.Timestamp(envelope.get("scan_timestamp")).to_pydatetime(),
            "signal_session": pd.Timestamp(envelope.get("signal_session")) if envelope.get("signal_session") else None,
            "universe_signature": envelope.get("universe_signature", ""),
            "engine_version": envelope.get("engine_version", ""),
            "provider_health": dict(envelope.get("provider_health") or {}),
            "failures": list(envelope.get("failures") or []),
            "diagnostics": list(envelope.get("diagnostics") or []),
            "row_count": row_count,
            "recovery_source": "GitHub durable store",
        }
        return snapshot, "Loaded durable GitHub recovery snapshot."
    except Exception as exc:
        return None, f"Durable GitHub load failed: {type(exc).__name__}: {exc}"


def load_best_available_snapshot(universe_name, universe_signature=None, engine_version=None):
    """Prefer compatible local SQLite, then fall back to compatible durable GitHub state."""
    def compatible(snapshot):
        if snapshot is None:
            return False
        if universe_signature and snapshot.get("universe_signature") != universe_signature:
            return False
        if engine_version and snapshot.get("engine_version") != engine_version:
            return False
        return True

    local = load_last_good_snapshot(universe_name)
    if compatible(local):
        local["recovery_source"] = "local SQLite"
        return local, "local SQLite", ""

    remote, message = load_durable_snapshot(universe_name)
    if compatible(remote):
        return remote, "GitHub durable store", ""

    if remote is not None and not compatible(remote):
        return None, "", "Durable snapshot exists but is incompatible with the current universe or engine version."
    if local is not None and not compatible(local) and not message:
        message = "Local snapshot exists but is incompatible with the current universe or engine version."
    return None, "", message


def _state_db_connect():
    """Open/create the Phase 2B local recovery database.

    Streamlit Community Cloud does not guarantee local-file persistence. This store
    is therefore a best-effort recovery layer, not an external durable backup.
    """
    STATE_DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(STATE_DB_PATH), timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scan_snapshots (
            universe_name TEXT PRIMARY KEY,
            saved_at TEXT NOT NULL,
            scan_timestamp TEXT NOT NULL,
            signal_session TEXT,
            universe_signature TEXT,
            engine_version TEXT,
            provider_health_json TEXT,
            failures_json TEXT,
            diagnostics_json TEXT,
            row_count INTEGER NOT NULL,
            data_blob BLOB NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scan_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_timestamp TEXT NOT NULL,
            universe_name TEXT NOT NULL,
            signal_session TEXT,
            requested_count INTEGER NOT NULL,
            result_count INTEGER NOT NULL,
            elapsed_seconds REAL,
            status TEXT,
            coverage REAL,
            usable_coverage REAL,
            failures_json TEXT,
            diagnostics_json TEXT,
            provider_health_json TEXT
        )
        """
    )
    conn.commit()
    return conn


def persistence_status():
    try:
        with _state_db_connect() as conn:
            conn.execute("SELECT 1").fetchone()
        return True, f"Best-effort local SQLite recovery store available: {STATE_DB_PATH}"
    except Exception as exc:
        return False, f"Recovery snapshot store unavailable: {type(exc).__name__}: {exc}"


def _encode_dataframe(df):
    payload = pickle.dumps(df, protocol=pickle.HIGHEST_PROTOCOL)
    return sqlite3.Binary(zlib.compress(payload, level=6))


def _decode_dataframe(blob):
    return pickle.loads(zlib.decompress(blob))


def persistable_last_good(results, health):
    if results is None or results.empty:
        return False
    if health.get("status") != "HEALTHY":
        return False
    usable = health.get("usable_coverage", 0.0)
    try:
        return float(usable) >= PERSIST_MIN_USABLE_COVERAGE
    except Exception:
        return False


def save_last_good_snapshot(universe_name, results, timestamp, failures, universe_signature,
                            signal_session, engine_version, health, diagnostics):
    """Persist one best-effort last-good snapshot per universe."""
    if not persistable_last_good(results, health):
        return False, "Scan did not meet the persistent last-good quality threshold."
    try:
        with _state_db_connect() as conn:
            conn.execute(
                """
                INSERT INTO scan_snapshots (
                    universe_name, saved_at, scan_timestamp, signal_session,
                    universe_signature, engine_version, provider_health_json,
                    failures_json, diagnostics_json, row_count, data_blob
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(universe_name) DO UPDATE SET
                    saved_at=excluded.saved_at,
                    scan_timestamp=excluded.scan_timestamp,
                    signal_session=excluded.signal_session,
                    universe_signature=excluded.universe_signature,
                    engine_version=excluded.engine_version,
                    provider_health_json=excluded.provider_health_json,
                    failures_json=excluded.failures_json,
                    diagnostics_json=excluded.diagnostics_json,
                    row_count=excluded.row_count,
                    data_blob=excluded.data_blob
                """,
                (
                    universe_name,
                    datetime.now().isoformat(timespec="seconds"),
                    pd.Timestamp(timestamp).isoformat(),
                    pd.Timestamp(signal_session).isoformat() if signal_session is not None and not pd.isna(signal_session) else None,
                    universe_signature,
                    engine_version,
                    json.dumps(health, default=_json_default),
                    json.dumps(list(failures or []), default=_json_default),
                    json.dumps(list(diagnostics or []), default=_json_default),
                    int(len(results)),
                    _encode_dataframe(results),
                ),
            )
            conn.commit()
        return True, f"Saved {len(results):,}-row last-good recovery snapshot."
    except Exception as exc:
        return False, f"Could not save recovery snapshot: {type(exc).__name__}: {exc}"


def load_last_good_snapshot(universe_name):
    try:
        with _state_db_connect() as conn:
            row = conn.execute(
                """
                SELECT scan_timestamp, signal_session, universe_signature, engine_version,
                       provider_health_json, failures_json, diagnostics_json, row_count, data_blob
                FROM scan_snapshots WHERE universe_name = ?
                """,
                (universe_name,),
            ).fetchone()
        if not row:
            return None
        scan_timestamp, signal_session, signature, engine_version, health_json, failures_json, diagnostics_json, row_count, blob = row
        df = _decode_dataframe(blob)
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            return None
        return {
            "df": df,
            "timestamp": pd.Timestamp(scan_timestamp).to_pydatetime(),
            "signal_session": pd.Timestamp(signal_session) if signal_session else None,
            "universe_signature": signature or "",
            "engine_version": engine_version or "",
            "provider_health": json.loads(health_json) if health_json else {},
            "failures": json.loads(failures_json) if failures_json else [],
            "diagnostics": json.loads(diagnostics_json) if diagnostics_json else [],
            "row_count": int(row_count or len(df)),
        }
    except Exception:
        return None


def log_scan_run(universe_name, signal_session, requested_count, result_count, elapsed_seconds,
                 failures, health, diagnostics):
    """Persist compact structured scan-run diagnostics for later troubleshooting."""
    try:
        with _state_db_connect() as conn:
            conn.execute(
                """
                INSERT INTO scan_runs (
                    run_timestamp, universe_name, signal_session, requested_count,
                    result_count, elapsed_seconds, status, coverage, usable_coverage,
                    failures_json, diagnostics_json, provider_health_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now().isoformat(timespec="seconds"),
                    universe_name,
                    pd.Timestamp(signal_session).isoformat() if signal_session is not None and not pd.isna(signal_session) else None,
                    int(requested_count),
                    int(result_count),
                    float(elapsed_seconds) if np.isfinite(elapsed_seconds) else None,
                    str(health.get("status", "UNKNOWN")),
                    float(health.get("coverage", np.nan)) if pd.notna(health.get("coverage", np.nan)) else None,
                    float(health.get("usable_coverage", np.nan)) if pd.notna(health.get("usable_coverage", np.nan)) else None,
                    json.dumps(list(failures or []), default=_json_default),
                    json.dumps(list(diagnostics or []), default=_json_default),
                    json.dumps(health, default=_json_default),
                ),
            )
            conn.execute(
                """
                DELETE FROM scan_runs
                WHERE id NOT IN (
                    SELECT id FROM scan_runs ORDER BY id DESC LIMIT ?
                )
                """,
                (PERSISTENT_RUN_LOG_LIMIT,),
            )
            conn.commit()
        return True
    except Exception:
        return False


def recent_scan_runs(universe_name, limit=5):
    try:
        with _state_db_connect() as conn:
            rows = conn.execute(
                """
                SELECT run_timestamp, signal_session, requested_count, result_count,
                       elapsed_seconds, status, coverage, usable_coverage
                FROM scan_runs
                WHERE universe_name = ?
                ORDER BY id DESC LIMIT ?
                """,
                (universe_name, int(limit)),
            ).fetchall()
        return pd.DataFrame(
            rows,
            columns=["Run Time", "Signal Session", "Requested", "Analyzed", "Seconds", "Status", "Coverage", "Usable Coverage"],
        )
    except Exception:
        return pd.DataFrame()


def apply_snapshot_to_session(universe_name, snapshot, recovered=False, preserve_provider_health=False):
    """Apply a validated local or durable recovery snapshot to this Streamlit session."""
    st.session_state.scan_df = snapshot["df"].copy()
    st.session_state.scan_ranked_df = pd.DataFrame()
    st.session_state.scan_ranked_regime_label = ""
    st.session_state.scan_ranked_engine_version = ""
    st.session_state.scan_universe_name = universe_name
    st.session_state.scan_timestamp = snapshot.get("timestamp")
    st.session_state.scan_errors = list(snapshot.get("failures", []))
    st.session_state.scan_universe_signature = snapshot.get("universe_signature", "")
    st.session_state.scan_signal_session = snapshot.get("signal_session")
    st.session_state.scan_data_engine_version = snapshot.get("engine_version", "")
    st.session_state.scan_diagnostics = list(snapshot.get("diagnostics", []))
    st.session_state.last_good_scans[universe_name] = {
        "df": snapshot["df"].copy(),
        "timestamp": snapshot.get("timestamp"),
        "failures": list(snapshot.get("failures", [])),
        "universe_signature": snapshot.get("universe_signature", ""),
        "signal_session": snapshot.get("signal_session"),
        "engine_version": snapshot.get("engine_version", ""),
        "diagnostics": list(snapshot.get("diagnostics", [])),
    }
    if recovered and not preserve_provider_health:
        old_health = dict(snapshot.get("provider_health", {}) or {})
        recovery_source = snapshot.get("recovery_source", "recovery store")
        st.session_state.persistence_restore_source = recovery_source
        st.session_state.provider_health = {
            **old_health,
            "status": "RECOVERED",
            "message": (
                f"Recovered a last-good snapshot from {recovery_source}. The live market-data provider "
                "has not yet been retested in this app session."
            ),
        }


def scanner_frame_diagnostic(df):
    """Compact final-state diagnostic for one scanner frame."""
    valid, reason = price_data_status(df, min_rows=126)
    x = completed_session_frame(df)
    latest = pd.NaT
    if not x.empty and "Date" in x.columns:
        latest = pd.to_datetime(x["Date"], errors="coerce").max()
    if not valid:
        return {
            "usable": False,
            "confidence": "LOW",
            "latest_session": "" if pd.isna(latest) else pd.Timestamp(latest).strftime("%Y-%m-%d"),
            "note": reason,
        }
    conf = data_confidence(x, recent_lookback=14)
    return {
        "usable": not bool(conf.get("block", True)),
        "confidence": conf.get("level", "LOW"),
        "latest_session": "" if pd.isna(latest) else pd.Timestamp(latest).strftime("%Y-%m-%d"),
        "note": conf.get("message", ""),
    }


def format_elapsed_short(seconds):
    try:
        value = float(seconds)
    except Exception:
        return "N/A"
    if not np.isfinite(value):
        return "N/A"
    if value < 1.0:
        return "<1s"
    if value < 10:
        return f"{value:.1f}s"
    return f"{value:.0f}s"


def scan_universe_signature(universe_df):
    """Stable hash of the ticker set used by a scanner run.

    A same-name universe is not assumed to be identical: watchlists can change and
    index constituent sources can update. The signature prevents reuse when the
    actual symbol set differs from the cached scan.
    """
    if universe_df is None or universe_df.empty or "Ticker" not in universe_df.columns:
        return ""
    tickers = sorted({clean_symbol(v) for v in universe_df["Ticker"].tolist() if clean_symbol(v)})
    payload = "\n".join(tickers).encode("utf-8")
    return hashlib.sha1(payload).hexdigest()


def reusable_session_scan(universe_name, universe_df, now=None):
    """Return (reusable, age_seconds, signal_session, reason).

    Phase 2A.1 reuses the finished scan only when all material cache keys match:
    same universe name, same ticker-set signature, same completed XNYS session,
    same data-engine version, and age within the scanner result TTL.
    """
    cached = st.session_state.get("scan_df", pd.DataFrame())
    if cached is None or cached.empty:
        return False, np.nan, pd.NaT, "no cached scan"
    if st.session_state.get("scan_universe_name", "") != universe_name:
        return False, np.nan, pd.NaT, "different universe"

    current_signature = scan_universe_signature(universe_df)
    if not current_signature or st.session_state.get("scan_universe_signature", "") != current_signature:
        return False, np.nan, pd.NaT, "universe constituents changed"

    current_session = expected_latest_completed_us_session(now=now)
    stored_session = st.session_state.get("scan_signal_session")
    if stored_session is None or pd.isna(stored_session):
        return False, np.nan, current_session, "cached signal session unavailable"
    stored_session = pd.Timestamp(stored_session).tz_localize(None).normalize()
    if stored_session != pd.Timestamp(current_session).tz_localize(None).normalize():
        return False, np.nan, current_session, "new completed XNYS session available"

    if st.session_state.get("scan_data_engine_version", "") != ENGINE_VERSION:
        return False, np.nan, current_session, "scanner engine version changed"

    timestamp = st.session_state.get("scan_timestamp")
    if timestamp is None:
        return False, np.nan, current_session, "cached scan timestamp unavailable"
    try:
        age_seconds = max(0.0, (datetime.now() - pd.Timestamp(timestamp).to_pydatetime()).total_seconds())
    except Exception:
        return False, np.nan, current_session, "cached scan age unavailable"

    if age_seconds > SCAN_RESULT_REUSE_TTL:
        return False, age_seconds, current_session, "cached scan expired"
    return True, age_seconds, current_session, "same universe/session within TTL"

def us_market_today(now=None):
    """Return the current New York calendar date as a naive normalized Timestamp."""
    try:
        if now is None:
            ts = pd.Timestamp.now(tz="America/New_York")
        else:
            ts = pd.Timestamp(now)
            if ts.tzinfo is None:
                ts = ts.tz_localize("America/New_York")
            else:
                ts = ts.tz_convert("America/New_York")
        return ts.tz_localize(None).normalize()
    except Exception:
        return pd.Timestamp.today().normalize()

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
        "User-Agent": "tradewithedge SwingMomentumScanner/Phase2C https://github.com/tradewithedge/swing-momentum-scanner",
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


def refresh_universe(name, watchlist_text):
    """Refresh only the constituent cache that failed; never clear all app caches."""
    if name == "S&P 500":
        load_sp500.clear()
    elif name == "Nasdaq-100":
        load_nasdaq100.clear()
    elif name == "Russell 2000 / IWM":
        load_iwm.clear()
    elif name == "Combined US":
        load_sp500.clear()
        load_nasdaq100.clear()
        load_iwm.clear()
    return get_universe(name, watchlist_text)


# ---------------------------
# Yahoo downloads
# ---------------------------

def _normalize_event_timestamp(value):
    """Best-effort conversion of provider event values to a naive pandas Timestamp."""
    if value is None:
        return pd.NaT
    if isinstance(value, (list, tuple, pd.Series, pd.Index, np.ndarray)):
        values = list(value)
        if not values:
            return pd.NaT
        value = values[0]
    try:
        if isinstance(value, (int, float, np.integer, np.floating)) and np.isfinite(value):
            ts = pd.to_datetime(value, unit="s", errors="coerce", utc=True)
        else:
            ts = pd.to_datetime(value, errors="coerce", utc=True)
        if pd.isna(ts):
            return pd.NaT
        return pd.Timestamp(ts).tz_convert(None)
    except Exception:
        return pd.NaT


def _split_earnings_dates(values, today=None):
    """Return (next_date, last_date) using the New York market calendar date."""
    dates = []
    for value in values:
        ts = _normalize_event_timestamp(value)
        if pd.notna(ts):
            dates.append(pd.Timestamp(ts).normalize())
    if not dates:
        return pd.NaT, pd.NaT
    dates = sorted(set(dates))
    market_day = us_market_today() if today is None else pd.Timestamp(today).normalize()
    future = [d for d in dates if d >= market_day]
    past = [d for d in dates if d < market_day]
    return (min(future) if future else pd.NaT, max(past) if past else pd.NaT)


@st.cache_data(ttl=SEC_REFERENCE_CACHE_TTL, show_spinner=False)
def load_sec_ticker_reference():
    """Ticker -> CIK reference used only for fallback metadata / filing verification."""
    mapping = {}
    try:
        response = requests.get(SEC_TICKERS_URL, headers=safe_headers(), timeout=METADATA_HTTP_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
        for item in payload.values():
            ticker = clean_symbol(item.get("ticker", ""))
            cik = item.get("cik_str")
            if ticker and cik is not None:
                mapping[ticker] = str(int(cik)).zfill(10)
    except Exception:
        pass
    return mapping


def _provider_number(value):
    """Extract Yahoo quoteSummary raw numeric values without assuming one response shape."""
    if isinstance(value, dict):
        value = value.get("raw", value.get("fmt"))
    try:
        number = float(value)
        return number if np.isfinite(number) else np.nan
    except Exception:
        return np.nan


@st.cache_data(ttl=COMPANY_SNAPSHOT_TTL, show_spinner=False)
def yahoo_direct_fundamentals(symbol):
    """Secondary Yahoo HTTP route used only to repair missing yfinance metadata fields."""
    result = {
        "sector": "", "industry": "", "market_cap": np.nan,
        "trailing_pe": np.nan, "forward_pe": np.nan, "trailing_eps": np.nan,
        "route_notes": [],
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; SwingMomentumScanner/Phase2C)",
        "Accept": "application/json,text/plain,*/*",
    }
    # Lightweight quote endpoint first.
    try:
        response = requests.get(
            YAHOO_QUOTE_API,
            params={"symbols": clean_symbol(symbol)},
            headers=headers,
            timeout=METADATA_HTTP_TIMEOUT,
        )
        response.raise_for_status()
        rows = ((response.json().get("quoteResponse") or {}).get("result") or [])
        if rows:
            row = rows[0]
            result["market_cap"] = _provider_number(row.get("marketCap"))
            result["trailing_pe"] = _provider_number(row.get("trailingPE"))
            result["forward_pe"] = _provider_number(row.get("forwardPE"))
            result["trailing_eps"] = _provider_number(row.get("epsTrailingTwelveMonths"))
            result["route_notes"].append("Yahoo direct quote")
    except Exception:
        pass

    # quoteSummary can repair profile fields and valuation fields when available.
    try:
        url = YAHOO_QUOTE_SUMMARY_API.format(symbol=requests.utils.quote(clean_symbol(symbol), safe="-"))
        response = requests.get(
            url,
            params={"modules": "assetProfile,summaryDetail,defaultKeyStatistics,price"},
            headers=headers,
            timeout=METADATA_HTTP_TIMEOUT,
        )
        response.raise_for_status()
        payload = (((response.json().get("quoteSummary") or {}).get("result") or [{}])[0]) or {}
        profile = payload.get("assetProfile") or {}
        summary = payload.get("summaryDetail") or {}
        stats = payload.get("defaultKeyStatistics") or {}
        price = payload.get("price") or {}
        result["sector"] = str(profile.get("sector", "") or "")
        result["industry"] = str(profile.get("industry", "") or "")
        if pd.isna(result["market_cap"]):
            result["market_cap"] = _provider_number(price.get("marketCap", summary.get("marketCap")))
        if pd.isna(result["trailing_pe"]):
            result["trailing_pe"] = _provider_number(summary.get("trailingPE"))
        if pd.isna(result["forward_pe"]):
            result["forward_pe"] = _provider_number(summary.get("forwardPE", stats.get("forwardPE")))
        if pd.isna(result["trailing_eps"]):
            result["trailing_eps"] = _provider_number(stats.get("trailingEps"))
        result["route_notes"].append("Yahoo direct quoteSummary")
    except Exception:
        pass
    return result



@st.cache_data(ttl=COMPANY_SNAPSHOT_TTL, show_spinner=False)
def nasdaq_direct_fundamentals(symbol):
    """Independent non-Yahoo repair for profile fields and market cap.

    Nasdaq's public quote-summary payload exposes Sector, Industry and MarketCap.
    This route is used only to fill fields still missing after the two Yahoo routes.
    """
    result = {
        "sector": "", "industry": "", "market_cap": np.nan,
        "route_note": "", "http_status": None,
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.nasdaq.com/market-activity/stocks",
        "Origin": "https://www.nasdaq.com",
    }
    try:
        url = NASDAQ_QUOTE_SUMMARY_API.format(symbol=requests.utils.quote(clean_symbol(symbol), safe="-"))
        response = requests.get(
            url, params={"assetclass": "stocks"}, headers=headers, timeout=METADATA_HTTP_TIMEOUT
        )
        result["http_status"] = response.status_code
        response.raise_for_status()
        payload = response.json() or {}
        data = payload.get("data") or {}
        summary = data.get("summaryData") or {}

        def summary_value(key):
            item = summary.get(key) or {}
            return item.get("value") if isinstance(item, dict) else item

        result["sector"] = str(summary_value("Sector") or "").strip()
        result["industry"] = str(summary_value("Industry") or "").strip()
        mc_text = str(summary_value("MarketCap") or "").strip()
        if mc_text:
            cleaned = re.sub(r"[^0-9.\-]", "", mc_text)
            try:
                mc = float(cleaned)
                if np.isfinite(mc) and mc > 0:
                    result["market_cap"] = mc
            except Exception:
                pass
        result["route_note"] = "Nasdaq quote summary"
    except Exception as exc:
        result["route_note"] = f"Nasdaq quote summary unavailable: {type(exc).__name__}"
    return result

def _sec_numeric_text(value):
    """Parse a positive share-count value from SEC inline-XBRL/display text."""
    text = html_lib.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\xa0", " ")
    # Preserve decimal point/sign for generic IXBRL handling; remove thousands separators.
    text = text.replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return np.nan
    try:
        number = float(match.group(0))
        return number if np.isfinite(number) else np.nan
    except Exception:
        return np.nan


def _sec_extract_shares_from_filing_html(document_text):
    """Extract exact common shares outstanding from an SEC filing/R1 HTML document.

    CompanyFacts can omit some dimensionally-qualified cover-page DEI facts. Filing-level
    inline XBRL is therefore a necessary secondary SEC route, not a different provider.
    """
    text = str(document_text or "")
    concepts = ("EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding")

    # Preferred path: the actual inline-XBRL fact, including optional scale/sign attributes.
    for concept in concepts:
        pattern = re.compile(
            rf'<ix:nonfraction\b(?P<attrs>[^>]*\bname=["\'][^"\']*{concept}["\'][^>]*)>'
            rf'(?P<body>.*?)</ix:nonfraction>',
            re.IGNORECASE | re.DOTALL,
        )
        for match in pattern.finditer(text):
            attrs = match.group("attrs") or ""
            value = _sec_numeric_text(match.group("body"))
            if pd.isna(value) or value <= 0:
                continue
            scale_match = re.search(r'\bscale=["\'](-?\d+)["\']', attrs, re.IGNORECASE)
            if scale_match:
                try:
                    value *= 10 ** int(scale_match.group(1))
                except Exception:
                    pass
            sign_match = re.search(r'\bsign=["\'](-)["\']', attrs, re.IGNORECASE)
            if sign_match:
                value = -abs(value)
            if np.isfinite(value) and value >= 1_000_000:
                return float(value), f"SEC filing inline XBRL: {concept}"

    # R1 / rendered filing fallback. This is constrained to the explicit cover-page label
    # and requires a plausible public-company share count to avoid harvesting unrelated values.
    plain = html_lib.unescape(re.sub(r"<[^>]+>", " ", text))
    plain = re.sub(r"\s+", " ", plain)
    label_patterns = [
        r"Entity Common Stock\s*,?\s*Shares Outstanding",
        r"number of shares outstanding of each of the issuer.{0,120}?common stock",
    ]
    for label_pattern in label_patterns:
        label_match = re.search(label_pattern, plain, re.IGNORECASE)
        if not label_match:
            continue
        window = plain[label_match.end(): label_match.end() + 420]
        for raw in re.findall(r"\d[\d, ]{5,}", window):
            value = _sec_numeric_text(raw)
            if pd.notna(value) and 1_000_000 <= value <= 1_000_000_000_000:
                return float(value), "SEC filing cover-page rendered fact"
    return np.nan, ""


def _sec_recent_filing_shares(cik, recent):
    """Try recent 10-Q/10-K filing-level documents when CompanyFacts lacks shares."""
    result = {"shares": np.nan, "source": "", "filing_date": "", "form": "", "url": "", "note": ""}
    recent = recent or {}
    forms = recent.get("form") or []
    accessions = recent.get("accessionNumber") or []
    primary_docs = recent.get("primaryDocument") or []
    filing_dates = recent.get("filingDate") or []
    candidates = []
    for idx, form in enumerate(forms):
        form_text = str(form or "").upper()
        if not (form_text.startswith("10-Q") or form_text.startswith("10-K")):
            continue
        if idx >= len(accessions):
            continue
        try:
            filed = pd.Timestamp(filing_dates[idx]).normalize() if idx < len(filing_dates) else pd.Timestamp("1900-01-01")
        except Exception:
            filed = pd.Timestamp("1900-01-01")
        candidates.append((filed, idx, form_text))
    candidates.sort(reverse=True, key=lambda x: x[0])

    cik_path = str(int(str(cik)))
    for filed, idx, form_text in candidates[:4]:
        accession = re.sub(r"\D", "", str(accessions[idx] or ""))
        if not accession:
            continue
        primary_doc = str(primary_docs[idx] if idx < len(primary_docs) else "").strip()
        docs = ["R1.htm"]
        if primary_doc and primary_doc not in docs:
            docs.append(primary_doc)
        for document in docs:
            url = f"{SEC_ARCHIVES_BASE}/{cik_path}/{accession}/{document}"
            try:
                response = requests.get(url, headers=safe_headers(), timeout=METADATA_HTTP_TIMEOUT)
                if response.status_code != 200:
                    continue
                shares, source = _sec_extract_shares_from_filing_html(response.text)
                if pd.notna(shares) and shares > 0:
                    result.update({
                        "shares": float(shares),
                        "source": f"{source} ({form_text})",
                        "filing_date": str(filed.date()),
                        "form": form_text,
                        "url": url,
                        "note": "Recovered from filing-level SEC data because CompanyFacts did not expose a usable shares-outstanding fact.",
                    })
                    return result
            except Exception:
                continue
    result["note"] = "No usable shares-outstanding fact was found in recent SEC 10-Q/10-K filing-level documents."
    return result


@st.cache_data(ttl=SEC_REFERENCE_CACHE_TTL, show_spinner=False)
def sec_company_fallback(symbol):
    """Independent SEC fallback: SIC industry, shares outstanding, and recent 8-K 2.02 event evidence."""
    result = {
        "cik": "", "industry": "", "shares_outstanding": np.nan,
        "shares_source": "", "shares_filing_date": "", "shares_recovery_note": "",
        "recent_results_date": None, "recent_results_source": "",
        "foreign_issuer": False,
    }
    cik = load_sec_ticker_reference().get(clean_symbol(symbol), "")
    if not cik:
        return result
    result["cik"] = cik
    recent = {}

    # Submissions: SIC description + recent 8-K Item 2.02 (results of operations).
    try:
        response = requests.get(
            SEC_SUBMISSIONS_URL.format(cik=cik), headers=safe_headers(), timeout=METADATA_HTTP_TIMEOUT
        )
        response.raise_for_status()
        payload = response.json()
        result["industry"] = str(payload.get("sicDescription", "") or "")
        recent = ((payload.get("filings") or {}).get("recent") or {})
        forms = recent.get("form") or []
        result["foreign_issuer"] = any(str(form or "").upper().startswith(("20-F", "40-F")) for form in forms)
        dates = recent.get("filingDate") or []
        items = recent.get("items") or []
        candidates = []
        for idx, form in enumerate(forms):
            form_text = str(form or "").upper()
            item_text = str(items[idx] if idx < len(items) else "")
            if form_text.startswith("8-K") and "2.02" in item_text:
                try:
                    filing_date = pd.Timestamp(dates[idx]).normalize()
                    candidates.append(filing_date)
                except Exception:
                    pass
        if candidates:
            recent_date = max(candidates)
            if 0 <= (us_market_today() - recent_date).days <= 10:
                result["recent_results_date"] = recent_date
                result["recent_results_source"] = "SEC 8-K Item 2.02"
    except Exception:
        pass

    # Company facts: latest reported common shares outstanding for market-cap repair.
    try:
        response = requests.get(
            SEC_COMPANYFACTS_URL.format(cik=cik), headers=safe_headers(), timeout=METADATA_HTTP_TIMEOUT
        )
        response.raise_for_status()
        facts = response.json().get("facts") or {}
        candidates = []
        for taxonomy, concept in [
            ("dei", "EntityCommonStockSharesOutstanding"),
            ("us-gaap", "CommonStockSharesOutstanding"),
        ]:
            concept_payload = ((facts.get(taxonomy) or {}).get(concept) or {})
            unit_rows = (concept_payload.get("units") or {}).get("shares") or []
            for row in unit_rows:
                value = _provider_number(row.get("val"))
                if pd.isna(value) or value <= 0:
                    continue
                try:
                    end_date = pd.Timestamp(row.get("end") or row.get("filed")).normalize()
                except Exception:
                    end_date = pd.Timestamp("1900-01-01")
                candidates.append((end_date, value))
        if candidates:
            best_end, best_value = max(candidates, key=lambda x: x[0])
            result["shares_outstanding"] = best_value
            result["shares_source"] = "SEC CompanyFacts XBRL shares outstanding"
            result["shares_filing_date"] = str(best_end.date()) if best_end.year > 1900 else ""
    except Exception:
        pass

    # CompanyFacts is intentionally not treated as authoritative coverage for every filing fact.
    # Some cover-page DEI facts can be absent from the aggregate API. Fall back to the latest
    # 10-Q/10-K filing-level XBRL/rendered cover page before declaring shares unavailable.
    if pd.isna(result.get("shares_outstanding", np.nan)) or result.get("shares_outstanding", 0) <= 0:
        filing_shares = _sec_recent_filing_shares(cik, recent)
        if pd.notna(filing_shares.get("shares", np.nan)) and filing_shares.get("shares", 0) > 0:
            result["shares_outstanding"] = float(filing_shares["shares"])
            result["shares_source"] = filing_shares.get("source", "SEC filing-level shares outstanding")
            result["shares_filing_date"] = filing_shares.get("filing_date", "")
            result["shares_recovery_note"] = filing_shares.get("note", "")
        else:
            result["shares_recovery_note"] = filing_shares.get("note", "")
    return result


def sec_market_cap_from_fallback(sec_payload, fallback_close):
    """Return a conservative SEC shares × completed-session close market-cap fallback.

    This helper is shared by the production fallback path and the Phase 2C controlled
    verification tool so the test exercises the same calculation semantics used live.
    Foreign issuers are intentionally excluded because ADR/share-unit relationships can
    make a simple reported-shares × US close calculation misleading.
    """
    sec_payload = sec_payload or {}
    if bool(sec_payload.get("foreign_issuer", False)):
        return {
            "ok": False, "value": np.nan, "shares": np.nan, "close": np.nan,
            "share_source": sec_payload.get("shares_source", ""),
            "share_filing_date": sec_payload.get("shares_filing_date", ""),
            "reason": "Foreign issuer/ADR protection blocked a simple SEC shares × US close estimate.",
        }
    shares = _provider_number(sec_payload.get("shares_outstanding"))
    try:
        close_value = float(fallback_close)
    except Exception:
        close_value = np.nan
    if pd.isna(shares) or shares <= 0:
        return {
            "ok": False, "value": np.nan, "shares": shares, "close": close_value,
            "share_source": sec_payload.get("shares_source", ""),
            "share_filing_date": sec_payload.get("shares_filing_date", ""),
            "reason": "SEC shares outstanding were unavailable or invalid after CompanyFacts + filing-level recovery.",
        }
    if pd.isna(close_value) or close_value <= 0:
        return {
            "ok": False, "value": np.nan, "shares": shares, "close": close_value,
            "share_source": sec_payload.get("shares_source", ""),
            "share_filing_date": sec_payload.get("shares_filing_date", ""),
            "reason": "Completed-session close was unavailable or invalid.",
        }
    return {
        "ok": True,
        "value": float(shares) * close_value,
        "shares": float(shares),
        "close": float(close_value),
        "share_source": sec_payload.get("shares_source", "SEC shares outstanding"),
        "share_filing_date": sec_payload.get("shares_filing_date", ""),
        "reason": "",
    }


@st.cache_data(ttl=5 * 60, show_spinner=False)
def phase2c_controlled_market_cap_fallback_test(symbol, fallback_close):
    """Deterministically exercise the production independent market-cap fallback chain.

    Diagnostic only: both Yahoo market-cap routes are deliberately ignored. The test first
    tries the same Nasdaq quote-summary repair used in production. SEC shares × completed-
    session close remains the tertiary route if Nasdaq is unavailable.
    """
    ticker = clean_symbol(symbol)
    nasdaq = nasdaq_direct_fundamentals(ticker)
    nasdaq_mc = _provider_number(nasdaq.get("market_cap"))
    if pd.notna(nasdaq_mc) and nasdaq_mc > 0:
        return {
            "status": "PASS", "ticker": ticker, "field": "Market Cap",
            "control": "Yahoo/yfinance + Yahoo direct market-cap values intentionally suppressed for this diagnostic.",
            "source": "Nasdaq quote summary fallback",
            "route": "NASDAQ", "value": float(nasdaq_mc),
            "shares": np.nan, "close": np.nan, "share_source": "",
            "share_filing_date": "", "cik": "", "industry": nasdaq.get("industry", ""),
            "foreign_issuer": False, "reason": "",
            "route_detail": nasdaq.get("route_note", "Nasdaq quote summary"),
        }

    # Tertiary SEC route: retained in production, but no longer the single point of failure.
    sec_payload = sec_company_fallback(ticker)
    calc = sec_market_cap_from_fallback(sec_payload, fallback_close)
    return {
        "status": "PASS" if calc.get("ok") else "FAIL",
        "ticker": ticker, "field": "Market Cap",
        "control": "Yahoo/yfinance + Yahoo direct market-cap values intentionally suppressed for this diagnostic.",
        "source": (
            f"{calc.get('share_source') or 'SEC reported shares'} × completed-session close"
            if calc.get("ok") else "Independent fallback unavailable"
        ),
        "route": "SEC" if calc.get("ok") else "NONE",
        "share_source": calc.get("share_source", ""),
        "share_filing_date": calc.get("share_filing_date", ""),
        "shares_recovery_note": sec_payload.get("shares_recovery_note", ""),
        "value": calc.get("value", np.nan), "shares": calc.get("shares", np.nan),
        "close": calc.get("close", np.nan), "reason": (
            f"Nasdaq repair unavailable ({nasdaq.get('route_note', 'no usable market cap')}); "
            f"SEC tertiary repair also failed: {calc.get('reason', '')}"
        ),
        "cik": sec_payload.get("cik", ""), "industry": sec_payload.get("industry", ""),
        "foreign_issuer": bool(sec_payload.get("foreign_issuer", False)),
        "route_detail": nasdaq.get("route_note", ""),
    }


@st.cache_data(ttl=NASDAQ_EARNINGS_CACHE_TTL, show_spinner=False)
def nasdaq_earnings_rows(date_value):
    """Fetch one Nasdaq earnings-calendar date. Nasdaq states this calendar is Zacks-derived/estimated."""
    date_text = pd.Timestamp(date_value).strftime("%Y-%m-%d")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.nasdaq.com/market-activity/earnings",
        "Origin": "https://www.nasdaq.com",
    }
    try:
        response = requests.get(
            NASDAQ_EARNINGS_API,
            params={"date": date_text},
            headers=headers,
            timeout=METADATA_HTTP_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json().get("data") or {}
        rows = data.get("rows") or ((data.get("calendar") or {}).get("rows")) or []
        return rows if isinstance(rows, list) else []
    except Exception:
        return None


def nasdaq_earnings_corroboration(symbol, candidate_date):
    """Corroborate a Yahoo estimate against Nasdaq/Zacks; never upgrades it to primary-source CONFIRMED."""
    if candidate_date is None or pd.isna(candidate_date):
        return {"status": "UNAVAILABLE", "date": None, "time": "", "source": "Nasdaq/Zacks earnings calendar"}
    candidate = pd.Timestamp(candidate_date).normalize()
    ticker = clean_symbol(symbol)
    exact_rows = nasdaq_earnings_rows(candidate)
    if exact_rows is None:
        return {"status": "UNAVAILABLE", "date": None, "time": "", "source": "Nasdaq/Zacks earnings calendar"}
    for row in exact_rows:
        if clean_symbol(row.get("symbol", "")) == ticker:
            return {
                "status": "MATCH", "date": candidate,
                "time": str(row.get("time", "") or ""),
                "source": "Nasdaq/Zacks earnings calendar",
            }
    # Adjacent-date conflict detection is only worth the extra requests inside the
    # 14-day decision window. Farther-out dates remain estimates if exact corroboration
    # is unavailable; this keeps ticker lookup responsive.
    days_to_event = (candidate - us_market_today()).days
    if 0 <= days_to_event <= EARNINGS_CAUTION_DAYS:
        for offset in (-1, 1):
            other = candidate + pd.Timedelta(days=offset)
            other_rows = nasdaq_earnings_rows(other)
            if other_rows is None:
                continue
            for row in other_rows:
                if clean_symbol(row.get("symbol", "")) == ticker:
                    return {
                        "status": "CONFLICT", "date": other,
                        "time": str(row.get("time", "") or ""),
                        "source": "Nasdaq/Zacks earnings calendar",
                    }
    return {"status": "NOT_FOUND", "date": None, "time": "", "source": "Nasdaq/Zacks earnings calendar"}


def event_override_for_symbol(symbol):
    """Optional primary-source override hook via Streamlit Secrets.

    Example TOML:
      [event_overrides.AMAT]
      date = "2026-11-12"
      source = "Applied Materials Investor Relations"
    Only explicit operator-supplied primary/company evidence is labeled CONFIRMED.
    """
    try:
        root = st.secrets.get("event_overrides", {})
        entry = root.get(clean_symbol(symbol), {}) if hasattr(root, "get") else {}
        date_value = entry.get("date") if hasattr(entry, "get") else None
        source = str(entry.get("source", "") or "") if hasattr(entry, "get") else ""
        ts = _normalize_event_timestamp(date_value)
        if pd.notna(ts) and source:
            return {"date": pd.Timestamp(ts).normalize(), "source": source}
    except Exception:
        pass
    return None


@st.cache_data(ttl=COMPANY_SNAPSHOT_TTL, show_spinner=False)
def get_company_snapshot(symbol, fallback_close=None):
    """Phase 2C metadata pipeline with provenance, fallback repair and event corroboration."""
    snapshot = {
        "sector": "",
        "industry": "",
        "market_cap": np.nan,
        "trailing_pe": np.nan,
        "forward_pe": np.nan,
        "earnings_date": None,
        "next_earnings_date": None,
        "last_earnings_date": None,
        "earnings_source": "",
        "event_data_confidence": "LOW — UNKNOWN",
        "earnings_certainty": "UNKNOWN",
        "fundamental_data_confidence": "LOW",
        "fundamental_sources": {},
        "fundamental_field_status": {},
        "fundamental_field_notes": {},
        "trailing_eps": np.nan,
        "event_sources": [],
        "event_conflict": "",
        "event_window_start": None,
        "event_window_end": None,
        "event_window_note": "",
        "metadata_retrieved_at": pd.Timestamp.now(tz="America/New_York").strftime("%Y-%m-%d %H:%M:%S %Z"),
    }
    info = {}
    ticker = None
    try:
        ticker = yf.Ticker(symbol)
        try:
            fast = ticker.fast_info
            if fast:
                value = fast.get("market_cap", np.nan)
                if pd.notna(value):
                    snapshot["market_cap"] = value
                    snapshot["fundamental_sources"]["Market Cap"] = "Yahoo/yfinance fast_info"
        except Exception:
            pass

        try:
            info = ticker.info or {}
            field_map = {
                "sector": ("sector", "Sector"),
                "industry": ("industry", "Industry"),
                "trailing_pe": ("trailingPE", "Trailing P/E"),
                "forward_pe": ("forwardPE", "Forward P/E"),
            }
            for target, (source_key, label) in field_map.items():
                value = info.get(source_key, "" if target in {"sector", "industry"} else np.nan)
                if target in {"sector", "industry"}:
                    value = str(value or "")
                    if value:
                        snapshot[target] = value
                        snapshot["fundamental_sources"][label] = "Yahoo/yfinance info"
                elif pd.notna(value):
                    snapshot[target] = value
                    snapshot["fundamental_sources"][label] = "Yahoo/yfinance info"
            if pd.isna(snapshot["market_cap"]) and pd.notna(info.get("marketCap", np.nan)):
                snapshot["market_cap"] = info.get("marketCap")
                snapshot["fundamental_sources"]["Market Cap"] = "Yahoo/yfinance info"
            trailing_eps = _provider_number(info.get("trailingEps"))
            if pd.notna(trailing_eps):
                snapshot["trailing_eps"] = trailing_eps
        except Exception:
            info = {}

        # Yahoo event routes: useful forward estimates, but not primary-source confirmation.
        calendar_dates = []
        try:
            cal = ticker.calendar
            if isinstance(cal, dict):
                value = cal.get("Earnings Date")
                if isinstance(value, (list, tuple, pd.Series, pd.Index, np.ndarray)):
                    calendar_dates.extend(list(value))
                elif value is not None:
                    calendar_dates.append(value)
            elif isinstance(cal, pd.DataFrame) and not cal.empty:
                if "Earnings Date" in cal.index:
                    calendar_dates.extend(list(cal.loc["Earnings Date"].dropna().values))
                elif "Earnings Date" in cal.columns:
                    calendar_dates.extend(list(cal["Earnings Date"].dropna().values))
        except Exception:
            pass

        next_date, last_date = _split_earnings_dates(calendar_dates)
        if pd.notna(next_date):
            snapshot["next_earnings_date"] = next_date
            snapshot["earnings_source"] = "Yahoo calendar"
            snapshot["event_sources"].append({"Source": "Yahoo calendar", "Date": str(pd.Timestamp(next_date).date()), "Role": "Estimate"})
        if pd.notna(last_date):
            snapshot["last_earnings_date"] = last_date

        if snapshot["next_earnings_date"] is None:
            try:
                earnings_dates = ticker.get_earnings_dates(limit=12)
                values = []
                if isinstance(earnings_dates, pd.DataFrame) and not earnings_dates.empty:
                    values.extend(list(earnings_dates.index))
                    for col in earnings_dates.columns:
                        if "earnings date" in str(col).lower():
                            values.extend(list(earnings_dates[col].dropna().values))
                table_next, table_last = _split_earnings_dates(values)
                if pd.notna(table_next):
                    snapshot["next_earnings_date"] = table_next
                    snapshot["earnings_source"] = "Yahoo earnings dates"
                    snapshot["event_sources"].append({"Source": "Yahoo earnings dates", "Date": str(pd.Timestamp(table_next).date()), "Role": "Estimate"})
                if snapshot["last_earnings_date"] is None and pd.notna(table_last):
                    snapshot["last_earnings_date"] = table_last
            except Exception:
                pass

        if snapshot["next_earnings_date"] is None:
            try:
                timestamp_values = [
                    info.get("earningsTimestamp"),
                    info.get("earningsTimestampStart"),
                    info.get("earningsTimestampEnd"),
                ]
                ts_next, ts_last = _split_earnings_dates([v for v in timestamp_values if v is not None])
                if pd.notna(ts_next):
                    snapshot["next_earnings_date"] = ts_next
                    snapshot["earnings_source"] = "Yahoo quote timestamp"
                    snapshot["event_sources"].append({"Source": "Yahoo quote timestamp", "Date": str(pd.Timestamp(ts_next).date()), "Role": "Estimate"})
                if snapshot["last_earnings_date"] is None and pd.notna(ts_last):
                    snapshot["last_earnings_date"] = ts_last
            except Exception:
                pass
    except Exception:
        ticker = None

    # Fundamental repair route 2: direct Yahoo HTTP endpoints, filling only missing fields.
    needs_yahoo_repair = (
        not snapshot["sector"] or not snapshot["industry"] or pd.isna(snapshot["market_cap"])
        or pd.isna(snapshot["trailing_pe"]) or pd.isna(snapshot["forward_pe"])
    )
    if needs_yahoo_repair:
        direct = yahoo_direct_fundamentals(symbol)
        for key, label in [
            ("sector", "Sector"), ("industry", "Industry"), ("market_cap", "Market Cap"),
            ("trailing_pe", "Trailing P/E"), ("forward_pe", "Forward P/E"),
        ]:
            missing = (not snapshot[key]) if key in {"sector", "industry"} else pd.isna(snapshot[key])
            candidate = direct.get(key)
            candidate_valid = bool(candidate) if key in {"sector", "industry"} else pd.notna(candidate)
            if missing and candidate_valid:
                snapshot[key] = candidate
                snapshot["fundamental_sources"][label] = "Yahoo direct HTTP fallback"
        if pd.isna(snapshot.get("trailing_eps", np.nan)) and pd.notna(direct.get("trailing_eps", np.nan)):
            snapshot["trailing_eps"] = direct.get("trailing_eps")

    # Independent repair route 3: Nasdaq quote summary. This is deliberately non-Yahoo
    # and fills only profile/market-cap fields that remain unresolved after both Yahoo routes.
    needs_nasdaq_repair = (
        not snapshot["sector"] or not snapshot["industry"] or pd.isna(snapshot["market_cap"])
    )
    if needs_nasdaq_repair:
        nasdaq_fund = nasdaq_direct_fundamentals(symbol)
        for key, label in [("sector", "Sector"), ("industry", "Industry")]:
            if not snapshot[key] and nasdaq_fund.get(key):
                snapshot[key] = str(nasdaq_fund[key])
                snapshot["fundamental_sources"][label] = "Nasdaq quote summary fallback"
        if pd.isna(snapshot["market_cap"]) and pd.notna(nasdaq_fund.get("market_cap", np.nan)):
            snapshot["market_cap"] = float(nasdaq_fund["market_cap"])
            snapshot["fundamental_sources"]["Market Cap"] = "Nasdaq quote summary fallback"

    # Independent SEC fallback is tertiary when profile/market-cap metadata is still incomplete,
    # or when a recent results filing can reduce post-event ambiguity. SEC access is best-effort
    # because hosted-cloud IPs can occasionally be rate-limited or classified by SEC fair-access controls.
    need_sec_fallback = (
        not snapshot["industry"]
        or pd.isna(snapshot["market_cap"])
        or snapshot.get("next_earnings_date") is None
    )
    sec = sec_company_fallback(symbol) if need_sec_fallback else {
        "industry": "", "shares_outstanding": np.nan,
        "shares_source": "", "shares_filing_date": "", "shares_recovery_note": "",
        "recent_results_date": None, "recent_results_source": "",
        "foreign_issuer": False,
    }
    if not snapshot["industry"] and sec.get("industry"):
        snapshot["industry"] = sec["industry"]
        snapshot["fundamental_sources"]["Industry"] = "SEC SIC description"
    if pd.isna(snapshot["market_cap"]):
        sec_mc = sec_market_cap_from_fallback(sec, fallback_close)
        if sec_mc.get("ok"):
            snapshot["market_cap"] = sec_mc["value"]
            share_source = sec_mc.get("share_source") or "SEC reported shares"
            snapshot["fundamental_sources"]["Market Cap"] = (
                f"{share_source} × completed-session close (fallback estimate)"
            )

    # Recent SEC 8-K Item 2.02 is primary filing evidence that results were released.
    if sec.get("recent_results_date") is not None:
        sec_date = pd.Timestamp(sec["recent_results_date"]).normalize()
        current_last = snapshot.get("last_earnings_date")
        if current_last is None or pd.isna(current_last) or abs((pd.Timestamp(current_last).normalize() - sec_date).days) <= 3:
            snapshot["last_earnings_date"] = sec_date
            snapshot["event_sources"].append({"Source": sec.get("recent_results_source", "SEC 8-K Item 2.02"), "Date": str(sec_date.date()), "Role": "Primary filing / historical confirmation"})

    # Optional operator-supplied company/IR primary source has highest authority.
    override = event_override_for_symbol(symbol)
    if override is not None:
        override_date = pd.Timestamp(override["date"]).normalize()
        snapshot["next_earnings_date"] = override_date if override_date >= us_market_today() else snapshot.get("next_earnings_date")
        if override_date < us_market_today():
            snapshot["last_earnings_date"] = override_date
        snapshot["earnings_date"] = override_date
        snapshot["earnings_source"] = override["source"]
        snapshot["earnings_certainty"] = "CONFIRMED"
        snapshot["event_data_confidence"] = "HIGH — CONFIRMED"
        snapshot["event_sources"].append({"Source": override["source"], "Date": str(override_date.date()), "Role": "Primary/company confirmation"})
    else:
        next_date = snapshot.get("next_earnings_date")
        last_date = snapshot.get("last_earnings_date")
        today = us_market_today()
        if next_date is not None and not pd.isna(next_date):
            next_date = pd.Timestamp(next_date).normalize()
            snapshot["earnings_date"] = next_date
            corroboration = nasdaq_earnings_corroboration(symbol, next_date)
            if corroboration.get("status") == "MATCH":
                snapshot["earnings_certainty"] = "CORROBORATED"
                snapshot["event_data_confidence"] = "MEDIUM-HIGH — CORROBORATED"
                snapshot["earnings_source"] = f"{snapshot.get('earnings_source') or 'Yahoo'} + Nasdaq/Zacks calendar"
                snapshot["event_sources"].append({
                    "Source": corroboration["source"], "Date": str(pd.Timestamp(corroboration["date"]).date()),
                    "Role": "Independent estimate corroboration",
                })
            elif corroboration.get("status") == "CONFLICT":
                other_date = pd.Timestamp(corroboration["date"]).normalize()
                gap_days = abs((other_date - next_date).days)
                if gap_days <= 1:
                    window_start = min(next_date, other_date)
                    window_end = max(next_date, other_date)
                    snapshot["earnings_certainty"] = "ESTIMATE_WINDOW"
                    snapshot["event_data_confidence"] = "MEDIUM — ESTIMATE WINDOW"
                    snapshot["event_window_start"] = window_start
                    snapshot["event_window_end"] = window_end
                    snapshot["event_window_note"] = (
                        f"Independent estimate sources differ by {gap_days} day: "
                        f"expected earnings window {window_start:%d-%b-%Y} to {window_end:%d-%b-%Y}. "
                        "The company has not yet confirmed the exact date."
                    )
                    snapshot["earnings_source"] = f"{snapshot.get('earnings_source') or 'Yahoo'} + Nasdaq/Zacks calendar"
                    snapshot["event_sources"].append({
                        "Source": corroboration["source"], "Date": str(other_date.date()), "Role": "Adjacent independent estimate",
                    })
                else:
                    snapshot["earnings_certainty"] = "CONFLICT"
                    snapshot["event_data_confidence"] = "LOW — SOURCE CONFLICT"
                    snapshot["event_conflict"] = (
                        f"Yahoo estimate {next_date:%d-%b-%Y} conflicts with Nasdaq/Zacks {other_date:%d-%b-%Y}."
                    )
                    snapshot["event_sources"].append({
                        "Source": corroboration["source"], "Date": str(other_date.date()), "Role": "Conflicting estimate",
                    })
            else:
                snapshot["earnings_certainty"] = "ESTIMATED"
                snapshot["event_data_confidence"] = "MEDIUM — ESTIMATED"
        elif last_date is not None and not pd.isna(last_date):
            last_date = pd.Timestamp(last_date).normalize()
            snapshot["earnings_date"] = last_date
            days_since = (today - last_date).days
            snapshot["earnings_certainty"] = "UNKNOWN"
            snapshot["event_data_confidence"] = (
                "MEDIUM — NEXT DATE UNKNOWN" if 0 <= days_since <= RECENT_EARNINGS_GRACE_DAYS
                else "LOW — UNKNOWN"
            )
            if not snapshot.get("earnings_source"):
                snapshot["earnings_source"] = "Yahoo/SEC earnings history"

    # Phase 2C finishing semantics: distinguish AVAILABLE, NOT_MEANINGFUL and UNRESOLVED.
    # A valuation multiple can be legitimately N/M (for example, trailing P/E when TTM EPS <= 0);
    # that is not a provider/data-quality failure and should not reduce confidence by itself.
    statuses = {
        "Sector": "AVAILABLE" if bool(snapshot.get("sector")) else "UNRESOLVED",
        "Industry": "AVAILABLE" if bool(snapshot.get("industry")) else "UNRESOLVED",
        "Market Cap": "AVAILABLE" if pd.notna(snapshot.get("market_cap")) else "UNRESOLVED",
        "Trailing P/E": "AVAILABLE" if pd.notna(snapshot.get("trailing_pe")) else "UNRESOLVED",
        "Forward P/E": "AVAILABLE" if pd.notna(snapshot.get("forward_pe")) else "UNRESOLVED",
    }
    notes = {}
    trailing_eps = snapshot.get("trailing_eps", np.nan)
    if statuses["Trailing P/E"] == "UNRESOLVED" and pd.notna(trailing_eps) and float(trailing_eps) <= 0:
        statuses["Trailing P/E"] = "NOT_MEANINGFUL"
        notes["Trailing P/E"] = f"N/M — trailing EPS is {float(trailing_eps):.2f}; a positive trailing P/E is not meaningful."
        snapshot["fundamental_sources"].setdefault("Trailing P/E", "Yahoo earnings/valuation data")

    snapshot["fundamental_field_status"] = statuses
    snapshot["fundamental_field_notes"] = notes

    profile_resolved = statuses["Sector"] == "AVAILABLE" or statuses["Industry"] == "AVAILABLE"
    fundamental_resolved = int(profile_resolved)
    fundamental_resolved += int(statuses["Market Cap"] == "AVAILABLE")
    fundamental_resolved += int(statuses["Trailing P/E"] in {"AVAILABLE", "NOT_MEANINGFUL"})
    fundamental_resolved += int(statuses["Forward P/E"] == "AVAILABLE")
    snapshot["fundamental_data_confidence"] = (
        "HIGH" if fundamental_resolved >= 3 else "MEDIUM" if fundamental_resolved >= 1 else "LOW"
    )
    return snapshot


def evaluate_earnings_event(metadata, today=None):
    """Convert earnings metadata into a conservative event-risk state.

    Phase 1.3 separates *date availability* from *date certainty*:
      CONFIRMED = explicitly verified by a primary/company source or override.
      ESTIMATED       = provider-supplied future date (Yahoo routes in this build).
      ESTIMATE_WINDOW = adjacent independent estimates disagree by one day; exact timing unconfirmed.
      UNKNOWN         = no usable next event date.

    Estimated dates still protect the account: if an estimate falls inside the
    hard danger window, the engine blocks new swing entries conservatively.
    """
    market_day = us_market_today() if today is None else pd.Timestamp(today).normalize()
    next_date = metadata.get("next_earnings_date")
    last_date = metadata.get("last_earnings_date")
    legacy_date = metadata.get("earnings_date")
    source = metadata.get("earnings_source", "") or "Unverified"
    certainty = str(metadata.get("earnings_certainty", "UNKNOWN") or "UNKNOWN").upper()
    if certainty not in {"CONFIRMED", "CORROBORATED", "ESTIMATED", "ESTIMATE_WINDOW", "CONFLICT", "UNKNOWN"}:
        certainty = "UNKNOWN"
    conflict_detail = str(metadata.get("event_conflict", "") or "")

    def _fmt(ts):
        return pd.Timestamp(ts).normalize().strftime("%d-%b-%Y")

    def _confidence_for(cert):
        if cert == "CONFIRMED":
            return "HIGH — CONFIRMED"
        if cert == "CORROBORATED":
            return "MEDIUM-HIGH — CORROBORATED"
        if cert == "ESTIMATED":
            return "MEDIUM — ESTIMATED"
        if cert == "ESTIMATE_WINDOW":
            return "MEDIUM — ESTIMATE WINDOW"
        if cert == "CONFLICT":
            return "LOW — SOURCE CONFLICT"
        return "LOW — UNKNOWN"


    if certainty == "ESTIMATE_WINDOW":
        start = metadata.get("event_window_start")
        end = metadata.get("event_window_end")
        if start is None or pd.isna(start):
            start = next_date
        if end is None or pd.isna(end):
            end = next_date
        start = pd.Timestamp(start).normalize() if start is not None and not pd.isna(start) else pd.NaT
        end = pd.Timestamp(end).normalize() if end is not None and not pd.isna(end) else pd.NaT
        if pd.notna(start) and pd.notna(end):
            earliest_days = (start - market_day).days
            latest_days = (end - market_day).days
            day_text = (
                f"{earliest_days}–{latest_days} days away" if earliest_days != latest_days
                else ("Today" if earliest_days == 0 else f"{earliest_days} days away")
            )
            window_text = f"{_fmt(start)} to {_fmt(end)} — {day_text}"
            note = str(metadata.get("event_window_note", "") or "")
            return {
                "text": window_text, "risk": True, "state": "WINDOW", "block": True,
                "block_reason": (note + " " if note else "")
                    + "New swing entries are blocked until the exact event timing is sufficiently verified.",
                "event_date": start, "source": source,
                "confidence": "MEDIUM — ESTIMATE WINDOW", "certainty": "ESTIMATE_WINDOW",
            }

    if certainty == "CONFLICT":
        return {
            "text": conflict_detail or "Earnings sources disagree on the next report date",
            "risk": True, "state": "UNKNOWN", "block": True,
            "block_reason": (conflict_detail or "Earnings-date sources conflict.")
                + " New swing entries are blocked until the event date is re-verified.",
            "event_date": pd.Timestamp(next_date).normalize() if next_date is not None and not pd.isna(next_date) else pd.NaT,
            "source": source, "confidence": "LOW — SOURCE CONFLICT", "certainty": "CONFLICT",
        }

    if next_date is not None and not pd.isna(next_date):
        event_date = pd.Timestamp(next_date).normalize()
        days = (event_date - market_day).days
        confidence = _confidence_for(certainty)
        if days < 0:
            return {
                "text": f"{_fmt(event_date)} — provider date has passed; next date needs re-verification",
                "risk": False, "state": "UNKNOWN", "block": True,
                "block_reason": "The stored earnings date has passed and the next earnings date is not verified.",
                "event_date": event_date, "source": source, "confidence": "LOW — UNKNOWN",
                "certainty": "UNKNOWN",
            }

        text = f"{_fmt(event_date)} — Today" if days == 0 else f"{_fmt(event_date)} — {days} days away"
        label = (
            "Confirmed earnings" if certainty == "CONFIRMED" else
            "Corroborated earnings estimate" if certainty == "CORROBORATED" else
            "Estimated earnings date"
        )

        if days <= EARNINGS_HARD_BLOCK_DAYS:
            return {
                "text": text, "risk": True, "state": "HIGH", "block": True,
                "block_reason": (
                    f"{label} is {text}. New swing entries are blocked inside the "
                    f"{EARNINGS_HARD_BLOCK_DAYS}-day earnings danger window."
                ),
                "event_date": event_date, "source": source, "confidence": confidence,
                "certainty": certainty,
            }
        if days <= EARNINGS_CAUTION_DAYS:
            return {
                "text": text, "risk": True, "state": "CAUTION", "block": False,
                "block_reason": "", "event_date": event_date, "source": source,
                "confidence": confidence, "certainty": certainty,
            }
        return {
            "text": text, "risk": False, "state": "CLEAR", "block": False,
            "block_reason": "", "event_date": event_date, "source": source,
            "confidence": confidence, "certainty": certainty,
        }

    if last_date is not None and not pd.isna(last_date):
        event_date = pd.Timestamp(last_date).normalize()
        days_since = (market_day - event_date).days
        if 0 <= days_since <= RECENT_EARNINGS_GRACE_DAYS:
            text = (
                f"{_fmt(event_date)} — reported today" if days_since == 0 else
                f"{_fmt(event_date)} — reported {days_since} day{'s' if days_since != 1 else ''} ago"
            )
            return {
                "text": text, "risk": False, "state": "RECENT", "block": False,
                "block_reason": "", "event_date": event_date, "source": source,
                "confidence": "MEDIUM — NEXT DATE UNKNOWN", "certainty": "UNKNOWN",
            }
        return {
            "text": f"Last verified report: {_fmt(event_date)} — next earnings date not verified",
            "risk": False, "state": "UNKNOWN", "block": True,
            "block_reason": "The next earnings date could not be verified. Unknown event risk is not treated as safe.",
            "event_date": event_date, "source": source, "confidence": "LOW — UNKNOWN",
            "certainty": "UNKNOWN",
        }

    # Compatibility path for metadata created by an older cached app version.
    if legacy_date is not None and not pd.isna(legacy_date):
        event_date = pd.Timestamp(legacy_date).normalize()
        delta = (event_date - market_day).days
        if delta >= 0:
            temp = dict(metadata)
            temp["next_earnings_date"] = event_date
            temp["last_earnings_date"] = None
            temp.setdefault("earnings_certainty", "ESTIMATED")
            return evaluate_earnings_event(temp, today=market_day)
        if -delta <= RECENT_EARNINGS_GRACE_DAYS:
            temp = dict(metadata)
            temp["next_earnings_date"] = None
            temp["last_earnings_date"] = event_date
            return evaluate_earnings_event(temp, today=market_day)

    return {
        "text": "Unknown — earnings date not verified",
        "risk": False, "state": "UNKNOWN", "block": True,
        "block_reason": "Earnings timing could not be verified. Unknown event risk is not treated as safe.",
        "event_date": pd.NaT, "source": source, "confidence": "LOW — UNKNOWN",
        "certainty": "UNKNOWN",
    }

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
    """Return (is_valid, reason) using completed daily sessions only."""
    x = completed_session_frame(df)
    if x.empty:
        return False, "No valid OHLC price rows were returned."
    if len(x) < min_rows:
        return False, f"Only {len(x)} valid trading days were returned; at least {min_rows} are required."

    latest = x.iloc[-1]
    for col in ["Open", "High", "Low", "Close"]:
        if pd.isna(latest[col]) or not np.isfinite(latest[col]) or latest[col] <= 0:
            return False, f"Latest {col} value is invalid."

    return True, ""



@st.cache_resource(show_spinner=False)
def get_nyse_calendar():
    """Return the XNYS exchange calendar when the optional dependency is available."""
    if xcals is None:
        return None
    try:
        return xcals.get_calendar(NYSE_CALENDAR_NAME)
    except Exception:
        return None


def market_calendar_source():
    """Human-readable source for diagnostics and provider-health reporting."""
    return "exchange_calendars/XNYS" if get_nyse_calendar() is not None else "weekday fallback"


def _now_new_york(now=None):
    try:
        if now is None:
            return pd.Timestamp.now(tz="America/New_York")
        ts = pd.Timestamp(now)
        if ts.tzinfo is None:
            return ts.tz_localize("America/New_York")
        return ts.tz_convert("America/New_York")
    except Exception:
        return pd.Timestamp.now(tz="America/New_York")


def expected_latest_completed_us_session(now=None):
    """Return the latest US equity session safe to use for completed-daily signals.

    Phase 2A uses the XNYS exchange calendar when available, so weekends, exchange
    holidays and early closes are handled by the exchange schedule itself. A two-hour
    publication buffer is applied after the official session close before a same-day
    Yahoo daily bar is accepted as completed. If exchange_calendars is unavailable,
    the prior conservative weekday/18:00 ET behavior remains as a deployment-safe
    fallback and is surfaced through ``market_calendar_source()``.
    """
    now_ny = _now_new_york(now)
    session_date = now_ny.tz_localize(None).normalize()
    cal = get_nyse_calendar()

    if cal is not None:
        try:
            if cal.is_session(session_date):
                close_utc = cal.session_close(session_date)
                close_ny = pd.Timestamp(close_utc).tz_convert("America/New_York")
                ready_at = close_ny + pd.Timedelta(minutes=MARKET_DATA_PUBLICATION_BUFFER_MINUTES)
                if now_ny >= ready_at:
                    return session_date
                previous = cal.previous_session(session_date)
                return pd.Timestamp(previous).tz_localize(None).normalize()

            previous = cal.date_to_session(session_date, direction="previous")
            return pd.Timestamp(previous).tz_localize(None).normalize()
        except Exception:
            # Fall through to conservative legacy behavior if the calendar itself
            # cannot answer an out-of-range or malformed date request.
            pass

    # Deployment-safe fallback. This is intentionally conservative and is no longer
    # the preferred path once exchange_calendars is installed in the app environment.
    d = now_ny.normalize()
    if now_ny.weekday() < 5 and now_ny.hour >= 18:
        expected = d
    else:
        expected = d - pd.Timedelta(days=1)
    while expected.weekday() >= 5:
        expected -= pd.Timedelta(days=1)
    return expected.tz_localize(None).normalize()


def completed_session_frame(df, now=None):
    """Return only completed US daily bars for signal/scoring calculations.

    Yahoo can expose an in-progress same-day daily candle during the regular
    session. Swing signals must not use that partial bar because volume, close,
    RSI, momentum, ATR and EMA location are still changing. The raw provider
    frame may retain it, but the decision engine works only from sessions at or
    before the latest expected completed US session.
    """
    attrs = dict(getattr(df, "attrs", {}) or {}) if df is not None else {}
    x = sanitize_ohlcv(df)
    if x.empty or "Date" not in x.columns:
        return x

    x = x.copy()
    x["Date"] = pd.to_datetime(x["Date"], errors="coerce")
    x = x.dropna(subset=["Date"])
    try:
        if x["Date"].dt.tz is not None:
            x["Date"] = x["Date"].dt.tz_localize(None)
    except Exception:
        pass

    x["Date"] = x["Date"].dt.normalize()
    raw_latest = x["Date"].max() if not x.empty else pd.NaT
    cutoff = expected_latest_completed_us_session(now=now)
    x = x[x["Date"] <= cutoff].sort_values("Date").drop_duplicates("Date", keep="last").reset_index(drop=True)

    x.attrs.update(attrs)
    x.attrs["signal_session_cutoff"] = cutoff
    x.attrs["raw_latest_date"] = raw_latest
    x.attrs["excluded_incomplete_session"] = bool(pd.notna(raw_latest) and raw_latest > cutoff)
    x.attrs["market_calendar_source"] = market_calendar_source()
    return x


def market_data_freshness(df):
    x = completed_session_frame(df)
    if x.empty or "Date" not in x.columns:
        return False, pd.NaT, pd.NaT, "No valid dated OHLCV rows."

    latest = pd.to_datetime(x["Date"], errors="coerce").dropna().max()
    if pd.isna(latest):
        return False, pd.NaT, pd.NaT, "Latest market-data date is unavailable."

    latest = pd.Timestamp(latest).tz_localize(None).normalize()
    expected = expected_latest_completed_us_session()
    fresh = latest >= expected
    source = market_calendar_source()
    reason = (
        f"Latest completed daily bar: {latest:%d-%b-%Y}. Expected latest XNYS session: {expected:%d-%b-%Y} ({source})."
        if fresh else
        f"Latest completed daily bar: {latest:%d-%b-%Y}; expected at least {expected:%d-%b-%Y} ({source})."
    )
    return fresh, latest, expected, reason


def expected_recent_us_sessions(lookback_days=14):
    """Return the latest expected XNYS sessions, with a conservative fallback."""
    end = expected_latest_completed_us_session()
    cal = get_nyse_calendar()
    if cal is not None:
        try:
            # Use a generous calendar window, then take the exact number of exchange
            # sessions requested. This naturally excludes weekends and NYSE holidays.
            start = end - pd.Timedelta(days=max(45, lookback_days * 4))
            sessions = cal.sessions_in_range(start, end)
            return pd.DatetimeIndex([pd.Timestamp(d).tz_localize(None).normalize() for d in sessions[-lookback_days:]])
        except Exception:
            pass

    start = end - pd.Timedelta(days=lookback_days * 2)
    days = pd.date_range(start=start, end=end, freq="B")
    return pd.DatetimeIndex(days[-lookback_days:])

def missing_recent_sessions(df, lookback_days=14):
    x = completed_session_frame(df)
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
    """Return continuity status against expected exchange sessions."""
    missing = missing_recent_sessions(df, lookback_days=lookback_days)
    if not missing:
        return True, [], f"No missing XNYS sessions in the last {lookback_days} expected sessions ({market_calendar_source()})."

    formatted = ", ".join(pd.Timestamp(d).strftime("%d-%b-%Y") for d in missing)
    return False, missing, f"Missing recent session(s): {formatted}."



def data_confidence(df, recent_lookback=14):
    """
    Practical data-confidence model.

    HIGH:
      - latest completed session is current
      - no recent expected exchange-session gaps

    MEDIUM:
      - latest session is current
      - exactly one recent missing exchange session
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
            "message": f"Latest completed XNYS session is current and no recent exchange-session gaps were detected ({market_calendar_source()}).",
            "missing": [],
        }

    if len(missing) == 1:
        d = pd.Timestamp(missing[0]).strftime("%d-%b-%Y")
        return {
            "level": "MEDIUM",
            "score": 70,
            "block": False,
            "message": (
                f"Latest completed session is current, but one isolated recent data gap was detected: {d}. "
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


def trend_side(trend):
    """Map display trend labels to LONG/SHORT structural direction."""
    t = str(trend or "")
    if t in {"Strong bullish", "Bullish"}:
        return "LONG"
    if t in {"Strong bearish", "Bearish"}:
        return "SHORT"
    return ""


def directional_alignment(trend, momentum_score, rs_edge):
    """Return the strict directional state used by the actionability gate.

    Price structure, momentum and relative strength must point the same way.
    This prevents a bullish EMA structure with negative momentum/RS (or the
    inverse for shorts) from being treated as entry-ready.
    """
    side = trend_side(trend)
    mom = finite_or_nan(momentum_score)
    rs = finite_or_nan(rs_edge)

    if side == "LONG":
        if not np.isfinite(mom) or mom < 15:
            return "", "Bullish price structure lacks sufficient positive momentum (need Momentum Score ≥ +15)."
        if not np.isfinite(rs) or rs <= 0:
            return "", "Bullish price structure lacks positive relative strength versus SPY (RS Edge must be > 0)."
        return "LONG", ""

    if side == "SHORT":
        if not np.isfinite(mom) or mom > -15:
            return "", "Bearish price structure lacks sufficient negative momentum (need Momentum Score ≤ -15)."
        if not np.isfinite(rs) or rs >= 0:
            return "", "Bearish price structure lacks negative relative strength versus SPY (RS Edge must be < 0)."
        return "SHORT", ""

    return "", "EMA trend structure is mixed; directional structure must form before entry-location repair is actionable."


def assess_candidate_quality(trend, momentum_score, rs_edge, volume_ratio=np.nan, acceleration=""):
    """Direction-aware candidate-quality model used by ticker diagnostics.

    Candidate quality asks whether the name is worth watching; it does not
    override entry/actionability gates. Momentum and RS only earn points when
    they support the candidate direction. Opposing momentum/RS are penalties.
    """
    trend_dir = trend_side(trend)
    mom = finite_or_nan(momentum_score)
    rs = finite_or_nan(rs_edge)
    vol = finite_or_nan(volume_ratio)

    # For mixed price structure, strong momentum + RS may still make a watchlist
    # candidate, but it cannot become action-ready until structure aligns.
    candidate_dir = trend_dir
    if not candidate_dir:
        if np.isfinite(mom) and np.isfinite(rs) and mom >= 15 and rs > 0:
            candidate_dir = "LONG"
        elif np.isfinite(mom) and np.isfinite(rs) and mom <= -15 and rs < 0:
            candidate_dir = "SHORT"

    points = 0
    if str(trend) in {"Strong bullish", "Strong bearish"}:
        points += 2
    elif trend_dir:
        points += 1

    if candidate_dir == "LONG":
        if np.isfinite(mom):
            points += 2 if mom >= 70 else 1 if mom >= 40 else -2 if mom <= -15 else -1 if mom < 15 else 0
        if np.isfinite(rs):
            points += 2 if rs >= 15 else 1 if rs >= 5 else -2 if rs <= -10 else -1 if rs < 0 else 0
    elif candidate_dir == "SHORT":
        if np.isfinite(mom):
            points += 2 if mom <= -70 else 1 if mom <= -40 else -2 if mom >= 15 else -1 if mom > -15 else 0
        if np.isfinite(rs):
            points += 2 if rs <= -15 else 1 if rs <= -5 else -2 if rs >= 10 else -1 if rs > 0 else 0

    if np.isfinite(vol) and vol >= 1.2:
        points += 1
    if str(acceleration) == "Decelerating":
        points -= 1

    quality = "A+" if points >= 6 else "A" if points >= 5 else "B+" if points >= 4 else "B" if points >= 3 else "C"
    return {"quality": quality, "points": int(points), "direction": candidate_dir}


def unique_text_items(items):
    """De-duplicate explanatory bullets while preserving order."""
    out = []
    seen = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def directional_repair_requirements(trend, momentum_score, rs_edge):
    """Return every failed directional condition, not just the first one."""
    side = trend_side(trend)
    mom = finite_or_nan(momentum_score)
    rs = finite_or_nan(rs_edge)
    items = []

    if side == "LONG":
        if not np.isfinite(mom) or mom < 15:
            mom_text = "unavailable" if not np.isfinite(mom) else f"{mom:.1f}"
            items.append(f"Bullish price structure needs Momentum Score ≥ +15; current score is {mom_text}.")
        if not np.isfinite(rs) or rs <= 0:
            rs_text = "unavailable" if not np.isfinite(rs) else f"{rs:+.1f} pp"
            items.append(f"Bullish price structure needs positive RS Edge versus SPY; current RS Edge is {rs_text}.")
    elif side == "SHORT":
        if not np.isfinite(mom) or mom > -15:
            mom_text = "unavailable" if not np.isfinite(mom) else f"{mom:.1f}"
            items.append(f"Bearish price structure needs Momentum Score ≤ -15; current score is {mom_text}.")
        if not np.isfinite(rs) or rs >= 0:
            rs_text = "unavailable" if not np.isfinite(rs) else f"{rs:+.1f} pp"
            items.append(f"Bearish price structure needs negative RS Edge versus SPY; current RS Edge is {rs_text}.")
    else:
        items.append("EMA trend structure is mixed; directional price structure must align before an entry can qualify.")

    return unique_text_items(items)


def entry_caution_items(direction, ema20_distance_pct, stop_pct):
    """Soft caution bands inside the hard entry limits."""
    direction = str(direction or "").upper()
    dist = finite_or_nan(ema20_distance_pct)
    stop_value = finite_or_nan(stop_pct)
    items = []

    if direction == "LONG" and np.isfinite(dist) and NEAR_EXTENSION_CAUTION_PCT <= dist <= 8.0:
        items.append(
            f"Entry is near the no-chase limit: price is {dist:+.1f}% vs EMA20 "
            f"(hard block above +8.0%)."
        )
    elif direction == "SHORT" and np.isfinite(dist) and -8.0 <= dist <= -NEAR_EXTENSION_CAUTION_PCT:
        items.append(
            f"Entry is near the downside no-chase limit: price is {dist:+.1f}% vs EMA20 "
            f"(hard block below -8.0%)."
        )

    if np.isfinite(stop_value) and NEAR_STOP_CAUTION_PCT <= stop_value <= 0.10:
        items.append(
            f"Stop geometry is on the wide side: {stop_value:.1%} from the midpoint entry "
            f"(10% hard cap)."
        )
    return unique_text_items(items)


def entry_geometry_grade(stop_pct, rr_midpoint, cautions=None):
    """Grade price/risk geometry independently from candidate quality/actionability."""
    stop_value = finite_or_nan(stop_pct)
    rr_value = finite_or_nan(rr_midpoint)
    if not np.isfinite(stop_value):
        return "C — NOT READY"
    if stop_value <= 0.06 and np.isfinite(rr_value) and rr_value >= 2:
        grade = "A"
    elif stop_value <= 0.08:
        grade = "B+"
    else:
        grade = "B"
    if cautions:
        if grade == "A":
            grade = "B+"
        return f"{grade} — CAUTION"
    return grade


def trade_plan_r_metrics(direction, entry_low, entry_high, stop, target1, target2):
    """Return midpoint and full-entry-zone R multiples for transparent execution math."""
    vals = [finite_or_nan(v) for v in [entry_low, entry_high, stop, target1, target2]]
    if not all(np.isfinite(v) for v in vals):
        return {"mid": np.nan, "t1": np.nan, "t2": np.nan, "zone_min": np.nan, "zone_max": np.nan}
    lo, hi, stop_v, t1_v, t2_v = vals
    mid = (lo + hi) / 2.0
    d = str(direction or "").upper()

    def rr_for(entry, target):
        if d == "LONG":
            risk = entry - stop_v
            reward = target - entry
        elif d == "SHORT":
            risk = stop_v - entry
            reward = entry - target
        else:
            return np.nan
        if risk <= 0:
            return np.nan
        return reward / risk

    t1_r = rr_for(mid, t1_v)
    t2_r = rr_for(mid, t2_v)
    zone = [rr_for(lo, t2_v), rr_for(hi, t2_v)]
    zone = [v for v in zone if np.isfinite(v)]
    return {
        "mid": t2_r,
        "t1": t1_r,
        "t2": t2_r,
        "zone_min": min(zone) if zone else np.nan,
        "zone_max": max(zone) if zone else np.nan,
    }


def candidate_strengths_and_risks(diag):
    """Canonical direction-aware explainability for the Decision Summary."""
    trend = diag.get("Trend", "Mixed")
    mom = finite_or_nan(diag.get("Momentum Score"))
    rs = finite_or_nan(diag.get("RS Edge"))
    vol = finite_or_nan(diag.get("Volume Ratio"))
    candidate_dir = diag.get("Candidate Direction") or assess_candidate_quality(
        trend, mom, rs, vol, diag.get("Acceleration", "")
    )["direction"]

    strengths, risks = [], []
    if trend in {"Strong bullish", "Strong bearish"}:
        strengths.append(f"Strong trend: {trend}")
    elif trend in {"Bullish", "Bearish"}:
        strengths.append(f"Directional trend: {trend}")
    else:
        risks.append("EMA trend structure is mixed.")

    if candidate_dir == "LONG":
        if np.isfinite(rs):
            if rs >= 5:
                strengths.append(f"Relative strength edge vs SPY: {rs:+.1f} pp")
            elif rs <= 0:
                risks.append(f"Relative strength is not supporting the bullish setup: {rs:+.1f} pp vs SPY.")
        if np.isfinite(mom):
            if mom >= 40:
                strengths.append(f"Momentum Score: {mom:.1f}")
            elif mom < 15:
                risks.append(f"Momentum is insufficient for bullish alignment: {mom:.1f} (need ≥ +15).")
    elif candidate_dir == "SHORT":
        if np.isfinite(rs):
            if rs <= -5:
                strengths.append(f"Relative weakness vs SPY supports the short: {rs:+.1f} pp")
            elif rs >= 0:
                risks.append(f"Relative strength is not supporting the bearish setup: {rs:+.1f} pp vs SPY.")
        if np.isfinite(mom):
            if mom <= -40:
                strengths.append(f"Bearish Momentum Score: {mom:.1f}")
            elif mom > -15:
                risks.append(f"Momentum is insufficient for bearish alignment: {mom:.1f} (need ≤ -15).")
    else:
        if np.isfinite(mom) and abs(mom) >= 15:
            risks.append(f"Momentum ({mom:.1f}) is not aligned with a confirmed EMA trend structure.")
        if np.isfinite(rs) and abs(rs) >= 5:
            risks.append(f"RS Edge ({rs:+.1f} pp) is not yet paired with confirmed directional structure.")

    if np.isfinite(vol) and vol >= 1.2:
        strengths.append(f"Volume confirmation: {vol:.2f}x")
    elif np.isfinite(vol) and vol < 0.75:
        risks.append(f"Volume is weak: {vol:.2f}x")

    return unique_text_items(strengths), unique_text_items(risks)


def evaluate_entry_quality(direction, ema20_distance_pct, rsi14, stop_pct=np.nan, event_block=False, event_reason="", event_unknown=False, structure_reason=""):
    """Canonical price-entry gate shared by scanner and ticker diagnostics.

    `ema20_distance_pct` is expressed in percentage points (e.g. +8.5, not 0.085).
    Scanner calls may omit stop_pct; ticker diagnostics add stop geometry and event risk.
    """
    direction = str(direction or "").upper()
    dist = finite_or_nan(ema20_distance_pct)
    rsi_value = finite_or_nan(rsi14)
    stop_value = finite_or_nan(stop_pct)

    # Gate priority: event risk precedes directional structure; data confidence
    # is handled one layer above this function.
    if event_block:
        return {
            "state": "WAIT — VERIFY EVENT" if event_unknown else "WAIT — EVENT RISK",
            "quality": "WAIT", "block": True,
            "reason": event_reason or "Earnings/event timing is not safely verified.",
        }

    if direction not in {"LONG", "SHORT"}:
        return {
            "state": "WAIT FOR STRUCTURE", "quality": "WAIT", "block": True,
            "reason": structure_reason or "Trend, momentum and relative strength are not directionally aligned.",
        }

    if not np.isfinite(dist) or not np.isfinite(rsi_value):
        return {"state": "NO TRADE", "quality": "UNKNOWN", "block": True, "reason": "Entry location could not be validated."}

    if direction == "LONG" and (dist > 8.0 or rsi_value >= 75):
        return {
            "state": "WAIT FOR PULLBACK", "quality": "WAIT", "block": True,
            "reason": f"Extended: price is {dist:+.1f}% vs EMA20 and RSI is {rsi_value:.1f}. Do not chase.",
        }
    if direction == "SHORT" and (dist < -8.0 or rsi_value <= 25):
        return {
            "state": "WAIT FOR BOUNCE", "quality": "WAIT", "block": True,
            "reason": f"Oversold: price is {dist:+.1f}% vs EMA20 and RSI is {rsi_value:.1f}. Do not chase weakness.",
        }

    if np.isfinite(stop_value):
        if stop_value > 0.10:
            return {
                "state": "WAIT FOR BETTER ENTRY", "quality": "WAIT", "block": True,
                "reason": f"Required stop is {stop_value:.1%}, above the 10% hard cap.",
            }
        if stop_value < 0.02:
            return {
                "state": "WAIT FOR STRUCTURE", "quality": "WAIT", "block": True,
                "reason": f"Required stop is only {stop_value:.1%}, too tight for a normal swing setup.",
            }

    return {"state": "ACTIONABLE", "quality": "PASS", "block": False, "reason": ""}


def chart_frame_for_timeframe(base_df, timeframe):
    base = completed_session_frame(base_df).copy()
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

def scanner_frame_quality(df):
    """Return a comparable quality tuple for bulk-scan repair decisions.

    Higher is better: usable/non-blocked data, confidence score, latest completed
    session, then history length. This lets a targeted retry replace the primary
    frame only when it actually improves data quality.
    """
    valid, _ = price_data_status(df, min_rows=126)
    if not valid:
        return (0, 0, -1, 0)
    x = completed_session_frame(df)
    confidence = data_confidence(x, recent_lookback=14)
    latest = pd.to_datetime(x["Date"], errors="coerce").max() if "Date" in x.columns else pd.NaT
    latest_ord = int(pd.Timestamp(latest).value) if pd.notna(latest) else -1
    usable = 0 if bool(confidence.get("block", True)) else 1
    return (usable, int(confidence.get("score", 0)), latest_ord, int(len(x)))


def scanner_frame_usable(df):
    return scanner_frame_quality(df)[0] == 1


def better_scanner_frame(current_df, candidate_df):
    """Keep a repaired frame only when it improves scanner data quality."""
    return candidate_df if scanner_frame_quality(candidate_df) > scanner_frame_quality(current_df) else current_df


def compute_record(symbol, company, sector, df):
    valid, _ = price_data_status(df, min_rows=126)
    if not valid:
        return None

    x = completed_session_frame(df)
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

    confidence = data_confidence(x, recent_lookback=14)
    latest_date = pd.to_datetime(x["Date"], errors="coerce").max() if "Date" in x.columns else pd.NaT

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
        "Latest Date": latest_date,
        "Price Data Confidence": confidence["level"],
        "Price Data Confidence Score": confidence["score"],
        "Price Data Block": bool(confidence["block"]),
        "Price Data Note": confidence["message"],
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
    """Bulk scan with provider-health detection and targeted recovery.

    Phase 2A recovery policy:
      1) Download the normal Yahoo batch.
      2) If only a minority of symbols are bad/stale, retry only that subset.
      3) If a whole batch appears unhealthy, perform one conservative provider-level
         retry rather than hammering every ticker individually.
      4) Only a small remaining tail is eligible for individual Yahoo->Stooq repair.

    This preserves provider friendliness while rescuing isolated failures and stale
    bars that would otherwise unnecessarily lower scanner coverage.
    """
    if universe_df.empty:
        return pd.DataFrame(), [], {
            "status": "NO_UNIVERSE",
            "provider": "Yahoo",
            "coverage": 0.0,
            "usable_coverage": 0.0,
            "message": "Universe is empty.",
            "calendar_source": market_calendar_source(),
        }

    universe_df = universe_df.drop_duplicates("Ticker").reset_index(drop=True)
    metadata = universe_df.set_index("Ticker").to_dict("index")
    tickers = universe_df["Ticker"].tolist()

    records = []
    failures = []
    provider_batches_failed = 0
    total_batches = max(1, int(np.ceil(len(tickers) / YAHOO_BATCH_SIZE)))

    targeted_retry_symbols = 0
    targeted_retry_repaired = 0
    individual_repair_attempts = 0
    individual_repair_repaired = 0
    symbol_diagnostics = {}

    def diag_touch(symbol, batch_no, initial_note=""):
        item = symbol_diagnostics.setdefault(
            symbol,
            {
                "Ticker": symbol,
                "Batch": int(batch_no),
                "Initial Issue": initial_note,
                "Recovery Path": [],
                "Final State": "PENDING",
                "Confidence": "",
                "Latest Session": "",
                "Final Note": "",
            },
        )
        if initial_note and not item.get("Initial Issue"):
            item["Initial Issue"] = initial_note
        return item

    for batch_no, start_i in enumerate(range(0, len(tickers), YAHOO_BATCH_SIZE), start=1):
        batch = tickers[start_i : start_i + YAHOO_BATCH_SIZE]
        status_box.info(
            f"Yahoo bulk download {batch_no}/{total_batches} "
            f"({start_i + 1}-{min(start_i + len(batch), len(tickers))} of {len(tickers)})"
        )

        raw = download_batch_cached(tuple(batch), conservative=False)
        frames = {symbol: split_batch_result(raw, symbol) for symbol in batch}
        problem_symbols = [symbol for symbol in batch if not scanner_frame_usable(frames[symbol])]
        for symbol in problem_symbols:
            d0 = scanner_frame_diagnostic(frames[symbol])
            item = diag_touch(symbol, batch_no, d0.get("note", "Initial Yahoo batch frame was unusable."))
            item["Recovery Path"].append("Yahoo batch issue")

        broad_problem_threshold = max(2, int(np.ceil(len(batch) * 0.20)))
        broad_problem = len(problem_symbols) >= broad_problem_threshold

        if problem_symbols:
            if broad_problem:
                # Provider-level retry: one conservative full-batch request. This is
                # preferable to launching many individual requests during an outage.
                status_box.info(
                    f"Yahoo batch {batch_no}/{total_batches} is broadly degraded; "
                    "running one conservative provider retry."
                )
                raw_retry = download_batch_cached(tuple(batch), conservative=True)
                for symbol in batch:
                    retry_df = split_batch_result(raw_retry, symbol)
                    if symbol in problem_symbols:
                        diag_touch(symbol, batch_no)["Recovery Path"].append("Conservative Yahoo batch retry")
                    frames[symbol] = better_scanner_frame(frames[symbol], retry_df)
            else:
                # Isolated failures: retry only the affected subset.
                retry_subset = problem_symbols[:SCAN_TARGETED_RETRY_MAX_SYMBOLS]
                max_fraction = max(1, int(np.ceil(len(batch) * SCAN_TARGETED_RETRY_MAX_FRACTION)))
                retry_subset = retry_subset[:max_fraction]
                if retry_subset:
                    targeted_retry_symbols += len(retry_subset)
                    status_box.info(
                        f"Repairing {len(retry_subset)} isolated Yahoo symbol(s) in batch "
                        f"{batch_no}/{total_batches}."
                    )
                    retry_raw = download_batch_cached(tuple(retry_subset), conservative=True)
                    for symbol in retry_subset:
                        before = scanner_frame_quality(frames[symbol])
                        retry_df = split_batch_result(retry_raw, symbol)
                        diag_touch(symbol, batch_no)["Recovery Path"].append("Targeted Yahoo retry")
                        frames[symbol] = better_scanner_frame(frames[symbol], retry_df)
                        if scanner_frame_quality(frames[symbol]) > before:
                            targeted_retry_repaired += 1

        # After Yahoo retry, allow only a small tail of unresolved symbols to use
        # the independent individual route (Yahoo primary + Stooq gap repair).
        remaining = [symbol for symbol in batch if not scanner_frame_usable(frames[symbol])]
        remaining_total_capacity = max(0, SCAN_INDIVIDUAL_REPAIR_MAX_TOTAL - individual_repair_attempts)
        individual_subset = remaining[: min(SCAN_INDIVIDUAL_REPAIR_MAX_PER_BATCH, remaining_total_capacity)]

        # Never perform individual fallback when a large fraction of the provider
        # batch is still broken; that condition is treated as provider degradation.
        individual_tail_limit = min(
            SCAN_INDIVIDUAL_REPAIR_MAX_PER_BATCH,
            max(1, int(np.ceil(len(batch) * 0.10))),
        )
        if len(remaining) <= individual_tail_limit:
            for symbol in individual_subset:
                individual_repair_attempts += 1
                before = scanner_frame_quality(frames[symbol])
                repaired_df = download_one(symbol, "1y")
                diag_touch(symbol, batch_no)["Recovery Path"].append("Individual Yahoo → Stooq repair")
                frames[symbol] = better_scanner_frame(frames[symbol], repaired_df)
                if scanner_frame_quality(frames[symbol]) > before:
                    individual_repair_repaired += 1

        usable_in_batch = sum(1 for symbol in batch if scanner_frame_usable(frames[symbol]))
        if usable_in_batch < max(2, int(len(batch) * 0.20)):
            provider_batches_failed += 1

        for symbol in batch:
            df = frames[symbol]
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
                item = diag_touch(symbol, batch_no, "Scanner record could not be built from the final price frame.")
                if "Record computation failed" not in item["Recovery Path"]:
                    item["Recovery Path"].append("Record computation failed")

            if symbol in symbol_diagnostics:
                final_diag = scanner_frame_diagnostic(df)
                item = symbol_diagnostics[symbol]
                item["Final State"] = "USABLE" if final_diag.get("usable") and rec is not None else "UNRESOLVED"
                item["Confidence"] = final_diag.get("confidence", "")
                item["Latest Session"] = final_diag.get("latest_session", "")
                item["Final Note"] = final_diag.get("note", "")
                if not item["Recovery Path"]:
                    item["Recovery Path"].append("No recovery attempted")

        progress_bar.progress(min(batch_no / total_batches, 1.0))

        # Fail fast when the first two large batches are essentially unusable even
        # after the controlled recovery sequence.
        if batch_no >= 2 and provider_batches_failed == batch_no and len(records) < 5:
            break

    diagnostics = []
    for symbol in sorted(symbol_diagnostics):
        item = dict(symbol_diagnostics[symbol])
        item["Recovery Path"] = " → ".join(item.get("Recovery Path", []))
        diagnostics.append(item)

    coverage = len(records) / max(len(tickers), 1)
    usable_records = [r for r in records if not bool(r.get("Price Data Block", True))]
    usable_coverage = len(usable_records) / max(len(tickers), 1)
    repair_summary = (
        f"Targeted Yahoo retry: {targeted_retry_repaired}/{targeted_retry_symbols} improved; "
        f"individual repair: {individual_repair_repaired}/{individual_repair_attempts} improved."
    )

    if coverage < 0.20 or usable_coverage < 0.20:
        health = {
            "status": "PROVIDER_FAILURE",
            "provider": "Yahoo + targeted repair",
            "coverage": coverage,
            "usable_coverage": usable_coverage,
            "calendar_source": market_calendar_source(),
            "provider_batches_failed": provider_batches_failed,
            "diagnostics": diagnostics,
            "unresolved_symbols": list(failures),
            "targeted_retry_symbols": targeted_retry_symbols,
            "targeted_retry_repaired": targeted_retry_repaired,
            "individual_repair_attempts": individual_repair_attempts,
            "individual_repair_repaired": individual_repair_repaired,
            "message": (
                f"Bulk data integrity collapsed: raw coverage {len(records)}/{len(tickers)} "
                f"({coverage:.0%}); fresh/usable coverage {len(usable_records)}/{len(tickers)} "
                f"({usable_coverage:.0%}). Scan aborted to protect data integrity. {repair_summary}"
            ),
        }
    elif coverage < 0.80 or usable_coverage < 0.80:
        health = {
            "status": "DEGRADED",
            "provider": "Yahoo + targeted repair",
            "coverage": coverage,
            "usable_coverage": usable_coverage,
            "calendar_source": market_calendar_source(),
            "provider_batches_failed": provider_batches_failed,
            "diagnostics": diagnostics,
            "unresolved_symbols": list(failures),
            "targeted_retry_symbols": targeted_retry_symbols,
            "targeted_retry_repaired": targeted_retry_repaired,
            "individual_repair_attempts": individual_repair_attempts,
            "individual_repair_repaired": individual_repair_repaired,
            "message": (
                f"Bulk data is degraded: raw coverage {len(records)}/{len(tickers)} ({coverage:.0%}); "
                f"fresh/usable {len(usable_records)}/{len(tickers)} ({usable_coverage:.0%}). {repair_summary}"
            ),
        }
    else:
        health = {
            "status": "HEALTHY",
            "provider": "Yahoo + targeted repair",
            "coverage": coverage,
            "usable_coverage": usable_coverage,
            "calendar_source": market_calendar_source(),
            "provider_batches_failed": provider_batches_failed,
            "diagnostics": diagnostics,
            "unresolved_symbols": list(failures),
            "targeted_retry_symbols": targeted_retry_symbols,
            "targeted_retry_repaired": targeted_retry_repaired,
            "individual_repair_attempts": individual_repair_attempts,
            "individual_repair_repaired": individual_repair_repaired,
            "message": (
                f"Bulk coverage healthy: {len(records)}/{len(tickers)} ({coverage:.0%}); "
                f"fresh/usable {len(usable_records)}/{len(tickers)} ({usable_coverage:.0%}). {repair_summary}"
            ),
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
        df = completed_session_frame(download_one(symbol, "1y"))
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

    vix_df = completed_session_frame(download_one("^VIX", "6mo"))
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

def build_ranked_master(df):
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
    spy_df = completed_session_frame(download_one("SPY", "1y"))
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

    x["RS Edge"] = x.apply(rs_composite, axis=1)
    x["RS Rating"] = (
        x["RS Edge"]
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
    if "Price Data Block" in x.columns:
        price_confidence_ok = ~x["Price Data Block"].fillna(True).astype(bool)
    else:
        price_confidence_ok = pd.Series(True, index=x.index)

    x["Tradeable"] = price_ok & liquidity_ok & history_ok & atr_ok & data_ok & price_confidence_ok

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
        if bool(row.get("Price Data Block", False)):
            reasons.append("LOW price-data confidence")
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

    long_rs = (x["RS Rating"] >= 70) & (x["RS Edge"] > 0)
    short_rs = (x["RS Rating"] <= 31) & (x["RS Edge"] < 0)

    # Strict directional alignment used by scanner grading/actionability.
    aligned_long = long_trend & (x["Momentum Score"] >= 15) & (x["RS Edge"] > 0)
    aligned_short = short_trend & (x["Momentum Score"] <= -15) & (x["RS Edge"] < 0)

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
        + np.where(long_rs, 20, np.where((x["RS Rating"] >= 55) & (x["RS Edge"] > 0), 10, 0))
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
        + np.where(short_rs, 20, np.where((x["RS Rating"] <= 45) & (x["RS Edge"] < 0), 10, 0))
        + np.where((x["Momentum Score"] <= -40) & short_persistence, 15,
                   np.where(x["Momentum Score"] <= -25, 10, np.where(x["Momentum Score"] <= -15, 5, 0)))
        + np.where(liquidity_ok, 10, 0)
        + np.where(volume_strong, 10, np.where(volume_healthy, 6, 0))
        + np.where(short_location, 10, 0)
        + np.where(atr_ok, 5, 0)
        + np.where(short_regime, 5, 0)
    )

    # Candidate quality is direction-aware. Trend structure determines which
    # side's score is relevant; mixed structure is capped below watchlist grade.
    mixed_score = np.minimum(np.maximum(long_score, short_score), 64)
    x["Quality Score"] = np.where(
        long_trend,
        long_score,
        np.where(short_trend, short_score, mixed_score),
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
    x.loc[aligned_long, "Setup Type"] = "Trend Continuation"
    x.loc[aligned_short, "Setup Type"] = "Trend Continuation Short"
    x.loc[long_pullback, "Setup Type"] = "Pullback to Trend"
    x.loc[short_pullback, "Setup Type"] = "Rally into Resistance"
    x.loc[long_breakout, "Setup Type"] = "Breakout"
    x.loc[short_breakdown, "Setup Type"] = "Breakdown"

    # ---------------------------
    # Professional grade
    # ---------------------------
    x["Setup"] = "Avoid"
    x.loc[x["Tradeable"] & aligned_long & (x["Quality Score"] >= 90), "Setup"] = "A+ Long"
    x.loc[x["Tradeable"] & aligned_long & x["Quality Score"].between(82, 89.999), "Setup"] = "A Long"
    x.loc[x["Tradeable"] & aligned_long & x["Quality Score"].between(74, 81.999), "Setup"] = "B+ Long"
    x.loc[x["Tradeable"] & aligned_long & x["Quality Score"].between(65, 73.999), "Setup"] = "Long Watch"

    x.loc[x["Tradeable"] & aligned_short & (x["Quality Score"] >= 90), "Setup"] = "A+ Short"
    x.loc[x["Tradeable"] & aligned_short & x["Quality Score"].between(82, 89.999), "Setup"] = "A Short"
    x.loc[x["Tradeable"] & aligned_short & x["Quality Score"].between(74, 81.999), "Setup"] = "B+ Short"
    x.loc[x["Tradeable"] & aligned_short & x["Quality Score"].between(65, 73.999), "Setup"] = "Short Watch"

    x.loc[x["Momentum Score"].abs() < 15, "Setup"] = "Neutral"

    # Canonical price-entry gate. Scanner does not fetch earnings metadata for every
    # symbol; final event verification remains part of the ticker diagnostic.
    entry_results = []
    for idx, row in x.iterrows():
        if bool(aligned_long.loc[idx]):
            direction = "LONG"
            structure_reason = ""
        elif bool(aligned_short.loc[idx]):
            direction = "SHORT"
            structure_reason = ""
        else:
            direction = ""
            if bool(long_trend.loc[idx]):
                if row.get("Momentum Score", 0) < 15:
                    structure_reason = "Bullish trend lacks sufficient positive momentum (need ≥ +15)."
                else:
                    structure_reason = "Bullish trend lacks positive RS Edge versus SPY."
            elif bool(short_trend.loc[idx]):
                if row.get("Momentum Score", 0) > -15:
                    structure_reason = "Bearish trend lacks sufficient negative momentum (need ≤ -15)."
                else:
                    structure_reason = "Bearish trend lacks negative RS Edge versus SPY."
            else:
                structure_reason = "EMA trend structure is mixed; wait for directional structure."
        entry_results.append(
            evaluate_entry_quality(
                direction,
                row.get("EMA20 Distance %", np.nan),
                row.get("RSI14", np.nan),
                structure_reason=structure_reason,
            )
        )
    x["Price Entry State"] = [r["state"] for r in entry_results]
    price_data_block = x.get("Price Data Block", pd.Series(False, index=x.index)).fillna(True).astype(bool)
    raw_entry_pass = pd.Series([not r["block"] for r in entry_results], index=x.index)
    x["Price Entry Gate Pass"] = raw_entry_pass & ~price_data_block

    # Scanner candidate gate: only graded A+/A/B+ setups can advance to the
    # final VERIFY EVENT + STOP stage. Lower-quality/watch setups stay WAIT.
    scanner_candidate_ok = x["Setup"].astype(str).str.startswith(("A+ ", "A ", "B+ "))
    x["Candidate Quality Gate Pass"] = scanner_candidate_ok & x["Tradeable"].fillna(False).astype(bool)
    x["Entry Quality"] = np.where(
        price_data_block,
        "N/A — DATA ISSUE",
        np.where(raw_entry_pass, "PRICE READY", [r["quality"] for r in entry_results]),
    )
    x["Entry Block Reason"] = [r["reason"] for r in entry_results]
    x.loc[price_data_block, "Entry Block Reason"] = x.loc[price_data_block, "Price Data Note"].fillna(
        "LOW price-data confidence"
    ) if "Price Data Note" in x.columns else "LOW price-data confidence"
    x["Scanner Action"] = np.where(
        price_data_block,
        "DATA BLOCK",
        np.where(
            ~x["Candidate Quality Gate Pass"],
            "WAIT FOR QUALITY",
            np.where(x["Price Entry Gate Pass"], "VERIFY EVENT + STOP", x["Price Entry State"]),
        ),
    )

    # Explainability columns.
    x["Trend Check"] = np.where(long_trend | short_trend, "✅", "❌")
    x["Momentum Check"] = np.where(
        ((aligned_long & (x["Momentum Score"] >= 25)) | (aligned_short & (x["Momentum Score"] <= -25))),
        "✅", "❌"
    )
    x["RS Check"] = np.where((aligned_long & long_rs) | (aligned_short & short_rs), "✅", "❌")
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
        if not bool(row.get("Price Entry Gate Pass", False)) and row.get("Entry Block Reason"):
            note += " Candidate quality is separate from entry quality: WAIT — " + str(row.get("Entry Block Reason"))
        if strengths:
            note += " Strengths: " + ", ".join(strengths) + "."
        if weaknesses:
            note += " Improve/monitor: " + ", ".join(weaknesses) + "."
        return note

    x["Setup Note"] = x.apply(quality_note, axis=1)

    # Regime alignment.
    x["Regime Aligned"] = x.apply(is_regime_aligned, axis=1)


    # Ranking is now dominated by the professional Quality Score.
    # RS and volume are tie-breakers only.
    directional_rs_tiebreak = np.where(
        long_trend,
        x["RS Edge"].fillna(0).clip(-10, 10),
        np.where(short_trend, -x["RS Edge"].fillna(0).clip(-10, 10), 0),
    )
    x["Adjusted Score"] = (
        x["Quality Score"]
        + directional_rs_tiebreak * 0.20
        + (x["Volume Ratio"].fillna(1) - 1).clip(-0.5, 1.5) * 2.0
    )
    x["Adjusted Score"] = x["Adjusted Score"].replace([np.inf, -np.inf], np.nan).fillna(-9999.0)
    ranks = x["Adjusted Score"].rank(method="min", ascending=False, na_option="bottom")
    x["Rank"] = ranks.fillna(len(x) + 1).round().astype("Int64")
    return x


def apply_scanner_filters(ranked_df, min_composite, min_volume, rsi_low, rsi_high):
    """Cheap UI-only filter layer applied to the cached/session-ranked master scan."""
    if ranked_df.empty:
        return ranked_df
    x = ranked_df.copy()

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
    return x


def compute_search_diagnostic(symbol, company, df, metadata):
    """Decision-oriented swing diagnostic from validated daily price history."""
    valid, reason = price_data_status(df, min_rows=126)
    if not valid:
        return {"Data Valid": False, "Data Error": reason}

    x = completed_session_frame(df)
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
    prev_momentum_score = np.nan
    if all(pd.notna(v) for v in [prev_daily, prev_weekly, prev_monthly]):
        prev_momentum_score = prev_daily * 0.40 + prev_weekly * 0.35 + prev_monthly * 0.25
        acceleration_delta = composite - prev_momentum_score

        same_direction = (
            (composite > 0 and prev_momentum_score > 0) or
            (composite < 0 and prev_momentum_score < 0)
        )
        magnitude_change = abs(composite) - abs(prev_momentum_score)

        if same_direction and magnitude_change >= 10:
            acceleration = "Accelerating"
        elif same_direction and magnitude_change <= -10:
            acceleration = "Decelerating"
        elif not same_direction and abs(composite) >= 15:
            acceleration = "Direction Shift"
        else:
            acceleration = "Stable"
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
    spy = completed_session_frame(download_one("SPY", "1y"))
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

    next_earnings_date = metadata.get("next_earnings_date")
    last_earnings_date = metadata.get("last_earnings_date")
    earnings_date = metadata.get("earnings_date")
    fundamental_data_confidence = metadata.get("fundamental_data_confidence", "LOW") or "LOW"

    earnings_event = evaluate_earnings_event(metadata)
    earnings_text = earnings_event["text"]
    earnings_risk = bool(earnings_event["risk"])
    earnings_risk_state = earnings_event["state"]
    earnings_event_block = bool(earnings_event["block"])
    earnings_event_block_reason = earnings_event["block_reason"]
    earnings_source = earnings_event["source"]
    event_data_confidence = earnings_event["confidence"]
    earnings_certainty = earnings_event.get("certainty", "UNKNOWN")

    high20 = float(x["High"].iloc[-21:-1].max()) if len(x) >= 21 else close
    low20 = float(x["Low"].iloc[-21:-1].min()) if len(x) >= 21 else close

    # Phase 1.3 strict alignment: structure + momentum + RS must point together.
    aligned_direction, alignment_reason = directional_alignment(trend, composite, rs_score)
    long_bias = aligned_direction == "LONG"
    short_bias = aligned_direction == "SHORT"
    candidate_assessment = assess_candidate_quality(trend, composite, rs_score, vol_ratio, acceleration)
    candidate_quality = candidate_assessment["quality"]

    # v6.9 Tradeability & Risk Engine
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

    # Shared Phase-1 entry gate is authoritative for extension, stop geometry and event risk.
    gate_direction = "LONG" if long_bias else "SHORT" if short_bias else ""
    entry_gate = evaluate_entry_quality(
        gate_direction,
        dist_ema20 * 100.0 if np.isfinite(dist_ema20) else np.nan,
        rsi14,
        stop_pct=stop_pct,
        event_block=earnings_event_block,
        event_reason=earnings_event_block_reason,
        event_unknown=earnings_risk_state in {"UNKNOWN", "WINDOW"},
        structure_reason=alignment_reason,
    )
    # Preserve the independent price/risk geometry assessment even when candidate
    # quality later blocks actionability. This allows e.g. C Candidate + B+ entry
    # geometry to display honestly while still producing NO TRADE / WAIT FOR QUALITY.
    entry_geometry_pass = not entry_gate["block"]
    entry_cautions = entry_caution_items(
        gate_direction,
        dist_ema20 * 100.0 if np.isfinite(dist_ema20) else np.nan,
        stop_pct,
    ) if gate_direction else []
    entry_geometry_quality = entry_geometry_grade(stop_pct, rr, entry_cautions) if entry_geometry_pass else "C — NOT READY"

    # Canonical action gate priority:
    # Event → Candidate Quality → Directional Structure → Location → Stop/R:R.
    if earnings_event_block:
        authoritative_gate = entry_gate
    elif candidate_quality not in MIN_ACTIONABLE_CANDIDATE_GRADES:
        authoritative_gate = {
            "state": "WAIT FOR QUALITY",
            "quality": "WAIT",
            "block": True,
            "reason": (
                f"Candidate Quality is {candidate_quality}; minimum B+ quality is required "
                "before a new swing entry can become actionable."
            ),
        }
    else:
        authoritative_gate = entry_gate

    if authoritative_gate["block"]:
        bias = "WAIT"
        trade_state = authoritative_gate["state"]
        trade_block_reason = authoritative_gate["reason"]
        # Do not publish actionable levels when any higher-level gate blocks.
        entry_low = entry_high = stop = target1 = target2 = np.nan
    entry_quality = "PASS" if trade_state == "ACTIONABLE" else "WAIT"

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
        verdict = "C — WAIT / EVENT RISK" if trade_state == "WAIT — EVENT RISK" else "C — WAIT / MIXED"

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
    if extension == "Extended" and (composite > 0 or trend_side(trend) == "LONG"):
        chase_risk = "Elevated"
    elif extension == "Oversold" and (composite < 0 or trend_side(trend) == "SHORT"):
        chase_risk = "Elevated"

    if aligned_direction == "LONG":
        trade_comment = "Trend, momentum and relative strength are constructive on completed daily bars."
    elif aligned_direction == "SHORT":
        trade_comment = "Trend, momentum and relative weakness are constructive for shorts on completed daily bars."
    elif trend_side(trend) == "LONG":
        trade_comment = "Price trend is bullish, but momentum and/or relative strength are not sufficiently aligned."
    elif trend_side(trend) == "SHORT":
        trade_comment = "Price trend is bearish, but momentum and/or relative weakness are not sufficiently aligned."
    elif composite >= 15 and np.isfinite(rs_score) and rs_score > 0:
        trade_comment = "Momentum and relative strength are constructive, but EMA trend structure is not yet fully aligned."
    elif composite <= -15 and np.isfinite(rs_score) and rs_score < 0:
        trade_comment = "Bearish momentum and relative weakness are constructive, but EMA trend structure is not yet fully aligned."
    else:
        trade_comment = "Trend, momentum and relative strength are not sufficiently aligned. Waiting for structure is preferable."

    if chase_risk == "Elevated":
        if extension == "Extended":
            trade_comment += " Price is extended versus EMA20; do not chase while the higher-priority gate remains unresolved."
        else:
            trade_comment += " Price is stretched lower; do not chase weakness while the higher-priority gate remains unresolved."

    if trade_state != "ACTIONABLE" and trade_block_reason:
        trade_comment += f" Current action remains WAIT because: {trade_block_reason}"

    # Transparent R multiples are calculated from published actionable levels only.
    r_metrics = trade_plan_r_metrics(bias, entry_low, entry_high, stop, target1, target2)

    return {
        "Data Valid": True,
        "Data Error": "",
        "Signal Session": pd.Timestamp(latest["Date"]).normalize() if "Date" in latest.index and pd.notna(latest["Date"]) else pd.NaT,
        "In-Progress Session Excluded": bool(x.attrs.get("excluded_incomplete_session", False)),
        "Raw Latest Date": x.attrs.get("raw_latest_date", pd.NaT),
        "Ticker": symbol, "Company": company, "Close": close,
        "Candidate Quality": candidate_assessment["quality"],
        "Candidate Points": candidate_assessment["points"],
        "Candidate Direction": candidate_assessment["direction"],
        "Daily": daily, "Weekly": weekly, "Monthly": monthly, "Momentum Score": composite,
        "Momentum Grade": momentum_grade,
        "Acceleration": acceleration, "Acceleration Delta": acceleration_delta,
        "Previous Momentum Score": prev_momentum_score,
        "Trend": trend, "RSI14": rsi14,
        "Volume Ratio": vol_ratio, "Extension": extension,
        "Distance EMA20": dist_ema20,
        "RS vs SPY": rs_text,
        "RS 1M vs SPY": rs_1m,
        "RS 3M vs SPY": rs_3m,
        "RS 6M vs SPY": rs_6m,
        "RS Edge": rs_score,
        "Chase Risk": chase_risk,
        "Trade Comment": trade_comment,
        "Sector": sector, "Industry": industry, "Earnings": earnings_text,
        "Earnings Risk": earnings_risk, "Earnings Risk State": earnings_risk_state,
        "Earnings Date": earnings_date, "Next Earnings Date": next_earnings_date,
        "Last Earnings Date": last_earnings_date, "Earnings Source": earnings_source,
        "Earnings Certainty": earnings_certainty,
        "Event Data Confidence": event_data_confidence,
        "Fundamental Data Confidence": fundamental_data_confidence,
        "Fundamental Sources": metadata.get("fundamental_sources", {}),
        "Fundamental Field Status": metadata.get("fundamental_field_status", {}),
        "Fundamental Field Notes": metadata.get("fundamental_field_notes", {}),
        "Event Sources": metadata.get("event_sources", []),
        "Event Conflict": metadata.get("event_conflict", ""),
        "Event Window Note": metadata.get("event_window_note", ""),
        "Metadata Retrieved At": metadata.get("metadata_retrieved_at", ""),
        "Directional Alignment": aligned_direction,
        "Directional Alignment Reason": alignment_reason,
        "Entry Quality": entry_quality, "Bias": bias, "Verdict": verdict,
        "Entry Geometry Pass": entry_geometry_pass,
        "Entry Geometry Quality": entry_geometry_quality,
        "Entry Cautions": entry_cautions,
        "Entry Low": entry_low, "Entry High": entry_high, "Stop": stop,
        "Target 1": target1, "Target 2": target2, "RR": rr,
        "RR Midpoint": r_metrics["mid"], "T1 R": r_metrics["t1"], "T2 R": r_metrics["t2"],
        "RR Zone Min": r_metrics["zone_min"], "RR Zone Max": r_metrics["zone_max"],
        "Trade State": trade_state,
        "Trade Block Reason": trade_block_reason,
        "Stop %": stop_pct,
        "EMA20": ema20, "EMA50": ema50, "EMA200": ema200,
        "ATR14": atr,
        "Latest High": float(latest["High"]) if pd.notna(latest["High"]) else np.nan,
        "Latest Low": float(latest["Low"]) if pd.notna(latest["Low"]) else np.nan,
        "High20": high20, "Low20": low20,
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
            completed_for_meta = completed_session_frame(df)
            fallback_close = (
                float(completed_for_meta["Close"].iloc[-1])
                if not completed_for_meta.empty and "Close" in completed_for_meta.columns else np.nan
            )
            metadata = get_company_snapshot(selected_ticker, fallback_close)
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
                    f"🟢 **Price Data Confidence: HIGH** — {confidence['message']} "
                    f"Provider: **{data_provider}** • Retrieval: **{download_route}**."
                )
            elif confidence["level"] == "MEDIUM":
                st.warning(
                    f"🟠 **Price Data Confidence: MEDIUM** — {confidence['message']} "
                    f"Provider: **{data_provider}** • Retrieval: **{download_route}**."
                )
            else:
                st.error(
                    f"🔴 **Price Data Confidence: LOW — DO NOT ACT.** {confidence['message']} "
                    f"Provider: **{data_provider}** • Retrieval: **{download_route}**."
                )
                diag["Trade State"] = "DATA ISSUE"
                diag["Trade Block Reason"] = confidence["message"]
                diag["Bias"] = "WAIT"
                diag["Verdict"] = "C — DATA ISSUE / DO NOT ACT"
                diag["Entry Low"] = diag["Entry High"] = diag["Stop"] = np.nan
                diag["Target 1"] = diag["Target 2"] = diag["RR"] = np.nan
                diag["Stop %"] = np.nan

            st.markdown(f"### {selected_ticker} — {selected_company}")

            # Phase 1.3: one canonical direction-aware candidate-quality result.
            candidate_quality = diag.get("Candidate Quality", "C")

            if confidence["block"]:
                headline_action = "DO NOT ACT / DATA ISSUE"
            elif diag["Trade State"] == "ACTIONABLE":
                headline_action = f"{diag['Bias']} / ACTIONABLE"
            elif diag["Trade State"] == "WAIT — EVENT RISK":
                headline_action = "WAIT / EVENT RISK"
            elif diag["Trade State"] == "WAIT — VERIFY EVENT":
                headline_action = "WAIT / VERIFY EVENT"
            else:
                headline_action = str(diag["Trade State"]).replace(" — ", " / ")

            st.markdown(f"## {candidate_quality} CANDIDATE — {headline_action}")

            if confidence["block"]:
                entry_quality = "N/A — DATA ISSUE"
                action_label = "DO NOT ACT"
            elif diag["Trade State"] == "ACTIONABLE":
                entry_quality = diag.get("Entry Geometry Quality", "B+")

                if confidence["level"] == "MEDIUM":
                    base_grade = entry_quality.replace(" — CAUTION", "")
                    downgraded = {"A": "B+", "B+": "B", "B": "C"}.get(base_grade, base_grade)
                    entry_quality = downgraded + (" — CAUTION" if "CAUTION" in entry_quality else "")
                    action_label = f"{diag['Bias']} — REDUCED CONFIDENCE"
                else:
                    action_label = diag["Bias"]
            elif diag["Trade State"] == "WAIT FOR QUALITY" and diag.get("Entry Geometry Pass", False):
                # Candidate quality blocks the trade, but price/risk geometry is
                # still reported independently rather than falsely marked bad.
                entry_quality = diag.get("Entry Geometry Quality", "B+")
                action_label = "WAIT FOR QUALITY"
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
            cq4.metric("Price Data Confidence", confidence["level"])
            st.caption(
                "Quality Engine: **Candidate Quality** asks whether the stock is worth watching; "
                "**Entry Quality** asks whether the latest completed-session location/risk is acceptable; "
                "**Action** is the current decision. A strong candidate can correctly remain WAIT."
            )

            signal_session = diag.get("Signal Session", pd.NaT)
            signal_session_text = pd.Timestamp(signal_session).strftime("%d-%b-%Y") if pd.notna(signal_session) else "N/A"
            session_note = f"Technical signals use completed US daily bars only. Signal session: **{signal_session_text}**."
            if diag.get("In-Progress Session Excluded", False):
                session_note += " The provider's in-progress same-day daily candle was excluded from scoring."
            st.info(session_note)

            v1, v2, v3, v4 = st.columns(4)
            v1.metric("Signal Close", fmt_price(diag["Close"]))
            v2.metric("Momentum Score", f"{diag['Momentum Score']:.1f}")
            v3.metric("Trend", diag["Trend"])
            v4.metric("RS vs SPY", diag["RS vs SPY"])

            prev_mom = diag.get("Previous Momentum Score", np.nan)
            mom_delta = diag.get("Acceleration Delta", np.nan)
            if pd.notna(prev_mom) and pd.notna(mom_delta):
                if diag["Acceleration"] == "Accelerating":
                    st.success(
                        f"Momentum: **Accelerating** — 5 trading days ago **{prev_mom:.1f}** "
                        f"→ now **{diag['Momentum Score']:.1f}** "
                        f"({mom_delta:+.1f} pts)."
                    )
                elif diag["Acceleration"] == "Decelerating":
                    st.warning(
                        f"Momentum: **Decelerating** — 5 trading days ago **{prev_mom:.1f}** "
                        f"→ now **{diag['Momentum Score']:.1f}** "
                        f"({mom_delta:+.1f} pts)."
                    )
                elif diag["Acceleration"] == "Direction Shift":
                    st.warning(
                        f"Momentum: **Direction Shift** — 5 trading days ago **{prev_mom:.1f}** "
                        f"→ now **{diag['Momentum Score']:.1f}** "
                        f"({mom_delta:+.1f} pts)."
                    )
                else:
                    st.info(
                        f"Momentum: **Stable** — 5 trading days ago **{prev_mom:.1f}** "
                        f"→ now **{diag['Momentum Score']:.1f}** "
                        f"({mom_delta:+.1f} pts)."
                    )
            else:
                st.info("Momentum change comparison is unavailable.")

            st.caption(
                "Relative Strength = the stock's return minus SPY's return. "
                "Positive = outperforming SPY; negative = underperforming."
            )
            rs1, rs2, rs3, rs4 = st.columns(4)
            rs1.metric("RS 1M", "N/A" if pd.isna(diag["RS 1M vs SPY"]) else f"{diag['RS 1M vs SPY']:+.1f} pp")
            rs2.metric("RS 3M", "N/A" if pd.isna(diag["RS 3M vs SPY"]) else f"{diag['RS 3M vs SPY']:+.1f} pp")
            rs3.metric("RS 6M", "N/A" if pd.isna(diag["RS 6M vs SPY"]) else f"{diag['RS 6M vs SPY']:+.1f} pp")
            rs4.metric("RS Edge", "N/A" if pd.isna(diag["RS Edge"]) else f"{diag['RS Edge']:+.1f}")

            st.caption(
                "RS Edge = 20% × RS 1M + 35% × RS 3M + 45% × RS 6M. "
                "Values are percentage points of outperformance/underperformance vs SPY."
            )

            # Explicit calculation note directly under headline metrics.
            st.info(
                "**Momentum Score range: -100 to +100.** Formula = 40% Daily + 35% Weekly + 25% Monthly. "
                "Daily uses 1 trading day, Weekly 5 trading days, Monthly 20 trading days. "
                "Each component is scaled/capped to a -100 to +100 score, so the final Momentum Score is a weighted momentum reading, not a % return."
            )

            m1, m2, m3 = st.columns(3)
            m1.metric("Daily score", f"{diag['Daily']:.1f}")
            m2.metric("Weekly score", f"{diag['Weekly']:.1f}")
            m3.metric("Monthly score", f"{diag['Monthly']:.1f}")

            accel_delta = diag["Acceleration Delta"]
            st.caption(
                f"Momentum quality: **{diag['Momentum Grade']}** • "
                "Momentum state compares the absolute strength of the current completed-session Momentum Score with the score 5 trading days ago. "
                "A ≥10-point increase in magnitude = Accelerating; a ≥10-point decrease in magnitude = Decelerating."
            )

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

            # Explain unresolved metadata without misclassifying economically non-meaningful metrics as provider failures.
            field_status = diag.get("Fundamental Field Status", {}) or {}
            field_notes = diag.get("Fundamental Field Notes", {}) or {}
            missing_meta = []
            for field_name, display_name in [
                ("Sector", "sector"), ("Industry", "industry"), ("Market Cap", "market cap"),
                ("Trailing P/E", "trailing P/E"), ("Forward P/E", "forward P/E"),
            ]:
                if field_status.get(field_name, "UNRESOLVED") == "UNRESOLVED":
                    missing_meta.append(display_name)
            if str(diag["Event Data Confidence"]).startswith("LOW"):
                missing_meta.append("verified earnings date")
            if missing_meta:
                st.warning(
                    "Some fundamental/event metadata remains unresolved: "
                    + ", ".join(missing_meta)
                    + ". This is a data-availability issue, not an indication that the company lacks the underlying information."
                )
            nm_notes = [note for field, note in field_notes.items() if field_status.get(field) == "NOT_MEANINGFUL" and note]
            if nm_notes:
                st.info(" • ".join(nm_notes) + " This is not a data-quality failure.")

            md1, md2 = st.columns(2)
            md1.metric("Fundamental Data Confidence", diag["Fundamental Data Confidence"])
            md2.metric("Event Data Confidence", diag["Event Data Confidence"])

            event_state = diag.get("Earnings Risk State", "UNKNOWN")
            source_text = diag.get("Earnings Source", "Unverified")
            event_certainty = diag.get("Earnings Certainty", "UNKNOWN")
            certainty_display = {
                "CONFIRMED": "Confirmed",
                "CORROBORATED": "Corroborated estimate",
                "ESTIMATED": "Estimated",
                "ESTIMATE_WINDOW": "Estimated date window",
                "CONFLICT": "Conflicting sources",
                "UNKNOWN": "Unverified",
            }.get(event_certainty, event_certainty.title())
            if event_state == "WINDOW":
                st.warning(
                    f"⚠️ **Earnings estimate window** — {diag['Earnings']}. "
                    f"Source: {source_text}. The company has not yet confirmed the exact date; "
                    "new swing entries remain blocked until timing is sufficiently verified."
                )
            elif event_state == "UNKNOWN":
                st.error(
                    f"🔴 **Earnings timing UNKNOWN** — {diag['Earnings']}. "
                    "Unknown event risk is not treated as safe; actionable trade plans are blocked until the event is verified."
                )
            elif event_state == "HIGH":
                certainty_text = certainty_display
                st.warning(
                    f"⚠️ **Earnings danger window** — {diag['Earnings']}. "
                    f"Certainty: **{event_certainty}** • Source: {source_text}. "
                    f"{certainty_text} timing is inside the {EARNINGS_HARD_BLOCK_DAYS}-day hard block window."
                )
            elif event_state == "CAUTION":
                st.warning(
                    f"⚠️ **Upcoming earnings** — {diag['Earnings']}. "
                    f"Certainty: **{event_certainty}** • Source: {source_text}. "
                    "Event risk is inside the 14-day caution window."
                )
            elif event_state == "RECENT":
                st.info(
                    f"Earnings: {diag['Earnings']}. Source: {source_text}. "
                    "Recent post-earnings volatility may remain elevated; the next event date is not yet verified."
                )
            else:
                qualifier = certainty_display
                st.info(f"{qualifier} earnings date: {diag['Earnings']}. Source: {source_text}.")

            st.markdown("### Decision Summary")
            d1, d2 = st.columns(2)

            base_strengths, base_risks = candidate_strengths_and_risks(diag)

            with d1:
                st.markdown("**Why it qualifies / strengths**")
                strengths = list(base_strengths)
                if strengths:
                    for item in strengths:
                        st.write(f"• {item}")
                else:
                    st.write("• No major direction-aligned quality edge is currently confirmed.")

            with d2:
                if diag["Trade State"] == "ACTIONABLE":
                    st.markdown("**Key risks / entry considerations**")
                else:
                    st.markdown("**Why to wait / key risks**")
                risks = list(base_risks)
                if diag["Extension"] in ("Extended", "Oversold"):
                    risks.append(f"Entry location: {diag['Extension']}")
                if diag["Acceleration"] == "Decelerating":
                    prev_mom = diag.get("Previous Momentum Score", np.nan)
                    if pd.notna(prev_mom):
                        risks.append(
                            f"Momentum is decelerating: {prev_mom:.1f} → {diag['Momentum Score']:.1f} "
                            f"({diag['Acceleration Delta']:+.1f} pts in 5 trading days)"
                        )
                    else:
                        risks.append("Momentum is decelerating")
                if pd.notna(diag["Volume Ratio"]) and diag["Volume Ratio"] < 0.75:
                    risks.append(f"Volume is weak: {diag['Volume Ratio']:.2f}x")
                elif (
                    diag["Trade State"] == "ACTIONABLE"
                    and pd.notna(diag["Volume Ratio"])
                    and diag["Volume Ratio"] < 1.0
                ):
                    risks.append(
                        f"Volume participation is slightly below ideal: {diag['Volume Ratio']:.2f}x "
                        "(non-blocking; ≥1.0x preferred, ≥1.2x stronger)"
                    )
                for caution in diag.get("Entry Cautions", []):
                    risks.append(caution)
                if diag.get("Earnings Risk State") == "CAUTION":
                    certainty_word = "Confirmed" if diag.get("Earnings Certainty") == "CONFIRMED" else "Estimated"
                    risks.append(
                        f"{certainty_word} earnings timing is inside the {EARNINGS_CAUTION_DAYS}-day caution window: {diag['Earnings']}."
                    )
                if confidence["level"] != "HIGH":
                    risks.append(f"Price Data Confidence is {confidence['level']}")
                if diag["Trade State"] != "ACTIONABLE":
                    risks.append(diag.get("Trade Block Reason", diag["Trade State"]))
                risks = unique_text_items(risks)
                if risks:
                    for item in risks:
                        st.write(f"• {item}")
                else:
                    st.write("• No major blocking risk identified.")

            if diag["Trade State"] != "ACTIONABLE":
                st.markdown("### Setup Repair Map")
                st.caption(
                    "The engine first identifies **why the entry is not ready**, then shows what repairs that defect. "
                    "A repair threshold is **not an entry signal**. After repair, the setup must be recalculated and confirmed from the new structure."
                )

                ema20_now = diag.get("EMA20", np.nan)
                ema50_now = diag.get("EMA50", np.nan)
                atr_now = diag.get("ATR14", np.nan)
                close_now = diag.get("Close", np.nan)
                rsi_now = diag.get("RSI14", np.nan)
                vol_now = diag.get("Volume Ratio", np.nan)
                dist_now = diag.get("Distance EMA20", np.nan)
                stop_now = diag.get("Stop %", np.nan)

                bullish_candidate = diag.get("Directional Alignment") == "LONG"
                bearish_candidate = diag.get("Directional Alignment") == "SHORT"

                # Failure-specific diagnosis.
                defects = []
                if confidence["block"]:
                    defects.append("DATA")
                if diag.get("Earnings Risk State") == "UNKNOWN":
                    defects.append("EVENT VERIFICATION")
                elif diag.get("Earnings Risk State") == "HIGH":
                    defects.append("EARNINGS WINDOW")
                if diag.get("Extension") in {"Extended", "Oversold"}:
                    defects.append("PRICE EXTENSION")
                if pd.notna(rsi_now) and (rsi_now >= 75 or rsi_now <= 25):
                    defects.append("RSI EXTENSION")
                if diag["Trade State"] == "WAIT FOR BETTER ENTRY":
                    defects.append("STOP GEOMETRY")
                if diag["Acceleration"] == "Decelerating":
                    defects.append("MOMENTUM DECELERATION")
                if pd.notna(vol_now) and vol_now < 1.0:
                    defects.append("WEAK VOLUME")
                if candidate_quality not in MIN_ACTIONABLE_CANDIDATE_GRADES:
                    defects.append("CANDIDATE QUALITY")
                if not bullish_candidate and not bearish_candidate:
                    defects.append("DIRECTIONAL ALIGNMENT")

                # De-duplicate while preserving order.
                defects = list(dict.fromkeys(defects))

                if "DATA" in defects:
                    repair_mode = "DATA REPAIR"
                elif "EVENT VERIFICATION" in defects:
                    repair_mode = "EVENT VERIFICATION"
                elif "EARNINGS WINDOW" in defects:
                    repair_mode = "WAIT THROUGH EARNINGS"
                elif "CANDIDATE QUALITY" in defects:
                    repair_mode = "WAIT FOR QUALITY"
                elif "DIRECTIONAL ALIGNMENT" in defects:
                    repair_mode = "WAIT FOR STRUCTURE"
                elif "PRICE EXTENSION" in defects or "RSI EXTENSION" in defects:
                    repair_mode = "PULLBACK / CONSOLIDATION" if bullish_candidate else "BOUNCE / CONSOLIDATION"
                elif "STOP GEOMETRY" in defects:
                    repair_mode = "RISK GEOMETRY REPAIR"
                elif "MOMENTUM DECELERATION" in defects:
                    repair_mode = "MOMENTUM STABILIZATION"
                elif "WEAK VOLUME" in defects:
                    repair_mode = "PARTICIPATION CONFIRMATION"
                else:
                    repair_mode = "MAINTAIN QUALITY"

                st.markdown(f"**Repair Mode: {repair_mode}**")
                if defects:
                    st.caption("Current defects: " + " • ".join(defects))
                else:
                    st.caption("No major repair defect is currently identified.")

                repair_items = []
                if diag.get("Earnings Risk State") == "UNKNOWN":
                    repair_items.append("Verify the next earnings date before any actionable swing entry is published.")
                elif diag.get("Earnings Risk State") == "HIGH":
                    certainty_word = "confirmed" if diag.get("Earnings Certainty") == "CONFIRMED" else "estimated"
                    repair_items.append(f"Wait until the {certainty_word} earnings danger window has passed: {diag['Earnings']}.")
                if "CANDIDATE QUALITY" in defects:
                    repair_items.append(
                        f"Candidate Quality is {candidate_quality}; improve to at least B+ before a new swing entry can become actionable."
                    )
                if "DIRECTIONAL ALIGNMENT" in defects:
                    repair_items.extend(
                        directional_repair_requirements(
                            diag.get("Trend"), diag.get("Momentum Score"), diag.get("RS Edge")
                        )
                    )
                    if diag.get("Extension") == "Extended" and pd.notna(dist_now):
                        repair_items.append(f"Secondary no-chase condition: price is {dist_now:+.1%} vs EMA20; it must return to ≤ +8.0% before a long entry can qualify.")
                    elif diag.get("Extension") == "Oversold" and pd.notna(dist_now):
                        repair_items.append(f"Secondary no-chase condition: price is {dist_now:+.1%} vs EMA20; it must recover to ≥ -8.0% before a short entry can qualify.")
                repair_boundary = invalidation = np.nan
                preferred_low = preferred_high = np.nan

                if bullish_candidate and all(pd.notna(v) for v in [ema20_now, atr_now, close_now]):
                    raw_low = max(0.01, ema20_now - 0.25 * atr_now)
                    raw_high = ema20_now + 0.50 * atr_now
                    inv_candidates = [v for v in [ema50_now, ema20_now - 1.25 * atr_now] if pd.notna(v) and v > 0]
                    invalidation = max(inv_candidates) if inv_candidates else np.nan

                    # Internal consistency: preferred long entry area must remain ABOVE structural invalidation.
                    safety = 0.15 * atr_now
                    preferred_low = max(raw_low, invalidation + safety) if pd.notna(invalidation) else raw_low
                    preferred_high = max(raw_high, preferred_low)
                    repair_boundary = ema20_now * 1.08

                    if pd.notna(dist_now) and dist_now > 0.08:
                        repair_items.append(
                            f"Price extension must repair from {dist_now:+.1%} vs EMA20 to **≤ +8.0%**. "
                            f"Current no-chase boundary: {fmt_price(repair_boundary)}."
                        )
                    if pd.notna(rsi_now) and rsi_now >= 75:
                        repair_items.append(f"RSI must cool from {rsi_now:.1f} to **below 75**.")
                    if diag["Trade State"] == "WAIT FOR BETTER ENTRY":
                        repair_items.append("Required stop distance must recalculate to **≤10%** from the new proposed entry.")
                    if diag["Acceleration"] == "Decelerating":
                        repair_items.append("Momentum must stabilize or re-accelerate; the dashboard will show the new 5-day comparison.")
                    if pd.notna(vol_now) and vol_now < 1.0:
                        repair_items.append(f"Volume is {vol_now:.2f}x; prefer ≥1.0x, with ≥1.2x stronger.")

                elif bearish_candidate and all(pd.notna(v) for v in [ema20_now, atr_now, close_now]):
                    raw_low = ema20_now - 0.50 * atr_now
                    raw_high = ema20_now + 0.25 * atr_now
                    inv_candidates = [v for v in [ema50_now, ema20_now + 1.25 * atr_now] if pd.notna(v) and v > 0]
                    invalidation = min(inv_candidates) if inv_candidates else np.nan

                    # Internal consistency: preferred short repair area must remain BELOW structural invalidation.
                    safety = 0.15 * atr_now
                    preferred_high = min(raw_high, invalidation - safety) if pd.notna(invalidation) else raw_high
                    preferred_low = min(raw_low, preferred_high)
                    repair_boundary = ema20_now * 0.92

                    if pd.notna(dist_now) and dist_now < -0.08:
                        repair_items.append(
                            f"Downside extension must repair from {dist_now:+.1%} vs EMA20 to **≥ -8.0%**. "
                            f"Current no-chase boundary: {fmt_price(repair_boundary)}."
                        )
                    if pd.notna(rsi_now) and rsi_now <= 25:
                        repair_items.append(f"RSI must recover from {rsi_now:.1f} to **above 25**.")
                    if diag["Trade State"] == "WAIT FOR BETTER ENTRY":
                        repair_items.append("Required stop distance must recalculate to **≤10%** from the new proposed short entry.")
                    if diag["Acceleration"] == "Decelerating":
                        repair_items.append("Bearish momentum must stabilize or re-accelerate before entry.")
                    if pd.notna(vol_now) and vol_now < 1.0:
                        repair_items.append(f"Volume is {vol_now:.2f}x; prefer ≥1.0x, with ≥1.2x stronger.")

                event_primary_block = diag.get("Earnings Risk State") in {"HIGH", "UNKNOWN"}
                publish_price_repair = (
                    (bullish_candidate or bearish_candidate)
                    and candidate_quality in MIN_ACTIONABLE_CANDIDATE_GRADES
                    and not event_primary_block
                    and not confidence["block"]
                )

                if event_primary_block:
                    if diag.get("Earnings Risk State") == "HIGH":
                        certainty_word = "confirmed" if diag.get("Earnings Certainty") == "CONFIRMED" else "estimated"
                        st.info(
                            f"**No pre-earnings price repair area is published.** The {certainty_word} earnings window is the primary blocker. "
                            "Wait through/verify the event, then rerun the ticker so price structure, volume, ATR, momentum, RSI and EMA location "
                            "are recalculated from completed post-event daily bars."
                        )
                    else:
                        st.info(
                            "**No price repair area is published while earnings timing is unverified.** Verify the event first, then "
                            "recalculate the setup from completed daily bars."
                        )
                elif publish_price_repair:
                    r1, r2, r3 = st.columns(3)
                    r1.metric("Preferred Repair Area", f"{fmt_price(preferred_low)} – {fmt_price(preferred_high)}")
                    r2.metric(
                        "No-Chase Boundary",
                        fmt_price(repair_boundary)
                    )
                    r3.metric("Structure Invalidation", fmt_price(invalidation))

                    st.caption(
                        "**Preferred Repair Area** = where location/risk may become more attractive; it is not an order instruction. "
                        "**No-Chase Boundary** = the ±8% EMA20 extension limit; crossing back inside it only repairs one gate. "
                        "**Structure Invalidation** = a structural reference used to prevent the repair area from contradicting the risk framework."
                    )

                    st.info(
                        "**Confirmation is dynamic after repair.** The engine will NOT use the current completed bar's high/low as a permanent trigger. "
                        "After the pullback, bounce, or base forms, rerun the ticker. The engine then recalculates momentum, RS, RSI, "
                        "volume, stop distance and the new local price structure before publishing an actionable trade plan."
                    )
                else:
                    exact_failures = directional_repair_requirements(
                        diag.get("Trend"), diag.get("Momentum Score"), diag.get("RS Edge")
                    ) if "DIRECTIONAL ALIGNMENT" in defects else []
                    if "CANDIDATE QUALITY" in defects:
                        exact_failures.insert(0, f"Candidate Quality {candidate_quality} is below the minimum B+ actionability threshold.")
                    message = " ".join(unique_text_items(exact_failures))
                    if diag.get("Extension") in {"Extended", "Oversold"}:
                        message += " Entry extension remains a secondary no-chase condition until the higher-priority gate repairs."
                    st.info(
                        "**No price repair area is published while higher-priority quality/structure gates are unresolved.** "
                        + (message or "Recalculate after the failed gates repair.")
                    )

                st.markdown("**Conditions required to improve the setup**")
                if not repair_items:
                    repair_items.append(
                        "Require aligned trend, momentum and relative strength while preserving acceptable entry location, stop distance and R:R."
                    )
                if confidence["level"] == "MEDIUM":
                    repair_items.append("Restore HIGH Price Data Confidence if the isolated data gap can be repaired.")
                if diag.get("Earnings Risk State") == "CAUTION":
                    certainty_word = "confirmed" if diag.get("Earnings Certainty") == "CONFIRMED" else "estimated"
                    repair_items.append(
                        f"Secondary event caution: {certainty_word} earnings timing is inside the {EARNINGS_CAUTION_DAYS}-day caution window ({diag['Earnings']})."
                    )
                repair_items = unique_text_items(repair_items)
                for item in repair_items:
                    st.write(f"• {item}")

            else:
                st.markdown("### Entry Considerations")
                st.caption(
                    "This setup has already passed the entry-quality gate. Items below are **non-blocking considerations**, "
                    "not repairs that must occur before entry."
                )
                considerations = []

                if pd.notna(diag["Volume Ratio"]) and diag["Volume Ratio"] < 1.0:
                    considerations.append(
                        f"Volume participation is {diag['Volume Ratio']:.2f}x of the 20-day average. "
                        "≥1.0x is preferred; ≥1.2x provides stronger confirmation."
                    )

                if diag["Acceleration"] == "Decelerating":
                    prev_mom = diag.get("Previous Momentum Score", np.nan)
                    if pd.notna(prev_mom):
                        considerations.append(
                            f"Momentum is decelerating: {prev_mom:.1f} → {diag['Momentum Score']:.1f} "
                            f"({diag['Acceleration Delta']:+.1f} pts in 5 trading days)."
                        )
                    else:
                        considerations.append("Momentum is decelerating; monitor follow-through.")

                considerations.extend(diag.get("Entry Cautions", []))

                if diag.get("Earnings Risk State") == "CAUTION":
                    certainty_word = "Confirmed" if diag.get("Earnings Certainty") == "CONFIRMED" else "Estimated"
                    considerations.append(
                        f"{certainty_word} earnings timing is inside the {EARNINGS_CAUTION_DAYS}-day caution window: {diag['Earnings']}."
                    )

                if confidence["level"] != "HIGH":
                    considerations.append(
                        f"Price Data Confidence is {confidence['level']}; use additional caution."
                    )

                considerations = unique_text_items(considerations)
                if considerations:
                    for item in considerations:
                        st.write(f"• {item}")
                else:
                    st.success("No material non-blocking entry consideration is currently identified.")

            st.markdown("### Trade Plan")
            st.caption(
                "Trade plan is shown only when candidate quality and entry quality both pass the risk gates. "
                "Hard stop-distance cap: **10%**. Extended/oversold names are blocked from actionable entries."
            )

            if diag["Trade State"] != "ACTIONABLE":
                st.warning(
                    f"**{diag['Trade State']}** — {diag.get('Trade Block Reason', '')}"
                )
                if diag["Trade State"] == "WAIT FOR QUALITY" and diag.get("Entry Geometry Pass", False):
                    st.info(
                        f"Price/risk geometry is independently graded {diag.get('Entry Geometry Quality', 'acceptable')}, "
                        f"but Candidate Quality {candidate_quality} is below the minimum B+ actionability threshold. "
                        "A technically acceptable entry does not override insufficient candidate edge."
                    )
                elif candidate_quality in {"A+", "A", "B+"}:
                    st.info(
                        "Candidate quality remains high, but the current entry/actionability gate is not acceptable. "
                        "Candidate quality and entry quality are intentionally scored separately."
                    )
                elif candidate_quality == "B":
                    st.info(
                        "The stock remains a watchlist candidate, but current quality/alignment is not strong enough for an actionable swing entry."
                    )
                else:
                    st.info(
                        "Candidate quality and entry quality are currently insufficient for an actionable swing setup. "
                        "Reassess only after the identified defects repair."
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
                p5.metric("R:R @ midpoint", fmt_ratio(diag.get("RR Midpoint", diag["RR"])))

                t1, t2 = st.columns(2)
                t1.metric("Target 1", fmt_price(diag["Target 1"]))
                t2.metric("Target 2", fmt_price(diag["Target 2"]))

                rr1, rr2, rr3 = st.columns(3)
                rr1.metric("T1 R @ midpoint", fmt_ratio(diag.get("T1 R", np.nan)))
                rr2.metric("T2 R @ midpoint", fmt_ratio(diag.get("T2 R", np.nan)))
                zone_min = diag.get("RR Zone Min", np.nan)
                zone_max = diag.get("RR Zone Max", np.nan)
                zone_text = (
                    f"{zone_min:.1f}R – {zone_max:.1f}R"
                    if pd.notna(zone_min) and pd.notna(zone_max) else "N/A"
                )
                rr3.metric("T2 R across entry zone", zone_text)

                if diag.get("Entry Cautions"):
                    for caution in diag["Entry Cautions"]:
                        st.warning(caution)
                elif pd.notna(diag["Stop %"]) and diag["Stop %"] > 0.08:
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
                    ("Momentum Change", "Current Momentum Score vs 5 trading days ago; the dashboard shows both values and the point change"),
                    ("Trend", "Price/EMA20/EMA50/EMA200 stacking"),
                    ("RS vs SPY", "1M / 3M / 6M excess return vs SPY; RS Edge weights 20% / 35% / 45%"),
                    ("Volume Ratio", "Current volume ÷ 20-day average volume"),
                    ("Extension", ">8% above EMA20 or RSI≥75 = Extended; >8% below EMA20 or RSI≤25 = Oversold"),
                    ("Trade Plan", "Requires candidate-quality gate + entry-quality gate; R:R uses midpoint entry and also shows T1/T2 plus the full entry-zone T2 range"),
                ]
                st.dataframe(pd.DataFrame(rows, columns=["Signal", "Rule"]), hide_index=True, use_container_width=True)

            with st.expander("Fundamental snapshot", expanded=False):
                f1, f2, f3 = st.columns(3)
                mc = diag["Market Cap"]
                field_status = diag.get("Fundamental Field Status", {}) or {}
                field_notes = diag.get("Fundamental Field Notes", {}) or {}
                trailing_pe_display = (
                    "N/M" if field_status.get("Trailing P/E") == "NOT_MEANINGFUL"
                    else ("N/A" if pd.isna(diag["Trailing PE"]) else f"{diag['Trailing PE']:.1f}")
                )
                f1.metric("Market Cap", "N/A" if pd.isna(mc) else f"${mc/1e9:,.1f}B")
                f2.metric("Trailing P/E", trailing_pe_display)
                f3.metric("Forward P/E", "N/A" if pd.isna(diag["Forward PE"]) else f"{diag['Forward PE']:.1f}")


                fundamental_sources = diag.get("Fundamental Sources", {}) or {}
                event_sources = diag.get("Event Sources", []) or []
                provenance_rows = []
                fundamental_order = ["Sector", "Industry", "Market Cap", "Trailing P/E", "Forward P/E"]
                for field in fundamental_order:
                    status = field_status.get(field, "UNRESOLVED")
                    source_name = fundamental_sources.get(field, "")
                    if status == "AVAILABLE":
                        role = "Available fundamental metadata"
                    elif status == "NOT_MEANINGFUL":
                        role = field_notes.get(field, "Not meaningful for the current earnings profile")
                    else:
                        role = "Unresolved after configured retrieval/fallback routes"
                    provenance_rows.append({
                        "Field": field,
                        "Source": source_name or ("No verified source" if status == "UNRESOLVED" else "Derived semantic classification"),
                        "Role": role,
                    })
                for row in event_sources:
                    if isinstance(row, dict):
                        provenance_rows.append({
                            "Field": "Earnings date",
                            "Source": row.get("Source", ""),
                            "Role": row.get("Role", "Event evidence") + (f" • {row.get('Date')}" if row.get("Date") else ""),
                        })
                if provenance_rows:
                    st.markdown("**Metadata provenance**")
                    st.dataframe(pd.DataFrame(provenance_rows), hide_index=True, use_container_width=True)
                else:
                    st.caption("Metadata provenance is unavailable for this lookup.")
                if diag.get("Metadata Retrieved At"):
                    st.caption(f"Metadata retrieved (ET): {diag['Metadata Retrieved At']}.")
                if diag.get("Event Window Note"):
                    st.warning(f"Event estimate window: {diag['Event Window Note']}")
                if diag.get("Event Conflict"):
                    st.error(f"Event-source conflict: {diag['Event Conflict']}")

            # Temporary Phase 2C final-verification tool. It is deliberately isolated from
            # production metadata/decision state and can be removed after the live fallback
            # sanity test passes.
            with st.expander("Phase 2C controlled independent fallback verification (temporary)", expanded=False):
                st.caption(
                    "Diagnostic only — this does not alter Candidate Quality, Entry Quality, Action, "
                    "or the production Fundamental Data Confidence. It deliberately ignores Yahoo "
                    "market-cap values and exercises the independent non-Yahoo fallback chain (Nasdaq first, SEC tertiary)."
                )
                if st.button(
                    "Run controlled independent market-cap fallback test",
                    key=f"phase2c_independent_mc_test_{selected_ticker}",
                    use_container_width=True,
                ):
                    with st.spinner(f"Testing independent SEC fallback for {selected_ticker}..."):
                        fallback_test = phase2c_controlled_market_cap_fallback_test(selected_ticker, fallback_close)
                    if fallback_test.get("status") == "PASS":
                        test_value = fallback_test.get("value", np.nan)
                        production_value = diag.get("Market Cap", np.nan)
                        st.success(
                            f"✅ CONTROLLED FALLBACK PASS — {selected_ticker} Market Cap recovered via "
                            f"{fallback_test.get('source', 'independent fallback')}: ${test_value/1e9:,.1f}B."
                        )
                        if fallback_test.get("route") == "SEC":
                            t1, t2, t3 = st.columns(3)
                            t1.metric("SEC shares", f"{fallback_test.get('shares', np.nan)/1e9:,.3f}B")
                            t2.metric("Completed-session close", f"${fallback_test.get('close', np.nan):,.2f}")
                            t3.metric("Fallback market cap", f"${test_value/1e9:,.1f}B")
                            if fallback_test.get("share_filing_date"):
                                st.caption(
                                    f"SEC share source: {fallback_test.get('share_source', '')} • "
                                    f"filing/as-of reference: {fallback_test.get('share_filing_date', '')}."
                                )
                        else:
                            t1, t2 = st.columns(2)
                            t1.metric("Independent route", "Nasdaq")
                            t2.metric("Fallback market cap", f"${test_value/1e9:,.1f}B")
                        if pd.notna(production_value) and production_value > 0:
                            diff_pct = ((test_value / float(production_value)) - 1.0) * 100.0
                            st.caption(
                                f"Production market cap: ${float(production_value)/1e9:,.1f}B • "
                                f"controlled fallback difference: {diff_pct:+.1f}%. "
                                "The comparison is informational because SEC-reported shares can have a different as-of date."
                            )
                        st.dataframe(
                            pd.DataFrame([
                                {
                                    "Field": "Market Cap",
                                    "Primary route": "Suppressed by controlled test",
                                    "Yahoo direct repair": "Suppressed by controlled test",
                                    "Fallback source": fallback_test.get("source", ""),
                                    "Route": fallback_test.get("route", ""),
                                    "Share filing/reference date": fallback_test.get("share_filing_date", ""),
                                    "CIK": fallback_test.get("cik", ""),
                                    "Result": "PASS",
                                }
                            ]),
                            hide_index=True,
                            use_container_width=True,
                        )
                    else:
                        st.error(
                            f"❌ CONTROLLED FALLBACK NOT VERIFIED — {fallback_test.get('reason') or 'SEC fallback did not return a usable market-cap estimate.'}"
                        )
                        if fallback_test.get("shares_recovery_note"):
                            st.caption(f"SEC recovery detail: {fallback_test.get('shares_recovery_note')}")
                        if fallback_test.get("foreign_issuer"):
                            st.info(
                                "This ticker was intentionally protected by the foreign-issuer/ADR guard. "
                                "Use a US domestic issuer such as MSFT for this controlled verification."
                            )


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
    elif h_status == "RECOVERED":
        st.info(f"🔵 **Data Health: RECOVERED SNAPSHOT** — {health.get('message', '')}")
    else:
        st.info("🔵 **Data Health:** Run a scan to test current bulk-provider coverage.")

    st.caption(
        f"Market-session calendar: **{health.get('calendar_source', market_calendar_source())}** • "
        f"Signal bars require a {MARKET_DATA_PUBLICATION_BUFFER_MINUTES}-minute post-close publication buffer."
    )

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

    # Phase 2B.2: on a fresh Streamlit session, recover the most recent compatible
    # last-good snapshot. Local SQLite is checked first, then the configured
    # reboot-safe GitHub durable store.
    # A stale signal session may be displayed as a recovery snapshot, but pressing
    # Run will still force a fresh scan because reusable_session_scan validates the
    # current completed XNYS session and 15-minute TTL.
    current_restore_signature = scan_universe_signature(universe_df)
    restore_key = f"{universe_name}:{current_restore_signature}"
    restore_checked = st.session_state.persistence_restore_checked.get(restore_key, False)
    if (
        not restore_checked
        and st.session_state.scan_df.empty
        and current_restore_signature
    ):
        st.session_state.persistence_restore_checked[restore_key] = True
        snapshot, restore_source, restore_message = load_best_available_snapshot(
            universe_name, current_restore_signature, ENGINE_VERSION
        )
        if snapshot is not None:
            snapshot["recovery_source"] = restore_source
            apply_snapshot_to_session(universe_name, snapshot, recovered=True)
            snap_session = snapshot.get("signal_session")
            st.session_state.persistent_restore_notice = (
                f"Recovered last-good {universe_name} snapshot from **{restore_source}** • "
                f"saved {snapshot['timestamp']:%Y-%m-%d %H:%M:%S}"
                + (f" • signal session {pd.Timestamp(snap_session):%d-%b-%Y}." if snap_session is not None else ".")
            )
            st.rerun()
        elif restore_message:
            st.session_state.persistent_restore_notice = f"Recovery check: {restore_message}"

    if st.session_state.persistent_restore_notice:
        st.info(st.session_state.persistent_restore_notice)

    st.info(
        "**Professional Quality Engine:** first applies hard tradeability/data gates, then scores each stock from **0–100**. "
        "A+ ≥90, A ≥82, B+ ≥74. **Scanner grade is candidate quality, not permission to enter.** "
        "Price-ready candidates must still verify earnings/event timing and stop geometry in Ticker Search before action."
    )

    st.caption(
        "Hard tradeability gates: price ≥ $10 • 20-day average dollar volume ≥ $20M "
        "• at least 126 trading days of history • ATR% between 1% and 8%."
    )

    with st.expander("Scanner filters", expanded=False):
        f1, f2, f3 = st.columns(3)
        with f1:
            min_composite = st.slider("Minimum Momentum Score", 0, 100, 60, 5)
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

    force_refresh_scan = st.button(
        "Force refresh market data",
        use_container_width=True,
        help=(
            "Bypass the valid same-session finished-scan cache and force a fresh Yahoo batch download. "
            "Use this when you explicitly want to retest the provider or refresh market data before the normal TTL expires."
        ),
    )
    st.caption(
        f"Repeat Run requests reuse a valid same-universe / same-completed-XNYS-session scan for up to "
        f"{SCAN_RESULT_REUSE_TTL // 60} minutes. Force refresh bypasses that reuse cache."
    )

    if clear_scan:
        st.session_state.scan_df = pd.DataFrame()
        st.session_state.scan_ranked_df = pd.DataFrame()
        st.session_state.scan_ranked_regime_label = ""
        st.session_state.scan_ranked_engine_version = ""
        st.session_state.scan_universe_name = ""
        st.session_state.scan_timestamp = None
        st.session_state.scan_errors = []
        st.session_state.scan_universe_signature = ""
        st.session_state.scan_signal_session = None
        st.session_state.scan_data_engine_version = ""
        st.session_state.scan_diagnostics = []
        st.session_state.persistent_restore_notice = ""
        st.session_state.persistence_restore_source = ""
        st.session_state.persistence_restore_checked[restore_key] = True
        st.rerun()

    scan_requested = run_scan or force_refresh_scan
    if scan_requested:
        request_started_perf = time.perf_counter()
        if universe_df.empty:
            universe_df = refresh_universe(universe_name, watchlist_text)
        if universe_df.empty:
            st.error(
                f"Could not load the {universe_name} constituent list. "
                "Please retry once. If it still fails, use My Watchlist while the source is unavailable."
            )
            st.stop()

        reuse_ok = False
        cache_age = np.nan
        signal_session = expected_latest_completed_us_session()
        if not force_refresh_scan:
            reuse_ok, cache_age, signal_session, _ = reusable_session_scan(universe_name, universe_df)

        if reuse_ok:
            reuse_elapsed = time.perf_counter() - request_started_perf
            st.session_state.last_scan_execution_seconds = reuse_elapsed
            st.success(
                f"Reused current {universe_name} scan: {len(st.session_state.scan_df):,}/{len(universe_df):,} symbols • "
                f"reuse time {format_elapsed_short(reuse_elapsed)} • cached data age {cache_age:.0f}s • "
                f"signal session {pd.Timestamp(signal_session):%d-%b-%Y}. "
                "No Yahoo re-download or per-symbol recomputation was needed."
            )
        else:
            if force_refresh_scan:
                # Targeted cache invalidation only: do not clear unrelated Streamlit caches.
                try:
                    download_batch_cached.clear()
                except Exception:
                    pass
                try:
                    download_one.clear()
                except Exception:
                    pass
                st.info("Force refresh requested: scanner market-data caches were invalidated for this run.")

            progress = st.progress(0.0)
            status = st.empty()
            started = time.time()

            results, failures, health = run_market_scan(universe_df, progress, status)
            progress.empty()
            status.empty()

            st.session_state.provider_health = health
            st.session_state.scan_diagnostics = list(health.get("diagnostics", []))
            elapsed = time.time() - started
            st.session_state.last_scan_execution_seconds = elapsed
            current_signature = scan_universe_signature(universe_df)
            current_signal_session = expected_latest_completed_us_session()
            log_scan_run(
                universe_name,
                current_signal_session,
                len(universe_df),
                len(results),
                elapsed,
                failures,
                health,
                st.session_state.scan_diagnostics,
            )

            if health["status"] == "PROVIDER_FAILURE":
                previous = st.session_state.last_good_scans.get(universe_name)
                persistent_previous = None
                if previous is None or previous.get("df", pd.DataFrame()).empty:
                    candidate_snapshot, snapshot_source, _ = load_best_available_snapshot(
                        universe_name, current_signature, ENGINE_VERSION
                    )
                    if candidate_snapshot is not None:
                        candidate_snapshot["recovery_source"] = snapshot_source
                        persistent_previous = candidate_snapshot
                        previous = candidate_snapshot

                st.error(
                    f"🔴 **DATA PROVIDER FAILURE** — {health['message']} "
                    "The failed scan has NOT replaced your last valid results."
                )
                if previous is not None and not previous.get("df", pd.DataFrame()).empty:
                    if persistent_previous is not None:
                        apply_snapshot_to_session(
                            universe_name, persistent_previous, recovered=False, preserve_provider_health=True
                        )
                        # Keep current failed-provider diagnostics visible rather than replacing
                        # them with the historical snapshot diagnostics.
                        st.session_state.scan_diagnostics = list(health.get("diagnostics", []))
                    else:
                        st.session_state.scan_df = previous["df"].copy()
                        st.session_state.scan_ranked_df = pd.DataFrame()
                        st.session_state.scan_ranked_regime_label = ""
                        st.session_state.scan_ranked_engine_version = ""
                        st.session_state.scan_universe_name = universe_name
                        st.session_state.scan_timestamp = previous["timestamp"]
                        st.session_state.scan_errors = previous.get("failures", [])
                        st.session_state.scan_universe_signature = previous.get("universe_signature", "")
                        st.session_state.scan_signal_session = previous.get("signal_session")
                        st.session_state.scan_data_engine_version = previous.get("engine_version", "")
                    source_word = (
                        persistent_previous.get("recovery_source", "persisted recovery")
                        if persistent_previous is not None else "in-session"
                    )
                    st.warning(
                        f"Showing the {source_word} last-good {universe_name} scan from "
                        f"{previous['timestamp'].strftime('%Y-%m-%d %H:%M:%S')} instead."
                    )
                else:
                    st.warning(
                        "No compatible last-good recovery snapshot is available. "
                        "Please retry later rather than using an empty/partial scan."
                    )
            else:
                st.session_state.scan_df = results
                st.session_state.scan_ranked_df = pd.DataFrame()
                st.session_state.scan_ranked_regime_label = ""
                st.session_state.scan_ranked_engine_version = ""
                st.session_state.scan_universe_name = universe_name
                st.session_state.scan_timestamp = datetime.now()
                st.session_state.scan_errors = failures
                st.session_state.scan_universe_signature = current_signature
                st.session_state.scan_signal_session = current_signal_session
                st.session_state.scan_data_engine_version = ENGINE_VERSION

                if health["status"] == "HEALTHY":
                    if persistable_last_good(results, health):
                        st.session_state.last_good_scans[universe_name] = {
                            "df": results.copy(),
                            "timestamp": st.session_state.scan_timestamp,
                            "failures": list(failures),
                            "universe_signature": current_signature,
                            "signal_session": current_signal_session,
                            "engine_version": ENGINE_VERSION,
                            "diagnostics": list(st.session_state.scan_diagnostics),
                        }
                        saved, persist_message = save_last_good_snapshot(
                            universe_name,
                            results,
                            st.session_state.scan_timestamp,
                            failures,
                            current_signature,
                            current_signal_session,
                            ENGINE_VERSION,
                            health,
                            st.session_state.scan_diagnostics,
                        )
                        st.session_state.persistence_last_message = persist_message
                        durable_saved, durable_message = save_durable_snapshot(
                            universe_name,
                            results,
                            st.session_state.scan_timestamp,
                            failures,
                            current_signature,
                            current_signal_session,
                            ENGINE_VERSION,
                            health,
                            st.session_state.scan_diagnostics,
                        )
                        st.session_state.durable_persistence_last_message = durable_message
                    else:
                        st.session_state.persistence_last_message = (
                            f"Scan completed, but usable coverage did not meet the "
                            f"{PERSIST_MIN_USABLE_COVERAGE:.0%} last-good promotion threshold; "
                            "the previous recovery snapshot was preserved."
                        )
                    st.session_state.persistence_restore_checked[restore_key] = True
                    st.success(
                        f"Scan complete: {len(results):,}/{len(universe_df):,} symbols analyzed "
                        f"in {elapsed:.0f}s."
                    )
                else:
                    st.warning(
                        f"⚠️ Scan completed with degraded coverage: "
                        f"{len(results):,}/{len(universe_df):,} in {elapsed:.0f}s."
                    )

    with st.expander("Provider diagnostics & recovery", expanded=False):
        live_health = st.session_state.provider_health
        diag_rows = list(st.session_state.get("scan_diagnostics", []))
        d1, d2, d3, d4, d5 = st.columns(5)
        d1.metric("Provider status", live_health.get("status", "UNKNOWN"))
        coverage = live_health.get("coverage", np.nan)
        usable_coverage = live_health.get("usable_coverage", np.nan)
        d2.metric("Raw coverage", "N/A" if pd.isna(coverage) else f"{coverage:.1%}")
        d3.metric("Usable coverage", "N/A" if pd.isna(usable_coverage) else f"{usable_coverage:.1%}")
        d4.metric(
            "Targeted repairs",
            f"{int(live_health.get('targeted_retry_repaired', 0))}/{int(live_health.get('targeted_retry_symbols', 0))}",
        )
        d5.metric("Unresolved", f"{len(live_health.get('unresolved_symbols', [])):,}")

        if diag_rows:
            diag_df = pd.DataFrame(diag_rows)
            diag_cols = [
                "Ticker", "Batch", "Initial Issue", "Recovery Path", "Final State",
                "Confidence", "Latest Session", "Final Note",
            ]
            diag_cols = [c for c in diag_cols if c in diag_df.columns]
            st.dataframe(diag_df[diag_cols], hide_index=True, use_container_width=True)
        elif live_health.get("status") in {"HEALTHY", "DEGRADED", "PROVIDER_FAILURE"}:
            st.success("No symbol-level recovery issue was recorded for the current scan.")
        else:
            st.caption("Run a fresh scan to populate symbol-level provider diagnostics.")

        persisted_ok, persisted_message = persistence_status()
        if persisted_ok:
            st.caption(
                "Local recovery: available (best-effort SQLite; fast, but Streamlit may remove it on container restart)."
            )
            if st.session_state.persistence_last_message:
                st.caption(st.session_state.persistence_last_message)
        else:
            st.warning(persisted_message)

        durable_ok, durable_message = durable_store_status()
        if durable_ok:
            st.success(f"Durable recovery: CONFIGURED — {durable_message}")
            if st.session_state.durable_persistence_last_message:
                st.caption(st.session_state.durable_persistence_last_message)
        else:
            st.warning(f"Durable recovery: NOT CONFIGURED — {durable_message}")

        run_history = recent_scan_runs(universe_name, limit=5)
        if not run_history.empty:
            st.markdown("**Recent fresh-scan runs**")
            st.dataframe(
                run_history,
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Seconds": st.column_config.NumberColumn(format="%.1f"),
                    "Coverage": st.column_config.NumberColumn(format="%.3f"),
                    "Usable Coverage": st.column_config.NumberColumn(format="%.3f"),
                },
            )

    scan_df = st.session_state.scan_df
    if not scan_df.empty:
        needs_rank_rebuild = (
            st.session_state.scan_ranked_df.empty
            or st.session_state.scan_ranked_regime_label != regime["label"]
            or st.session_state.scan_ranked_engine_version != ENGINE_VERSION
        )
        if needs_rank_rebuild:
            with st.spinner("Building ranked master scan..."):
                st.session_state.scan_ranked_df = build_ranked_master(scan_df)
                st.session_state.scan_ranked_regime_label = regime["label"]
                st.session_state.scan_ranked_engine_version = ENGINE_VERSION

        ranked = apply_scanner_filters(
            st.session_state.scan_ranked_df,
            min_composite, min_volume, rsi_low, rsi_high,
        )

        scan_time = st.session_state.scan_timestamp
        when = scan_time.strftime("%H:%M:%S") if scan_time else ""
        st.caption(
            f"Showing cached session scan: **{st.session_state.scan_universe_name}** • "
            f"{len(ranked):,} stocks analyzed • {when}"
        )

        a_plus_mask = ranked["Setup"].isin(["A+ Long", "A+ Short"])
        a_plus_count = int(a_plus_mask.sum())
        quality_mask = ranked["Setup"].isin(["A+ Long","A Long","B+ Long","A+ Short","A Short","B+ Short"])
        price_ready_mask = quality_mask & ranked["Price Entry Gate Pass"].fillna(False)
        st.caption(
            "A+ = candidate Quality Score ≥90 after tradeability/data gates. "
            "A+ can correctly remain WAIT when current price location is poor; scanner price-readiness still requires event/stop verification."
        )
        k1, k2, k3, k4, k5 = st.columns(5)
        k1.metric("Stocks analyzed", f"{len(ranked):,}")
        k2.metric("Quality candidates", f"{int(quality_mask.sum()):,}")
        k3.metric("Price-ready", f"{int(price_ready_mask.sum()):,}")
        k4.metric("Regime-aligned", f"{int(ranked['Regime Aligned'].sum()):,}")

        with k5:
            st.caption("A+ candidates")
            if st.button(
                f"{a_plus_count:,}",
                key="a_plus_count_button",
                use_container_width=True,
                help="Tap to show the exact A+ candidate list, including WAIT states.",
            ):
                st.session_state.show_a_plus = not st.session_state.show_a_plus

        # A+ candidate drilldown
        if st.session_state.show_a_plus:
            st.markdown("### ⭐ A+ Quality Candidates")
            aplus = ranked[a_plus_mask].copy().sort_values(
                ["Adjusted Score", "Volume Ratio"],
                ascending=[False, False],
            )

            if aplus.empty:
                st.info("There are currently no A+ setups in this scan.")
            else:
                st.caption(
                    f"Exact A+ candidate count: **{len(aplus):,}**. "
                    "WAIT states are preserved; select a ticker below to inspect price structure."
                )

                aplus_cols = [
                    "Ticker", "Company", "Sector", "Setup", "Setup Type", "Quality Score",
                    "Entry Quality", "Scanner Action", "Price Data Confidence",
                    "RS Rating", "RS Edge", "Momentum Score", "RSI14",
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
                        "RS Edge": st.column_config.NumberColumn(format="%+.1f"),
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

                    c1, c2, c3, c4, c5, c6 = st.columns(6)
                    c1.metric("Candidate Grade", selected_row["Setup"])
                    c2.metric("Quality Score", f"{selected_row['Quality Score']:.0f}/100")
                    c3.metric("Entry Quality", selected_row.get("Entry Quality", "N/A"))
                    c4.metric("Scanner Action", selected_row.get("Scanner Action", "N/A"))
                    c5.metric("RS Rating", f"{int(selected_row['RS Rating'])}" if pd.notna(selected_row.get("RS Rating")) else "N/A")
                    c6.metric("Momentum Score", f"{selected_row['Momentum Score']:.1f}")

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
                        title=f"{selected_aplus} — A+ Candidate Chart ({timeframe})",
                        height=480,
                        xaxis_rangeslider_visible=False,
                        margin=dict(l=5, r=5, t=45, b=5),
                    )
                    st.plotly_chart(fig_aplus, use_container_width=True)

        view_mode = st.segmented_control(
            "View",
            ["Quality Candidates", "Price-Ready Candidates", "Passing Filters", "Regime-Aligned", "All"],
            default="Quality Candidates",
        )
        if view_mode == "Quality Candidates":
            shown = ranked[quality_mask].copy()
        elif view_mode == "Price-Ready Candidates":
            shown = ranked[price_ready_mask].copy()
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
            "Entry Quality", "Scanner Action", "Price Entry Gate Pass", "Price Data Confidence",
            "Tradeable", "Regime Aligned", "RS Rating", "RS Edge",
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
                "RS Edge": st.column_config.NumberColumn(format="%+.1f"),
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
