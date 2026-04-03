"""
all_models.py

Orchestrates all 1,221 indicator combinations across 100 time windows,
then analyses and ranks indicator consistency across those runs.

Simulation logic lives entirely in single_model.py.
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
  {seq_no}_trades.csv
  {seq_no}_run.log
  analysis_models.csv
  analysis_{trend,momentum,volatility,volume}.csv
  analysis_pair_{cat_a}_{cat_b}.csv  (6 pairs)
  batch_run_analysis.md
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
import single_model as S
from utils import (
    REPORTS_DIR, TOP_N, CATEGORIES, PF_CAP, MIN_BATCHES,
    load_all_summaries, analyse_full_models, analyse_category, analyse_pair,
    md_table,
)

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

        model_trades = [{'batch_no': seq_no, 'model_no': model_id, **tr} for tr in raw_trades]

        if model_trades and log_fh is not None:
            S._log_model_start(log_fh, seq_no, model_id, t, m, v, vol)
            for tr in model_trades:
                S._log_trade(log_fh, tr)
            S._log_model_end(log_fh, seq_no, model_id, metrics)

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

    df_full = S.load_or_build_signals()

    window_data, wstatus = S.prepare_window(df_full, DATE_START, DATE_END, RANDOM_SEED)
    if window_data is None:
        sys.exit(f"[{seq_no}] ERROR: {wstatus} for {DATE_START} -> {DATE_END}")
    df, sample_idx, day_dict, day_pos_map = window_data
    print(f"[{seq_no}][filter]  {DATE_START} -> {DATE_END}: {len(df):,} rows")
    print(f"[{seq_no}]          Trading days indexed: {len(day_dict):,}")

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


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSIS — MARKDOWN REPORT
# ══════════════════════════════════════════════════════════════════════════════

def write_markdown_report(models_df, category_dfs, pair_dfs, n_batches, total_rows, run_dir):
    now  = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    path = run_dir / 'batch_run_analysis.md'

    lines = []
    lines.append('# Batch Run Analysis — Indicator Consistency Report')
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
        ('trend',      'momentum'),
        ('trend',      'volatility'),
        ('trend',      'volume'),
        ('momentum',   'volatility'),
        ('momentum',   'volume'),
        ('volatility', 'volume'),
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

    run_ts  = datetime.datetime.now().strftime('%m%d%H%M')
    run_dir = REPORTS_DIR / run_ts
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"[main]  Output directory: {run_dir}")

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

    summarize_run(run_dir)
