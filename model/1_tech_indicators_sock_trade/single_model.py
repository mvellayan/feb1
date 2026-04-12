"""
single_model.py

Core trading simulation engine. Also runs as a standalone script to test a
single indicator combination across 500 time windows.

Engine functions (imported by batch_run_all_models.py)
───────────────────────────────────────────────────────
  load_or_build_signals()
  draw_sample(df, seed)
  compute_bracket(entry_avg, atr)
  compute_shares(entry_price)
  simulate_trade(df_day, entry_iloc, stop, target, sell_cols,
                 entry_price, entry_avg, shares, capture_evals)
  prepare_window(df_signals, window_start, window_end, seed)
      → ((df, sample_idx, day_dict, day_pos_map), status_str)
  run_combo(df, sample_idx, day_dict, day_pos_map, indicators, capture_evals)
      → (metrics_dict, trades_list)

Standalone usage
─────────────────
  python single_model.py
  python single_model.py --trend adx --momentum frc --volatility atr --volume vrc
  python single_model.py --trend ema --momentum rsi   # blank volatility + volume

Output  (../reports/single_{combo}_{mmddhhmi}/)
──────
  single_{combo}_runs.csv    – one row per run (window + seed + metrics)
  single_{combo}_trades.csv  – every trade across all 500 runs
  run.log                    – structured trade-by-trade log
"""

from __future__ import annotations

import argparse
import datetime
import random
import secrets
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from signals import add_buy_signals, add_sell_signals
from utils import REPORTS_DIR, PF_CAP, md_table

warnings.filterwarnings('ignore')

# ── file paths ─────────────────────────────────────────────────────────────────
_BASE        = Path(__file__).parent.parent.parent
EXTENDED_CSV = _BASE / 'data/stock/sq_AAPL_extended.csv'
SIGNALS_CSV  = _BASE / 'data/stock/sq_AAPL_signals.csv'

# ── simulation constants ───────────────────────────────────────────────────────
N_SAMPLE      = 10_000
RANDOM_SEED   = 42
TRADE_CAPITAL = 10_000.0
COMMISSION    = 2.00
ATR_STOP_MULT = 1.5
ATR_TARGET_RR = 2.0
EXIT_MINUTE      = 15 * 60 + 45   # 3:45 PM (kept for reference; exit is stop-loss only)
END_OF_WEEK_EXIT = False           # when True: hold through Friday 4 PM instead of EOD

# ── standalone run constants ───────────────────────────────────────────────────
N_RUNS      = 500
DATA_FIRST  = datetime.date(2023, 1, 1)   # overridable via --data-first
DATA_LAST   = datetime.date(2026, 2, 28)  # overridable via --data-last
WINDOW_DAYS = 14                           # overridable via --window-days
META_SEED   = 42


# ══════════════════════════════════════════════════════════════════════════════
# SIGNALS CSV  (load or build)
# ══════════════════════════════════════════════════════════════════════════════

_signals_cache: pd.DataFrame | None = None


def load_or_build_signals() -> pd.DataFrame:
    """
    Returns the signals DataFrame.
    Cached in memory after the first load — subsequent calls return instantly.
    """
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
# SAMPLE DRAWING
# ══════════════════════════════════════════════════════════════════════════════

def draw_sample(df: pd.DataFrame, seed: int = RANDOM_SEED) -> list:
    """
    Returns up to N_SAMPLE valid entry bar indices drawn with the given seed.
    Valid = after 10 AM, before 3:45 PM, no ATR spike, key indicators available.
    """
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
# TRADE SIMULATION UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

def compute_bracket(entry_avg: float, atr: float):
    """
    Returns (stop_loss, profit_target) based on ATR, or None if ATR invalid.
    """
    if np.isnan(atr) or atr <= 0:
        return None
    stop   = entry_avg - atr * ATR_STOP_MULT
    target = entry_avg + atr * ATR_STOP_MULT * ATR_TARGET_RR
    return round(stop, 4), round(target, 4)


def compute_shares(entry_price: float) -> int:
    """Floor integer shares purchasable with TRADE_CAPITAL."""
    if entry_price <= 0:
        return 0
    return int(TRADE_CAPITAL / entry_price)


def simulate_trade(
    df_day:        pd.DataFrame,
    entry_iloc:    int,
    stop:          float,
    target:        float,
    sell_cols:     list,
    entry_price:   float,          # avg_ask — actual cost paid to enter
    entry_avg:     float,          # average (WAP) at entry — seeds the high-water mark
    shares:        int,
    capture_evals: bool = False,   # when True, include bar-by-bar eval list in result
) -> dict:
    """
    Walks forward from entry_iloc+1 through end of day.

    Prices used:
      entry_price  = avg_ask   (what was paid to buy)
      entry_avg    = average   (WAP at entry; seeds the high-water mark)
      decision ref = average   (WAP each bar; drives trailing-stop updates and trigger)
      exit_price   = avg_bid   (what the market pays on exit)

    Trailing stop: each bar, if average > high_water, raise stop by the same
    amount to lock in gains.
    Exit: stop-loss only (bar's average <= trailing stop).

    # Commented-out exits (kept for reference):
    #   1. Time box    -- bar's ses_minute >= EXIT_MINUTE -> exit at avg_bid
    #   3. Profit tgt  -- bar's average >= target -> exit at avg_bid
    #   4. Sell signal -- any sell col is 1    -> exit at avg_bid
    """
    exit_price  = None
    exit_reason = None
    exit_bar    = None
    bars_held   = 0
    evals       = []

    trailing_stop = stop
    high_water    = entry_avg

    for i in range(entry_iloc + 1, len(df_day)):
        bar     = df_day.iloc[i]
        bar_avg = float(bar['average'])
        bars_held += 1

        if bar_avg > high_water:
            gain          = bar_avg - high_water
            trailing_stop = round(trailing_stop + gain, 4)
            high_water    = bar_avg

        if capture_evals:
            evals.append((str(bar['date']), round(bar_avg, 4), round(trailing_stop, 4)))

        # # 1. Time box
        # if int(bar['ses_minute']) >= EXIT_MINUTE:
        #     exit_price  = float(bar['avg_bid'])
        #     exit_reason = 'time_box'
        #     exit_bar    = bar
        #     break

        # 2. Trailing stop-loss — triggered when WAP falls to/below stop
        if bar_avg <= trailing_stop:
            exit_price  = float(bar['avg_bid'])
            exit_reason = 'stop_loss'
            exit_bar    = bar
            break

        # # 3. Profit target
        # if bar_avg >= target:
        #     exit_price  = float(bar['avg_bid'])
        #     exit_reason = 'profit_target'
        #     exit_bar    = bar
        #     break

        # # 4. Sell signals from this model's 4 indicators
        # for sc in sell_cols:
        #     if int(bar.get(sc, 0)):
        #         exit_price  = float(bar['avg_bid'])
        #         exit_reason = f'sell_{sc[5:]}'
        #         exit_bar    = bar
        #         break
        # if exit_price is not None:
        #     break

    if exit_price is None:
        last        = df_day.iloc[-1]
        exit_price  = float(last['avg_bid'])
        exit_reason = 'eod_forced'
        exit_bar    = last

    exit_time = str(exit_bar['date']) if exit_bar is not None else ''

    cost       = shares * entry_price
    proceeds   = shares * exit_price
    pnl_dollar = proceeds - cost - COMMISSION
    pnl_pct    = (pnl_dollar / cost * 100) if cost > 0 else 0.0

    result = {
        'exit_price':  round(exit_price, 4),
        'exit_time':   exit_time,
        'exit_reason': exit_reason,
        'bars_held':   bars_held,
        'shares':      shares,
        'cost':        round(cost, 2),
        'proceeds':    round(proceeds, 2),
        'pnl_dollar':  round(pnl_dollar, 2),
        'pnl_pct':     round(pnl_pct, 4),
        'is_winner':   pnl_dollar > 0,
    }
    if capture_evals:
        result['_evals'] = evals
    return result


# ══════════════════════════════════════════════════════════════════════════════
# END-OF-WEEK CONTEXT BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def _build_eow_context(
    df: pd.DataFrame,
    entry_global_idx: int,
) -> tuple[pd.DataFrame | None, int | None]:
    """
    Build a combined DataFrame spanning from the entry bar's trading day through
    the Friday of that same week (or the same day if already Friday).

    Returns (df_slice, entry_iloc) where df_slice is reset-indexed and
    entry_iloc is the position of the entry bar within it.
    Returns (None, None) if the entry bar cannot be located in the slice.
    """
    trade_date = df.loc[entry_global_idx, 'fnd_trade_date']
    entry_dt   = pd.Timestamp(str(trade_date))
    dow        = entry_dt.dayofweek          # Mon=0 … Fri=4
    days_ahead = (4 - dow) % 7              # 0 if already Friday
    friday_dt  = entry_dt + pd.Timedelta(days=days_ahead)

    fnd_ts = pd.to_datetime(df['fnd_trade_date'])
    mask   = (fnd_ts >= entry_dt) & (fnd_ts <= friday_dt)
    df_slice = df[mask].reset_index(drop=True)

    if df_slice.empty:
        return None, None

    # Locate entry bar by its unique 1-minute timestamp
    entry_ts = df.loc[entry_global_idx, 'date']
    hit      = df_slice.index[df_slice['date'] == entry_ts]
    if len(hit) == 0:
        return None, None

    return df_slice, int(hit[0])


# ══════════════════════════════════════════════════════════════════════════════
# LOG WRITERS
# ══════════════════════════════════════════════════════════════════════════════

_LOG_WIDTH    = 72
_LOG_EVAL_HEAD = 5
_LOG_EVAL_TAIL = 5


def _ruler(label: str) -> str:
    return label + '-' * max(0, _LOG_WIDTH - len(label))


def _write_evals(fh, header: str, rows: list, fmt):
    """Write an eval table limited to first/last N rows."""
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


def _log_model_start(fh, seq_no, model_id, t, m, v, vol):
    header = f"\n\n=====  START:  seq_no: [{seq_no}] model_id: [{model_id}]  "
    fh.write(f"\n{_ruler(header)}\n")
    fh.write(f"trend: [{t}] momentum: [{m}] volatility: [{v}] volume: [{vol}]\n")
    fh.write("\n----- Details\n\n")


def _log_trade(fh, tr):
    """Write one trade block in labeled field: [value] format."""
    fh.write(
        f"trade_no: [{tr['trade_no']}], "
        f"entry_time: [{tr['entry_time']}] "
        f"entry_price: [{tr['entry_price']}]\n"
    )
    fh.write(
        f"stop_loss: [{tr['stop_loss']}] "
        f"profit_target: [{tr['profit_target']}]\n"
    )
    fh.write(
        f"atr_at_entry: [{tr['atr_at_entry']}] "
        f"rsi_at_entry: [{tr['rsi_at_entry']}] "
        f"adx_at_entry: [{tr['adx_at_entry']}] "
        f"vwap_at_entry: [{tr['vwap_at_entry']}]\n"
    )
    fh.write("\n")

    def _fmt(r):
        t = str(r[0]).split(' ')[-1] if ' ' in str(r[0]) else str(r[0])
        return f"    {t}    {r[1]}    {r[2]}\n"
    _write_evals(fh, "eval_time    eval_price    stop_loss", tr.get('_evals', []), _fmt)
    fh.write("\n")

    fh.write(
        f"exit_time: [{tr['exit_time']}] "
        f"exit_price: [{tr['exit_price']}] "
        f"exit_reason: [{tr['exit_reason']}] "
        f"bars_held: [{tr['bars_held']}]\n"
    )
    fh.write(
        f"shares: [{tr['shares']}] "
        f"cost: [{tr['cost']}] "
        f"proceeds: [{tr['proceeds']}] "
        f"pnl_dollar: [{tr['pnl_dollar']}] "
        f"pnl_pct: [{tr['pnl_pct']}] "
        f"is_winner: [{'TRUE' if tr['is_winner'] else 'FALSE'}]\n\n"
    )


def _log_model_end(fh, seq_no, model_id, metrics):
    """Write the summary block and END marker for one model/run."""
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
    """
    Filter df_signals to the window, draw sample, build day structures.

    Returns:
      ((df, sample_idx, day_dict, day_pos_map), 'ok')   on success
      (None, 'no_data')                                  if window is empty
      (None, 'no_sample')                                if no valid bars to sample
    """
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
# CORE SIMULATION — one indicator combo on pre-built window data
# ══════════════════════════════════════════════════════════════════════════════

def run_combo(
    df:               pd.DataFrame,
    sample_idx:       list,
    day_dict:         dict,
    day_pos_map:      dict,
    indicators:       dict[str, str],
    capture_evals:    bool = False,
    end_of_week_exit: bool = False,
) -> tuple[dict, list[dict]]:
    """
    Fire the composite buy signal for the given indicators on the pre-built
    sample, simulate each trade, and return (metrics, trades).

    metrics keys:
      n_trades, win_rate, avg_entry, avg_exit, avg_duration_bars,
      total_pnl, avg_pnl, profit_factor, sharpe, max_drawdown,
      pnl_positive, status

    Each trade dict contains: trade_no, indicator names, trade_date, entry_time,
    entry_price, stop_loss, profit_target, snapshot fields, exit fields, P&L
    fields, and _evals (if capture_evals=True).  Caller prepends context fields
    (run_no / batch_no / model_no) before writing to CSV.
    """
    active    = {cat: ind for cat, ind in indicators.items() if ind}
    buy_cols  = [f'bsig_{ind}' for ind in active.values()]
    sell_cols = [f'ssig_{ind}' for ind in active.values()]

    sample_df = df.loc[sample_idx].copy()
    composite = pd.Series(True, index=sample_df.index)
    for bc in buy_cols:
        if bc in sample_df.columns:
            composite &= sample_df[bc].astype(bool)

    fired_idx = sample_df.index[composite].tolist()
    trades    = []
    trade_no  = 0

    for idx in fired_idx:
        row         = df.loc[idx]
        entry_price = float(row['avg_ask'])
        entry_avg   = float(row['average'])
        atr_val     = row['atr_14']
        atr         = float(atr_val) if pd.notna(atr_val) else 0.0
        bracket     = compute_bracket(entry_avg, atr)
        if bracket is None:
            continue
        stop, target = bracket
        shares = compute_shares(entry_price)
        if shares == 0:
            continue

        trade_date = row['fnd_trade_date']

        if end_of_week_exit:
            df_sim, entry_iloc_sim = _build_eow_context(df, idx)
            if df_sim is None or entry_iloc_sim is None:
                continue
        else:
            entry_iloc_sim = day_pos_map.get(idx)
            df_sim         = day_dict.get(trade_date)
            if df_sim is None or entry_iloc_sim is None:
                continue

        result = simulate_trade(
            df_sim, entry_iloc_sim, stop, target,
            sell_cols, entry_price, entry_avg, shares,
            capture_evals=capture_evals,
        )
        # Distinguish forced EOW exit from same-day EOD exit in the output
        if end_of_week_exit and result.get('exit_reason') == 'eod_forced':
            result['exit_reason'] = 'eow_forced'
        evals    = result.pop('_evals', [])
        trade_no += 1

        rsi_val  = row.get('rsi_14',   np.nan)
        adx_val  = row.get('adx_14',   np.nan)
        vwap_val = row.get('vwp_vwap', np.nan)

        trades.append({
            'trade_no':      trade_no,
            **{cat: indicators.get(cat, '') for cat in ['trend', 'momentum', 'volatility', 'volume']},
            'trade_date':    str(trade_date),
            'entry_time':    str(row['date']),
            'entry_price':   round(entry_price, 4),
            'stop_loss':     stop,
            'profit_target': target,
            'atr_at_entry':  round(atr, 4),
            'rsi_at_entry':  round(float(rsi_val)  if pd.notna(rsi_val)  else np.nan, 4),
            'adx_at_entry':  round(float(adx_val)  if pd.notna(adx_val)  else np.nan, 4),
            'vwap_at_entry': round(float(vwap_val) if pd.notna(vwap_val) else np.nan, 4),
            **result,
            '_evals':        evals,
        })

    if not trades:
        return _empty_metrics('no_signals'), []

    tdf   = pd.DataFrame(trades)
    wins  = tdf[tdf['is_winner']]
    loses = tdf[~tdf['is_winner']]

    gross_w = wins['pnl_dollar'].sum()  if len(wins)  > 0 else 0.0
    gross_l = loses['pnl_dollar'].sum() if len(loses) > 0 else 0.0
    pf      = abs(gross_w / gross_l) if gross_l < 0 else float('inf')

    cum      = tdf['pnl_dollar'].cumsum()
    drawdown = (cum - cum.cummax()).min()

    tdf2      = tdf.copy()
    tdf2['_dt'] = pd.to_datetime(tdf2['entry_time']).dt.date
    daily_pnl = tdf2.groupby('_dt')['pnl_dollar'].sum()
    sharpe    = (
        daily_pnl.mean() / daily_pnl.std() * np.sqrt(252)
        if daily_pnl.std() > 0 else 0.0
    )

    metrics = {
        'n_trades':          len(trades),
        'win_rate':          round(tdf['is_winner'].mean() * 100, 1),
        'avg_entry':         round(tdf['entry_price'].mean(), 2),
        'avg_exit':          round(tdf['exit_price'].mean(), 2),
        'avg_duration_bars': round(tdf['bars_held'].mean(), 1),
        'total_pnl':         round(tdf['pnl_dollar'].sum(), 2),
        'avg_pnl':           round(tdf['pnl_dollar'].mean(), 2),
        'profit_factor':     round(min(pf, 1e9), 3),
        'sharpe':            round(sharpe, 3),
        'max_drawdown':      round(drawdown, 2),
        'pnl_positive':      tdf['pnl_dollar'].sum() > 0,
        'status':            'ok',
    }
    return metrics, trades


def _empty_metrics(status: str) -> dict:
    return {
        'n_trades': 0, 'win_rate': 0.0,
        'avg_entry': 0.0, 'avg_exit': 0.0, 'avg_duration_bars': 0.0,
        'total_pnl': 0.0, 'avg_pnl': 0.0,
        'profit_factor': 0.0, 'sharpe': 0.0, 'max_drawdown': 0.0,
        'pnl_positive': False, 'status': status,
    }


# ══════════════════════════════════════════════════════════════════════════════
# SINGLE-RUN WRAPPER  (prepare window + run combo + attach context)
# ══════════════════════════════════════════════════════════════════════════════

def run_single(
    df_signals:       pd.DataFrame,
    indicators:       dict[str, str],
    window_start:     str,
    window_end:       str,
    seed:             int,
    run_no:           int,
    capture_evals:    bool = False,
    end_of_week_exit: bool = False,
) -> tuple[dict, list[dict]]:
    """
    Convenience wrapper used by the standalone __main__.
    Calls prepare_window() then run_combo(), then prepends run context to every
    trade dict and assembles the full summary row.
    """
    window_data, status = prepare_window(df_signals, window_start, window_end, seed)
    if window_data is None:
        return _empty_summary(run_no, indicators, window_start, window_end, seed, status), []

    df, sample_idx, day_dict, day_pos_map = window_data
    metrics, trades = run_combo(
        df, sample_idx, day_dict, day_pos_map, indicators,
        capture_evals=capture_evals,
        end_of_week_exit=end_of_week_exit,
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
        **{cat: indicators[cat] for cat in ['trend', 'momentum', 'volatility', 'volume']},
        **metrics,
    }
    return summary, full_trades


def _empty_summary(run_no, indicators, window_start, window_end, seed, status) -> dict:
    return {
        'run_no':       run_no,
        'window_start': window_start,
        'window_end':   window_end,
        'seed':         seed,
        **{cat: indicators[cat] for cat in ['trend', 'momentum', 'volatility', 'volume']},
        **_empty_metrics(status),
    }


# ══════════════════════════════════════════════════════════════════════════════
# STANDALONE CLI
# ══════════════════════════════════════════════════════════════════════════════

def parse_args() -> tuple[dict[str, str], bool, int, datetime.date, datetime.date, int]:
    parser = argparse.ArgumentParser(
        description='Run a single indicator combination across 500 time windows.'
    )
    parser.add_argument('--trend',      default='adx', help='Trend indicator      (blank = skip)')
    parser.add_argument('--momentum',   default='frc', help='Momentum indicator   (blank = skip)')
    parser.add_argument('--volatility', default='atr', help='Volatility indicator (blank = skip)')
    parser.add_argument('--volume',     default='vrc', help='Volume indicator     (blank = skip)')
    parser.add_argument(
        '--end-of-week-exit', action='store_true', default=False,
        help='Hold positions through Friday 4 PM instead of closing at end of day',
    )
    parser.add_argument(
        '--seed', type=int, default=None,
        help='Meta RNG seed (omit for a fresh random seed each run)',
    )
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
        args.end_of_week_exit,
        seed,
        datetime.date.fromisoformat(args.data_first),
        datetime.date.fromisoformat(args.data_last),
        args.window_days,
    )


def combo_label(indicators: dict[str, str]) -> str:
    """Short label from non-blank indicators, e.g. 'adx_frc_atr_vrc'."""
    return '_'.join(v for v in indicators.values() if v) or 'none'


def generate_test_runs(n: int = N_RUNS, meta_seed: int = META_SEED) -> list[tuple[str, str, int]]:
    """
    Returns n (window_start, window_end, seed) tuples, sorted chronologically.
    Windows and seeds are both derived from meta_seed for reproducibility.
    """
    max_start  = DATA_LAST - datetime.timedelta(days=WINDOW_DAYS)
    total_days = (max_start - DATA_FIRST).days

    rng     = np.random.default_rng(meta_seed)
    offsets = rng.choice(total_days, size=n, replace=False)
    seeds   = rng.integers(1, 10_000, size=n)

    runs = sorted(
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
    return runs


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
    indicators, end_of_week_exit, meta_seed, DATA_FIRST, DATA_LAST, WINDOW_DAYS = parse_args()
    label      = combo_label(indicators)
    active     = {k: v for k, v in indicators.items() if v}

    if not active:
        print("ERROR: all categories are blank — nothing to test.")
        sys.exit(1)

    t   = indicators['trend']
    m   = indicators['momentum']
    v   = indicators['volatility']
    vol = indicators['volume']

    run_ts  = datetime.datetime.now().strftime('%m%d%H%M')
    run_dir = REPORTS_DIR / f'single_{label}_{run_ts}'
    run_dir.mkdir(parents=True, exist_ok=True)

    exit_mode = 'end-of-week' if end_of_week_exit else 'end-of-day'
    print(f"\n{'='*60}")
    print(f"  Single Model Batch Test")
    print(f"{'='*60}")
    print(f"  Indicators : {indicators}")
    print(f"  Active cats: {list(active.keys())}")
    print(f"  Exit mode  : {exit_mode}")
    print(f"  Meta seed  : {meta_seed}")
    print(f"  Runs       : {N_RUNS}  (window={WINDOW_DAYS} cal days each)")
    print(f"  Data range : {DATA_FIRST} -> {DATA_LAST}")
    print(f"  Output dir : {run_dir}")
    print(f"{'='*60}\n")

    df_signals = load_or_build_signals()
    test_runs  = generate_test_runs(N_RUNS, meta_seed=meta_seed)

    all_summaries = []
    all_trades    = []

    log_path = run_dir / 'run.log'
    with open(log_path, 'w', encoding='utf-8') as log_fh:
        log_fh.write(
            f"combo: [{label}]  runs: {N_RUNS}  window_days: {WINDOW_DAYS}"
            f"  exit_mode: [{exit_mode}]"
            f"  generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        )

        for run_no, (window_start, window_end, seed) in enumerate(test_runs, 1):
            print(
                f"  [Run {run_no:>3}/{N_RUNS}]  "
                f"{window_start} -> {window_end}  seed={seed:<5}",
                end='  ',
            )

            summary, trades = run_single(
                df_signals, indicators, window_start, window_end, seed, run_no,
                capture_evals=True,
                end_of_week_exit=end_of_week_exit,
            )

            print(f"trades={summary['n_trades']:<4}  pnl=${summary['total_pnl']:>8,.2f}  [{summary['status']}]")

            all_summaries.append(summary)

            if trades:
                _log_model_start(log_fh, run_no, '-', t, m, v, vol)
                for tr in trades:
                    _log_trade(log_fh, tr)
                _log_model_end(log_fh, run_no, '-', summary)

            for tr in trades:
                tr.pop('_evals', None)
            all_trades.extend(trades)

    print(f"\n[output]  Log    -> {log_path}")

    runs_df   = pd.DataFrame(all_summaries)
    runs_path = run_dir / f'single_{label}_runs.csv'
    runs_df.to_csv(runs_path, index=False)
    print(f"[output]  Runs   -> {runs_path}  ({len(runs_df)} rows)")

    if all_trades:
        trades_df   = pd.DataFrame(all_trades)
        trades_path = run_dir / f'single_{label}_trades.csv'
        trades_df.to_csv(trades_path, index=False)
        print(f"[output]  Trades -> {trades_path}  ({len(trades_df):,} rows)")
    else:
        print("[output]  No trades to write.")

    print(f"\n{'='*60}")
    print("  AGGREGATE RESULTS")
    print(f"{'='*60}")
    print_aggregate(runs_df, label)
    print(f"\n{'='*60}\n")


if __name__ == '__main__':
    main()
