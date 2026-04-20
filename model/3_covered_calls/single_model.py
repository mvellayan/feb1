"""
single_model.py — 3_covered_calls

Entry: composite 4-indicator buy signal → immediately open stock + 12 covered call variants.

Strategy:
  On buy signal → buy 100 shares at avg_ask and simultaneously simulate selling
  12 different call option contracts (3 expiries × 4 strikes).

  Expiries:  w0 = Friday of entry week
             w1 = Friday of following week
             w2 = Friday two weeks after entry week

  Strikes (relative to floor(entry_price)):
    s+1 = floor(entry_price) + 1
    s+2 = floor(entry_price) + 2
    s-1 = floor(entry_price) - 1
    s-2 = floor(entry_price) - 2

Exit (per variant):
  1. option_ask − max(0, avg_bid − strike) < BUYBACK_TV → buyback
     — time-value-based: the cost to close, net of intrinsic, has fallen below threshold
  2. Expiry Friday ≥ 15:00:
       stock avg_bid > strike (ITM) → assigned — stock sold at strike
       stock avg_bid ≤ strike (OTM) → expired_otm — stock sold at avg_bid
  3. Window end without resolution → window_end

No stop loss.  Position closes only when covered call closes.
Each of the 12 option variants is an independent simulation.
Metrics are computed per (combo × option_variant).
"""

from __future__ import annotations

import argparse
import datetime
import math
import random
import secrets
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

# ── path setup ─────────────────────────────────────────────────────────────────
_HERE    = Path(__file__).parent
_MODEL1A = _HERE.parent / '1a_tech_indicators_sock_trade'
_BASE    = _HERE.parent.parent          # /feb1/
sys.path.insert(0, str(_MODEL1A))
sys.path.insert(0, str(_HERE))

from signals import add_buy_signals, add_sell_signals
from utils   import PF_CAP, md_table

REPORTS_DIR = Path(__file__).parent / 'reports'

warnings.filterwarnings('ignore')

# ── file paths ─────────────────────────────────────────────────────────────────
EXTENDED_CSV = _BASE / 'data/stock/sq_AAPL_extended.csv'
SIGNALS_CSV  = _BASE / 'data/stock/sq_AAPL_signals.csv'
OPTION_INDEX = _BASE / 'data/option_index.csv'
OPTIONS_DIR  = _BASE / 'data/options'

# ── simulation constants ───────────────────────────────────────────────────────
N_SAMPLE              = 10_000
RANDOM_SEED           = 42
COMMISSION            = 2.00
BUYBACK_TV            = 0.25    # buyback threshold on option-ask time value
CC_TV_MIN             = 1.00    # entry gate: minimum opening time value
CC_TV_MAX             = 3.00    # entry gate: maximum opening time value
MAX_QUOTE_AGE_MINUTES = 30      # freshness guard — reject stale option open quote
EXPIRY_QUOTE_MIN_HOUR = 15      # expiry-day quote at or after 3 PM
SHARES                = 100     # always 100 shares (fixed)

# ── option variant definitions ─────────────────────────────────────────────────
# Strike labels encode direction and chain-step (not a dollar offset):
#   s-0 = first available strike strictly below entry price
#   s-1 = next strike below s-0
#   s-2 = next strike below s-1
#   s+0 = first available strike strictly above entry price
#   s+1 = next strike above s+0
#   s+2 = next strike above s+1
EXPIRY_WEEKS    = ['w0', 'w1', 'w2']
STRIKE_LABELS   = ['s-2', 's-1', 's-0', 's+0', 's+1', 's+2']
OPTION_VARIANTS = [(ew, sl) for ew in EXPIRY_WEEKS for sl in STRIKE_LABELS]   # 18 total

# ── standalone run constants ───────────────────────────────────────────────────
N_RUNS      = 100
DATA_FIRST  = datetime.date(2023, 1, 1)
DATA_LAST   = datetime.date(2026, 2, 28)
WINDOW_DAYS = 14
META_SEED   = 42


# ══════════════════════════════════════════════════════════════════════════════
# SIGNALS CSV  (load or build)
# ══════════════════════════════════════════════════════════════════════════════

_signals_cache: pd.DataFrame | None = None


def load_or_build_signals() -> pd.DataFrame:
    global _signals_cache
    if _signals_cache is not None:
        print("[signals] Using in-memory cache.")
        return _signals_cache

    if SIGNALS_CSV.exists():
        print(f"[signals] Loading cached {SIGNALS_CSV} ...")
        df = pd.read_csv(SIGNALS_CSV, parse_dates=['date'], low_memory=False)
        print(f"  Loaded  : {df.shape[0]:,} rows x {df.shape[1]} columns")
        _signals_cache = df
        return df

    print(f"[signals] {SIGNALS_CSV} not found -- building from {EXTENDED_CSV} ...")
    if not EXTENDED_CSV.exists():
        raise FileNotFoundError(
            f"Extended CSV not found: {EXTENDED_CSV}\n"
            "Run 1_compute_indicators.py first."
        )

    df = pd.read_csv(EXTENDED_CSV, parse_dates=['date'], low_memory=False)
    print(f"  Extended loaded : {df.shape[0]:,} rows x {df.shape[1]} columns")
    print("  Adding buy signals ...")
    df = add_buy_signals(df)
    print("  Adding sell signals ...")
    df = add_sell_signals(df)

    SIGNALS_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(SIGNALS_CSV, index=False)
    print(f"  Saved   : {SIGNALS_CSV}  ({df.shape[1]} columns)")
    _signals_cache = df
    return df


# ══════════════════════════════════════════════════════════════════════════════
# OPTION INDEX  (load once, cache in memory)
# ══════════════════════════════════════════════════════════════════════════════

_option_index_cache: pd.DataFrame | None = None


def load_option_index() -> pd.DataFrame:
    global _option_index_cache
    if _option_index_cache is not None:
        return _option_index_cache
    df = pd.read_csv(OPTION_INDEX)
    _option_index_cache = df
    return df


# ══════════════════════════════════════════════════════════════════════════════
# OPTION DATA  (per-contract file, cached by expiry+strike key)
# ══════════════════════════════════════════════════════════════════════════════

# Each cache entry is a dict of numpy arrays (not a DataFrame):
#   { 'dates': datetime64[ns], 'avg_ask': float64, 'avg_bid': float64 }
_option_data_cache: dict[str, dict | None] = {}


def _option_file_path(expiry_int: int, strike: float) -> Path:
    year_2 = str(expiry_int)[:2]
    fname  = f"oq_{expiry_int}C{int(round(strike * 1000)):08d}.csv"
    return OPTIONS_DIR / year_2 / fname


def load_option_data(contract: dict) -> dict | None:
    """Load option CSV and cache numpy arrays for O(log n) binary-search lookups."""
    key = f"{contract['expiration_date']}_{contract['strike_price']}"
    if key in _option_data_cache:
        return _option_data_cache[key]
    path = _option_file_path(contract['expiration_date'], contract['strike_price'])
    if not path.exists():
        _option_data_cache[key] = None
        return None
    df = pd.read_csv(path, parse_dates=['date'], low_memory=False)
    df = df.sort_values('date').reset_index(drop=True)
    opt_data = {
        'dates':   df['date'].values.astype('datetime64[ns]'),
        'avg_ask': df['avg_ask'].values.astype(np.float64),
        'avg_bid': df['avg_bid'].values.astype(np.float64),
    }
    _option_data_cache[key] = opt_data
    return opt_data


def get_option_price_at(
    opt_data:        dict | None,
    ts,                               # pd.Timestamp, np.datetime64, or str
    col:             str,
    max_age_minutes: int | None = None,
) -> float | None:
    """Return the most recent value of `col` at or before `ts`.
    Binary search (O(log n)) on the sorted dates array.
    If max_age_minutes is set, returns None if the quote is stale."""
    if opt_data is None:
        return None
    dates = opt_data['dates']
    if len(dates) == 0:
        return None
    ts_ns = pd.Timestamp(ts).to_datetime64()   # normalise to datetime64[ns]
    idx   = int(np.searchsorted(dates, ts_ns, side='right')) - 1
    if idx < 0:
        return None
    if max_age_minutes is not None:
        age_ns = float(ts_ns.astype(np.int64) - dates[idx].astype(np.int64))
        if age_ns / 6e10 > max_age_minutes:    # 6e10 ns = 1 minute
            return None
    val = float(opt_data[col][idx])
    return val if val > 0 else None


def _lookup_prices_vectorized(
    opt_data:        dict | None,
    bar_times:       np.ndarray,      # datetime64[ns] array
    col:             str,
    max_age_minutes: int | None = None,
) -> np.ndarray:
    """
    Vectorized as-of price lookup for an array of bar timestamps.
    One searchsorted call replaces N individual get_option_price_at calls.
    Returns float64 array aligned to bar_times; NaN where no valid quote.
    """
    if opt_data is None or len(opt_data['dates']) == 0:
        return np.full(len(bar_times), np.nan)

    dates   = opt_data['dates']
    values  = opt_data[col]

    # For each bar, index of last option quote at-or-before that bar
    indices = np.searchsorted(dates, bar_times, side='right') - 1
    clamped = np.maximum(indices, 0)

    raw   = values[clamped].astype(np.float64)
    valid = indices >= 0                          # bar is after the first quote

    if max_age_minutes is not None:
        # age in nanoseconds → minutes;  6e10 ns = 1 min
        age_ns = (bar_times.astype(np.int64) - dates[clamped].astype(np.int64)).astype(np.float64)
        valid  = valid & (age_ns / 6e10 <= max_age_minutes)

    valid &= raw > 0
    return np.where(valid, raw, np.nan)


def _has_expiry_quote(opt_data: dict | None, expiry_date: datetime.date) -> bool:
    """True if opt_data has at least one quote on expiry date at or after 3 PM."""
    if opt_data is None or len(opt_data['dates']) == 0:
        return False
    threshold = (pd.Timestamp(expiry_date) + pd.Timedelta(hours=EXPIRY_QUOTE_MIN_HOUR)).to_datetime64()
    end       = (pd.Timestamp(expiry_date) + pd.Timedelta(days=1)).to_datetime64()
    dates     = opt_data['dates']
    return bool(np.any((dates >= threshold) & (dates < end)))


# ══════════════════════════════════════════════════════════════════════════════
# OPTION VARIANT HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _get_expiry_friday(entry_date: datetime.date, weeks_ahead: int) -> datetime.date:
    """Return the Friday of the week `weeks_ahead` weeks from entry_date's week."""
    dow = entry_date.weekday()   # Mon=0, Fri=4
    days_to_friday = (4 - dow) % 7   # 0 if already Friday
    return entry_date + datetime.timedelta(days=days_to_friday + weeks_ahead * 7)


def _find_strike_by_chain(
    option_index: pd.DataFrame,
    expiry_int:   int,
    entry_price:  float,
    strike_label: str,
) -> float | None:
    """
    Walk the option chain for a given expiry and return the Nth available call
    strike above or below entry_price.

    Strike label format:  s{direction}{step}
      direction '+' → above entry_price;  '-' → below entry_price
      step 0 = first, 1 = second, 2 = third

    Examples (entry_price = 175.42, chain = [172, 173, 174, 175, 176, 177, 178]):
      s-0 → 175   (first below 175.42)
      s-1 → 174   (second below)
      s-2 → 173   (third below)
      s+0 → 176   (first above 175.42)
      s+1 → 177   (second above)
      s+2 → 178   (third above)
    """
    strikes = (
        option_index[
            (option_index['call_put']        == 'C') &
            (option_index['expiration_date'] == expiry_int)
        ]['strike_price']
        .drop_duplicates()
        .sort_values()
        .tolist()
    )
    if not strikes:
        return None

    direction = strike_label[1]   # '+' or '-'
    step      = int(strike_label[2])   # 0, 1, 2

    if direction == '-':
        candidates = sorted([s for s in strikes if s < entry_price], reverse=True)
    else:
        candidates = sorted([s for s in strikes if s > entry_price])

    return float(candidates[step]) if len(candidates) > step else None


def find_cc_variant(
    entry_date:   datetime.date,
    entry_ts:     pd.Timestamp,
    entry_price:  float,
    expiry_label: str,    # 'w0', 'w1', 'w2'
    strike_label: str,    # 's-2', 's-1', 's-0', 's+0', 's+1', 's+2'
) -> dict | None:
    """
    Find a CC variant by walking the option chain.

    Strike is selected by position relative to entry_price (not by dollar offset).
    Does NOT gate on expiry quote existence — returns the variant if the contract
    exists. open_price may be None if no fresh entry quote is available.

    Returns a dict with all variant fields, or None if no matching strike in chain.
    """
    option_index = load_option_index()
    weeks_ahead  = int(expiry_label[1])
    friday       = _get_expiry_friday(entry_date, weeks_ahead)
    expiry_int   = int(friday.strftime('%y%m%d'))
    variant_key  = f"{expiry_label}/{strike_label}"

    strike = _find_strike_by_chain(option_index, expiry_int, entry_price, strike_label)
    if strike is None:
        return None

    cands = option_index[
        (option_index['call_put']        == 'C') &
        (option_index['expiration_date'] == expiry_int) &
        (option_index['strike_price']    == strike)
    ]
    if cands.empty:
        return None

    contract = cands.iloc[0].to_dict()
    opt_data = load_option_data(contract)

    open_price = get_option_price_at(opt_data, entry_ts, 'avg_bid', MAX_QUOTE_AGE_MINUTES)

    return {
        'expiry_label':  expiry_label,
        'strike_label':  strike_label,
        'variant_key':   variant_key,
        'contract':      contract,
        'opt_data':      opt_data,
        'strike':        strike,
        'expiry_date':   friday,
        'expiry_int':    expiry_int,
        'open_price':    open_price,
    }


def find_all_cc_variants(
    entry_date:  datetime.date,
    entry_ts:    pd.Timestamp,
    entry_price: float,
) -> dict:
    """Return dict keyed by (expiry_label, strike_label) → variant dict or None."""
    result = {}
    for expiry_label, strike_label in OPTION_VARIANTS:
        result[(expiry_label, strike_label)] = find_cc_variant(
            entry_date, entry_ts, entry_price, expiry_label, strike_label
        )
    return result


# ══════════════════════════════════════════════════════════════════════════════
# SAMPLE DRAWING
# ══════════════════════════════════════════════════════════════════════════════

def draw_sample(df: pd.DataFrame, seed: int = RANDOM_SEED) -> list:
    valid_mask = (
        (df['ses_after_10']   == 1) &
        (df['ses_before_345'] == 1) &
        (df['atr_spike']      == 0) &
        df['atr_14'].notna() &
        df['rsi_14'].notna() &
        df['chp_14'].notna()
    )
    valid_idx = df.index[valid_mask].tolist()
    n = min(N_SAMPLE, len(valid_idx))
    random.seed(seed)
    sample = random.sample(valid_idx, n)
    sample.sort()
    print(f"[sample]  Valid bars: {len(valid_idx):,}  ->  sampled: {n:,}")
    return sample


# ══════════════════════════════════════════════════════════════════════════════
# CONTEXT BUILDER  (entry date through end of df)
# ══════════════════════════════════════════════════════════════════════════════

def _build_cc_context(
    df: pd.DataFrame,
    entry_global_idx: int,
) -> tuple[pd.DataFrame | None, int | None]:
    """
    Return (df, entry_global_idx) directly — no copy, no column conversion.

    The forward walk in simulate_all_variants starts at entry_global_idx + 1,
    so bars before the entry are never touched. df already spans only the
    current window (set up by prepare_window) so no further filtering is needed.
    """
    if entry_global_idx >= len(df):
        return None, None
    return df, entry_global_idx


# ══════════════════════════════════════════════════════════════════════════════
# VARIANT RESULT BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def _build_result(
    variant:         dict,
    exit_time_str:   str,        # ISO timestamp string
    bar_avg_bid:     float,      # stock market bid at exit bar
    cc_close_reason: str,
    cc_close_price:  float | None,
    bars_held:       int,
    entry_price:     float,
    shares:          int,
) -> dict:
    """Build a result dict for a closed variant from pre-extracted scalar values."""
    stock_pnl = shares * bar_avg_bid - shares * entry_price - COMMISSION

    if variant['open_price'] is not None and cc_close_price is not None:
        opt_pnl      = (variant['open_price'] - cc_close_price) * shares - COMMISSION
        combined_pnl = stock_pnl + opt_pnl
        is_winner    = combined_pnl > 0
        data_status  = 'ok'
    else:
        opt_pnl      = None
        combined_pnl = None
        is_winner    = None
        data_status  = 'no_open_price' if variant['open_price'] is None else 'no_close_price'

    return {
        'variant_key':        variant['variant_key'],
        'expiry_label':       variant['expiry_label'],
        'strike_label':       variant['strike_label'],
        'strike':             variant['strike'],
        'expiry_date':        variant['expiry_date'],
        'open_price':         variant['open_price'],
        'cc_close_price':     cc_close_price,
        'cc_close_reason':    cc_close_reason,
        'cc_close_time':      exit_time_str,
        'stock_exit_price':   round(bar_avg_bid, 4),
        'stock_exit_reason':  cc_close_reason,
        'stock_exit_time':    exit_time_str,
        'bars_held':          bars_held,
        'shares':             shares,
        'stock_pnl':          round(stock_pnl, 2),
        'option_pnl':         round(opt_pnl, 2) if opt_pnl is not None else None,
        'combined_pnl':       round(combined_pnl, 2) if combined_pnl is not None else None,
        'is_winner':          is_winner,
        'data_status':        data_status,
    }


def _not_found_result(expiry_label: str, strike_label: str) -> dict:
    """Result dict for a variant where no contract was found."""
    variant_key = f"{expiry_label}/{strike_label}"
    return {
        'variant_key':       variant_key,
        'expiry_label':      expiry_label,
        'strike_label':      strike_label,
        'strike':            None,
        'expiry_date':       None,
        'open_price':        None,
        'cc_close_price':    None,
        'cc_close_reason':   'no_contract',
        'cc_close_time':     None,
        'stock_exit_price':  None,
        'stock_exit_reason': None,
        'stock_exit_time':   None,
        'bars_held':         None,
        'shares':            None,
        'stock_pnl':         None,
        'option_pnl':        None,
        'combined_pnl':      None,
        'is_winner':         None,
        'data_status':       'no_contract',
    }


# ══════════════════════════════════════════════════════════════════════════════
# CORE MULTI-VARIANT SIMULATION
# ══════════════════════════════════════════════════════════════════════════════

def simulate_all_variants(
    df_slice:      pd.DataFrame,
    entry_iloc:    int,
    all_variants:  dict,
    entry_price:   float,
    shares:        int,
    capture_evals: bool = False,   # retained for API compat; evals not computed
) -> tuple[dict, list]:
    """
    Determine exit for all 18 variants using vectorized numpy operations.

    For each open variant:
      1. Pre-extract numpy arrays from df_slice once (avoids per-bar iloc).
      2. One _lookup_prices_vectorized call gives the option-ask array for all
         future bars in a single searchsorted pass.
      3. np.argmax on boolean masks finds the first buyback / expiry bar —
         no Python bar-by-bar loop needed.

    Returns (variant_results, [])  — evals list is always empty (was never used).
    """
    open_variants  = {k: v for k, v in all_variants.items() if v is not None}
    closed_results = {}

    n_future = len(df_slice) - entry_iloc - 1

    if n_future <= 0:
        # No bars after entry — immediate window end for all open variants
        last         = df_slice.iloc[-1]
        last_time_np = last['date']
        last_str     = str(pd.Timestamp(last_time_np))
        last_bid     = float(last['avg_bid'])
        for key, variant in open_variants.items():
            late_ask = get_option_price_at(variant['opt_data'], last_time_np, 'avg_ask')
            late_tv  = (
                late_ask - max(0.0, last_bid - variant['strike'])
                if late_ask is not None else None
            )
            if late_tv is not None and late_tv < BUYBACK_TV:
                closed_results[key] = _build_result(
                    variant, last_str, last_bid,
                    'buyback_late_data', late_ask, 0, entry_price, shares
                )
            else:
                close_bid = get_option_price_at(variant['opt_data'], last_time_np, 'avg_bid')
                closed_results[key] = _build_result(
                    variant, last_str, last_bid,
                    'window_end', close_bid, 0, entry_price, shares
                )
    else:
        # Pre-extract numpy arrays — one allocation, no per-bar pandas overhead
        future        = df_slice.iloc[entry_iloc + 1:]
        bar_times_np  = future['date'].values.astype('datetime64[ns]')
        bar_avg_bids  = future['avg_bid'].values.astype(np.float64)

        for key, variant in open_variants.items():
            opt_data = variant['opt_data']

            # Vectorized ask lookup — one searchsorted pass for all future bars
            opt_asks = _lookup_prices_vectorized(
                opt_data, bar_times_np, 'avg_ask', MAX_QUOTE_AGE_MINUTES
            )

            # Buyback: first bar whose ask-side time value falls below the threshold
            #   tv_ask = option_ask - max(0, avg_bid - strike)
            strike   = variant['strike']
            intrinsic = np.maximum(0.0, bar_avg_bids - strike)
            tv_ask    = opt_asks - intrinsic
            buyback_mask = ~np.isnan(tv_ask) & (tv_ask < BUYBACK_TV)
            buyback_idx  = int(np.argmax(buyback_mask)) if buyback_mask.any() else n_future

            # Expiry: first bar at or after 3 PM on expiry Friday
            expiry_dt64 = (
                pd.Timestamp(variant['expiry_date']) + pd.Timedelta(hours=EXPIRY_QUOTE_MIN_HOUR)
            ).to_datetime64()
            expiry_mask = bar_times_np >= expiry_dt64
            expiry_idx  = int(np.argmax(expiry_mask)) if expiry_mask.any() else n_future

            exit_idx = min(buyback_idx, expiry_idx)

            if exit_idx >= n_future:
                # Window end (neither buyback nor expiry fired before data ran out)
                exit_str  = str(pd.Timestamp(bar_times_np[-1]))
                exit_bid  = float(bar_avg_bids[-1])
                bars_held = n_future
                late_ask  = get_option_price_at(opt_data, bar_times_np[-1], 'avg_ask')
                late_tv   = (
                    late_ask - max(0.0, exit_bid - strike)
                    if late_ask is not None else None
                )
                if late_tv is not None and late_tv < BUYBACK_TV:
                    cc_reason = 'buyback_late_data'
                    cc_price  = late_ask
                else:
                    cc_price  = get_option_price_at(opt_data, bar_times_np[-1], 'avg_bid')
                    cc_reason = 'window_end'
            else:
                exit_str  = str(pd.Timestamp(bar_times_np[exit_idx]))
                exit_bid  = float(bar_avg_bids[exit_idx])
                bars_held = exit_idx + 1

                if exit_idx == buyback_idx:
                    # Buyback wins; also handles the tie case (same bar)
                    cc_reason = 'buyback'
                    cc_price  = float(opt_asks[buyback_idx])
                else:
                    # Expiry
                    if exit_bid > variant['strike']:
                        cc_reason = 'assigned'
                        cc_price  = 0.0
                        exit_bid  = float(variant['strike'])   # sold at strike
                    else:
                        cc_reason = 'expired_otm'
                        cc_price  = 0.0

            closed_results[key] = _build_result(
                variant, exit_str, exit_bid,
                cc_reason, cc_price, bars_held, entry_price, shares
            )

    # Not-found variants (contract missing from option index)
    for key in OPTION_VARIANTS:
        if key not in closed_results:
            expiry_label, strike_label = key
            closed_results[key] = _not_found_result(expiry_label, strike_label)

    return closed_results, []   # evals always empty


# ══════════════════════════════════════════════════════════════════════════════
# LOG WRITERS — TABLE FORMAT
# ══════════════════════════════════════════════════════════════════════════════

_LOG_EVAL_HEAD = 5
_LOG_EVAL_TAIL = 5

# Ordered variant keys for display
_VARIANT_DISPLAY_ORDER = list(OPTION_VARIANTS)


def _fw(val, width: int, align: str = 'left') -> str:
    """Format a value to fixed width."""
    s = str(val) if val is not None else 'N/A'
    if align == 'right':
        return s.rjust(width)
    elif align == 'center':
        return s.center(width)
    return s.ljust(width)


def _log_trade_table(
    fh,
    trade_no:        int,
    indicators:      dict,
    entry_snapshot:  dict,
    variant_results: dict,
    evals:           list,
):
    """Write a trade to the log in ASCII table format."""
    t   = indicators.get('trend', '').upper()
    m   = indicators.get('momentum', '').upper()
    v   = indicators.get('volatility', '').upper()
    vol = indicators.get('volume', '').upper()
    combo_str    = '+'.join(x for x in (t, m, v, vol) if x)
    entry_time   = entry_snapshot['entry_time']
    entry_price  = entry_snapshot['entry_price']
    shares       = entry_snapshot['shares']
    atr_val      = entry_snapshot.get('atr_at_entry', 'N/A')
    rsi_val      = entry_snapshot.get('rsi_at_entry', 'N/A')
    adx_val      = entry_snapshot.get('adx_at_entry', 'N/A')
    vwap_val     = entry_snapshot.get('vwap_at_entry', 'N/A')

    def _fmt_num(v, fmt='.2f'):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return 'N/A'
        try:
            return format(float(v), fmt)
        except (TypeError, ValueError):
            return str(v)

    fh.write('\n' + '=' * 80 + '\n')
    fh.write(
        f" TRADE #{trade_no}  |  {combo_str}  |"
        f"  Entry: {entry_time}  |  ${entry_price} x {shares}sh\n"
    )
    fh.write('=' * 80 + '\n')
    fh.write(
        f" ATR: {_fmt_num(atr_val)}   RSI: {_fmt_num(rsi_val)}"
        f"   ADX: {_fmt_num(adx_val)}   VWAP: ${_fmt_num(vwap_val)}\n"
    )

    # ── Option matrix at entry ─────────────────────────────────────────────────
    fh.write('\n OPTION MATRIX AT ENTRY:\n')
    fh.write(' +----------+--------+--------+-----------+\n')
    fh.write(' | Variant  | Expiry | Strike | Open Bid  |\n')
    fh.write(' +----------+--------+--------+-----------+\n')
    for key in _VARIANT_DISPLAY_ORDER:
        res = variant_results.get(key)
        if res is None:
            variant_key  = f"{key[0]}/{key[1]}"
            expiry_str   = '--'
            strike_str   = '--'
            open_bid_str = 'N/A'
        else:
            variant_key  = res['variant_key']
            expiry_str   = str(res['expiry_date'].strftime('%y%m%d')) if res['expiry_date'] else '--'
            strike_str   = _fmt_num(res['strike'], '.2f') if res['strike'] is not None else '--'
            op           = res['open_price']
            open_bid_str = f"{op:>9.2f}" if op is not None else '       N/A'
        fh.write(
            f" | {_fw(variant_key, 8)} | {_fw(expiry_str, 6)} "
            f"| {_fw(strike_str, 6)} | {_fw(open_bid_str, 9)} |\n"
        )
    fh.write(' +----------+--------+--------+-----------+\n')

    # ── Price evals (first 5 / last 5) ────────────────────────────────────────
    if evals:
        # Collect all variant keys that appear in evals
        all_vkeys = []
        for key in _VARIANT_DISPLAY_ORDER:
            vkey = f"{key[0]}/{key[1]}"
            all_vkeys.append(vkey)

        # Build header
        col_w = 8
        time_w = 20
        stock_w = 8
        header_row = f" | {'Time':<{time_w}} | {'Stock':<{stock_w}} |"
        for vk in all_vkeys:
            header_row += f" {_fw(vk, col_w)} |"
        sep = ' +' + '-' * (time_w + 2) + '+' + '-' * (stock_w + 2) + '+' + ('-' * (col_w + 2) + '+') * len(all_vkeys)

        fh.write('\n PRICE EVALS (first 5 / last 5 bars):\n')
        fh.write(sep + '\n')
        fh.write(header_row + '\n')
        fh.write(sep + '\n')

        def _write_eval_row(er):
            t_str    = _fw(er['time'], time_w)
            stk_str  = _fw(f"{er['stock_wap']:.2f}", stock_w)
            row      = f" | {t_str} | {stk_str} |"
            for vk in all_vkeys:
                ask = er['asks'].get(vk)
                ask_str = f"{ask:.2f}" if ask is not None else 'N/A'
                row += f" {_fw(ask_str, col_w)} |"
            fh.write(row + '\n')

        head = evals[:_LOG_EVAL_HEAD]
        tail = evals[-_LOG_EVAL_TAIL:] if len(evals) > _LOG_EVAL_HEAD + _LOG_EVAL_TAIL else []
        skip = len(evals) - len(head) - len(tail)

        for er in head:
            _write_eval_row(er)
        if skip > 0:
            skip_row = f" | {'... ' + str(skip) + ' rows skipped':<{time_w}} |" + ' ' * (stock_w + 2) + '|'
            for _ in all_vkeys:
                skip_row += ' ' * (col_w + 2) + '|'
            fh.write(skip_row + '\n')
        for er in tail:
            _write_eval_row(er)
        fh.write(sep + '\n')

    # ── Results table ──────────────────────────────────────────────────────────
    fh.write('\n RESULTS:\n')
    fh.write(' +----------+---------------------+--------------+---------+---------+---------+-------+\n')
    fh.write(' | Variant  | Exit Time           | Exit Reason  | Stock$  | Option$ | Total$  |  Win? |\n')
    fh.write(' +----------+---------------------+--------------+---------+---------+---------+-------+\n')
    for key in _VARIANT_DISPLAY_ORDER:
        res = variant_results.get(key)
        if res is None:
            variant_key  = f"{key[0]}/{key[1]}"
            exit_time    = _fw('N/A', 19)
            exit_reason  = _fw('N/A', 12)
            stock_str    = _fw('N/A', 7, 'right')
            opt_str      = _fw('N/A', 7, 'right')
            total_str    = _fw('N/A', 7, 'right')
            win_str      = _fw('N/A', 5, 'center')
        else:
            variant_key = res['variant_key']
            et          = res.get('cc_close_time') or 'N/A'
            exit_time   = _fw(et, 19)
            exit_reason = _fw(res['cc_close_reason'], 12)

            sp = res['stock_pnl']
            op = res['option_pnl']
            tp = res['combined_pnl']

            stock_str = _fw((f"+{sp:.2f}" if sp >= 0 else f"{sp:.2f}") if sp is not None else 'N/A', 7, 'right')
            opt_str   = _fw((f"+{op:.2f}" if op >= 0 else f"{op:.2f}") if op is not None else 'N/A', 7, 'right')
            total_str = _fw((f"+{tp:.2f}" if tp >= 0 else f"{tp:.2f}") if tp is not None else 'N/A', 7, 'right')
            iw        = res['is_winner']
            win_str   = _fw('YES' if iw is True else ('NO' if iw is False else 'N/A'), 5, 'center')

        fh.write(
            f" | {_fw(variant_key, 8)} | {exit_time} | {exit_reason} |"
            f" {stock_str} | {opt_str} | {total_str} | {win_str} |\n"
        )
    fh.write(' +----------+---------------------+--------------+---------+---------+---------+-------+\n')
    fh.write('\n')


def _log_model_start(fh, seq_no, model_id, t, m, v, vol):
    header = f"-----  START:  seq_no: [{seq_no}] model_id: [{model_id}]  "
    fh.write(f"\n{header + '-' * max(0, 72 - len(header))}\n")
    fh.write(f"trend: [{t}] momentum: [{m}] volatility: [{v}] volume: [{vol}]\n")
    fh.write("\n----- Details\n\n")


def _log_model_end(fh, seq_no, model_id, metrics):
    fh.write("----- Summary\n")
    fh.write(
        f"number_of_trades: [{int(metrics['n_trades'])}]    "
        f"win_rate: [{metrics['win_rate']}]\n"
    )
    fh.write(
        f"avg_entry_price: [{metrics['avg_entry']}]    "
        f"avg_exit_price: [{metrics['avg_exit']}]    "
        f"avg_bars_held: [{metrics['avg_duration_bars']}]\n\n"
    )
    fh.write(
        f"total_pnl: [{metrics['total_pnl']}]    "
        f"avg_pnl: [{metrics['avg_pnl']}]    "
        f"profit_factor: [{metrics['profit_factor']}]\n"
    )
    fh.write(
        f"sharpe: [{metrics['sharpe']}]    "
        f"max_drawdown: [{metrics['max_drawdown']}]\n"
    )
    footer = f"\n-----  END:  seq_no: {seq_no}, model_id: {model_id},  "
    fh.write(f"{footer + '-' * max(0, 72 - len(footer))}\n")


# ══════════════════════════════════════════════════════════════════════════════
# WINDOW PREPARATION
# ══════════════════════════════════════════════════════════════════════════════

def prepare_window(
    df_signals:   pd.DataFrame,
    window_start: str,
    window_end:   str,
    seed:         int,
) -> tuple:
    mask = (
        (df_signals['date'] >= pd.Timestamp(window_start)) &
        (df_signals['date'] <= pd.Timestamp(window_end))
    )
    df = df_signals[mask].reset_index(drop=True)
    if df.empty:
        return None, 'no_data'

    sample_idx = draw_sample(df, seed)
    if not sample_idx:
        return None, 'no_sample'

    df = df.copy()
    day_dict    = {}
    day_pos_map = {}
    for date, group in df.groupby('fnd_trade_date'):
        grp_reset = group.reset_index(drop=True)
        day_dict[date] = grp_reset
        for pos, global_idx in enumerate(group.index):
            day_pos_map[global_idx] = pos

    return (df, sample_idx, day_dict, day_pos_map), 'ok'


# ══════════════════════════════════════════════════════════════════════════════
# METRICS HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _calc_metrics(positions: list[dict]) -> dict:
    from collections import defaultdict
    n     = len(positions)
    wins  = [p for p in positions if p['is_winner']]
    loses = [p for p in positions if not p['is_winner']]

    gross_w = sum(p['combined_pnl'] for p in wins)  if wins  else 0.0
    gross_l = sum(p['combined_pnl'] for p in loses) if loses else 0.0
    pf      = abs(gross_w / gross_l) if gross_l < 0 else float('inf')

    pnls = [p['combined_pnl'] for p in positions]
    cum  = pd.Series(pnls).cumsum()
    drawdown = (cum - cum.cummax()).min()

    daily: dict = defaultdict(float)
    for p in positions:
        dt = str(p['entry_time'])[:10]
        daily[dt] += p['combined_pnl']
    daily_s = pd.Series(list(daily.values()))
    sharpe  = (
        daily_s.mean() / daily_s.std() * np.sqrt(252)
        if daily_s.std() > 0 else 0.0
    )

    return {
        'n_trades':          n,
        'win_rate':          round(sum(1 for p in positions if p['is_winner']) / n * 100, 1),
        'avg_entry':         round(sum(p['entry_price'] for p in positions) / n, 2),
        'avg_exit':          round(sum(p['exit_price']  for p in positions) / n, 2),
        'avg_duration_bars': round(sum(p['bars_held']   for p in positions) / n, 1),
        'total_pnl':         round(sum(pnls), 2),
        'avg_pnl':           round(sum(pnls) / n, 2),
        'profit_factor':     round(min(pf, 1e9), 3),
        'sharpe':            round(sharpe, 3),
        'max_drawdown':      round(float(drawdown), 2),
        'pnl_positive':      sum(pnls) > 0,
        'status':            'ok',
    }


def _calc_variant_metrics(
    positions: list[dict],
    variant_key: str,
    expiry_label: str,
    strike_label: str,
) -> dict:
    """Compute metrics for a single variant's positions."""
    valid = [p for p in positions if p.get('combined_pnl') is not None and p.get('is_winner') is not None]
    if not valid:
        em = _empty_metrics('no_valid_positions')
        em['variant_key']  = variant_key
        em['expiry_label'] = expiry_label
        em['strike_label'] = strike_label
        return em
    m = _calc_metrics(valid)
    m['variant_key']  = variant_key
    m['expiry_label'] = expiry_label
    m['strike_label'] = strike_label
    return m


def _empty_metrics(status: str) -> dict:
    return {
        'n_trades': 0, 'win_rate': 0.0,
        'avg_entry': 0.0, 'avg_exit': 0.0, 'avg_duration_bars': 0.0,
        'total_pnl': 0.0, 'avg_pnl': 0.0,
        'profit_factor': 0.0, 'sharpe': 0.0, 'max_drawdown': 0.0,
        'pnl_positive': False, 'status': status,
    }


# ══════════════════════════════════════════════════════════════════════════════
# CORE SIMULATION — one indicator combo, all 12 variants
# ══════════════════════════════════════════════════════════════════════════════

def run_combo(
    df:            pd.DataFrame,
    sample_idx:    list,
    day_dict:      dict,
    day_pos_map:   dict,
    indicators:    dict[str, str],
    capture_evals: bool = False,
) -> tuple[dict, list[dict], list[dict]]:
    """
    Fire the composite buy signal, open stock + all 12 CC variants at entry,
    simulate each variant to close.

    Returns (metrics_dict, trades_list, positions_list).

    trades_list: flat list — for each entry × each of 12 variants:
      - one row with leg='stock'
      - one row with leg='option' (skipped if open_price is None)

    positions_list: one row per entry × variant that has combined_pnl is not None

    metrics_dict: aggregated across ALL variants with valid combined_pnl
    """
    active   = {cat: ind for cat, ind in indicators.items() if ind}
    buy_cols = [f'bsig_{ind}' for ind in active.values()]

    sample_df = df.loc[sample_idx].copy()
    composite = pd.Series(True, index=sample_df.index)
    for bc in buy_cols:
        if bc in sample_df.columns:
            composite &= sample_df[bc].astype(bool)

    fired_idx  = sample_df.index[composite].tolist()
    positions  = []   # combined_pnl-valid entries for aggregate metrics
    trades     = []   # flat trade rows (stock + option legs)
    pos_list   = []   # per-entry × variant position rows
    trade_no   = 0

    for idx in fired_idx:
        row          = df.loc[idx]
        entry_price  = float(row['avg_ask'])
        entry_ts     = pd.Timestamp(row['date'])
        entry_date   = entry_ts.date()
        trade_date   = row['fnd_trade_date']

        atr_val  = row.get('atr_14',   np.nan)
        rsi_val  = row.get('rsi_14',   np.nan)
        adx_val  = row.get('adx_14',   np.nan)
        vwap_val = row.get('vwp_vwap', np.nan)

        entry_snapshot = {
            'entry_time':    str(row['date']),
            'entry_price':   round(entry_price, 4),
            'shares':        SHARES,
            'atr_at_entry':  round(float(atr_val)  if pd.notna(atr_val)  else np.nan, 4),
            'rsi_at_entry':  round(float(rsi_val)  if pd.notna(rsi_val)  else np.nan, 4),
            'adx_at_entry':  round(float(adx_val)  if pd.notna(adx_val)  else np.nan, 4),
            'vwap_at_entry': round(float(vwap_val) if pd.notna(vwap_val) else np.nan, 4),
        }

        # Find all 12 variants
        all_variants = find_all_cc_variants(entry_date, entry_ts, entry_price)

        # Entry TV gate: drop variants whose opening time value is outside
        # [CC_TV_MIN, CC_TV_MAX]. Variants missing an open_price also fail here.
        cc_tv_by_key = {}
        for key, variant in list(all_variants.items()):
            if variant is None:
                continue
            op_price = variant.get('open_price')
            strike_v = variant.get('strike')
            if op_price is None or strike_v is None:
                all_variants[key] = None
                continue
            cc_tv = op_price - max(0.0, entry_price - strike_v)
            if cc_tv < CC_TV_MIN or cc_tv > CC_TV_MAX:
                all_variants[key] = None
                continue
            cc_tv_by_key[key] = round(cc_tv, 4)

        # If every variant failed the TV gate, skip this entry entirely
        if not any(v is not None for v in all_variants.values()):
            continue

        # Build context slice from entry day through end of df
        df_slice, entry_iloc = _build_cc_context(df, idx)
        if df_slice is None or entry_iloc is None:
            continue

        # Simulate all variants in one forward walk
        variant_results, evals = simulate_all_variants(
            df_slice, entry_iloc, all_variants, entry_price, SHARES,
            capture_evals=capture_evals,
        )

        trade_no += 1

        base = {
            'trade_no':   trade_no,
            'trade_date': str(trade_date),
            **{cat: indicators.get(cat, '') for cat in ['trend', 'momentum', 'volatility', 'volume']},
            **entry_snapshot,
        }

        # Emit trade rows and position rows for each variant
        for key in OPTION_VARIANTS:
            expiry_label, strike_label = key
            res = variant_results.get(key)
            if res is None:
                res = _not_found_result(expiry_label, strike_label)

            variant_key    = res['variant_key']
            combined_pnl   = res['combined_pnl']
            is_winner      = res['is_winner']
            stock_pnl      = res['stock_pnl']
            option_pnl     = res['option_pnl']
            open_price     = res['open_price']
            cc_close_price = res['cc_close_price']
            exit_time      = res.get('cc_close_time') or res.get('stock_exit_time')
            exit_price     = res.get('stock_exit_price')
            exit_reason    = res.get('cc_close_reason')
            bars_held      = res['bars_held']
            strike         = res['strike']
            expiry_date    = res['expiry_date']
            data_status    = res['data_status']

            # Stock leg
            stock_cost     = SHARES * entry_price if exit_price is not None else None
            stock_proceeds = SHARES * exit_price  if exit_price is not None else None

            cc_tv_entry = cc_tv_by_key.get(key)

            trades.append({
                **base,
                'leg':          'stock',
                'variant_key':  variant_key,
                'expiry_label': expiry_label,
                'strike_label': strike_label,
                'strike':       strike,
                'expiry_date':  str(expiry_date) if expiry_date else None,
                'cc_open_price': open_price,
                'cc_tv_at_entry': cc_tv_entry,
                'exit_time':    exit_time,
                'exit_price':   exit_price,
                'exit_reason':  exit_reason,
                'bars_held':    bars_held,
                'shares':       SHARES,
                'cost':         round(stock_cost, 2) if stock_cost is not None else None,
                'proceeds':     round(stock_proceeds, 2) if stock_proceeds is not None else None,
                'pnl_dollar':   stock_pnl,
                'option_pnl':   option_pnl,
                'combined_pnl': combined_pnl,
                'is_winner':    is_winner,
                'data_status':  data_status,
            })

            # Option leg (only if open_price is available)
            if open_price is not None:
                opt_cost     = open_price * SHARES
                opt_proceeds = cc_close_price * SHARES if cc_close_price is not None else None
                trades.append({
                    **base,
                    'leg':          'option',
                    'variant_key':  variant_key,
                    'expiry_label': expiry_label,
                    'strike_label': strike_label,
                    'strike':       strike,
                    'expiry_date':  str(expiry_date) if expiry_date else None,
                    'cc_open_price': open_price,
                    'cc_tv_at_entry': cc_tv_entry,
                    'exit_time':    exit_time,
                    'exit_price':   cc_close_price,
                    'exit_reason':  exit_reason,
                    'bars_held':    bars_held,
                    'shares':       SHARES,
                    'cost':         round(opt_cost, 2),
                    'proceeds':     round(opt_proceeds, 2) if opt_proceeds is not None else None,
                    'pnl_dollar':   option_pnl,
                    'option_pnl':   option_pnl,
                    'combined_pnl': combined_pnl,
                    'is_winner':    is_winner,
                    'data_status':  data_status,
                })

            # Position row (for metrics computation)
            if combined_pnl is not None and is_winner is not None and exit_price is not None and bars_held is not None:
                pos_row = {
                    'trade_no':     trade_no,
                    'variant_key':  variant_key,
                    'expiry_label': expiry_label,
                    'strike_label': strike_label,
                    'entry_time':   entry_snapshot['entry_time'],
                    'entry_price':  entry_price,
                    'exit_price':   exit_price,
                    'bars_held':    bars_held,
                    'stock_pnl':    stock_pnl,
                    'option_pnl':   option_pnl,
                    'combined_pnl': combined_pnl,
                    'is_winner':    is_winner,
                    **{cat: indicators.get(cat, '') for cat in ['trend', 'momentum', 'volatility', 'volume']},
                    'trade_date':   str(trade_date),
                }
                pos_list.append(pos_row)
                positions.append(pos_row)   # for aggregate metrics


    if not positions:
        return _empty_metrics('no_signals'), [], []

    return _calc_metrics(positions), trades, pos_list


# ══════════════════════════════════════════════════════════════════════════════
# SINGLE-RUN WRAPPER
# ══════════════════════════════════════════════════════════════════════════════

def run_single(
    df_signals:   pd.DataFrame,
    indicators:   dict[str, str],
    window_start: str,
    window_end:   str,
    seed:         int,
    run_no:       int,
    capture_evals: bool = False,
) -> tuple[dict, list[dict], list[dict]]:
    window_data, status = prepare_window(df_signals, window_start, window_end, seed)
    if window_data is None:
        return _empty_summary(run_no, indicators, window_start, window_end, seed, status), [], []

    df, sample_idx, day_dict, day_pos_map = window_data
    metrics, trades, pos_list = run_combo(
        df, sample_idx, day_dict, day_pos_map, indicators,
        capture_evals=capture_evals,
    )

    context = {
        'run_no':       run_no,
        'window_start': window_start,
        'window_end':   window_end,
        'seed':         seed,
    }
    full_trades   = [{**context, **tr} for tr in trades]
    full_pos_list = [{**context, **p}  for p  in pos_list]

    summary = {
        **context,
        **{cat: indicators[cat] for cat in ['trend', 'momentum', 'volatility', 'volume']},
        **metrics,
    }
    return summary, full_trades, full_pos_list


def _empty_summary(run_no, indicators, window_start, window_end, seed, status) -> dict:
    return {
        'run_no':        run_no,
        'window_start':  window_start,
        'window_end':    window_end,
        'seed':          seed,
        **{cat: indicators[cat] for cat in ['trend', 'momentum', 'volatility', 'volume']},
        **_empty_metrics(status),
    }


# ══════════════════════════════════════════════════════════════════════════════
# STANDALONE CLI
# ══════════════════════════════════════════════════════════════════════════════

def parse_args():
    parser = argparse.ArgumentParser(
        description='Run one indicator combo across N windows (all 12 CC variants).'
    )
    parser.add_argument('--trend',      default='adx',  help='Trend indicator')
    parser.add_argument('--momentum',   default='frc',  help='Momentum indicator')
    parser.add_argument('--volatility', default='atr',  help='Volatility indicator')
    parser.add_argument('--volume',     default='vrc',  help='Volume indicator')
    parser.add_argument('--seed', type=int, default=None,
                        help='Meta RNG seed (omit for a fresh random seed each run)')
    parser.add_argument(
        '--data-first', type=str, default='2023-01-01',
        help='Start of data range (YYYY-MM-DD, default: 2023-01-01)',
    )
    parser.add_argument(
        '--data-last', type=str, default='2026-02-28',
        help='End of data range (YYYY-MM-DD, default: 2026-02-28)',
    )
    parser.add_argument(
        '--window-days', type=int, default=14,
        help='Calendar days per test window (default: 14)',
    )
    parser.add_argument(
        '--cc_tv_min', type=float, default=1.00,
        help='Minimum opening time value to open a position (default: 1.00)',
    )
    parser.add_argument(
        '--cc_tv_max', type=float, default=3.00,
        help='Maximum opening time value to open a position (default: 3.00)',
    )
    parser.add_argument(
        '--buyback_tv', type=float, default=0.25,
        help='Buyback threshold on the ask-side time value (default: 0.25)',
    )
    parser.add_argument(
        '--expiry-label', nargs='+', default=None,
        metavar='LABEL',
        help='Expiry weeks to include: w0 w1 w2 (default: all three)',
    )
    parser.add_argument(
        '--strike-label', nargs='+', default=None,
        metavar='LABEL',
        help='Strike labels to include: s-2 s-1 s-0 s+0 s+1 s+2 (default: all six)',
    )
    args = parser.parse_args()

    global BUYBACK_TV, CC_TV_MIN, CC_TV_MAX, OPTION_VARIANTS
    BUYBACK_TV = args.buyback_tv
    CC_TV_MIN  = args.cc_tv_min
    CC_TV_MAX  = args.cc_tv_max

    expiry_filter = args.expiry_label or EXPIRY_WEEKS
    strike_filter = args.strike_label or STRIKE_LABELS

    invalid_expiry = [e for e in expiry_filter if e not in EXPIRY_WEEKS]
    invalid_strike = [s for s in strike_filter if s not in STRIKE_LABELS]
    if invalid_expiry:
        parser.error(f"Invalid --expiry-label value(s): {invalid_expiry}. Choose from {EXPIRY_WEEKS}")
    if invalid_strike:
        parser.error(f"Invalid --strike-label value(s): {invalid_strike}. Choose from {STRIKE_LABELS}")

    OPTION_VARIANTS = [(ew, sl) for ew in expiry_filter for sl in strike_filter]

    indicators = {
        'trend':      args.trend.strip().lower(),
        'momentum':   args.momentum.strip().lower(),
        'volatility': args.volatility.strip().lower(),
        'volume':     args.volume.strip().lower(),
    }
    seed = args.seed if args.seed is not None else secrets.randbelow(2**32)
    return (
        indicators,
        seed,
        datetime.date.fromisoformat(args.data_first),
        datetime.date.fromisoformat(args.data_last),
        args.window_days,
    )


def combo_label(indicators: dict[str, str]) -> str:
    return '_'.join(v for v in indicators.values() if v) or 'none'


def generate_test_runs(n: int = N_RUNS, meta_seed: int = META_SEED) -> list[tuple[str, str, int]]:
    max_start  = DATA_LAST - datetime.timedelta(days=WINDOW_DAYS)
    total_days = (max_start - DATA_FIRST).days
    rng        = np.random.default_rng(meta_seed)
    offsets    = rng.choice(total_days, size=n, replace=False)
    seeds      = rng.integers(1, 10_000, size=n)
    return sorted(
        [
            (
                (DATA_FIRST + datetime.timedelta(days=int(d))).strftime('%Y-%m-%d'),
                (DATA_FIRST + datetime.timedelta(days=int(d) + WINDOW_DAYS)).strftime('%Y-%m-%d'),
                int(s),
            )
            for d, s in zip(offsets, seeds)
        ],
        key=lambda t: t[0],
    )


def write_single_report(
    runs_df:    pd.DataFrame,
    label:      str,
    indicators: dict,
    run_dir:    'Path',
    argv:       list,
) -> None:
    """Write run_analysis.md with full parameters and aggregate summary."""
    import json as _json

    now  = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    path = run_dir / 'run_analysis.md'

    expiry_active = sorted({ew for ew, _ in OPTION_VARIANTS})
    strike_active = sorted({sl for _, sl in OPTION_VARIANTS})
    active = runs_df[runs_df['n_trades'] > 0]
    n_runs   = len(runs_df)
    n_active = len(active)

    lines = [f'# Single Model Run — {label}']
    lines.append(f'\n_Generated: {now}_\n')

    # ── Parameters ────────────────────────────────────────────────────────────
    lines.append('## Run Parameters\n')
    lines.append('| Parameter | Value |')
    lines.append('|---|---|')
    lines.append(f'| Command            | `{" ".join(argv)}` |')
    lines.append(f'| Combo              | `{label}` |')
    lines.append(f'| Trend              | {indicators.get("trend", "—")} |')
    lines.append(f'| Momentum           | {indicators.get("momentum", "—")} |')
    lines.append(f'| Volatility         | {indicators.get("volatility", "—")} |')
    lines.append(f'| Volume             | {indicators.get("volume", "—")} |')
    lines.append(f'| Expiry labels      | {", ".join(expiry_active)} |')
    lines.append(f'| Strike labels      | {", ".join(strike_active)} |')
    lines.append(f'| Variants tested    | {len(OPTION_VARIANTS)} |')
    lines.append(f'| Date range         | {DATA_FIRST} → {DATA_LAST} |')
    lines.append(f'| Runs               | {N_RUNS} |')
    lines.append(f'| Window length      | {WINDOW_DAYS} calendar days |')
    lines.append(f'| Sample bars/window | {N_SAMPLE:,} |')
    lines.append(f'| Shares per trade   | {SHARES} |')
    lines.append(f'| Commission         | ${COMMISSION:.2f} per round-trip |')
    lines.append(f'| cc_tv range        | ${CC_TV_MIN:.2f} – ${CC_TV_MAX:.2f} |')
    lines.append(f'| Buyback tv         | ${BUYBACK_TV:.2f} |')
    lines.append(f'| Output directory   | `{run_dir.name}` |')
    lines.append('')

    # ── Aggregate summary ─────────────────────────────────────────────────────
    lines.append('## Aggregate Results\n')
    if n_active == 0:
        lines.append('_No trades fired across any run._\n')
    else:
        pf_capped   = active['profit_factor'].clip(upper=PF_CAP)
        pnl_hit     = active['pnl_positive'].sum()
        lines.append('| Metric | Value |')
        lines.append('|---|---|')
        lines.append(f'| Runs with trades   | {n_active} / {n_runs} |')
        lines.append(f'| PnL hit rate       | {pnl_hit / n_active * 100:.0f}%  ({pnl_hit}/{n_active} runs profitable) |')
        lines.append(f'| Avg total PnL      | ${active["total_pnl"].mean():,.2f}  (std ${active["total_pnl"].std():,.2f}) |')
        lines.append(f'| Total PnL (sum)    | ${active["total_pnl"].sum():,.2f} |')
        lines.append(f'| Avg win rate       | {active["win_rate"].mean():.1f}% |')
        lines.append(f'| Avg Sharpe         | {active["sharpe"].mean():.3f} |')
        lines.append(f'| Avg profit factor  | {pf_capped.mean():.3f} |')
        lines.append(f'| Avg max drawdown   | ${active["max_drawdown"].mean():,.2f} |')
        lines.append(f'| Avg trades / run   | {active["n_trades"].mean():.1f} |')
        lines.append(f'| Total trades       | {int(active["n_trades"].sum())} |')
        lines.append('')

        # Per-run table
        lines.append('## Per-Run Results\n')
        display = active[['n_trades', 'win_rate', 'total_pnl', 'avg_pnl',
                           'sharpe', 'profit_factor', 'max_drawdown',
                           'status']].copy()
        display.index.name = 'run'
        display = display.reset_index()
        display['run'] = display['run'] + 1
        display.columns = ['run', 'trades', 'win_rate', 'total_pnl', 'avg_pnl',
                            'sharpe', 'profit_factor', 'max_drawdown', 'status']
        display['total_pnl']      = display['total_pnl'].round(2)
        display['avg_pnl']        = display['avg_pnl'].round(2)
        display['sharpe']         = display['sharpe'].round(3)
        display['profit_factor']  = display['profit_factor'].round(3)
        display['max_drawdown']   = display['max_drawdown'].round(2)
        lines.append(md_table(display, n=len(display)))
        lines.append('')

    path.write_text('\n'.join(lines), encoding='utf-8')
    print(f"[output]  Report -> {path}")


_TRADING_MINS_PER_DAY = 6.5 * 60   # 390 minutes per trading day


def restructure_trades(df: pd.DataFrame) -> pd.DataFrame:
    """
    Merge stock and option legs into one row per covered-call position.

    - Pulls cc_close_price from the option leg's exit_price.
    - Renames entry_price → entry_stock_price, exit_price → stock_exit_price,
      pnl_dollar → stock_pnl.
    - Adds days_held = bars_held / 390.
    - Drops: leg, variant_key, trade_date, atr/rsi/adx/vwap_at_entry,
             cost, proceeds, is_winner, data_status.
    """
    if df.empty:
        return df

    # Detect which ID columns are present (differs between all_models / single_model)
    id_cols = [c for c in ['batch_no', 'model_id', 'run_no', 'trade_no',
                            'expiry_label', 'strike_label']
               if c in df.columns]

    stock = df[df['leg'] == 'stock'].copy()
    opt   = df[df['leg'] == 'option'][id_cols + ['exit_price']].copy()
    opt   = opt.rename(columns={'exit_price': 'cc_close_price'})

    merged = stock.merge(opt, on=id_cols, how='left')

    merged = merged.rename(columns={
        'entry_price': 'entry_stock_price',
        'exit_price':  'stock_exit_price',
        'pnl_dollar':  'stock_pnl',
    })

    merged['days_held'] = (merged['bars_held'] / _TRADING_MINS_PER_DAY).round(2)

    # cc_open_days: calendar days from entry to expiry at 16:00
    if 'entry_time' in merged.columns and 'expiry_date' in merged.columns:
        entry_dt  = pd.to_datetime(merged['entry_time'])
        expiry_dt = pd.to_datetime(merged['expiry_date']) + pd.Timedelta(hours=16)
        merged['cc_open_days'] = (
            (expiry_dt - entry_dt).dt.total_seconds() / 86400
        ).round(2)

    col_order = [
        'batch_no', 'model_id', 'run_no', 'trade_no',
        'trend', 'momentum', 'volatility', 'volume',
        'expiry_label', 'strike_label', 'strike', 'expiry_date',
        'entry_time', 'exit_time',
        'entry_stock_price', 'stock_exit_price',
        'cc_open_price', 'cc_open_days', 'cc_close_price',
        'exit_reason', 'bars_held', 'days_held', 'shares',
        'stock_pnl', 'option_pnl', 'combined_pnl',
    ]
    cols = [c for c in col_order if c in merged.columns]
    return merged[cols].reset_index(drop=True)


def print_aggregate(runs_df: pd.DataFrame, label: str):
    active   = runs_df[runs_df['n_trades'] > 0]
    n_runs   = len(runs_df)
    n_active = len(active)
    if n_active == 0:
        print("  No trades fired across any run.")
        return
    pf_capped = active['profit_factor'].clip(upper=PF_CAP)
    print(f"\n  Combo         : {label}")
    print(f"  Runs          : {n_runs}  ({n_active} with trades)")
    print(f"  PnL hit rate  : {(active['pnl_positive'].sum() / n_active * 100):.0f}%  "
          f"({active['pnl_positive'].sum()}/{n_active} runs profitable)")
    print(f"  Avg total PnL : ${active['total_pnl'].mean():,.2f}  "
          f"(std ${active['total_pnl'].std():,.2f})")
    print(f"  Avg win rate  : {active['win_rate'].mean():.1f}%")
    print(f"  Avg Sharpe    : {active['sharpe'].mean():.3f}")
    print(f"  Avg PF (cap)  : {pf_capped.mean():.3f}")
    print(f"  Avg drawdown  : ${active['max_drawdown'].mean():,.2f}")
    print(f"  Avg trades/run: {active['n_trades'].mean():.1f}")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    global DATA_FIRST, DATA_LAST, WINDOW_DAYS
    indicators, meta_seed, DATA_FIRST, DATA_LAST, WINDOW_DAYS = parse_args()
    label = combo_label(indicators)
    active = {k: v for k, v in indicators.items() if v}

    if not active:
        print("ERROR: all categories are blank — nothing to test.")
        sys.exit(1)

    t   = indicators['trend']
    m   = indicators['momentum']
    v   = indicators['volatility']
    vol = indicators['volume']

    run_ts  = datetime.datetime.now().strftime('%m%d%H%M')
    run_dir = REPORTS_DIR / f'cc_{label}_{run_ts}'
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  Single Model — Covered Call Entry (3_covered_calls)")
    print(f"{'='*60}")
    print(f"  Indicators  : {indicators}")
    expiry_active = sorted({ew for ew, _ in OPTION_VARIANTS})
    strike_active = sorted({sl for _, sl in OPTION_VARIANTS})
    print(f"  Variants    : {len(OPTION_VARIANTS)}  "
          f"expiry={expiry_active}  strike={strike_active}")
    print(f"  Meta seed   : {meta_seed}")
    print(f"  Runs        : {N_RUNS}  (window={WINDOW_DAYS} cal days each)")
    print(f"  Data range  : {DATA_FIRST} -> {DATA_LAST}")
    print(f"  Output dir  : {run_dir}")
    print(f"{'='*60}\n")

    df_signals = load_or_build_signals()
    test_runs  = generate_test_runs(N_RUNS, meta_seed=meta_seed)

    all_summaries = []
    all_trades    = []

    log_path = run_dir / 'run.log'
    with open(log_path, 'w', encoding='utf-8') as log_fh:
        log_fh.write(
            f"combo: [{label}]  variants: 12  runs: {N_RUNS}  window_days: {WINDOW_DAYS}"
            f"  model: [cc_3_variants]"
            f"  generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        )

        for run_no, (window_start, window_end, seed) in enumerate(test_runs, 1):
            print(
                f"  [Run {run_no:>3}/{N_RUNS}]  "
                f"{window_start} -> {window_end}  seed={seed:<5}",
                end='  ',
            )

            try:
                summary, trades, pos_list = run_single(
                    df_signals, indicators,
                    window_start, window_end, seed, run_no,
                    capture_evals=True,
                )
            except Exception as exc:
                err_msg = f"ERROR in run_single run_no={run_no}: {exc}"
                print(err_msg)
                log_fh.write(f"\n{err_msg}\n")
                continue

            print(f"trades={summary['n_trades']:<4}  pnl=${summary['total_pnl']:>8,.2f}  "
                  f"[{summary['status']}]")
            all_summaries.append(summary)

            # Group trades by trade_no to log per-trade
            trade_groups: dict[int, list] = {}
            for tr in trades:
                if tr.get('leg') == 'stock':
                    tno = tr.get('trade_no', 0)
                    if tno not in trade_groups:
                        trade_groups[tno] = []
                    trade_groups[tno].append(tr)

            if trade_groups:
                _log_model_start(log_fh, run_no, '-', t, m, v, vol)
                for tno in sorted(trade_groups.keys()):
                    # Reconstruct variant_results for this trade_no
                    vr = {}
                    for tr in trades:
                        if tr.get('trade_no') == tno and tr.get('leg') == 'stock':
                            ek  = tr.get('expiry_label', '')
                            sl  = tr.get('strike_label', '')
                            key = (ek, sl)
                            vr[key] = {
                                'variant_key':     tr.get('variant_key', ''),
                                'expiry_label':    ek,
                                'strike_label':    sl,
                                'strike':          tr.get('strike'),
                                'expiry_date':     (
                                    datetime.date.fromisoformat(tr['expiry_date'])
                                    if tr.get('expiry_date') else None
                                ),
                                'open_price':      tr.get('cc_open_price'),
                                'cc_close_price':  tr.get('exit_price'),
                                'cc_close_reason': tr.get('exit_reason', ''),
                                'cc_close_time':   tr.get('exit_time'),
                                'stock_exit_price': tr.get('exit_price'),
                                'stock_exit_time': tr.get('exit_time'),
                                'stock_pnl':       tr.get('pnl_dollar'),
                                'option_pnl':      tr.get('option_pnl'),
                                'combined_pnl':    tr.get('combined_pnl'),
                                'is_winner':       tr.get('is_winner'),
                                'data_status':     tr.get('data_status', ''),
                            }

                    # Get first trade row for entry snapshot
                    first_tr = trade_groups[tno][0]
                    entry_snap = {
                        'entry_time':    first_tr.get('entry_time', ''),
                        'entry_price':   first_tr.get('entry_price', 0.0),
                        'shares':        first_tr.get('shares', SHARES),
                        'atr_at_entry':  first_tr.get('atr_at_entry'),
                        'rsi_at_entry':  first_tr.get('rsi_at_entry'),
                        'adx_at_entry':  first_tr.get('adx_at_entry'),
                        'vwap_at_entry': first_tr.get('vwap_at_entry'),
                    }
                    _log_trade_table(log_fh, tno, indicators, entry_snap, vr, [])
                _log_model_end(log_fh, run_no, '-', summary)

            all_trades.extend(trades)

    print(f"\n[output]  Log    -> {log_path}")

    runs_df   = pd.DataFrame(all_summaries)
    runs_path = run_dir / f'cc_{label}_runs.csv'
    runs_df.to_csv(runs_path, index=False)
    print(f"[output]  Runs   -> {runs_path}  ({len(runs_df)} rows)")

    if all_trades:
        trades_df   = restructure_trades(pd.DataFrame(all_trades))
        trades_path = run_dir / f'cc_{label}_trades.csv'
        trades_df.to_csv(trades_path, index=False)
        print(f"[output]  Trades -> {trades_path}  ({len(trades_df):,} rows)")
    else:
        print("[output]  No trades to write.")

    print(f"\n{'='*60}")
    print("  AGGREGATE RESULTS (all variants combined)")
    print(f"{'='*60}")
    print_aggregate(runs_df, label)
    print(f"\n{'='*60}\n")

    write_single_report(runs_df, label, indicators, run_dir, sys.argv)


if __name__ == '__main__':
    main()
