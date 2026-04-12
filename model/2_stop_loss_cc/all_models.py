"""
all_models.py — 2_stop_loss_cc

Orchestrates all 1,221 indicator combinations across 100 time windows
using the covered-call stop-loss exit strategy.

Simulation logic lives in single_model.py (this directory).
This file handles:
  - combination generation  (TREND × MOMENTUM × VOLATILITY × VOLUME)
  - window scheduling       (100 unique windows per execution)
  - CSV and log output
  - post-execution analysis and markdown report

Pipeline per window
───────────────────
1.  S.load_or_build_signals()       — load or build signals CSV (cached in memory)
2.  S.prepare_window(...)           — filter window, draw sample, build day structures
3.  run_all_models(...)             — loop 1,221 combos, call S.run_combo() for each
4.  write_summary_csv / trades_csv  — persist results
5.  summarize_run(run_dir)          — rank indicators after all 100 windows complete

Output  ../reports/{mmddhhmi}/
──────
  {seq_no}_summary.csv
  {seq_no}_trades.csv        (includes 'leg' column: stock / option)
  {seq_no}_run.log
  analysis_models.csv
  analysis_{trend,momentum,volatility,volume}.csv
  analysis_pair_{cat_a}_{cat_b}.csv  (6 pairs)
  batch_run_analysis.md
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import os
import secrets
import sys
import warnings
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

_HERE   = Path(__file__).parent
_MODEL1 = _HERE.parent / '1a_tech_indicators_sock_trade'
sys.path.insert(0, str(_MODEL1))  # utils/signals from model 1
sys.path.insert(0, str(_HERE))    # single_model from model 2 (takes precedence)

import single_model as S
from utils import (
    TOP_N, CATEGORIES, PF_CAP, MIN_BATCHES,
    load_all_summaries, analyse_full_models, analyse_category, analyse_pair,
    md_table,
)

REPORTS_DIR = Path(__file__).parent / 'reports'

warnings.filterwarnings('ignore')

# ── configuration ──────────────────────────────────────────────────────────────
DATE_START  = '2023-01-01'
DATE_END    = '2024-12-31'
RANDOM_SEED = 313

# ── indicator categories ────────────────────────────────────────────────────────
TREND      = ['ema', 'macd', 'adx', 'sar', 'don', 'arn', 'vtx']
MOMENTUM   = ['rsi', 'sto', 'cci', 'cmo', 'tsi', 'roc', 'frc', 'srsi', 'rmi', 'macd']
VOLATILITY = ['atr', 'bbd', 'chp']
VOLUME     = ['vwap', 'obv', 'mfi', 'klg', 'frc', 'vrc']


# ══════════════════════════════════════════════════════════════════════════════
# COMBINATION GENERATOR
# ══════════════════════════════════════════════════════════════════════════════

def generate_combos() -> list:
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
    out  = summary_df[summary_df['n_trades'] > 0].rename(columns={
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
        'batch_no', 'model_no', 'trade_no', 'leg',
        'trend', 'momentum', 'volatility', 'volume',
        'trade_date', 'entry_time', 'exit_time', 'entry_price',
        'stop_loss', 'profit_target',
        'atr_at_entry', 'rsi_at_entry', 'adx_at_entry', 'vwap_at_entry',
        'exit_price', 'exit_reason', 'bars_held',
        'shares', 'cost', 'proceeds', 'pnl_dollar', 'pnl_pct', 'is_winner',
        'cc_option_symbol', 'cc_strike', 'cc_expiry', 'cc_open_time', 'cc_open_price',
    ]
    cols = [c for c in col_order if c in trades_df.columns]
    trades_df[cols].to_csv(path, index=False)
    print(f"[{seq_no}][report]  Trades   -> {path}  ({len(trades_df):,} records)")
    return path


# ══════════════════════════════════════════════════════════════════════════════
# MAIN BACKTEST LOOP
# ══════════════════════════════════════════════════════════════════════════════

def run_all_models(
    df:          pd.DataFrame,
    sample_idx:  list,
    day_dict:    dict,
    day_pos_map: dict,
    seq_no:      int,
    log_fh=None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    combos   = generate_combos()
    n_combos = len(combos)

    summary_rows = []
    all_trades   = []

    print(f"\n[{seq_no}][backtest] Running {n_combos:,} model combinations ...")
    print(f"[{seq_no}]           Sample size : {len(sample_idx):,} bars")

    for model_id, (t, m, v, vol) in enumerate(combos, 1):
        indicators = {'trend': t, 'momentum': m, 'volatility': v, 'volume': vol}

        try:
            metrics, raw_trades = S.run_combo(
                df, sample_idx, day_dict, day_pos_map, indicators,
                capture_evals=(log_fh is not None),
            )
        except Exception as exc:
            err_msg = f"[{seq_no}|M{model_id}] ERROR in run_combo({t},{m},{v},{vol}): {exc}"
            print(err_msg)
            if log_fh is not None:
                log_fh.write(f"\n{err_msg}\n")
            continue

        summary_rows.append({
            'batch_no': seq_no, 'model_id': model_id,
            'trend': t, 'momentum': m, 'volatility': v, 'volume': vol,
            **metrics,
        })

        model_trades = [{'batch_no': seq_no, 'model_no': model_id, **tr} for tr in raw_trades]

        # Log stock-leg rows (which carry all CC details); option leg is supplementary
        stock_trades = [tr for tr in model_trades if tr.get('leg') == 'stock']
        if stock_trades and log_fh is not None:
            S._log_model_start(log_fh, seq_no, model_id, t, m, v, vol)
            for tr in stock_trades:
                S._log_trade(log_fh, tr)
            S._log_model_end(log_fh, seq_no, model_id, metrics)

        for tr in model_trades:
            tr.pop('_evals',         None)
            tr.pop('_cc_evals',      None)
            tr.pop('_cc_candidates', None)
            tr.pop('_opt_pnl',       None)
        all_trades.extend(model_trades)

        if model_id % 100 == 0 or model_id == n_combos:
            print(f"  [{seq_no}|M{model_id:>5}/{n_combos}]  trades so far: {len(all_trades):,}")

    print(f"\n[{seq_no}][backtest] Complete.  Total trades: {len(all_trades):,}")

    trades_df = pd.DataFrame(all_trades) if all_trades else pd.DataFrame()
    return pd.DataFrame(summary_rows), trades_df


# ══════════════════════════════════════════════════════════════════════════════
# SINGLE WINDOW EXECUTION
# ══════════════════════════════════════════════════════════════════════════════

def run_model_set(
    seq_no:     int,
    run_dir:    Path,
    date_start: str,
    date_end:   str,
    seed:       int,
):
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"\n{'='*60}")
    print(f"[{seq_no}]  AAPL Intraday Backtest (CC Exit)  |  start at {ts}")
    print(f"[{seq_no}]  Window: {date_start} -> {date_end}  seed: {seed}")
    print(f"{'='*60}\n")

    df_full = S.load_or_build_signals()   # cached after first load in this process

    window_data, wstatus = S.prepare_window(df_full, date_start, date_end, seed)
    if window_data is None:
        print(f"[{seq_no}] ERROR: {wstatus} for {date_start} -> {date_end}")
        return
    df, sample_idx, day_dict, day_pos_map = window_data
    print(f"[{seq_no}][filter]  {date_start} -> {date_end}: {len(df):,} rows")
    print(f"[{seq_no}]          Trading days indexed: {len(day_dict):,}")

    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / f'{seq_no}_run.log'
    with open(log_path, 'w', encoding='utf-8') as log_fh:
        log_fh.write(
            f"seq_no: [{seq_no}]  window: {date_start} -> {date_end}"
            f"  seed: {seed}  model: [cc_exit]  generated: {ts}\n"
        )
        summary_df, trades_df = run_all_models(
            df, sample_idx, day_dict, day_pos_map, seq_no, log_fh=log_fh,
        )
    print(f"[{seq_no}][report]  Log      -> {log_path}")

    write_summary_csv(summary_df, seq_no, run_dir)
    if not trades_df.empty:
        write_trades_csv(trades_df, seq_no, run_dir)

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


# ──────────────────────────────────────────────────────────────────────────────
# Top-level worker wrapper — must be at module level to be picklable by
# multiprocessing on macOS/Windows (spawn start method).
# ──────────────────────────────────────────────────────────────────────────────

def _run_window(args: tuple) -> int:
    """Unpack args and run one window. Returns seq_no on success."""
    seq_no, run_dir, date_start, date_end, seed = args
    run_model_set(seq_no, run_dir, date_start, date_end, seed)
    return seq_no


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSIS — MARKDOWN REPORT
# ══════════════════════════════════════════════════════════════════════════════

def write_markdown_report(models_df, category_dfs, pair_dfs, n_batches, total_rows, run_dir):
    now  = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    path = run_dir / 'batch_run_analysis.md'

    lines = ['# Batch Run Analysis — Covered Call Exit — Indicator Consistency Report']
    lines.append(f'\n_Generated: {now}_\n')
    lines.append('## Overview\n')
    lines.append('| Metric | Value |')
    lines.append('|---|---|')
    lines.append(f'| Run directory           | {run_dir.name} |')
    lines.append(f'| Runs analysed           | {n_batches} |')
    lines.append(f'| Total model-runs        | {total_rows:,} |')
    lines.append(f'| Unique models (4-tuple) | {len(models_df):,} |')
    lines.append(f'| Min runs threshold      | {MIN_BATCHES} |')
    lines.append(f'| Profit factor cap       | {PF_CAP} |')
    lines.append('')
    lines.append('### Consistency Score Formula\n')
    lines.append('```')
    lines.append('score = 0.40 × pnl_hit_rate')
    lines.append('      + 0.25 × sharpe_hit_rate')
    lines.append('      + 0.20 × avg_win_rate / 100')
    lines.append('      + 0.15 × avg_profit_factor / 10')
    lines.append('(all terms normalised to 0–1, score reported 0–100)')
    lines.append('```\n')

    display_model_cols = [
        'rank', 'trend', 'momentum', 'volatility', 'volume',
        'batch_count', 'avg_trades', 'avg_win_rate', 'avg_total_pnl',
        'pnl_hit_rate', 'avg_sharpe', 'avg_pf', 'consistency_score',
    ]

    lines.append(f'## Top {TOP_N} Full Model Combinations\n')
    lines.append(md_table(models_df[display_model_cols]))
    lines.append('')

    bottom = models_df[display_model_cols].tail(TOP_N).iloc[::-1].copy()
    bottom['rank'] = range(1, len(bottom) + 1)
    lines.append(f'## Bottom {TOP_N} Full Model Combinations\n')
    lines.append(md_table(bottom))
    lines.append('')

    for cat, cdf in category_dfs.items():
        display_cols = [
            'rank', cat, 'batch_count', 'avg_trades', 'avg_win_rate',
            'avg_total_pnl', 'pnl_hit_rate', 'avg_sharpe', 'consistency_score',
        ]
        lines.append(f'## {cat.capitalize()} Indicator Rankings\n')
        lines.append(md_table(cdf[display_cols], n=len(cdf)))
        lines.append('')

    for (cat_a, cat_b), pdf in pair_dfs.items():
        display_cols = [
            'rank', cat_a, cat_b, 'batch_count', 'avg_trades',
            'avg_win_rate', 'avg_total_pnl', 'pnl_hit_rate',
            'avg_sharpe', 'consistency_score',
        ]
        lines.append(f'## Pair Rankings: {cat_a.capitalize()} × {cat_b.capitalize()}\n')
        lines.append(md_table(pdf[display_cols]))
        lines.append('')

    path.write_text('\n'.join(lines), encoding='utf-8')
    print(f"[report]  Markdown  -> {path}")


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSIS — SUMMARIZE RUN
# ══════════════════════════════════════════════════════════════════════════════

def summarize_run(run_dir: Path):
    print(f"\n{'='*60}")
    print(f"  Batch Run Analysis — {run_dir.name}")
    print(f"{'='*60}\n")

    df, n_batches = load_all_summaries(run_dir=run_dir)

    print("[analyse] Full model combinations ...")
    models_df = analyse_full_models(df, n_batches)
    models_df.to_csv(run_dir / 'analysis_models.csv', index=False)
    print(f"          {len(models_df):,} models ranked  ->  analysis_models.csv")

    category_dfs = {}
    for cat in CATEGORIES:
        print(f"[analyse] {cat} indicators ...")
        cdf = analyse_category(df, n_batches, cat)
        cdf.to_csv(run_dir / f'analysis_{cat}.csv', index=False)
        print(f"          {len(cdf)} indicators  ->  analysis_{cat}.csv")
        category_dfs[cat] = cdf

    pair_dfs = {}
    pairs = [
        ('trend', 'momentum'), ('trend', 'volatility'), ('trend', 'volume'),
        ('momentum', 'volatility'), ('momentum', 'volume'), ('volatility', 'volume'),
    ]
    for cat_a, cat_b in pairs:
        print(f"[analyse] Pair {cat_a} × {cat_b} ...")
        pdf = analyse_pair(df, n_batches, cat_a, cat_b)
        out_name = f'analysis_pair_{cat_a}_{cat_b}.csv'
        pdf.to_csv(run_dir / out_name, index=False)
        print(f"          {len(pdf)} pairs ranked  ->  {out_name}")
        pair_dfs[(cat_a, cat_b)] = pdf

    print("\n[report]  Writing markdown ...")
    write_markdown_report(models_df, category_dfs, pair_dfs, n_batches, len(df), run_dir)

    print(f"\n{'='*60}")
    print("  TOP 5 CONSISTENT MODELS")
    print(f"{'='*60}")
    for _, r in models_df.head(5).iterrows():
        print(
            f"  #{int(r['rank']):>3}  "
            f"{r['trend'].upper():>4}+{r['momentum'].upper():<5}+"
            f"{r['volatility'].upper():>3}+{r['volume'].upper():<4}  "
            f"score={r['consistency_score']:.1f}  "
            f"pnl_hit={r['pnl_hit_rate']*100:.0f}%  "
            f"avg_pnl=${r['avg_total_pnl']:,.0f}  "
            f"avg_sharpe={r['avg_sharpe']:.2f}"
        )

    print(f"\n{'='*60}")
    print("  INDICATOR RANKINGS BY CATEGORY")
    print(f"{'='*60}")
    for cat, cdf in category_dfs.items():
        ranked = '  >  '.join(
            f"{r[cat].upper()}({r['consistency_score']:.0f})"
            for _, r in cdf.iterrows()
        )
        print(f"  {cat.upper():<12}: {ranked}")

    print(f"\n{'='*60}\n")


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    _parser = argparse.ArgumentParser(
        description='Run all 1,221 CC-exit models across 100 windows.'
    )
    _parser.add_argument(
        '--seed', type=int, default=None,
        help='Master RNG seed (omit for a fresh random seed each run)',
    )
    _parser.add_argument(
        '--workers', type=int, default=max(1, (os.cpu_count() or 1) - 1),
        help='Number of parallel worker processes (default: cpu_count - 1)',
    )
    _parser.add_argument(
        '--data-first', type=str, default='2023-01-01',
        help='Start of data range (YYYY-MM-DD, default: 2023-01-01)',
    )
    _parser.add_argument(
        '--data-last', type=str, default='2026-02-28',
        help='End of data range (YYYY-MM-DD, default: 2026-02-28)',
    )
    _parser.add_argument(
        '--window-days', type=int, default=14,
        help='Calendar days per test window (default: 14)',
    )
    _args = _parser.parse_args()
    RANDOM_SEED = _args.seed if _args.seed is not None else secrets.randbelow(2**32)
    N_WORKERS   = _args.workers
    DATA_FIRST  = datetime.date.fromisoformat(_args.data_first)
    DATA_LAST   = datetime.date.fromisoformat(_args.data_last)
    WINDOW_DAYS = _args.window_days
    print(f"[main]  Master seed : {RANDOM_SEED}")
    print(f"[main]  Workers     : {N_WORKERS}")

    run_ts  = datetime.datetime.now().strftime('%m%d%H%M')
    run_dir = REPORTS_DIR / run_ts
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"[main]  Output directory: {run_dir}")

    # ── Build the signals CSV in the main process before spawning workers.
    # This avoids multiple workers racing to write the same file.
    print("[main]  Loading/building signals CSV ...")
    S.load_or_build_signals()
    print("[main]  Signals ready.\n")

    # ── Generate all 100 window specs ─────────────────────────────────────────
    max_start  = DATA_LAST - datetime.timedelta(days=WINDOW_DAYS)
    total_days = (max_start - DATA_FIRST).days

    master_rng = np.random.default_rng(RANDOM_SEED)
    offsets    = master_rng.choice(total_days, size=100, replace=False).tolist()
    run_seeds  = master_rng.integers(1, 10_000, size=100).tolist()
    runs       = sorted(zip(offsets, run_seeds), key=lambda x: x[0])

    window_args = []
    for seq_no, (offset, seed) in enumerate(runs, 1):
        date_start = (DATA_FIRST + datetime.timedelta(days=int(offset))).strftime('%Y-%m-%d')
        date_end   = (DATA_FIRST + datetime.timedelta(days=int(offset) + WINDOW_DAYS)).strftime('%Y-%m-%d')
        window_args.append((seq_no, run_dir, date_start, date_end, int(seed)))
        print(f"  [{seq_no:>3}]  {date_start} -> {date_end}  seed={seed}")

    # ── Dispatch to worker pool ────────────────────────────────────────────────
    print(f"\n[main]  Submitting {len(window_args)} windows to {N_WORKERS} workers ...\n")
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

    # ── Post-run analysis (single-threaded, all CSVs now written) ─────────────
    summarize_run(run_dir)
