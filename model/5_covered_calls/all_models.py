"""
all_models.py — 5_covered_calls

Signal-driven covered-call strategy with "union" composite buy signal.

Entry
─────
For each window, the entry universe is bars where BOTH:
    (any bsig_trend is 1)   AND   (any bsig_momentum is 1)

    TREND    = ['ema','macd','adx','sar','don','arn','vtx']
    MOMENTUM = ['rsi','sto','cci','cmo','tsi','roc','frc','srsi','rmi','macd']

Signal-fired bars are iterated in chronological order (no random draw).
For each active (expiry_label, strike_label) variant, look up the sold-call
bid at entry_ts and compute the opening time value:

    cc_tv = option_bid(entry_ts) - max(0, avg_ask(entry_ts) - strike)

Open the position iff:
  - variant has a valid option quote (≤ MAX_QUOTE_AGE_MINUTES old)
  - cc_tv_min ≤ cc_tv ≤ cc_tv_max
  - at least COOLDOWN_MINUTES have passed since the variant's last accepted entry

Batch counter (per variant):
  - --batch_size is the per-variant cap on accepted entries.
  - Window ends when every variant has filled its batch OR all signal-fire
    bars have been exhausted.

Exit (per position)
───────────────────
1. buyback  — option_ask(bar) - max(0, avg_bid(bar) - strike) < buyback_tv
2. expiry   — bar at or after expiry_date + 15:00
               avg_bid > strike  → assigned      (stock sold at strike)
               avg_bid ≤ strike  → expired_otm   (stock sold at avg_bid)
3. window_end — neither fired before data ran out

Output  reports/{mmddhhmi}/
──────
  {seq_no}_summary.csv    — one row per variant with metrics + funnel block
  {seq_no}_trades.csv     — one row per accepted (variant × entry)
  {seq_no}_run.log        — trade-by-trade log
  trades.csv              — consolidated from the per-window files
  summary.csv             — consolidated
  analysis_variants.csv   — variants ranked by consistency_score
  batch_run_analysis.md   — run params, variant rankings, funnel aggregate
  all_runs.csv            — appended (one row per variant per run)
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import json
import os
import secrets
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

# ── path setup: reuse option-chain helpers from 3_covered_calls ────────────────
_HERE    = Path(__file__).parent
_MODEL3  = _HERE.parent / '3_covered_calls'
_MODEL1A = _HERE.parent / '1a_tech_indicators_sock_trade'
_BASE    = _HERE.parent.parent
sys.path.insert(0, str(_MODEL1A))
sys.path.insert(0, str(_MODEL3))
sys.path.insert(0, str(_HERE))

from single_model import (                      # noqa: E402
    EXPIRY_WEEKS, STRIKE_LABELS,
    MAX_QUOTE_AGE_MINUTES, EXPIRY_QUOTE_MIN_HOUR,
    SHARES, COMMISSION,
    load_option_index, load_option_data, get_option_price_at,
    _lookup_prices_vectorized, find_cc_variant,
)
from utils import md_table                      # noqa: E402

# ── score tuning ───────────────────────────────────────────────────────────────
# Profitability term uses tanh(avg_per_trade_pnl / PROF_K) to bound [-1,+1].
# At K=30, $+10/trade → prof_norm≈0.63; $+30 → 0.88; $-10 → 0.37; $-30 → 0.12.
PROF_K      = 30.0
# Profit-factor saturation: pf / (pf + PF_SAT_K) — replaces the old hard cap.
PF_SAT_K    = 5.0

warnings.filterwarnings('ignore')

REPORTS_DIR  = Path(__file__).parent / 'reports'
SIGNALS_CSV  = _BASE / 'data/stock/sq_AAPL_signals.csv'

# Buy signals aggregated via OR within each category, AND across categories
TREND    = ['ema', 'macd', 'adx', 'sar', 'don', 'arn', 'vtx']
MOMENTUM = ['rsi', 'sto', 'cci', 'cmo', 'tsi', 'roc', 'frc', 'srsi', 'rmi', 'macd']

_BSIG_COLS = sorted({f'bsig_{k}' for k in (TREND + MOMENTUM)})

_REQUIRED_COLS = [
    'date', 'fnd_trade_date',
    'avg_ask', 'avg_bid', 'average',
    'ses_after_10', 'ses_before_345', 'atr_spike',
    'atr_14', 'rsi_14', 'adx_14', 'vwp_vwap',
] + _BSIG_COLS

# ── constants ──────────────────────────────────────────────────────────────────
COOLDOWN_MINUTES      = 60
_TRADING_MINS_PER_DAY = 390


# ══════════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ══════════════════════════════════════════════════════════════════════════════

_signals_cache: pd.DataFrame | None = None


def load_signals() -> pd.DataFrame:
    global _signals_cache
    if _signals_cache is not None:
        return _signals_cache
    print(f"[data] Loading {SIGNALS_CSV} ...")
    df = pd.read_csv(
        SIGNALS_CSV, parse_dates=['date'],
        usecols=_REQUIRED_COLS, low_memory=False,
    )
    print(f"  Loaded: {df.shape[0]:,} rows × {df.shape[1]} cols")
    _signals_cache = df
    return df


def prepare_window(
    df_full:      pd.DataFrame,
    window_start: str,
    window_end:   str,
    seed:         int,   # retained for API compat; entry selection is deterministic
) -> tuple:
    """
    Filter to window, apply session/spike filters, AND require the union
    buy-signal composite to fire:
        (any bsig_trend == 1) AND (any bsig_momentum == 1)
    Returns a chronologically-sorted list of signal-fire bar indices.
    """
    mask = (
        (df_full['date'] >= pd.Timestamp(window_start)) &
        (df_full['date'] <= pd.Timestamp(window_end))
    )
    df = df_full[mask].reset_index(drop=True)
    if df.empty:
        return None, 'no_data'

    trend_cols = [f'bsig_{t}' for t in TREND    if f'bsig_{t}' in df.columns]
    mom_cols   = [f'bsig_{m}' for m in MOMENTUM if f'bsig_{m}' in df.columns]
    if not trend_cols or not mom_cols:
        return None, 'missing_bsig_cols'

    trend_any = df[trend_cols].to_numpy().any(axis=1)
    mom_any   = df[mom_cols].to_numpy().any(axis=1)

    valid_mask = (
        (df['ses_after_10']   == 1) &
        (df['ses_before_345'] == 1) &
        (df['atr_spike']      == 0) &
        df['avg_ask'].notna() &
        df['avg_bid'].notna() &
        trend_any & mom_any
    )
    signal_idx = df.index[valid_mask].to_numpy()
    if len(signal_idx) == 0:
        return None, 'no_signal_bars'

    # Chronological order — cooldown prunes as we walk
    return (df, signal_idx.tolist()), 'ok'


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY SNAPSHOT
# ══════════════════════════════════════════════════════════════════════════════

def _entry_snapshot(row: pd.Series) -> dict:
    def _rnd(v):
        return round(float(v), 4) if pd.notna(v) else None
    return {
        'atr_at_entry':  _rnd(row.get('atr_14')),
        'rsi_at_entry':  _rnd(row.get('rsi_14')),
        'adx_at_entry':  _rnd(row.get('adx_14')),
        'vwap_at_entry': _rnd(row.get('vwp_vwap')),
    }


# ══════════════════════════════════════════════════════════════════════════════
# FORWARD-WALK SIMULATION (one variant, TV-based buyback)
# ══════════════════════════════════════════════════════════════════════════════

def simulate_variant_tv(
    df:          pd.DataFrame,
    entry_idx:   int,
    variant:     dict,
    entry_price: float,
    buyback_tv:  float,
    shares:      int,
) -> dict:
    """
    Walk forward from entry_idx+1 and close the variant at the first exit:
       1. buyback  : option_ask - max(0, avg_bid - strike) < buyback_tv
       2. expiry   : bar at or after expiry_date + EXPIRY_QUOTE_MIN_HOUR
       3. window_end
    """
    opt_data = variant['opt_data']
    strike   = variant['strike']

    n_future = len(df) - entry_idx - 1
    if n_future <= 0:
        last     = df.iloc[-1]
        last_ts  = last['date']
        last_bid = float(last['avg_bid'])
        return _build_tv_result(
            variant, str(pd.Timestamp(last_ts)), last_bid,
            'window_end', None, 0, entry_price, shares,
        )

    future       = df.iloc[entry_idx + 1:]
    bar_times    = future['date'].values.astype('datetime64[ns]')
    bar_avg_bids = future['avg_bid'].values.astype(np.float64)

    opt_asks = _lookup_prices_vectorized(
        opt_data, bar_times, 'avg_ask', MAX_QUOTE_AGE_MINUTES,
    )
    intrinsic = np.maximum(0.0, bar_avg_bids - strike)
    tv_ask    = opt_asks - intrinsic          # NaN where opt_asks is NaN

    buyback_mask = ~np.isnan(tv_ask) & (tv_ask < buyback_tv)
    buyback_idx  = int(np.argmax(buyback_mask)) if buyback_mask.any() else n_future

    expiry_dt64 = (
        pd.Timestamp(variant['expiry_date'])
        + pd.Timedelta(hours=EXPIRY_QUOTE_MIN_HOUR)
    ).to_datetime64()
    expiry_mask = bar_times >= expiry_dt64
    expiry_idx  = int(np.argmax(expiry_mask)) if expiry_mask.any() else n_future

    exit_idx = min(buyback_idx, expiry_idx)

    if exit_idx >= n_future:
        # Neither buyback nor expiry fired before the window data ran out
        exit_str  = str(pd.Timestamp(bar_times[-1]))
        exit_bid  = float(bar_avg_bids[-1])
        bars_held = n_future
        late_ask  = get_option_price_at(opt_data, bar_times[-1], 'avg_ask')
        if late_ask is not None:
            late_tv = late_ask - max(0.0, exit_bid - strike)
            if late_tv < buyback_tv:
                return _build_tv_result(
                    variant, exit_str, exit_bid,
                    'buyback_late_data', late_ask, bars_held,
                    entry_price, shares,
                )
        close_bid = get_option_price_at(opt_data, bar_times[-1], 'avg_bid')
        return _build_tv_result(
            variant, exit_str, exit_bid,
            'window_end', close_bid, bars_held,
            entry_price, shares,
        )

    exit_str  = str(pd.Timestamp(bar_times[exit_idx]))
    exit_bid  = float(bar_avg_bids[exit_idx])
    bars_held = exit_idx + 1

    if exit_idx == buyback_idx:
        cc_reason = 'buyback'
        cc_price  = float(opt_asks[buyback_idx])
    else:
        if exit_bid > strike:
            cc_reason = 'assigned'
            cc_price  = 0.0
            exit_bid  = float(strike)
        else:
            cc_reason = 'expired_otm'
            cc_price  = 0.0

    return _build_tv_result(
        variant, exit_str, exit_bid, cc_reason, cc_price,
        bars_held, entry_price, shares,
    )


def _build_tv_result(
    variant:       dict,
    exit_time_str: str,
    bar_avg_bid:   float,
    cc_reason:     str,
    cc_price:      float | None,
    bars_held:     int,
    entry_price:   float,
    shares:        int,
) -> dict:
    stock_pnl = shares * bar_avg_bid - shares * entry_price - COMMISSION

    if variant['open_price'] is not None and cc_price is not None:
        opt_pnl      = (variant['open_price'] - cc_price) * shares - COMMISSION
        combined_pnl = stock_pnl + opt_pnl
        is_winner    = combined_pnl > 0
        data_status  = 'ok'
    else:
        opt_pnl      = None
        combined_pnl = None
        is_winner    = None
        data_status  = 'no_open_price' if variant['open_price'] is None else 'no_close_price'

    return {
        'variant_key':       variant['variant_key'],
        'expiry_label':      variant['expiry_label'],
        'strike_label':      variant['strike_label'],
        'strike':            variant['strike'],
        'expiry_date':       variant['expiry_date'],
        'open_price':        variant['open_price'],
        'cc_close_price':    cc_price,
        'cc_close_reason':   cc_reason,
        'cc_close_time':     exit_time_str,
        'stock_exit_price':  round(bar_avg_bid, 4),
        'stock_exit_reason': cc_reason,
        'stock_exit_time':   exit_time_str,
        'bars_held':         bars_held,
        'shares':            shares,
        'stock_pnl':         round(stock_pnl, 2),
        'option_pnl':        round(opt_pnl, 2) if opt_pnl is not None else None,
        'combined_pnl':      round(combined_pnl, 2) if combined_pnl is not None else None,
        'is_winner':         is_winner,
        'data_status':       data_status,
    }


# ══════════════════════════════════════════════════════════════════════════════
# METRICS
# ══════════════════════════════════════════════════════════════════════════════

def _calc_metrics(positions: list[dict]) -> dict:
    from collections import defaultdict
    n     = len(positions)
    wins  = [p for p in positions if p['is_winner']]
    loses = [p for p in positions if not p['is_winner']]
    gross_w = sum(p['combined_pnl'] for p in wins)  if wins  else 0.0
    gross_l = sum(p['combined_pnl'] for p in loses) if loses else 0.0
    pf = abs(gross_w / gross_l) if gross_l < 0 else float('inf')

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
        'n_trades':          0,
        'win_rate':          0.0,
        'avg_entry':         0.0,
        'avg_exit':          0.0,
        'avg_duration_bars': 0.0,
        'total_pnl':         0.0,
        'avg_pnl':           0.0,
        'profit_factor':     0.0,
        'sharpe':            0.0,
        'max_drawdown':      0.0,
        'pnl_positive':      False,
        'status':            status,
    }


# ══════════════════════════════════════════════════════════════════════════════
# BATCH RUNNER (one window, all selected variants)
# ══════════════════════════════════════════════════════════════════════════════

def run_window_batch(
    df:                pd.DataFrame,
    signal_idx:        list,
    variants_selected: list,
    batch_size:        int,
    cc_tv_min:         float,
    cc_tv_max:         float,
    buyback_tv:        float,
    seq_no:            int,
    log_fh=None,
) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    """
    Execute one window.  Returns (summary_df, trades_df, fires_seen).
    signal_idx is a chronologically-sorted list of bars where the union
    composite buy signal fired.
    """
    var_state = {
        key: {
            'last_entry_ts':  None,
            'accepted_count': 0,
            'no_quote':       0,
            'tv_fail_low':    0,
            'tv_fail_high':   0,
            'cooldown_skip':  0,
            'accepted':       0,
            'trades':         [],
            'positions':      [],
        }
        for key in variants_selected
    }

    fires_seen    = 0
    trade_no      = 0
    all_trades    = []
    cooldown_td   = pd.Timedelta(minutes=COOLDOWN_MINUTES)

    for global_idx in signal_idx:
        if all(var_state[k]['accepted_count'] >= batch_size for k in variants_selected):
            break

        row         = df.loc[global_idx]
        entry_price = float(row['avg_ask'])
        entry_ts    = pd.Timestamp(row['date'])
        entry_date  = entry_ts.date()

        accepted_this_draw = []   # list of (key, variant, cc_tv)
        fires_seen        += 1

        for key in variants_selected:
            if var_state[key]['accepted_count'] >= batch_size:
                continue
            expiry_label, strike_label = key

            variant = find_cc_variant(
                entry_date, entry_ts, entry_price, expiry_label, strike_label,
            )
            if variant is None or variant['open_price'] is None:
                var_state[key]['no_quote'] += 1
                continue

            cc_tv = variant['open_price'] - max(0.0, entry_price - variant['strike'])

            last_ts = var_state[key]['last_entry_ts']
            if last_ts is not None and (entry_ts - last_ts) < cooldown_td:
                var_state[key]['cooldown_skip'] += 1
                continue
            if cc_tv < cc_tv_min:
                var_state[key]['tv_fail_low'] += 1
                continue
            if cc_tv > cc_tv_max:
                var_state[key]['tv_fail_high'] += 1
                continue

            var_state[key]['accepted']        += 1
            var_state[key]['accepted_count']  += 1
            var_state[key]['last_entry_ts']    = entry_ts
            accepted_this_draw.append((key, variant, cc_tv))

        if not accepted_this_draw:
            continue

        trade_no += 1
        entry_snap       = _entry_snapshot(row)
        entry_trade_rows = []

        for key, variant, cc_tv in accepted_this_draw:
            result = simulate_variant_tv(
                df, global_idx, variant, entry_price, buyback_tv, SHARES,
            )

            trade_row = {
                'batch_no':       seq_no,
                'trade_no':       trade_no,
                'entry_time':     str(row['date']),
                'entry_price':    round(entry_price, 4),
                'cc_tv_at_entry': round(cc_tv, 4),
                'expiry_label':   key[0],
                'strike_label':   key[1],
                'variant_key':    variant['variant_key'],
                'strike':         variant['strike'],
                'expiry_date':    str(variant['expiry_date']),
                'cc_open_price':  round(variant['open_price'], 4),
                'exit_time':      result['cc_close_time'],
                'exit_price':     result['stock_exit_price'],
                'exit_reason':    result['cc_close_reason'],
                'cc_close_price': result['cc_close_price'],
                'bars_held':      result['bars_held'],
                'shares':         SHARES,
                'stock_pnl':      result['stock_pnl'],
                'option_pnl':     result['option_pnl'],
                'combined_pnl':   result['combined_pnl'],
                'is_winner':      result['is_winner'],
                'data_status':    result['data_status'],
                **entry_snap,
            }
            all_trades.append(trade_row)
            var_state[key]['trades'].append(trade_row)
            entry_trade_rows.append((key, variant, cc_tv, result, trade_row))

            if (result.get('combined_pnl') is not None
                    and result.get('is_winner') is not None
                    and result.get('stock_exit_price') is not None):
                var_state[key]['positions'].append({
                    'trade_no':     trade_no,
                    'entry_time':   str(row['date']),
                    'entry_price':  entry_price,
                    'exit_price':   result['stock_exit_price'],
                    'bars_held':    result['bars_held'],
                    'stock_pnl':    result['stock_pnl'],
                    'option_pnl':   result['option_pnl'],
                    'combined_pnl': result['combined_pnl'],
                    'is_winner':    result['is_winner'],
                })

        if log_fh is not None:
            _log_trade(log_fh, trade_no, row, entry_snap, entry_trade_rows)

    # ── Build summary rows ────────────────────────────────────────────────────
    summary_rows = []
    for key in variants_selected:
        st = var_state[key]
        expiry_label, strike_label = key
        draws = (st['no_quote'] + st['tv_fail_low'] + st['tv_fail_high']
                 + st['cooldown_skip'] + st['accepted'])
        base = {
            'batch_no':       seq_no,
            'expiry_label':   expiry_label,
            'strike_label':   strike_label,
            'variant_key':    f"{expiry_label}/{strike_label}",
            'draws':          draws,
            'no_quote':       st['no_quote'],
            'tv_fail_low':    st['tv_fail_low'],
            'tv_fail_high':   st['tv_fail_high'],
            'cooldown_skip':  st['cooldown_skip'],
            'accepted':       st['accepted'],
            'batch_full_pct': round(st['accepted'] / batch_size * 100, 1)
                              if batch_size > 0 else 0.0,
        }
        if st['positions']:
            metrics = _calc_metrics(st['positions'])
            tv_vals = [t['cc_tv_at_entry'] for t in st['trades']
                       if t.get('cc_tv_at_entry') is not None]
            metrics['avg_cc_tv_at_entry'] = round(float(np.mean(tv_vals)), 4) if tv_vals else 0.0
            metrics['avg_stock_pnl']  = round(float(np.mean([p['stock_pnl']  for p in st['positions']])), 2)
            metrics['avg_option_pnl'] = round(float(np.mean([p['option_pnl'] for p in st['positions']])), 2)
        else:
            metrics = _empty_metrics('no_trades')
            metrics['avg_cc_tv_at_entry'] = 0.0
            metrics['avg_stock_pnl']      = 0.0
            metrics['avg_option_pnl']     = 0.0
        summary_rows.append({**base, **metrics})

    trades_df  = pd.DataFrame(all_trades) if all_trades else pd.DataFrame()
    summary_df = pd.DataFrame(summary_rows)
    return summary_df, trades_df, fires_seen


# ══════════════════════════════════════════════════════════════════════════════
# LOG WRITER
# ══════════════════════════════════════════════════════════════════════════════

def _fmt_pnl(v):
    if v is None:
        return 'N/A'
    return f"{v:+.2f}"


def _log_trade(fh, trade_no, row, entry_snap, entry_trade_rows):
    fh.write('\n' + '=' * 90 + '\n')
    fh.write(
        f" TRADE #{trade_no}  |  Entry: {row['date']}  "
        f"|  Stock avg_ask: ${float(row['avg_ask']):.2f}\n"
    )
    fh.write('=' * 90 + '\n')

    def _fmt(v, fmt='.2f'):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return 'N/A'
        try:
            return format(float(v), fmt)
        except (TypeError, ValueError):
            return str(v)

    fh.write(
        f" ATR: {_fmt(entry_snap.get('atr_at_entry'))}   "
        f"RSI: {_fmt(entry_snap.get('rsi_at_entry'))}   "
        f"ADX: {_fmt(entry_snap.get('adx_at_entry'))}   "
        f"VWAP: ${_fmt(entry_snap.get('vwap_at_entry'))}\n\n"
    )

    fh.write(' +----------+--------+--------+-----------+--------+---------------------+--------------+---------+---------+---------+\n')
    fh.write(' | Variant  | Expiry | Strike | Open Bid  | cc_tv  | Exit Time           | Exit Reason  | Stock$  | Option$ | Total$  |\n')
    fh.write(' +----------+--------+--------+-----------+--------+---------------------+--------------+---------+---------+---------+\n')
    for key, variant, cc_tv, result, tr in entry_trade_rows:
        vkey       = variant['variant_key']
        exp_str    = variant['expiry_date'].strftime('%y%m%d')
        strike_str = f"{variant['strike']:.2f}"
        op_str     = f"{variant['open_price']:.2f}"
        tv_str     = f"{cc_tv:.2f}"
        exit_time  = (result.get('cc_close_time') or 'N/A')[:19]
        reason     = result.get('cc_close_reason', 'N/A')
        sp = _fmt_pnl(tr.get('stock_pnl'))
        op = _fmt_pnl(tr.get('option_pnl'))
        cp = _fmt_pnl(tr.get('combined_pnl'))
        fh.write(
            f" | {vkey:<8} | {exp_str:<6} | {strike_str:>6} | {op_str:>9} "
            f"| {tv_str:>6} | {exit_time:<19} | {reason:<12} "
            f"| {sp:>7} | {op:>7} | {cp:>7} |\n"
        )
    fh.write(' +----------+--------+--------+-----------+--------+---------------------+--------------+---------+---------+---------+\n')


# ══════════════════════════════════════════════════════════════════════════════
# OUTPUT WRITERS
# ══════════════════════════════════════════════════════════════════════════════

def restructure_trades(df: pd.DataFrame) -> pd.DataFrame:
    """
    One row per accepted (variant × entry).
    Drops: model_id, trend, momentum, volatility, volume  (not present).
    Adds:  cc_tv (right after cc_open_price), cc_open_days, days_held.
    """
    if df.empty:
        return df
    out = df.copy()
    out = out.rename(columns={
        'entry_price':    'entry_stock_price',
        'exit_price':     'stock_exit_price',
        'cc_tv_at_entry': 'cc_tv',
    })
    out['days_held'] = (out['bars_held'] / _TRADING_MINS_PER_DAY).round(2)
    if 'entry_time' in out.columns and 'expiry_date' in out.columns:
        entry_dt  = pd.to_datetime(out['entry_time'])
        expiry_dt = pd.to_datetime(out['expiry_date']) + pd.Timedelta(hours=16)
        out['cc_open_days'] = (
            (expiry_dt - entry_dt).dt.total_seconds() / 86400
        ).round(2)

    col_order = [
        'batch_no', 'trade_no',
        'expiry_label', 'strike_label', 'strike', 'expiry_date',
        'entry_time', 'exit_time',
        'entry_stock_price', 'stock_exit_price',
        'cc_open_price', 'cc_tv', 'cc_open_days', 'cc_close_price',
        'exit_reason', 'bars_held', 'days_held', 'shares',
        'stock_pnl', 'option_pnl', 'combined_pnl',
    ]
    cols = [c for c in col_order if c in out.columns]
    return out[cols].reset_index(drop=True)


def write_summary_csv(summary_df: pd.DataFrame, seq_no: int, run_dir: Path) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / f'{seq_no}_summary.csv'
    out  = summary_df.rename(columns={
        'n_trades':          'number_of_trades',
        'avg_entry':         'avg_entry_price',
        'avg_exit':          'avg_exit_price',
        'avg_duration_bars': 'avg_bars_held',
    })
    col_order = [
        'batch_no', 'expiry_label', 'strike_label', 'variant_key',
        'number_of_trades', 'win_rate',
        'avg_entry_price', 'avg_exit_price', 'avg_bars_held',
        'avg_cc_tv_at_entry',
        'total_pnl', 'avg_pnl', 'profit_factor', 'sharpe', 'max_drawdown',
        'avg_stock_pnl', 'avg_option_pnl',
        'draws', 'no_quote', 'tv_fail_low', 'tv_fail_high',
        'cooldown_skip', 'accepted', 'batch_full_pct',
        'pnl_positive', 'status',
    ]
    cols = [c for c in col_order if c in out.columns]
    out  = out[cols]
    out.to_csv(path, index=False)
    print(f"[{seq_no}][report]  Summary  -> {path}  ({len(out):,} rows)")
    return path


def write_trades_csv(trades_df: pd.DataFrame, seq_no: int, run_dir: Path) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / f'{seq_no}_trades.csv'
    out  = restructure_trades(trades_df)
    out.to_csv(path, index=False)
    print(f"[{seq_no}][report]  Trades   -> {path}  ({len(out):,} rows)")
    return path


# ══════════════════════════════════════════════════════════════════════════════
# SINGLE WINDOW EXECUTION
# ══════════════════════════════════════════════════════════════════════════════

def run_model_set(
    seq_no:            int,
    run_dir:           Path,
    date_start:        str,
    date_end:          str,
    seed:              int,
    batch_size:        int,
    cc_tv_min:         float,
    cc_tv_max:         float,
    buyback_tv:        float,
    variants_selected: list,
):
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"\n{'='*60}")
    print(f"[{seq_no}]  AAPL CC Signal-Driven  |  start at {ts}")
    print(f"[{seq_no}]  Window: {date_start} -> {date_end}  seed: {seed}")
    print(f"[{seq_no}]  Variants: {len(variants_selected)}  "
          f"batch={batch_size:,}  "
          f"cc_tv=[{cc_tv_min:.2f},{cc_tv_max:.2f}]  buyback_tv={buyback_tv:.2f}")
    print(f"{'='*60}\n")

    df_full = load_signals()
    window_data, wstatus = prepare_window(df_full, date_start, date_end, seed)
    if window_data is None:
        print(f"[{seq_no}] ERROR: {wstatus} for {date_start} -> {date_end}")
        return
    df, signal_idx = window_data
    print(f"[{seq_no}][filter]  {date_start} -> {date_end}: {len(df):,} rows, "
          f"{len(signal_idx):,} signal-fire bars")

    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / f'{seq_no}_run.log'
    with open(log_path, 'w', encoding='utf-8') as log_fh:
        log_fh.write(
            f"seq_no: [{seq_no}]  window: {date_start} -> {date_end}  "
            f"seed: {seed}  generated: {ts}\n"
            f"variants: {len(variants_selected)}  batch_size: {batch_size}  "
            f"cc_tv: [{cc_tv_min}, {cc_tv_max}]  buyback_tv: {buyback_tv}\n"
        )
        summary_df, trades_df, fires_seen = run_window_batch(
            df, signal_idx, variants_selected,
            batch_size, cc_tv_min, cc_tv_max, buyback_tv,
            seq_no, log_fh=log_fh,
        )
    print(f"[{seq_no}][report]  Log      -> {log_path}")
    print(f"[{seq_no}]          fires processed: {fires_seen:,}/{len(signal_idx):,}")

    write_summary_csv(summary_df, seq_no, run_dir)
    if not trades_df.empty:
        write_trades_csv(trades_df, seq_no, run_dir)

    active = summary_df[summary_df['n_trades'] > 0]
    print(f"\n{'='*60}")
    print(f"[{seq_no}]  RUN COMPLETE")
    print(f"{'='*60}")
    print(f"[{seq_no}]  Variants with trades : {len(active)}/{len(summary_df)}")
    print(f"[{seq_no}]  Total trades         : {len(trades_df):,}")

    if not active.empty:
        best  = active.loc[active['total_pnl'].idxmax()]
        worst = active.loc[active['total_pnl'].idxmin()]
        print(f"[{seq_no}]  Best  : {best['variant_key']:<12}  "
              f"P&L=${best['total_pnl']:,.2f}  Sharpe={best['sharpe']:.2f}  "
              f"accepted={int(best['accepted'])}")
        print(f"[{seq_no}]  Worst : {worst['variant_key']:<12}  "
              f"P&L=${worst['total_pnl']:,.2f}  Sharpe={worst['sharpe']:.2f}  "
              f"accepted={int(worst['accepted'])}")

    print(f"{'='*60}\n")


# ── top-level worker wrapper (for multiprocessing pickling) ───────────────────

def _run_window(args: tuple) -> int:
    (seq_no, run_dir, date_start, date_end, seed,
     batch_size, cc_tv_min, cc_tv_max, buyback_tv,
     expiry_filter, strike_filter) = args
    variants = [(e, s) for e in expiry_filter for s in strike_filter]
    run_model_set(
        seq_no, run_dir, date_start, date_end, seed,
        batch_size, cc_tv_min, cc_tv_max, buyback_tv,
        variants,
    )
    return seq_no


# ══════════════════════════════════════════════════════════════════════════════
# CROSS-WINDOW ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def _pf_mean_uncapped(series: pd.Series) -> float:
    """
    Mean profit factor without capping.  Values ≥ 1e8 are the "no-losses"
    sentinel emitted by _calc_metrics (gross_l == 0 → float('inf') → clipped
    to 1e9).  They are not real magnitudes, so exclude them from the mean; if
    every window is a sentinel, return inf so the saturation still maps → 1.
    """
    s = series.replace([np.inf, -np.inf], np.nan)
    s = s.where(s < 1e8, np.nan)
    m = s.mean()
    if pd.isna(m):
        return float('inf') if (series >= 1e8).any() else 0.0
    return float(m)


def _score_components(
    pnl_hit:           float,
    sharpe_hit:        float,
    win_rate_0_100:    float,
    pf_mean:           float,
    avg_per_trade_pnl: float,
) -> float:
    """
    Blend consistency + profitability into a 0-100 score.

        0.25 × pnl_hit_rate
      + 0.15 × sharpe_hit_rate
      + 0.15 × avg_win_rate / 100
      + 0.15 × avg_pf / (avg_pf + PF_SAT_K)           (saturation, uncapped)
      + 0.30 × (tanh(avg_per_trade_pnl / PROF_K) + 1)/2
    """
    pf_sat = 1.0 if np.isinf(pf_mean) else (
        pf_mean / (pf_mean + PF_SAT_K) if pf_mean > 0 else 0.0
    )
    prof_norm = 0.5 + 0.5 * np.tanh(avg_per_trade_pnl / PROF_K)
    raw = (
        0.25 * pnl_hit
        + 0.15 * sharpe_hit
        + 0.15 * win_rate_0_100 / 100.0
        + 0.15 * pf_sat
        + 0.30 * prof_norm
    )
    return round(min(100.0, max(0.0, raw * 100.0)), 1)


def analyse_variants(df: pd.DataFrame, n_batches: int, run_ts: str = '') -> pd.DataFrame:
    """One row per variant, ranked by overall score across all windows."""
    active = df[df['number_of_trades'] > 0]
    rows   = []
    for (el, sl), grp in active.groupby(['expiry_label', 'strike_label']):
        vkey         = f"{el}/{sl}"
        pnl_hit      = float((grp['total_pnl'] > 0).mean())
        sharpe_hit   = float((grp['sharpe']    > 0).mean())
        win_rate     = float(grp['win_rate'].mean())
        pf_mean      = _pf_mean_uncapped(grp['profit_factor'])

        total_trades = int(grp['number_of_trades'].sum())
        total_pnl    = float(grp['total_pnl'].sum())
        avg_per_trade_pnl = total_pnl / total_trades if total_trades > 0 else 0.0

        # weighted average of (avg_bars_held / 390) by number_of_trades
        if 'avg_bars_held' in grp.columns and total_trades > 0:
            avg_days_held = round(
                float((grp['avg_bars_held'] * grp['number_of_trades']).sum())
                / total_trades / _TRADING_MINS_PER_DAY, 2
            )
        else:
            avg_days_held = 0.0

        score = _score_components(pnl_hit, sharpe_hit, win_rate, pf_mean, avg_per_trade_pnl)

        rows.append({
            'run_ts':            run_ts,
            'variant_key':       vkey,
            'expiry_label':      el,
            'strike_label':      sl,
            'batch_count':       int(grp['batch_no'].nunique()) if 'batch_no' in grp.columns else n_batches,
            'total_trades':      total_trades,
            'avg_trades':        round(grp['number_of_trades'].mean(), 1),
            'avg_cc_tv':         round(grp['avg_cc_tv_at_entry'].mean(), 3)
                                  if 'avg_cc_tv_at_entry' in grp.columns else 0.0,
            'avg_win_rate':      round(win_rate, 1),
            'avg_total_pnl':     round(grp['total_pnl'].mean(), 2),
            'avg_per_trade_pnl': round(avg_per_trade_pnl, 2),
            'avg_days_held':     avg_days_held,
            'total_pnl':         round(total_pnl, 2),
            'pnl_hit_rate':      round(pnl_hit, 3),
            'avg_sharpe':        round(grp['sharpe'].mean(), 3),
            'avg_pf':            round(pf_mean, 3) if not np.isinf(pf_mean) else float('inf'),
            'consistency_score': score,
        })
    if not rows:
        return pd.DataFrame(columns=[
            'run_ts', 'rank', 'variant_key', 'expiry_label', 'strike_label',
            'batch_count', 'total_trades', 'avg_trades', 'avg_cc_tv',
            'avg_win_rate', 'avg_total_pnl', 'avg_per_trade_pnl', 'avg_days_held',
            'total_pnl', 'pnl_hit_rate', 'avg_sharpe', 'avg_pf', 'consistency_score',
        ])
    result = pd.DataFrame(rows).sort_values('consistency_score', ascending=False)
    result.insert(0, 'rank', range(1, len(result) + 1))
    return result.reset_index(drop=True)


def aggregate_funnel(df: pd.DataFrame) -> pd.DataFrame:
    """Sum funnel counters across windows, per variant."""
    cols = ['draws', 'no_quote', 'tv_fail_low', 'tv_fail_high',
            'cooldown_skip', 'accepted']
    present = [c for c in cols if c in df.columns]
    if not present:
        return pd.DataFrame()
    agg = df.groupby(['expiry_label', 'strike_label'])[present].sum().reset_index()
    agg['accept_pct'] = np.where(
        agg['draws'] > 0, agg['accepted'] / agg['draws'] * 100, 0.0,
    ).round(1)
    return agg


def append_to_all_runs(df: pd.DataFrame, run_dir: Path, run_params: dict | None = None) -> None:
    """Append one row per variant per run to REPORTS_DIR/all_runs.csv."""
    all_runs_path = REPORTS_DIR / 'all_runs.csv'
    run_ts  = run_dir.name
    p       = run_params or {}
    active  = df[df['number_of_trades'] > 0].copy()
    if active.empty:
        print("[all_runs]  No active rows — skipping.")
        return

    rows = []
    for (el, sl), grp in active.groupby(['expiry_label', 'strike_label']):
        vkey    = f"{el}/{sl}"
        n_total = int(grp['number_of_trades'].sum())
        if n_total == 0:
            continue

        def _wavg(col: str, fallback: float = 0.0) -> float:
            if col not in grp.columns:
                return fallback
            return round(float((grp[col] * grp['number_of_trades']).sum() / n_total), 2)

        pf_mean    = (_pf_mean_uncapped(grp['profit_factor'])
                      if 'profit_factor' in grp.columns else 0.0)
        pnl_hit    = float((grp['total_pnl'] > 0).mean()) if 'total_pnl' in grp.columns else 0.0
        sharpe_col = grp['sharpe'] if 'sharpe' in grp.columns else pd.Series([0.0])
        sharpe_hit = float((sharpe_col > 0).mean())
        win_rate   = float(grp['win_rate'].mean()) if 'win_rate' in grp.columns else 0.0
        avg_per_trade_pnl = (
            float(grp['total_pnl'].sum()) / n_total if n_total > 0 else 0.0
        )
        score = _score_components(pnl_hit, sharpe_hit, win_rate, pf_mean, avg_per_trade_pnl)

        rows.append({
            'run_ts':             run_ts,
            'cc_tv_min':          p.get('cc_tv_min'),
            'cc_tv_max':          p.get('cc_tv_max'),
            'buyback_tv':         p.get('buyback_tv'),
            'expiry_label':       el,
            'strike_label':       sl,
            'avg_stock_pnl':      _wavg('avg_stock_pnl'),
            'avg_option_pnl':     _wavg('avg_option_pnl'),
            'avg_total_pnl':      _wavg('avg_pnl'),
            'avg_cc_tv':          _wavg('avg_cc_tv_at_entry'),
            'trade_count':        n_total,
            'win_pct':            _wavg('win_rate'),
            'total_pnl':          round(float(grp['total_pnl'].sum()), 2),
            'sharpe':             round(float(sharpe_col.mean()), 3),
            'profit_factor':      round(pf_mean, 3) if not np.isinf(pf_mean) else float('inf'),
            'max_drawdown':       round(float(grp['max_drawdown'].min()), 2)
                                  if 'max_drawdown' in grp.columns else 0.0,
            'avg_bars_held':      _wavg('avg_bars_held'),
            'avg_per_trade_pnl':  round(avg_per_trade_pnl, 2),
            'consistency_score':  score,
        })

    if not rows:
        print("[all_runs]  No rows to append.")
        return
    col_order = [
        'run_ts', 'cc_tv_min', 'cc_tv_max', 'buyback_tv',
        'expiry_label', 'strike_label',
        'avg_stock_pnl', 'avg_option_pnl', 'avg_total_pnl', 'avg_cc_tv',
        'trade_count', 'win_pct', 'total_pnl', 'avg_per_trade_pnl',
        'sharpe', 'profit_factor', 'max_drawdown', 'avg_bars_held',
        'consistency_score',
    ]
    new_df = pd.DataFrame(rows)
    new_df = new_df[[c for c in col_order if c in new_df.columns]]

    write_header = not all_runs_path.exists()
    new_df.to_csv(all_runs_path, mode='a', header=write_header, index=False)
    print(f"[all_runs]  Appended {len(new_df):,} rows  ->  {all_runs_path}")


# ══════════════════════════════════════════════════════════════════════════════
# MARKDOWN REPORT
# ══════════════════════════════════════════════════════════════════════════════

def write_markdown_report(
    variants_df:  pd.DataFrame,
    funnel_df:    pd.DataFrame,
    n_batches:    int,
    total_rows:   int,
    run_dir:      Path,
    run_params:   dict | None = None,
):
    p    = run_params or {}
    now  = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    path = run_dir / 'batch_run_analysis.md'

    lines = ['# Batch Run Analysis — Random-Entry Covered Calls']
    lines.append(f'\n_Generated: {now}_\n')
    lines.append('## Run Parameters\n')
    lines.append('| Parameter | Value |')
    lines.append('|---|---|')
    lines.append(f'| Run directory       | `{run_dir.name}` |')
    lines.append(f'| Date range          | {p.get("date_first","—")} → {p.get("date_last","—")} |')
    lines.append(f'| Windows run         | {p.get("n_windows", n_batches)} |')
    lines.append(f'| Windows analysed    | {n_batches} |')
    lines.append(f'| Window length       | {p.get("window_days","—")} calendar days |')
    lines.append(f'| Random seed         | {p.get("random_seed","—")} |')
    lines.append(f'| Workers             | {p.get("n_workers","—")} |')
    batch_size  = p.get('batch_size')
    lines.append(f'| Batch size / win    | {batch_size:,} |' if batch_size else '| Batch size / win    | — |')
    lines.append(f'| Entry signal        | (any bsig_trend) AND (any bsig_momentum) |')
    lines.append(f'| cc_tv range         | ${p.get("cc_tv_min","—")} – ${p.get("cc_tv_max","—")} |')
    lines.append(f'| Buyback tv          | ${p.get("buyback_tv","—")} |')
    lines.append(f'| Cooldown            | {COOLDOWN_MINUTES} min |')
    lines.append(f'| Expiry labels       | {", ".join(p.get("expiry_labels", []))} |')
    lines.append(f'| Strike labels       | {", ".join(p.get("strike_labels", []))} |')
    lines.append(f'| Variants tested     | {p.get("n_variants","—")} |')
    lines.append(f'| Shares per trade    | {SHARES} |')
    lines.append(f'| Commission          | ${COMMISSION:.2f} per round-trip |')
    lines.append(f'| Profit factor       | uncapped (saturation K={PF_SAT_K:g} in score) |')
    lines.append(f'| Profitability K     | ${PROF_K:g} (tanh scale for per-trade PnL term) |')
    lines.append(f'| Total summary rows  | {total_rows:,} |')
    if p.get('argv'):
        lines.append(f'| Command             | `{" ".join(p["argv"])}` |')
    lines.append('')

    lines.append('### Score Formula (blended consistency + profitability)\n')
    lines.append('```')
    lines.append('score = 0.25 × pnl_hit_rate                            (windows with total_pnl > 0)')
    lines.append('      + 0.15 × sharpe_hit_rate                         (windows with sharpe > 0)')
    lines.append('      + 0.15 × avg_win_rate / 100')
    lines.append(f'      + 0.15 × avg_pf / (avg_pf + {PF_SAT_K:g})                    (saturation, profit factor uncapped)')
    lines.append(f'      + 0.30 × (tanh(avg_per_trade_pnl / {PROF_K:g}) + 1)/2      (profitability term)')
    lines.append('(all terms bounded to 0–1, score reported 0–100)')
    lines.append('```\n')

    lines.append('## Variant Rankings\n')
    variant_display = [
        'run_ts', 'rank', 'variant_key', 'expiry_label', 'strike_label',
        'batch_count', 'total_trades', 'avg_trades', 'avg_cc_tv',
        'avg_win_rate', 'avg_total_pnl', 'avg_per_trade_pnl', 'avg_days_held', 'total_pnl',
        'pnl_hit_rate', 'avg_sharpe', 'avg_pf', 'consistency_score',
    ]
    if not variants_df.empty:
        lines.append(md_table(
            variants_df[[c for c in variant_display if c in variants_df.columns]],
            n=len(variants_df),
        ))
    else:
        lines.append('_No variants had active trades._')
    lines.append('')

    lines.append('## Entry Funnel — Aggregate Across Windows\n')
    if not funnel_df.empty:
        lines.append('| Variant | Draws | No Quote | TV Low | TV High | Cooldown | Accepted | Accept % |')
        lines.append('|---|---:|---:|---:|---:|---:|---:|---:|')
        for _, r in funnel_df.iterrows():
            vkey = f"{r['expiry_label']}/{r['strike_label']}"
            lines.append(
                f"| {vkey} "
                f"| {int(r['draws']):,} "
                f"| {int(r['no_quote']):,} "
                f"| {int(r['tv_fail_low']):,} "
                f"| {int(r['tv_fail_high']):,} "
                f"| {int(r['cooldown_skip']):,} "
                f"| {int(r['accepted']):,} "
                f"| {r['accept_pct']:.1f}% |"
            )
    else:
        lines.append('_No funnel data recorded._')
    lines.append('')

    path.write_text('\n'.join(lines), encoding='utf-8')
    print(f"[report]  Markdown  -> {path}")


# ══════════════════════════════════════════════════════════════════════════════
# CONSOLIDATE & SUMMARIZE
# ══════════════════════════════════════════════════════════════════════════════

def consolidate_csvs(run_dir: Path) -> None:
    for stem, pattern in [('summary', '*_summary.csv'), ('trades', '*_trades.csv')]:
        files = sorted(
            run_dir.glob(pattern),
            key=lambda p: int(p.stem.split('_')[0]),
        )
        if not files:
            continue
        out_path = run_dir / f'{stem}.csv'
        first = True
        with open(out_path, 'w', encoding='utf-8') as fout:
            for f in files:
                with open(f, encoding='utf-8') as fin:
                    header = fin.readline()
                    if first:
                        fout.write(header)
                        first = False
                    fout.write(fin.read())
                f.unlink()
        print(f"[consolidate]  {len(files)} files → {out_path.name}  "
              f"({out_path.stat().st_size / 1_048_576:.1f} MB)")


def summarize_run(run_dir: Path):
    print(f"\n{'='*60}")
    print(f"  Batch Run Analysis — {run_dir.name}")
    print(f"{'='*60}\n")

    params_path = run_dir / 'run_params.json'
    run_params  = json.loads(params_path.read_text()) if params_path.exists() else {}

    summary_path = run_dir / 'summary.csv'
    if summary_path.exists():
        df = pd.read_csv(summary_path, low_memory=False)
        n_batches = int(df['batch_no'].nunique()) if 'batch_no' in df.columns else 1
        print(f"[load]  summary.csv  |  {n_batches} runs  |  {len(df):,} rows")
    else:
        files = sorted(run_dir.glob('*_summary.csv'))
        if not files:
            print("[load]  No summary files found.")
            return
        df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
        n_batches = len(files)
        print(f"[load]  {n_batches} per-window summary files | {len(df):,} rows")

    numeric_cols = [
        'number_of_trades', 'win_rate', 'total_pnl', 'avg_pnl',
        'avg_bars_held', 'profit_factor', 'sharpe', 'max_drawdown',
        'avg_stock_pnl', 'avg_option_pnl', 'avg_cc_tv_at_entry',
        'draws', 'no_quote', 'tv_fail_low', 'tv_fail_high',
        'cooldown_skip', 'accepted', 'batch_full_pct',
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    print("[analyse] Variant rankings ...")
    variants_df = analyse_variants(df, n_batches, run_ts=run_dir.name)
    variants_df.to_csv(run_dir / 'analysis_variants.csv', index=False)
    print(f"          {len(variants_df)} variants ranked  ->  analysis_variants.csv")

    funnel_df = aggregate_funnel(df)

    print("\n[report]  Writing markdown ...")
    write_markdown_report(variants_df, funnel_df, n_batches, len(df), run_dir, run_params)

    print("\n[all_runs] Appending to all_runs.csv ...")
    append_to_all_runs(df, run_dir, run_params=run_params)

    print(f"\n{'='*60}")
    print("  VARIANT RANKINGS")
    print(f"{'='*60}")
    if not variants_df.empty:
        print(f"  {'Rank':<5} {'Variant':<12} {'Trades':>7}  {'$/trade':>8}  "
              f"{'Total $':>10}  {'Win%':>6}  {'PF':>7}  {'Score':>6}")
        for _, r in variants_df.iterrows():
            pf_disp = '   inf' if np.isinf(r.get('avg_pf', 0)) else f"{r['avg_pf']:>7.2f}"
            print(
                f"  #{int(r['rank']):<4} "
                f"{r['variant_key']:<12} "
                f"{int(r['total_trades']):>6,}   "
                f"${r['avg_per_trade_pnl']:>7,.2f}  "
                f"${r['total_pnl']:>9,.2f}  "
                f"{r['avg_win_rate']:>5.1f}%  "
                f"{pf_disp}  "
                f"{r['consistency_score']:>6.1f}"
            )
    print(f"{'='*60}\n")


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    _parser = argparse.ArgumentParser(
        description='Run signal-driven covered-call strategy across N windows. '
                    'Entry = (any bsig_trend) AND (any bsig_momentum); '
                    '60-min cooldown; variants gated by cc_tv range.'
    )
    _parser.add_argument('--seed', type=int, default=None,
                         help='Master RNG seed (omit for a fresh random seed each run)')
    _parser.add_argument('--workers', type=int,
                         default=max(1, (os.cpu_count() or 1) - 1),
                         help='Number of parallel worker processes (default: cpu_count - 1)')
    _parser.add_argument('--data-first', type=str, default='2023-01-01',
                         help='Start of data range (YYYY-MM-DD, default: 2023-01-01)')
    _parser.add_argument('--data-last', type=str, default='2026-02-28',
                         help='End of data range (YYYY-MM-DD, default: 2026-02-28)')
    _parser.add_argument('--window-days', type=int, default=14,
                         help='Calendar days per test window (default: 14)')
    _parser.add_argument('--windows', type=int, default=100,
                         help='Number of test windows (default: 100)')
    _parser.add_argument('--batch_size', type=int, default=1_000,
                         help='Max accepted trades per variant per window (default: 1000)')
    _parser.add_argument('--buyback_tv', type=float, default=0.25,
                         help='Buyback threshold on the ask-side time value (default: 0.25)')
    _parser.add_argument('--cc_tv_min', type=float, default=1.00,
                         help='Minimum opening time value to open a position (default: 1.00)')
    _parser.add_argument('--cc_tv_max', type=float, default=3.00,
                         help='Maximum opening time value to open a position (default: 3.00)')
    _parser.add_argument('--expiry-label', nargs='+', default=None, metavar='LABEL',
                         help='Expiry weeks to include: w0 w1 w2 (default: all three)')
    _parser.add_argument('--strike-label', nargs='+', default=None, metavar='LABEL',
                         help='Strike labels: s-2 s-1 s-0 s+0 s+1 s+2 (default: all six)')
    _args = _parser.parse_args()

    _expiry_filter = _args.expiry_label or EXPIRY_WEEKS
    _strike_filter = _args.strike_label or STRIKE_LABELS

    _invalid_expiry = [e for e in _expiry_filter if e not in EXPIRY_WEEKS]
    _invalid_strike = [s for s in _strike_filter if s not in STRIKE_LABELS]
    if _invalid_expiry:
        _parser.error(f"Invalid --expiry-label value(s): {_invalid_expiry}. "
                      f"Choose from {EXPIRY_WEEKS}")
    if _invalid_strike:
        _parser.error(f"Invalid --strike-label value(s): {_invalid_strike}. "
                      f"Choose from {STRIKE_LABELS}")

    _variants_selected = [(e, s) for e in _expiry_filter for s in _strike_filter]

    RANDOM_SEED = _args.seed if _args.seed is not None else secrets.randbelow(2**32)
    N_WORKERS   = _args.workers
    DATA_FIRST  = datetime.date.fromisoformat(_args.data_first)
    DATA_LAST   = datetime.date.fromisoformat(_args.data_last)
    WINDOW_DAYS = _args.window_days
    N_WINDOWS   = _args.windows

    run_ts  = datetime.datetime.now().strftime('%m%d%H%M')
    run_dir = REPORTS_DIR / run_ts
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"[main]  Master seed : {RANDOM_SEED}")
    print(f"[main]  Workers     : {N_WORKERS}")
    print(f"[main]  Windows     : {N_WINDOWS}  ({WINDOW_DAYS}-day each)")
    print(f"[main]  Date range  : {DATA_FIRST} → {DATA_LAST}")
    print(f"[main]  Batch size  : {_args.batch_size:,} trades/variant/window")
    print(f"[main]  Entry       : (any bsig_trend) AND (any bsig_momentum)")
    print(f"[main]  cc_tv range : ${_args.cc_tv_min:.2f} – ${_args.cc_tv_max:.2f}")
    print(f"[main]  Buyback tv  : ${_args.buyback_tv:.2f}")
    print(f"[main]  Cooldown    : {COOLDOWN_MINUTES} min")
    print(f"[main]  Expiry      : {_expiry_filter}")
    print(f"[main]  Strike      : {_strike_filter}")
    print(f"[main]  Variants    : {len(_variants_selected)}")
    print(f"[main]  Output dir  : {run_dir}")

    _run_params = {
        'run_ts':        run_ts,
        'random_seed':   RANDOM_SEED,
        'n_workers':     N_WORKERS,
        'n_windows':     N_WINDOWS,
        'window_days':   WINDOW_DAYS,
        'date_first':    str(DATA_FIRST),
        'date_last':     str(DATA_LAST),
        'batch_size':    _args.batch_size,
        'cc_tv_min':     _args.cc_tv_min,
        'cc_tv_max':     _args.cc_tv_max,
        'buyback_tv':    _args.buyback_tv,
        'cooldown_min':  COOLDOWN_MINUTES,
        'expiry_labels': _expiry_filter,
        'strike_labels': _strike_filter,
        'n_variants':    len(_variants_selected),
        'commission':    COMMISSION,
        'shares':        SHARES,
        'argv':          sys.argv,
    }
    (run_dir / 'run_params.json').write_text(
        json.dumps(_run_params, indent=2), encoding='utf-8',
    )

    # Warm caches in the main process before forking workers
    print("[main]  Loading signals CSV and option index ...")
    load_signals()
    load_option_index()
    print("[main]  Data ready.\n")

    # ── Generate N_WINDOWS window specs ───────────────────────────────────────
    max_start  = DATA_LAST - datetime.timedelta(days=WINDOW_DAYS)
    total_days = (max_start - DATA_FIRST).days

    if N_WINDOWS > total_days:
        raise ValueError(
            f"--windows {N_WINDOWS} exceeds available unique start days "
            f"({total_days}) for date range {DATA_FIRST} → {DATA_LAST} with "
            f"--window-days {WINDOW_DAYS}.  Reduce --windows or extend the range."
        )

    master_rng = np.random.default_rng(RANDOM_SEED)
    offsets    = master_rng.choice(total_days, size=N_WINDOWS, replace=False).tolist()
    run_seeds  = master_rng.integers(1, 10_000, size=N_WINDOWS).tolist()
    runs       = sorted(zip(offsets, run_seeds), key=lambda x: x[0])

    window_args = []
    for seq_no, (offset, seed) in enumerate(runs, 1):
        date_start = (DATA_FIRST + datetime.timedelta(days=int(offset))).strftime('%Y-%m-%d')
        date_end   = (DATA_FIRST + datetime.timedelta(days=int(offset) + WINDOW_DAYS)).strftime('%Y-%m-%d')
        window_args.append((
            seq_no, run_dir, date_start, date_end, int(seed),
            _args.batch_size,
            _args.cc_tv_min, _args.cc_tv_max, _args.buyback_tv,
            _expiry_filter, _strike_filter,
        ))
        print(f"  [{seq_no:>3}]  {date_start} -> {date_end}  seed={seed}")

    # ── Dispatch to worker pool ───────────────────────────────────────────────
    print(f"\n[main]  Submitting {N_WINDOWS} windows to {N_WORKERS} workers ...\n")
    completed = 0
    failed    = 0

    with concurrent.futures.ProcessPoolExecutor(max_workers=N_WORKERS) as pool:
        futures = {pool.submit(_run_window, args): args[0] for args in window_args}
        for fut in concurrent.futures.as_completed(futures):
            seq_no = futures[fut]
            try:
                fut.result()
                completed += 1
            except Exception as exc:
                failed += 1
                print(f"[main]  ERROR — window {seq_no} failed: {exc}")
            print(f"[main]  Progress: {completed + failed}/{len(window_args)} done  "
                  f"({completed} ok, {failed} failed)")

    print(f"\n[main]  All windows finished.  {completed} ok / {failed} failed.")

    print("\n[consolidate] Merging per-window CSVs ...")
    consolidate_csvs(run_dir)

    summarize_run(run_dir)
