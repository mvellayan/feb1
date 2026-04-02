"""
batch_run_all_models.py

Orchestrates all 1,221 indicator combinations across 100 time windows.

Simulation logic lives entirely in batch_run_single_model.py.
This file handles:
  - combination generation  (TREND × MOMENTUM × VOLATILITY × VOLUME)
  - window scheduling       (100 unique windows per execution)
  - CSV and log output
  - post-execution analysis via summarize_all_runs

Pipeline per window
───────────────────
1.  S.load_or_build_signals()       — load or build signals CSV (cached in memory)
2.  S.prepare_window(...)           — filter window, draw sample, build day structures
3.  run_all_models(...)             — loop 1,221 combos, call S.run_combo() for each
4.  write_summary_csv / trades_csv  — persist results
5.  summarize_all_runs.main()       — rank indicators after all 100 windows complete

Output  ../reports/{mmddhhmi}/
──────
  {seq_no}_summary.csv
  {seq_no}_trades.csv
  {seq_no}_run.log
"""

from __future__ import annotations

import argparse
import datetime
import sys
import warnings
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import batch_run_single_model as S
import summarize_all_runs
from batch_run_utils import REPORTS_DIR

warnings.filterwarnings('ignore')

# ── configuration ──────────────────────────────────────────────────────────────
DATE_START       = '2023-01-01'
DATE_END         = '2024-12-31'
RANDOM_SEED      = 313
END_OF_WEEK_EXIT = False   # override via --end-of-week-exit flag

# ── indicator categories ────────────────────────────────────────────────────────
TREND      = ['ema', 'macd', 'adx', 'sar', 'don', 'arn', 'vtx']
MOMENTUM   = ['rsi', 'sto', 'cci', 'cmo', 'tsi', 'roc', 'frc', 'srsi', 'rmi', 'macd']
VOLATILITY = ['atr', 'bbd', 'chp']
VOLUME     = ['vwap', 'obv', 'mfi', 'klg', 'frc', 'vrc']


# ══════════════════════════════════════════════════════════════════════════════
# COMBINATION GENERATOR
# ══════════════════════════════════════════════════════════════════════════════

def generate_combos() -> list:
    """
    Returns all valid (trend, momentum, volatility, volume) 4-tuples.
    Skips any combination where the same indicator appears in multiple slots.
    Total = 1,260 - 39 duplicates = 1,221 valid combinations.
    """
    combos = []
    for t, m, v, vol in product(TREND, MOMENTUM, VOLATILITY, VOLUME):
        if len({t, m, v, vol}) == 4:
            combos.append((t, m, v, vol))
    return combos


# ══════════════════════════════════════════════════════════════════════════════
# OUTPUT WRITERS
# ══════════════════════════════════════════════════════════════════════════════

def write_summary_csv(summary_df: pd.DataFrame, seq_no: int, run_dir: Path) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / f'{seq_no}_summary.csv'

    out = summary_df[summary_df['n_trades'] > 0].rename(columns={
        'n_trades':          'number_of_trades',
        'avg_entry':         'avg_entry_price',
        'avg_exit':          'avg_exit_price',
        'avg_duration_bars': 'avg_bars_held',
    })
    out.to_csv(path, index=False)
    print(f"[{seq_no}][report]  Summary  -> {path}  ({len(out):,} rows)")
    return path


def write_trades_csv(trades_df: pd.DataFrame, seq_no: int, run_dir: Path) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / f'{seq_no}_trades.csv'

    col_order = [
        'batch_no', 'model_no', 'trade_no',
        'trend', 'momentum', 'volatility', 'volume',
        'trade_date', 'entry_time', 'exit_time', 'entry_price',
        'stop_loss', 'profit_target',
        'atr_at_entry', 'rsi_at_entry', 'adx_at_entry', 'vwap_at_entry',
        'exit_price', 'exit_reason', 'bars_held',
        'shares', 'cost', 'proceeds',
        'pnl_dollar', 'pnl_pct', 'is_winner',
    ]
    cols = [c for c in col_order if c in trades_df.columns]
    trades_df[cols].to_csv(path, index=False)
    print(f"[{seq_no}][report]  Trades   -> {path}  ({len(trades_df):,} records)")
    return path


# ══════════════════════════════════════════════════════════════════════════════
# MAIN BACKTEST LOOP
# ══════════════════════════════════════════════════════════════════════════════

def run_all_models(
    df:               pd.DataFrame,
    sample_idx:       list,
    day_dict:         dict,
    day_pos_map:      dict,
    seq_no:           int,
    log_fh=None,
    end_of_week_exit: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Iterates over all 1,221 valid combinations, calling S.run_combo() for each.
    Returns (summary_df, trades_df).
    """
    combos   = generate_combos()
    n_combos = len(combos)

    summary_rows = []
    all_trades   = []

    print(f"\n[{seq_no}][backtest] Running {n_combos:,} model combinations ...")
    print(f"[{seq_no}]           Sample size : {len(sample_idx):,} bars")

    for model_id, (t, m, v, vol) in enumerate(combos, 1):
        indicators = {'trend': t, 'momentum': m, 'volatility': v, 'volume': vol}

        metrics, raw_trades = S.run_combo(
            df, sample_idx, day_dict, day_pos_map, indicators,
            capture_evals=(log_fh is not None),
            end_of_week_exit=end_of_week_exit,
        )

        summary_rows.append({
            'batch_no': seq_no, 'model_id': model_id,
            'trend': t, 'momentum': m, 'volatility': v, 'volume': vol,
            **metrics,
        })

        # Prepend batch/model context to each trade
        model_trades = [{'batch_no': seq_no, 'model_no': model_id, **tr} for tr in raw_trades]

        # Write log block (only models with trades)
        if model_trades and log_fh is not None:
            S._log_model_start(log_fh, seq_no, model_id, t, m, v, vol)
            for tr in model_trades:
                S._log_trade(log_fh, tr)
            S._log_model_end(log_fh, seq_no, model_id, metrics)

        # Strip _evals before accumulating for CSV
        for tr in model_trades:
            tr.pop('_evals', None)
        all_trades.extend(model_trades)

        if model_id % 100 == 0 or model_id == n_combos:
            print(f"  [{seq_no}|M{model_id:>5}/{n_combos}]  trades so far: {len(all_trades):,}")

    print(f"\n[{seq_no}][backtest] Complete.  Total trades: {len(all_trades):,}")

    trades_df = pd.DataFrame(all_trades) if all_trades else pd.DataFrame()
    return pd.DataFrame(summary_rows), trades_df


# ══════════════════════════════════════════════════════════════════════════════
# SINGLE WINDOW EXECUTION
# ══════════════════════════════════════════════════════════════════════════════

def run_model_set(seq_no: int, run_dir: Path, end_of_week_exit: bool = False):
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    exit_mode = 'end-of-week' if end_of_week_exit else 'end-of-day'
    print(f"\n{'='*60}")
    print(f"[{seq_no}]  AAPL Intraday Backtest  |  start at {ts}")
    print(f"[{seq_no}]  Window: {DATE_START} -> {DATE_END}  seed: {RANDOM_SEED}  exit: {exit_mode}")
    print(f"{'='*60}\n")

    # 1. Load signals (cached after first call)
    df_full = S.load_or_build_signals()

    # 2. Filter window, draw sample, build day structures
    window_data, wstatus = S.prepare_window(df_full, DATE_START, DATE_END, RANDOM_SEED)
    if window_data is None:
        sys.exit(f"[{seq_no}] ERROR: {wstatus} for {DATE_START} -> {DATE_END}")
    df, sample_idx, day_dict, day_pos_map = window_data
    print(f"[{seq_no}][filter]  {DATE_START} -> {DATE_END}: {len(df):,} rows")
    print(f"[{seq_no}]          Trading days indexed: {len(day_dict):,}")

    # 3. Run all 1,221 models (streaming log written during execution)
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / f'{seq_no}_run.log'
    with open(log_path, 'w', encoding='utf-8') as log_fh:
        log_fh.write(
            f"seq_no: [{seq_no}]  window: {DATE_START} -> {DATE_END}"
            f"  seed: {RANDOM_SEED}  exit_mode: [{exit_mode}]  generated: {ts}\n"
        )
        summary_df, trades_df = run_all_models(
            df, sample_idx, day_dict, day_pos_map, seq_no,
            log_fh=log_fh,
            end_of_week_exit=end_of_week_exit,
        )
    print(f"[{seq_no}][report]  Log      -> {log_path}")

    # 4. Write CSV reports
    write_summary_csv(summary_df, seq_no, run_dir)
    if not trades_df.empty:
        write_trades_csv(trades_df, seq_no, run_dir)

    # 5. Console summary
    sdf_active = summary_df[summary_df['n_trades'] > 0]
    print(f"\n{'='*60}")
    print(f"[{seq_no}]  RUN COMPLETE")
    print(f"{'='*60}")
    print(f"[{seq_no}]  Models run       : {len(summary_df):,}")
    print(f"[{seq_no}]  Models w/ trades : {len(sdf_active):,}")
    print(f"[{seq_no}]  Total trades     : {len(trades_df):,}")

    if not sdf_active.empty:
        best  = sdf_active.loc[sdf_active['total_pnl'].idxmax()]
        worst = sdf_active.loc[sdf_active['total_pnl'].idxmin()]
        print(
            f"[{seq_no}]  Best  model : [{seq_no}|M{int(best['model_id'])}] "
            f"{best['trend'].upper()}+{best['momentum'].upper()}+"
            f"{best['volatility'].upper()}+{best['volume'].upper()} "
            f"  P&L=${best['total_pnl']:,.2f}  Sharpe={best['sharpe']:.2f}"
        )
        print(
            f"[{seq_no}]  Worst model : [{seq_no}|M{int(worst['model_id'])}] "
            f"{worst['trend'].upper()}+{worst['momentum'].upper()}+"
            f"{worst['volatility'].upper()}+{worst['volume'].upper()} "
            f"  P&L=${worst['total_pnl']:,.2f}  Sharpe={worst['sharpe']:.2f}"
        )

    print(f"{'='*60}\n")


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    _parser = argparse.ArgumentParser(description='Run all 1,221 models across 100 windows.')
    _parser.add_argument(
        '--end-of-week-exit', action='store_true', default=False,
        help='Hold positions through Friday 4 PM instead of closing at end of day',
    )
    _args = _parser.parse_args()
    END_OF_WEEK_EXIT = _args.end_of_week_exit

    DATA_FIRST  = datetime.date(2023, 1, 1)
    DATA_LAST   = datetime.date(2026, 2, 28)
    WINDOW_DAYS = 14

    # One output directory per execution
    run_ts  = datetime.datetime.now().strftime('%m%d%H%M')
    run_dir = REPORTS_DIR / run_ts
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"[main]  Output directory: {run_dir}")

    # Master RNG generates both window offsets and per-run seeds together,
    # ensuring each window gets a unique (window, seed) pair.
    max_start  = DATA_LAST - datetime.timedelta(days=WINDOW_DAYS)
    total_days = (max_start - DATA_FIRST).days

    master_rng = np.random.default_rng(RANDOM_SEED)
    offsets    = master_rng.choice(total_days, size=100, replace=False).tolist()
    run_seeds  = master_rng.integers(1, 10_000, size=100).tolist()

    runs = sorted(zip(offsets, run_seeds), key=lambda x: x[0])

    for seq_no, (offset, seed) in enumerate(runs, 1):
        DATE_START  = (DATA_FIRST + datetime.timedelta(days=int(offset))).strftime('%Y-%m-%d')
        DATE_END    = (DATA_FIRST + datetime.timedelta(days=int(offset) + WINDOW_DAYS)).strftime('%Y-%m-%d')
        RANDOM_SEED = int(seed)
        print(f"[{seq_no}]  Window: {DATE_START} -> {DATE_END}  (seed={RANDOM_SEED})")
        run_model_set(seq_no, run_dir, end_of_week_exit=END_OF_WEEK_EXIT)

    # Summarise all runs in this execution
    print(f"\n{'='*60}")
    print(f"  Running summarize_all_runs for {run_dir.name}")
    print(f"{'='*60}\n")
    summarize_all_runs.main(run_dir=run_dir)
