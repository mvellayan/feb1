"""
all_models.py — 3_covered_calls

Orchestrates all 1,221 indicator combinations × 4 strike offsets = 4,884 scenarios
across 100 time windows.

The strategy opens a covered call immediately at entry (not at stop trigger).
Each combo is evaluated at four strike offsets:
  +3  OTM call  (capped upside, lower premium)
  +2  OTM call
  -2  ITM call  (higher premium, stock upside capped below entry)
  -3  ITM call

Purpose: determine which strike placement — ITM or OTM, and by how much — performs
best across all indicator models.

Simulation logic lives in single_model.py (this directory).
This file handles:
  - combination generation  (TREND × MOMENTUM × VOLATILITY × VOLUME × OFFSET)
  - window scheduling       (100 unique windows per execution)
  - CSV and log output
  - post-execution analysis and markdown report

Pipeline per window
───────────────────
1.  S.load_or_build_signals()       — load or build signals CSV (cached in memory)
2.  S.prepare_window(...)           — filter window, draw sample, build day structures
3.  run_all_models(...)             — loop 4,884 scenarios, call S.run_combo() for each
4.  write_summary_csv / trades_csv  — persist results
5.  summarize_run(run_dir)          — rank indicators and offsets after all 100 windows

Output  reports/{mmddhhmi}/
──────
  {seq_no}_summary.csv
  {seq_no}_trades.csv
  {seq_no}_run.log
  analysis_models.csv          — 1,221 combos ranked (across all offsets)
  analysis_offset.csv          — 4 offsets ranked by aggregate performance
  analysis_{cat}.csv           — per indicator category
  analysis_pair_{a}_{b}.csv    — 6 indicator pair cross-tabs
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
_MODEL1 = _HERE.parent / '1_tech_indicators_sock_trade'
sys.path.insert(0, str(_MODEL1))
sys.path.insert(0, str(_HERE))

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

# ── strike offsets ─────────────────────────────────────────────────────────────
STRIKE_OFFSETS = [3.0, 2.0, -2.0, -3.0]   # positive = OTM, negative = ITM
OFFSET_LABELS  = {3.0: '+3 OTM', 2.0: '+2 OTM', -2.0: '-2 ITM', -3.0: '-3 ITM'}

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
        'batch_no', 'model_id', 'strike_offset', 'trade_no', 'leg',
        'trend', 'momentum', 'volatility', 'volume',
        'trade_date', 'entry_time', 'exit_time', 'entry_price',
        'strike',
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
    """
    Run all 1,221 combos × 4 offsets = 4,884 scenarios on one window.
    Each (combo, offset) is independent.
    """
    combos   = generate_combos()
    n_combos = len(combos)
    n_total  = n_combos * len(STRIKE_OFFSETS)

    summary_rows = []
    all_trades   = []

    print(f"\n[{seq_no}][backtest] Running {n_combos:,} combos × "
          f"{len(STRIKE_OFFSETS)} offsets = {n_total:,} scenarios ...")
    print(f"[{seq_no}]           Sample size : {len(sample_idx):,} bars")

    for model_id, (t, m, v, vol) in enumerate(combos, 1):
        indicators = {'trend': t, 'momentum': m, 'volatility': v, 'volume': vol}

        for offset in STRIKE_OFFSETS:
            try:
                metrics, raw_trades = S.run_combo(
                    df, sample_idx, day_dict, day_pos_map, indicators, offset,
                    capture_evals=(log_fh is not None),
                )
            except Exception as exc:
                err_msg = (f"[{seq_no}|M{model_id}|{offset:+.0f}] "
                           f"ERROR in run_combo({t},{m},{v},{vol}): {exc}")
                print(err_msg)
                if log_fh is not None:
                    log_fh.write(f"\n{err_msg}\n")
                continue

            summary_rows.append({
                'batch_no':     seq_no,
                'model_id':     model_id,
                'strike_offset': offset,
                'trend': t, 'momentum': m, 'volatility': v, 'volume': vol,
                **metrics,
            })

            model_trades = [
                {'batch_no': seq_no, 'model_no': model_id, 'strike_offset': offset, **tr}
                for tr in raw_trades
            ]

            stock_trades = [tr for tr in model_trades if tr.get('leg') == 'stock']
            if stock_trades and log_fh is not None:
                S._log_model_start(log_fh, seq_no,
                                   f"{model_id}|{offset:+.0f}", t, m, v, vol)
                for tr in stock_trades:
                    S._log_trade(log_fh, tr)
                S._log_model_end(log_fh, seq_no,
                                 f"{model_id}|{offset:+.0f}", metrics)

            for tr in model_trades:
                tr.pop('_cc_evals', None)
                tr.pop('_opt_pnl',  None)
            all_trades.extend(model_trades)

        if model_id % 100 == 0 or model_id == n_combos:
            scenarios_done = model_id * len(STRIKE_OFFSETS)
            print(f"  [{seq_no}|M{model_id:>5}/{n_combos}]  "
                  f"scenarios: {scenarios_done:,}/{n_total:,}  "
                  f"trades so far: {len(all_trades):,}")

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
    print(f"[{seq_no}]  AAPL Intraday Backtest (CC Entry)  |  start at {ts}")
    print(f"[{seq_no}]  Window: {date_start} -> {date_end}  seed: {seed}")
    print(f"{'='*60}\n")

    df_full = S.load_or_build_signals()

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
            f"  seed: {seed}  model: [cc_entry]  generated: {ts}\n"
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
    print(f"[{seq_no}]  Scenarios run    : {len(summary_df):,}")
    print(f"[{seq_no}]  With trades      : {len(sdf_active):,}")
    print(f"[{seq_no}]  Total trades     : {len(trades_df):,}")

    if not sdf_active.empty:
        best  = sdf_active.loc[sdf_active['total_pnl'].idxmax()]
        worst = sdf_active.loc[sdf_active['total_pnl'].idxmin()]
        print(
            f"[{seq_no}]  Best  : [M{int(best['model_id'])}|{best['strike_offset']:+.0f}] "
            f"{best['trend'].upper()}+{best['momentum'].upper()}+"
            f"{best['volatility'].upper()}+{best['volume'].upper()} "
            f"  P&L=${best['total_pnl']:,.2f}  Sharpe={best['sharpe']:.2f}"
        )
        print(
            f"[{seq_no}]  Worst : [M{int(worst['model_id'])}|{worst['strike_offset']:+.0f}] "
            f"{worst['trend'].upper()}+{worst['momentum'].upper()}+"
            f"{worst['volatility'].upper()}+{worst['volume'].upper()} "
            f"  P&L=${worst['total_pnl']:,.2f}  Sharpe={worst['sharpe']:.2f}"
        )

    # ── Per-offset quick summary ──────────────────────────────────────────────
    print(f"\n[{seq_no}]  Per-offset breakdown (active scenarios only):")
    print(f"[{seq_no}]  {'Offset':<12} {'Scenarios':>9} {'Avg PnL':>10} "
          f"{'Win%':>7} {'Avg Sharpe':>11}")
    for offset in STRIKE_OFFSETS:
        sub = sdf_active[sdf_active['strike_offset'] == offset]
        if sub.empty:
            continue
        print(
            f"[{seq_no}]  "
            f"{OFFSET_LABELS[offset]:<12} "
            f"{len(sub):>9,} "
            f"${sub['total_pnl'].mean():>9,.2f} "
            f"{sub['win_rate'].mean():>6.1f}% "
            f"{sub['sharpe'].mean():>11.3f}"
        )

    print(f"{'='*60}\n")


# ── top-level worker wrapper (module-level for multiprocessing pickling) ───────

def _run_window(args: tuple) -> int:
    seq_no, run_dir, date_start, date_end, seed = args
    run_model_set(seq_no, run_dir, date_start, date_end, seed)
    return seq_no


# ══════════════════════════════════════════════════════════════════════════════
# OFFSET ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def analyse_offsets(df: pd.DataFrame, n_batches: int) -> pd.DataFrame:
    """
    Aggregate performance by strike_offset across all models and all batches.
    Returns one row per offset, ranked by consistency_score.
    """
    active = df[df['n_trades'] > 0]
    rows   = []
    for offset, grp in active.groupby('strike_offset'):
        pf_capped = grp['profit_factor'].clip(upper=PF_CAP)
        pnl_hit   = (grp['total_pnl'] > 0).mean()
        sharpe_hit = (grp['sharpe'] > 0).mean()
        score = min(100.0, (
            0.40 * pnl_hit
            + 0.25 * sharpe_hit
            + 0.20 * grp['win_rate'].mean() / 100
            + 0.15 * pf_capped.mean() / 10
        ) * 100)
        rows.append({
            'strike_offset':     offset,
            'label':             OFFSET_LABELS.get(offset, str(offset)),
            'batch_count':       int(grp['batch_no'].nunique()),
            'scenario_count':    len(grp),
            'avg_trades':        round(grp['n_trades'].mean(), 1),
            'avg_win_rate':      round(grp['win_rate'].mean(), 1),
            'avg_total_pnl':     round(grp['total_pnl'].mean(), 2),
            'pnl_hit_rate':      round(pnl_hit, 3),
            'avg_sharpe':        round(grp['sharpe'].mean(), 3),
            'avg_pf':            round(pf_capped.mean(), 3),
            'avg_drawdown':      round(grp['max_drawdown'].mean(), 2),
            'consistency_score': round(score, 1),
        })
    result = pd.DataFrame(rows).sort_values('consistency_score', ascending=False)
    result.insert(0, 'rank', range(1, len(result) + 1))
    return result.reset_index(drop=True)


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSIS — MARKDOWN REPORT
# ══════════════════════════════════════════════════════════════════════════════

def write_markdown_report(
    models_df:    pd.DataFrame,
    offset_df:    pd.DataFrame,
    category_dfs: dict,
    pair_dfs:     dict,
    n_batches:    int,
    total_rows:   int,
    run_dir:      Path,
):
    now  = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    path = run_dir / 'batch_run_analysis.md'

    lines = ['# Batch Run Analysis — Covered Call Entry — Strike Offset Study']
    lines.append(f'\n_Generated: {now}_\n')
    lines.append('## Overview\n')
    lines.append('| Metric | Value |')
    lines.append('|---|---|')
    lines.append(f'| Run directory             | {run_dir.name} |')
    lines.append(f'| Runs analysed             | {n_batches} |')
    lines.append(f'| Total scenario-runs       | {total_rows:,} |')
    lines.append(f'| Unique combos (4-tuple)   | {len(models_df):,} |')
    lines.append(f'| Strike offsets tested     | {", ".join(OFFSET_LABELS.values())} |')
    lines.append(f'| Min runs threshold        | {MIN_BATCHES} |')
    lines.append(f'| Profit factor cap         | {PF_CAP} |')
    lines.append('')
    lines.append('### Consistency Score Formula\n')
    lines.append('```')
    lines.append('score = 0.40 × pnl_hit_rate')
    lines.append('      + 0.25 × sharpe_hit_rate')
    lines.append('      + 0.20 × avg_win_rate / 100')
    lines.append('      + 0.15 × avg_profit_factor / 10')
    lines.append('(all terms normalised to 0–1, score reported 0–100)')
    lines.append('```\n')

    # ── Strike offset rankings ───────────────────────────────────────────────
    lines.append('## Strike Offset Rankings\n')
    lines.append('_Compares ITM vs OTM calls across all models and all windows._\n')
    offset_display_cols = [
        'rank', 'label', 'scenario_count', 'avg_trades', 'avg_win_rate',
        'avg_total_pnl', 'pnl_hit_rate', 'avg_sharpe', 'avg_pf',
        'avg_drawdown', 'consistency_score',
    ]
    lines.append(md_table(offset_df[[c for c in offset_display_cols
                                     if c in offset_df.columns]], n=len(offset_df)))
    lines.append('')

    # ── Top models (all offsets combined) ────────────────────────────────────
    display_model_cols = [
        'rank', 'trend', 'momentum', 'volatility', 'volume',
        'batch_count', 'avg_trades', 'avg_win_rate', 'avg_total_pnl',
        'pnl_hit_rate', 'avg_sharpe', 'avg_pf', 'consistency_score',
    ]
    lines.append(f'## Top {TOP_N} Indicator Combos (all offsets combined)\n')
    lines.append(md_table(models_df[display_model_cols]))
    lines.append('')

    bottom = models_df[display_model_cols].tail(TOP_N).iloc[::-1].copy()
    bottom['rank'] = range(1, len(bottom) + 1)
    lines.append(f'## Bottom {TOP_N} Indicator Combos\n')
    lines.append(md_table(bottom))
    lines.append('')

    # ── Per-offset top combos ─────────────────────────────────────────────────
    # Load combined summary for per-offset subsetting
    # (models_df is aggregated across offsets; we need per-offset breakdown)

    # ── Category rankings ─────────────────────────────────────────────────────
    for cat, cdf in category_dfs.items():
        display_cols = [
            'rank', cat, 'batch_count', 'avg_trades', 'avg_win_rate',
            'avg_total_pnl', 'pnl_hit_rate', 'avg_sharpe', 'consistency_score',
        ]
        lines.append(f'## {cat.capitalize()} Indicator Rankings\n')
        lines.append(md_table(cdf[[c for c in display_cols if c in cdf.columns]],
                               n=len(cdf)))
        lines.append('')

    # ── Pair rankings ─────────────────────────────────────────────────────────
    for (cat_a, cat_b), pdf in pair_dfs.items():
        display_cols = [
            'rank', cat_a, cat_b, 'batch_count', 'avg_trades',
            'avg_win_rate', 'avg_total_pnl', 'pnl_hit_rate',
            'avg_sharpe', 'consistency_score',
        ]
        lines.append(f'## Pair Rankings: {cat_a.capitalize()} × {cat_b.capitalize()}\n')
        lines.append(md_table(pdf[[c for c in display_cols if c in pdf.columns]]))
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

    # ── Offset rankings ───────────────────────────────────────────────────────
    print("[analyse] Strike offset rankings ...")
    offset_df = analyse_offsets(df, n_batches)
    offset_df.to_csv(run_dir / 'analysis_offset.csv', index=False)
    print(f"          {len(offset_df)} offsets ranked  ->  analysis_offset.csv")

    # ── Full model combinations (collapse across offsets) ─────────────────────
    print("[analyse] Full model combinations ...")
    models_df = analyse_full_models(df, n_batches)
    models_df.to_csv(run_dir / 'analysis_models.csv', index=False)
    print(f"          {len(models_df):,} models ranked  ->  analysis_models.csv")

    # ── Category rankings ─────────────────────────────────────────────────────
    category_dfs = {}
    for cat in CATEGORIES:
        print(f"[analyse] {cat} indicators ...")
        cdf = analyse_category(df, n_batches, cat)
        cdf.to_csv(run_dir / f'analysis_{cat}.csv', index=False)
        print(f"          {len(cdf)} indicators  ->  analysis_{cat}.csv")
        category_dfs[cat] = cdf

    # ── Pair rankings ─────────────────────────────────────────────────────────
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
    write_markdown_report(models_df, offset_df, category_dfs, pair_dfs,
                          n_batches, len(df), run_dir)

    # ── Console summary ───────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("  STRIKE OFFSET RANKINGS")
    print(f"{'='*60}")
    print(f"  {'Rank':<5} {'Label':<12} {'Avg PnL':>10} {'Win%':>7} "
          f"{'Sharpe':>8} {'Score':>7}")
    for _, r in offset_df.iterrows():
        print(
            f"  #{int(r['rank']):<4} "
            f"{r['label']:<12} "
            f"${r['avg_total_pnl']:>9,.2f} "
            f"{r['avg_win_rate']:>6.1f}% "
            f"{r['avg_sharpe']:>8.3f} "
            f"{r['consistency_score']:>7.1f}"
        )

    print(f"\n{'='*60}")
    print("  TOP 5 CONSISTENT MODELS (all offsets combined)")
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
        description='Run all 1,221 CC-entry models × 4 strike offsets across 100 windows.'
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
    print(f"[main]  Offsets     : {STRIKE_OFFSETS}")

    run_ts  = datetime.datetime.now().strftime('%m%d%H%M')
    run_dir = REPORTS_DIR / run_ts
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"[main]  Output directory: {run_dir}")

    # Build signals CSV in the main process before spawning workers.
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

    # ── Post-run analysis ─────────────────────────────────────────────────────
    summarize_run(run_dir)
