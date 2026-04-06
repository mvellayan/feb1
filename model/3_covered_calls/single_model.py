"""
single_model.py — 3_covered_calls

Entry: composite 4-indicator buy signal → immediately open stock + covered call.

Strategy:
  On buy signal → buy 100 shares at avg_ask + sell 1 call at a fixed strike offset.

  Strike offsets tested (AAPL $1 grid, floor to nearest dollar):
    +3  OTM call  (strike above entry price — limited upside, lower premium)
    +2  OTM call
    -2  ITM call  (strike below entry price — capped loss on stock, higher premium)
    -3  ITM call

Exit:
  1. CC ask < $0.50  → buy back call + sell stock at avg_bid  (cc_buyback)
  2. Expiry Friday ≥ 15:00:
       stock avg_bid > strike  (ITM) → assignment  (cc_assigned, stock sold at strike)
       stock avg_bid ≤ strike  (OTM) → expired OTM, sell stock at avg_bid

No stop loss.  No EOD forced exit on non-expiry days.
Position closes only when the covered call closes.

Trades CSV has a 'leg' column:
  'stock'  — the stock position row
  'option' — the short call row

Metrics are computed at the position level (combined stock + option P&L).
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
_HERE   = Path(__file__).parent
_MODEL1 = _HERE.parent / '1_tech_indicators_sock_trade'
_BASE   = _HERE.parent.parent          # /feb1/
sys.path.insert(0, str(_MODEL1))
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
CC_BUYBACK_THRESHOLD  = 0.50   # buy back the short call if ask drops below this
CC_MAX_EXPIRY_DAYS    = 4      # max calendar days to Friday expiration
MAX_STRIKE_GAP        = 1.0    # max $ between floored target strike and actual strike
MAX_QUOTE_AGE_MINUTES = 30     # freshness guard — reject stale option open quote
EXPIRY_QUOTE_MIN_HOUR = 15     # expiry-day quote must exist at or after 3 PM

STRIKE_OFFSETS = [3.0, 2.0, -2.0, -3.0]   # positive = OTM, negative = ITM

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

_option_data_cache: dict[str, pd.DataFrame | None] = {}


def _option_file_path(expiry_int: int, strike: float) -> Path:
    year_2 = str(expiry_int)[:2]
    fname  = f"oq_{expiry_int}C{int(round(strike * 1000)):08d}.csv"
    return OPTIONS_DIR / year_2 / fname


def load_option_data(contract: dict) -> pd.DataFrame | None:
    key  = f"{contract['expiration_date']}_{contract['strike_price']}"
    if key in _option_data_cache:
        return _option_data_cache[key]
    path = _option_file_path(contract['expiration_date'], contract['strike_price'])
    if not path.exists():
        _option_data_cache[key] = None
        return None
    df = pd.read_csv(path, parse_dates=['date'], low_memory=False)
    df = df.sort_values('date').reset_index(drop=True)
    _option_data_cache[key] = df
    return df


def get_option_price_at(
    option_df:       pd.DataFrame | None,
    ts:              pd.Timestamp,
    col:             str,
    max_age_minutes: int | None = None,
) -> float | None:
    """Return the most recent value of `col` at or before `ts`.
    If max_age_minutes is set, returns None if that quote is stale."""
    if option_df is None or option_df.empty:
        return None
    mask = option_df['date'] <= ts
    if not mask.any():
        return None
    row = option_df.loc[mask].iloc[-1]
    if max_age_minutes is not None:
        age = (ts - pd.Timestamp(row['date'])).total_seconds() / 60
        if age > max_age_minutes:
            return None
    val = float(row[col])
    return val if val > 0 else None


def _has_expiry_quote(option_df: pd.DataFrame | None, expiry_date: datetime.date) -> bool:
    """True if option_df has at least one quote on expiry date at or after 3 PM."""
    if option_df is None or option_df.empty:
        return False
    threshold = pd.Timestamp(expiry_date) + pd.Timedelta(hours=EXPIRY_QUOTE_MIN_HOUR)
    end       = pd.Timestamp(expiry_date) + pd.Timedelta(days=1)
    return bool(((option_df['date'] >= threshold) & (option_df['date'] < end)).any())


# ══════════════════════════════════════════════════════════════════════════════
# COVERED CALL LOOKUP — fixed offset at entry
# ══════════════════════════════════════════════════════════════════════════════

def find_cc_at_entry(
    entry_date:    datetime.date,
    entry_ts:      pd.Timestamp,
    entry_price:   float,
    strike_offset: float,
) -> tuple[dict | None, pd.DataFrame | None]:
    """
    Find and validate a covered call at a fixed strike offset from entry price.

    strike_offset > 0  →  OTM call  (strike above entry)
    strike_offset < 0  →  ITM call  (strike below entry)

    Strike = floor(entry_price + offset) — AAPL $1 increment grid.

    Validates:
      - Nearest Friday ≤ CC_MAX_EXPIRY_DAYS away
      - Contract exists in option index; gap ≤ MAX_STRIKE_GAP
      - Has a quote on expiry Friday at or after 15:00
      - Has a fresh open quote at entry time (≤ MAX_QUOTE_AGE_MINUTES)

    Returns (contract_dict, option_df) or (None, None) if any check fails.
    """
    option_index = load_option_index()

    dow        = entry_date.weekday()
    days_ahead = (4 - dow) % 7
    friday     = entry_date + datetime.timedelta(days=days_ahead)

    if (friday - entry_date).days > CC_MAX_EXPIRY_DAYS:
        return None, None

    expiry_int    = int(friday.strftime('%y%m%d'))
    target_strike = math.floor(entry_price + strike_offset)

    cands = option_index[
        (option_index['call_put']        == 'C') &
        (option_index['expiration_date'] == expiry_int) &
        (option_index['strike_price']    <= target_strike)
    ]
    if cands.empty:
        return None, None

    best_row = cands.loc[cands['strike_price'].idxmax()]
    strike   = float(best_row['strike_price'])
    gap      = target_strike - strike

    if gap > MAX_STRIKE_GAP:
        return None, None

    contract  = best_row.to_dict()
    option_df = load_option_data(contract)

    if not _has_expiry_quote(option_df, friday):
        return None, None

    premium = get_option_price_at(option_df, entry_ts, 'avg_bid',
                                  max_age_minutes=MAX_QUOTE_AGE_MINUTES)
    if premium is None:
        return None, None

    return contract, option_df


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
# EOW CONTEXT BUILDER  (entry date through Friday)
# ══════════════════════════════════════════════════════════════════════════════

def _build_eow_context(
    df: pd.DataFrame,
    entry_global_idx: int,
) -> tuple[pd.DataFrame | None, int | None]:
    """
    Build a DataFrame slice from the entry bar's trading date through that Friday.
    Returns (df_slice, entry_iloc) with reset index, or (None, None) on failure.
    """
    trade_date = df.loc[entry_global_idx, 'fnd_trade_date']
    entry_dt   = pd.Timestamp(str(trade_date))
    dow        = entry_dt.dayofweek
    days_ahead = (4 - dow) % 7
    friday_dt  = entry_dt + pd.Timedelta(days=days_ahead)

    fnd_ts   = pd.to_datetime(df['fnd_trade_date'])
    mask     = (fnd_ts >= entry_dt) & (fnd_ts <= friday_dt)
    df_slice = df[mask].reset_index(drop=True)

    if df_slice.empty:
        return None, None

    entry_ts = df.loc[entry_global_idx, 'date']
    hit      = df_slice.index[df_slice['date'] == entry_ts]
    if len(hit) == 0:
        return None, None

    return df_slice, int(hit[0])


# ══════════════════════════════════════════════════════════════════════════════
# COVERED CALL SIMULATION  (entry through Friday)
# ══════════════════════════════════════════════════════════════════════════════

def simulate_cc_position(
    df_eow:          pd.DataFrame,
    entry_iloc:      int,
    option_df:       pd.DataFrame,
    option_contract: dict,
    entry_price:     float,
    shares:          int,
    capture_evals:   bool = False,
) -> tuple[dict | None, dict | None]:
    """
    Open covered call at the entry bar, walk forward through Friday.

    Stock exit reasons:
      cc_buyback      — option ask fell below $0.50; stock sold at avg_bid
      cc_assigned     — option expired ITM; stock sold at strike price
      cc_expired_otm  — option expired OTM; stock sold at avg_bid

    Returns (stock_leg_dict, option_leg_dict) or (None, None) if the open
    premium is unavailable at entry.
    """
    strike    = float(option_contract['strike_price'])
    entry_bar = df_eow.iloc[entry_iloc]
    entry_ts  = pd.Timestamp(entry_bar['date'])

    cc_open_price = get_option_price_at(option_df, entry_ts, 'avg_bid',
                                        max_age_minutes=MAX_QUOTE_AGE_MINUTES)
    if cc_open_price is None:
        return None, None

    cc_close_price    = None
    cc_close_reason   = None
    cc_close_time     = None
    stock_exit_price  = None
    stock_exit_reason = None
    stock_exit_time   = None
    cc_bars = 0
    evals   = []

    for i in range(entry_iloc + 1, len(df_eow)):
        bar      = df_eow.iloc[i]
        bar_time = pd.Timestamp(bar['date'])
        cc_bars += 1

        if capture_evals:
            opt_ask_eval = (
                get_option_price_at(option_df, bar_time, 'avg_ask',
                                    max_age_minutes=MAX_QUOTE_AGE_MINUTES) or 0.0
            )
            evals.append((str(bar['date']), round(float(bar['average']), 4),
                          round(opt_ask_eval, 4)))

        opt_ask = get_option_price_at(option_df, bar_time, 'avg_ask',
                                      max_age_minutes=MAX_QUOTE_AGE_MINUTES)
        if opt_ask is not None and opt_ask < CC_BUYBACK_THRESHOLD:
            cc_close_price    = opt_ask
            cc_close_reason   = 'buyback'
            cc_close_time     = str(bar['date'])
            stock_exit_price  = float(bar['avg_bid'])
            stock_exit_reason = 'cc_buyback'
            stock_exit_time   = str(bar['date'])
            break

    if cc_close_price is None:
        # End-of-week close — check ITM vs OTM
        last            = df_eow.iloc[-1]
        stock_close_bid = float(last['avg_bid'])
        cc_close_time   = str(last['date'])
        stock_exit_time = str(last['date'])
        cc_close_price  = 0.0

        if stock_close_bid > strike:
            stock_exit_price  = strike
            stock_exit_reason = 'cc_assigned'
            cc_close_reason   = 'assigned'
        else:
            stock_exit_price  = stock_close_bid
            stock_exit_reason = 'cc_expired_otm'
            cc_close_reason   = 'expired_otm'

    # ── Stock leg ──────────────────────────────────────────────────────────────
    cost      = shares * entry_price
    proceeds  = shares * stock_exit_price
    stock_pnl = proceeds - cost - COMMISSION
    stock_pct = stock_pnl / cost * 100 if cost > 0 else 0.0

    stock_leg = {
        'exit_price':       round(stock_exit_price, 4),
        'exit_time':        stock_exit_time,
        'exit_reason':      stock_exit_reason,
        'bars_held':        cc_bars,
        'shares':           shares,
        'cost':             round(cost, 2),
        'proceeds':         round(proceeds, 2),
        'pnl_dollar':       round(stock_pnl, 2),
        'pnl_pct':          round(stock_pct, 4),
        'is_winner':        stock_pnl > 0,
        'cc_option_symbol': option_contract.get('localSymbol', ''),
        'cc_strike':        strike,
        'cc_expiry':        str(option_contract.get('expiration_date', '')),
        'cc_open_time':     str(entry_ts),
        'cc_open_price':    round(cc_open_price, 4),
        '_evals':           evals,
    }

    # ── Option leg ─────────────────────────────────────────────────────────────
    # Short call: collected premium at open, paid cc_close_price to close (or 0 at expiry)
    opt_pnl = (cc_open_price - cc_close_price) * shares - COMMISSION

    option_leg = {
        'entry_time':       str(entry_ts),
        'entry_price':      round(cc_open_price, 4),
        'exit_time':        cc_close_time,
        'exit_price':       round(cc_close_price, 4),
        'exit_reason':      cc_close_reason,
        'bars_held':        cc_bars,
        'shares':           shares,
        'cost':             round(cc_open_price * shares, 2),
        'proceeds':         round(cc_close_price * shares, 2),
        'pnl_dollar':       round(opt_pnl, 2),
        'pnl_pct':          0.0,
        'is_winner':        opt_pnl > 0,
        'cc_option_symbol': option_contract.get('localSymbol', ''),
        'cc_strike':        strike,
        'cc_expiry':        str(option_contract.get('expiration_date', '')),
    }

    return stock_leg, option_leg


# ══════════════════════════════════════════════════════════════════════════════
# LOG WRITERS
# ══════════════════════════════════════════════════════════════════════════════

_LOG_WIDTH    = 72
_LOG_EVAL_HEAD = 5
_LOG_EVAL_TAIL = 5


def _ruler(label: str) -> str:
    return label + '-' * max(0, _LOG_WIDTH - len(label))


def _log_model_start(fh, seq_no, model_id, t, m, v, vol):
    header = f"-----  START:  seq_no: [{seq_no}] model_id: [{model_id}]  "
    fh.write(f"\n{_ruler(header)}\n")
    fh.write(f"trend: [{t}] momentum: [{m}] volatility: [{v}] volume: [{vol}]\n")
    fh.write("\n----- Details\n\n")


def _write_evals(fh, header: str, rows: list, fmt):
    if not rows:
        return
    fh.write(f"    {header}\n")
    head = rows[:_LOG_EVAL_HEAD]
    tail = rows[_LOG_EVAL_TAIL * -1:] if len(rows) > _LOG_EVAL_HEAD + _LOG_EVAL_TAIL else []
    skip = len(rows) - len(head) - len(tail)
    for r in head:
        fh.write(fmt(r))
    if skip > 0:
        fh.write(f"    ... {skip} bars skipped ...\n")
    for r in tail:
        fh.write(fmt(r))
    fh.write("\n")


def _log_trade(fh, tr):
    offset_str = f"{tr['strike_offset']:+.0f}"
    fh.write(
        f"trade_no: [{tr['trade_no']}], "
        f"entry_time: [{tr['entry_time']}] "
        f"entry_price: [{tr['entry_price']}]\n"
    )
    fh.write(
        f"strike_offset: [{offset_str}]  "
        f"strike: [{tr.get('strike', '')}]  "
        f"cc_symbol: [{tr.get('cc_option_symbol', '')}]  "
        f"cc_expiry: [{tr.get('cc_expiry', '')}]\n"
    )
    fh.write(
        f"cc_open_time: [{tr.get('cc_open_time', '')}]  "
        f"cc_open_price: [{tr.get('cc_open_price', '')}]  "
        f"(buyback threshold: {CC_BUYBACK_THRESHOLD})\n"
    )
    fh.write(
        f"atr_at_entry: [{tr['atr_at_entry']}]  "
        f"rsi_at_entry: [{tr['rsi_at_entry']}]\n"
    )
    fh.write(
        f"adx_at_entry: [{tr['adx_at_entry']}]  "
        f"vwap_at_entry: [{tr['vwap_at_entry']}]\n"
    )
    fh.write("\n")

    cc_evals = tr.get('_cc_evals', [])

    def _fmt_cc(r):
        t = str(r[0]).split(' ')[-1] if ' ' in str(r[0]) else str(r[0])
        return f"    {t}    {r[1]}    {r[2]}\n"

    _write_evals(fh, "eval_time    stock_avg    option_ask", cc_evals, _fmt_cc)

    fh.write(
        f"exit_time: [{tr['exit_time']}]  "
        f"exit_price: [{tr['exit_price']}]  "
        f"exit_reason: [{tr['exit_reason']}]  "
        f"bars_held: [{tr['bars_held']}]\n"
    )
    fh.write(
        f"shares: [{tr['shares']}]  "
        f"cost: [{tr['cost']}]  "
        f"proceeds: [{tr['proceeds']}]\n"
    )

    stock_pnl = tr['pnl_dollar']
    opt_pnl   = tr.get('_opt_pnl')

    if opt_pnl is not None:
        combined     = round(stock_pnl + opt_pnl, 2)
        cost         = tr['cost']
        combined_pct = round(combined / cost * 100 if cost else 0.0, 4)
        fh.write(
            f"stock_sub_total: [{stock_pnl}]  "
            f"covered_call_sub_total: [{opt_pnl}]  "
            f"pnl_total: [{combined}]  pnl_pct: [{combined_pct}]  "
            f"is_winner: [{'TRUE' if combined > 0 else 'FALSE'}]\n\n"
        )
    else:
        fh.write(
            f"pnl_dollar: [{stock_pnl}]  "
            f"pnl_pct: [{tr['pnl_pct']}]  "
            f"is_winner: [{'TRUE' if tr['is_winner'] else 'FALSE'}]\n\n"
        )


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
    fh.write(f"{_ruler(footer)}\n")


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


def _empty_metrics(status: str) -> dict:
    return {
        'n_trades': 0, 'win_rate': 0.0,
        'avg_entry': 0.0, 'avg_exit': 0.0, 'avg_duration_bars': 0.0,
        'total_pnl': 0.0, 'avg_pnl': 0.0,
        'profit_factor': 0.0, 'sharpe': 0.0, 'max_drawdown': 0.0,
        'pnl_positive': False, 'status': status,
    }


# ══════════════════════════════════════════════════════════════════════════════
# CORE SIMULATION — one indicator combo + one strike offset
# ══════════════════════════════════════════════════════════════════════════════

def run_combo(
    df:            pd.DataFrame,
    sample_idx:    list,
    day_dict:      dict,
    day_pos_map:   dict,
    indicators:    dict[str, str],
    strike_offset: float,
    capture_evals: bool = False,
) -> tuple[dict, list[dict]]:
    """
    Fire the composite buy signal, open stock + CC at entry, simulate to close.
    Returns (metrics, trades_list).
    trades_list rows include a 'leg' field: 'stock' or 'option'.
    Metrics are computed from combined (stock + option) P&L per position.
    If no option is available for the requested offset, that signal is skipped.
    """
    active   = {cat: ind for cat, ind in indicators.items() if ind}
    buy_cols = [f'bsig_{ind}' for ind in active.values()]

    sample_df = df.loc[sample_idx].copy()
    composite = pd.Series(True, index=sample_df.index)
    for bc in buy_cols:
        if bc in sample_df.columns:
            composite &= sample_df[bc].astype(bool)

    fired_idx = sample_df.index[composite].tolist()
    positions  = []
    trades     = []
    trade_no   = 0

    for idx in fired_idx:
        row         = df.loc[idx]
        entry_price = float(row['avg_ask'])
        shares      = 100

        entry_ts   = pd.Timestamp(row['date'])
        entry_date = entry_ts.date()
        trade_date = row['fnd_trade_date']

        # ── Find CC at fixed offset ────────────────────────────────────────────
        contract, option_df = find_cc_at_entry(
            entry_date, entry_ts, entry_price, strike_offset
        )
        if contract is None:
            continue   # no valid option at this offset → skip bar

        strike = float(contract['strike_price'])

        # ── Build entry-to-Friday stock slice ─────────────────────────────────
        df_eow, entry_iloc_eow = _build_eow_context(df, idx)
        if df_eow is None or entry_iloc_eow is None:
            continue

        # ── Simulate CC position ───────────────────────────────────────────────
        stock_leg, opt_leg = simulate_cc_position(
            df_eow, entry_iloc_eow, option_df, contract,
            entry_price, shares, capture_evals,
        )
        if stock_leg is None:
            continue

        trade_no    += 1
        combined_pnl = stock_leg['pnl_dollar'] + opt_leg['pnl_dollar']

        atr_val  = row.get('atr_14',   np.nan)
        rsi_val  = row.get('rsi_14',   np.nan)
        adx_val  = row.get('adx_14',   np.nan)
        vwap_val = row.get('vwp_vwap', np.nan)

        entry_snapshot = {
            'entry_time':    str(row['date']),
            'entry_price':   round(entry_price, 4),
            'strike_offset': strike_offset,
            'strike':        strike,
            'atr_at_entry':  round(float(atr_val)  if pd.notna(atr_val)  else np.nan, 4),
            'rsi_at_entry':  round(float(rsi_val)  if pd.notna(rsi_val)  else np.nan, 4),
            'adx_at_entry':  round(float(adx_val)  if pd.notna(adx_val)  else np.nan, 4),
            'vwap_at_entry': round(float(vwap_val) if pd.notna(vwap_val) else np.nan, 4),
        }

        cc_evals = stock_leg.pop('_evals', [])
        stock_leg['_opt_pnl']  = opt_leg['pnl_dollar']

        positions.append({
            'combined_pnl': combined_pnl,
            'entry_price':  entry_price,
            'exit_price':   stock_leg['exit_price'],
            'bars_held':    stock_leg['bars_held'],
            'is_winner':    combined_pnl > 0,
            'entry_time':   str(row['date']),
        })

        base = {
            'trade_no':   trade_no,
            'trade_date': str(trade_date),
            **{cat: indicators.get(cat, '')
               for cat in ['trend', 'momentum', 'volatility', 'volume']},
            **entry_snapshot,
        }
        trades.append({**base, 'leg': 'stock', **stock_leg, '_cc_evals': cc_evals})
        trades.append({**base, 'leg': 'option', **opt_leg})

    if not positions:
        return _empty_metrics('no_signals'), []

    return _calc_metrics(positions), trades


# ══════════════════════════════════════════════════════════════════════════════
# SINGLE-RUN WRAPPER
# ══════════════════════════════════════════════════════════════════════════════

def run_single(
    df_signals:    pd.DataFrame,
    indicators:    dict[str, str],
    strike_offset: float,
    window_start:  str,
    window_end:    str,
    seed:          int,
    run_no:        int,
    capture_evals: bool = False,
) -> tuple[dict, list[dict]]:
    window_data, status = prepare_window(df_signals, window_start, window_end, seed)
    if window_data is None:
        return _empty_summary(run_no, indicators, strike_offset,
                              window_start, window_end, seed, status), []

    df, sample_idx, day_dict, day_pos_map = window_data
    metrics, trades = run_combo(
        df, sample_idx, day_dict, day_pos_map, indicators, strike_offset,
        capture_evals=capture_evals,
    )

    context = {
        'run_no':       run_no,
        'window_start': window_start,
        'window_end':   window_end,
        'seed':         seed,
    }
    full_trades = [{**context, **tr} for tr in trades]

    summary = {
        **context,
        'strike_offset': strike_offset,
        **{cat: indicators[cat] for cat in ['trend', 'momentum', 'volatility', 'volume']},
        **metrics,
    }
    return summary, full_trades


def _empty_summary(run_no, indicators, strike_offset,
                   window_start, window_end, seed, status) -> dict:
    return {
        'run_no':        run_no,
        'window_start':  window_start,
        'window_end':    window_end,
        'seed':          seed,
        'strike_offset': strike_offset,
        **{cat: indicators[cat] for cat in ['trend', 'momentum', 'volatility', 'volume']},
        **_empty_metrics(status),
    }


# ══════════════════════════════════════════════════════════════════════════════
# STANDALONE CLI
# ══════════════════════════════════════════════════════════════════════════════

def parse_args():
    parser = argparse.ArgumentParser(
        description='Run one indicator combo × one strike offset across N windows.'
    )
    parser.add_argument('--trend',      default='adx',  help='Trend indicator')
    parser.add_argument('--momentum',   default='frc',  help='Momentum indicator')
    parser.add_argument('--volatility', default='atr',  help='Volatility indicator')
    parser.add_argument('--volume',     default='vrc',  help='Volume indicator')
    parser.add_argument(
        '--offset', type=float, default=3.0,
        choices=[3.0, 2.0, -2.0, -3.0],
        help='Strike offset from entry price (+3/+2=OTM, -2/-3=ITM). Default: 3.0',
    )
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
    args = parser.parse_args()
    indicators = {
        'trend':      args.trend.strip().lower(),
        'momentum':   args.momentum.strip().lower(),
        'volatility': args.volatility.strip().lower(),
        'volume':     args.volume.strip().lower(),
    }
    seed = args.seed if args.seed is not None else secrets.randbelow(2**32)
    return (
        indicators,
        args.offset,
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
    rng     = np.random.default_rng(meta_seed)
    offsets = rng.choice(total_days, size=n, replace=False)
    seeds   = rng.integers(1, 10_000, size=n)
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


def print_aggregate(runs_df: pd.DataFrame, label: str, offset: float):
    active   = runs_df[runs_df['n_trades'] > 0]
    n_runs   = len(runs_df)
    n_active = len(active)
    if n_active == 0:
        print("  No trades fired across any run.")
        return
    pf_capped = active['profit_factor'].clip(upper=PF_CAP)
    print(f"\n  Combo         : {label}")
    print(f"  Strike offset : {offset:+.0f}  "
          f"({'OTM' if offset > 0 else 'ITM'} call)")
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
    indicators, strike_offset, meta_seed, DATA_FIRST, DATA_LAST, WINDOW_DAYS = parse_args()
    label  = combo_label(indicators)
    active = {k: v for k, v in indicators.items() if v}

    if not active:
        print("ERROR: all categories are blank — nothing to test.")
        sys.exit(1)

    t   = indicators['trend']
    m   = indicators['momentum']
    v   = indicators['volatility']
    vol = indicators['volume']
    off_tag = f"{strike_offset:+.0f}".replace('+', 'p').replace('-', 'm')

    run_ts  = datetime.datetime.now().strftime('%m%d%H%M')
    run_dir = REPORTS_DIR / f'cc_{label}_{off_tag}_{run_ts}'
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  Single Model — Covered Call Entry (3_covered_calls)")
    print(f"{'='*60}")
    print(f"  Indicators    : {indicators}")
    print(f"  Strike offset : {strike_offset:+.0f}  "
          f"({'OTM' if strike_offset > 0 else 'ITM'} call)")
    print(f"  Meta seed     : {meta_seed}")
    print(f"  Runs          : {N_RUNS}  (window={WINDOW_DAYS} cal days each)")
    print(f"  Data range    : {DATA_FIRST} -> {DATA_LAST}")
    print(f"  Output dir    : {run_dir}")
    print(f"{'='*60}\n")

    df_signals = load_or_build_signals()
    test_runs  = generate_test_runs(N_RUNS, meta_seed=meta_seed)

    all_summaries = []
    all_trades    = []

    log_path = run_dir / 'run.log'
    with open(log_path, 'w', encoding='utf-8') as log_fh:
        log_fh.write(
            f"combo: [{label}]  offset: [{strike_offset:+.0f}]  "
            f"runs: {N_RUNS}  window_days: {WINDOW_DAYS}"
            f"  model: [cc_entry]"
            f"  generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        )

        for run_no, (window_start, window_end, seed) in enumerate(test_runs, 1):
            print(
                f"  [Run {run_no:>3}/{N_RUNS}]  "
                f"{window_start} -> {window_end}  seed={seed:<5}",
                end='  ',
            )

            try:
                summary, trades = run_single(
                    df_signals, indicators, strike_offset,
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

            stock_trades = [tr for tr in trades if tr.get('leg') == 'stock']
            if stock_trades:
                _log_model_start(log_fh, run_no, '-', t, m, v, vol)
                for tr in stock_trades:
                    _log_trade(log_fh, tr)
                _log_model_end(log_fh, run_no, '-', summary)

            for tr in trades:
                tr.pop('_cc_evals', None)
                tr.pop('_opt_pnl',  None)
            all_trades.extend(trades)

    print(f"\n[output]  Log    -> {log_path}")

    runs_df   = pd.DataFrame(all_summaries)
    runs_path = run_dir / f'cc_{label}_{off_tag}_runs.csv'
    runs_df.to_csv(runs_path, index=False)
    print(f"[output]  Runs   -> {runs_path}  ({len(runs_df)} rows)")

    if all_trades:
        col_order = [
            'run_no', 'trade_no', 'leg',
            'trend', 'momentum', 'volatility', 'volume',
            'strike_offset', 'strike',
            'trade_date', 'entry_time', 'exit_time', 'entry_price',
            'atr_at_entry', 'rsi_at_entry', 'adx_at_entry', 'vwap_at_entry',
            'exit_price', 'exit_reason', 'bars_held',
            'shares', 'cost', 'proceeds', 'pnl_dollar', 'pnl_pct', 'is_winner',
            'cc_option_symbol', 'cc_strike', 'cc_expiry', 'cc_open_time', 'cc_open_price',
        ]
        trades_df   = pd.DataFrame(all_trades)
        cols        = [c for c in col_order if c in trades_df.columns]
        trades_path = run_dir / f'cc_{label}_{off_tag}_trades.csv'
        trades_df[cols].to_csv(trades_path, index=False)
        print(f"[output]  Trades -> {trades_path}  ({len(trades_df):,} rows)")
    else:
        print("[output]  No trades to write.")

    print(f"\n{'='*60}")
    print("  AGGREGATE RESULTS")
    print(f"{'='*60}")
    print_aggregate(runs_df, label, strike_offset)
    print(f"\n{'='*60}\n")


if __name__ == '__main__':
    main()
