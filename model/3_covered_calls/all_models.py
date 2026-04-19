"""
all_models.py — 3_covered_calls

Orchestrates all 1,221 indicator combinations across 100 time windows.
Each combination fires 18 CC variants simultaneously (3 expiries × 6 strikes).
Total scenario rows per window: 1,221 × 18 = 21,978.

The strategy opens all 12 covered call variants immediately at entry.
Each variant is an independent simulation with its own exit logic.

Purpose: determine which strike/expiry combination performs best across all
indicator models.

Simulation logic lives in single_model.py (this directory).
This file handles:
  - combination generation  (TREND × MOMENTUM × VOLATILITY × VOLUME)
  - window scheduling       (100 unique windows per execution)
  - CSV and log output
  - post-execution analysis and markdown report

Pipeline per window
───────────────────
1.  S.load_or_build_signals()           — load or build signals CSV (cached in memory)
2.  S.prepare_window(...)               — filter window, draw sample, build day structures
3.  run_all_models(...)                 — loop 1,221 combos, call S.run_combo() for each
4.  write_summary_csv / trades_csv      — persist results  (summary = 14,652 rows)
5.  summarize_run(run_dir)              — rank combos and variants after all 100 windows

Output  reports/{mmddhhmi}/
──────
  {seq_no}_summary.csv      — 21,978 rows (1,221 combos × 18 variants)
  {seq_no}_trades.csv       — all stock + option legs
  {seq_no}_run.log          — trade-by-trade table log
  analysis_combos.csv       — 1,221 combos ranked (across all variants)
  analysis_option_variants.csv — 12 option variants ranked
  analysis_{cat}.csv        — per indicator category
  analysis_pair_{a}_{b}.csv — 6 indicator pair cross-tabs
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

# ── indicator categories ────────────────────────────────────────────────────────
TREND      = ['ema', 'macd', 'adx', 'sar', 'don', 'arn', 'vtx']
MOMENTUM   = ['rsi', 'sto', 'cci', 'cmo', 'tsi', 'roc', 'frc', 'srsi', 'rmi', 'macd']
VOLATILITY = ['atr', 'bbd', 'chp']
VOLUME     = ['vwap', 'obv', 'mfi', 'klg', 'frc', 'vrc']

# Variant definitions (mirror single_model.py)
OPTION_VARIANTS = S.OPTION_VARIANTS   # [(expiry_label, strike_label), ...]
STRIKE_LABELS   = S.STRIKE_LABELS     # ['s-2', 's-1', 's-0', 's+0', 's+1', 's+2']


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
# PER-VARIANT METRICS  (called by run_all_models to build summary rows)
# ══════════════════════════════════════════════════════════════════════════════

def _calc_variant_metrics_from_positions(
    pos_list:     list[dict],
    expiry_label: str,
    strike_label: str,
    variant_key:  str,
    combo_info:   dict,
    seq_no:       int,
    model_id:     int,
) -> dict:
    """
    Compute metrics for one (combo × variant) from positions list filtered
    to that variant.
    """
    vp = [p for p in pos_list if p.get('expiry_label') == expiry_label
                              and p.get('strike_label') == strike_label
                              and p.get('combined_pnl') is not None
                              and p.get('is_winner') is not None]

    base = {
        'batch_no':     seq_no,
        'model_id':     model_id,
        'trend':        combo_info['trend'],
        'momentum':     combo_info['momentum'],
        'volatility':   combo_info['volatility'],
        'volume':       combo_info['volume'],
        'expiry_label': expiry_label,
        'strike_label': strike_label,
        'variant_key':  variant_key,
    }

    if not vp:
        return {**base, **S._empty_metrics('no_valid_positions')}

    metrics = S._calc_metrics(vp)
    n_vp = len(vp)
    metrics['avg_stock_pnl']  = round(sum(p.get('stock_pnl', 0) or 0 for p in vp) / n_vp, 2)
    metrics['avg_option_pnl'] = round(sum(p.get('option_pnl', 0) or 0 for p in vp) / n_vp, 2)
    return {**base, **metrics}


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
    out  = S.restructure_trades(trades_df)
    out.to_csv(path, index=False)
    print(f"[{seq_no}][report]  Trades   -> {path}  ({len(out):,} records)")
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
    Run all 1,221 combos on one window.
    Each combo produces 12 variant summary rows = 14,652 total rows.
    """
    combos   = generate_combos()
    n_combos = len(combos)
    n_total  = n_combos * len(OPTION_VARIANTS)

    summary_rows = []
    all_trades   = []

    print(f"\n[{seq_no}][backtest] Running {n_combos:,} combos × "
          f"{len(OPTION_VARIANTS)} variants = {n_total:,} scenarios ...")
    print(f"[{seq_no}]           Sample size : {len(sample_idx):,} bars")

    for model_id, (t, m, v, vol) in enumerate(combos, 1):
        indicators = {'trend': t, 'momentum': m, 'volatility': v, 'volume': vol}
        combo_info = {'trend': t, 'momentum': m, 'volatility': v, 'volume': vol}

        try:
            metrics, raw_trades, pos_list = S.run_combo(
                df, sample_idx, day_dict, day_pos_map, indicators,
                capture_evals=False,
            )
        except Exception as exc:
            err_msg = (f"[{seq_no}|M{model_id}] "
                       f"ERROR in run_combo({t},{m},{v},{vol}): {exc}")
            print(err_msg)
            if log_fh is not None:
                log_fh.write(f"\n{err_msg}\n")
            continue

        # Build one summary row per variant
        for expiry_label, strike_label in OPTION_VARIANTS:
            variant_key = f"{expiry_label}/{strike_label}"
            row = _calc_variant_metrics_from_positions(
                pos_list, expiry_label, strike_label, variant_key,
                combo_info, seq_no, model_id,
            )
            summary_rows.append(row)

        model_trades = [
            {'batch_no': seq_no, 'model_id': model_id, **tr}
            for tr in raw_trades
        ]

        # Write log for this model (stock legs only, grouped by trade_no)
        if log_fh is not None and raw_trades:
            stock_trades = [tr for tr in raw_trades if tr.get('leg') == 'stock']
            if stock_trades:
                S._log_model_start(log_fh, seq_no, str(model_id), t, m, v, vol)

                # Group by trade_no
                trade_nos = sorted({tr['trade_no'] for tr in stock_trades})
                for tno in trade_nos:
                    vr = {}
                    for tr in raw_trades:
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
                    first_tr = next(tr for tr in stock_trades if tr['trade_no'] == tno)
                    entry_snap = {
                        'entry_time':    first_tr.get('entry_time', ''),
                        'entry_price':   first_tr.get('entry_price', 0.0),
                        'shares':        first_tr.get('shares', S.SHARES),
                        'atr_at_entry':  first_tr.get('atr_at_entry'),
                        'rsi_at_entry':  first_tr.get('rsi_at_entry'),
                        'adx_at_entry':  first_tr.get('adx_at_entry'),
                        'vwap_at_entry': first_tr.get('vwap_at_entry'),
                    }
                    S._log_trade_table(log_fh, tno, indicators, entry_snap, vr, [])

                S._log_model_end(log_fh, seq_no, str(model_id), metrics)

        all_trades.extend(model_trades)

        if model_id % 100 == 0 or model_id == n_combos:
            print(f"  [{seq_no}|M{model_id:>5}/{n_combos}]  "
                  f"trades so far: {len(all_trades):,}  "
                  f"summary rows: {len(summary_rows):,}")

    print(f"\n[{seq_no}][backtest] Complete.  Total trades: {len(all_trades):,}  "
          f"Summary rows: {len(summary_rows):,}")

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
    print(f"[{seq_no}]  AAPL Intraday Backtest (CC Variants)  |  start at {ts}")
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
            f"  seed: {seed}  model: [cc_variants]  generated: {ts}\n"
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
    print(f"[{seq_no}]  Summary rows     : {len(summary_df):,}")
    print(f"[{seq_no}]  With trades      : {len(sdf_active):,}")
    print(f"[{seq_no}]  Total trade rows : {len(trades_df):,}")

    if not sdf_active.empty:
        best  = sdf_active.loc[sdf_active['total_pnl'].idxmax()]
        worst = sdf_active.loc[sdf_active['total_pnl'].idxmin()]
        print(
            f"[{seq_no}]  Best  : [M{int(best['model_id'])}|{best['variant_key']}] "
            f"{best['trend'].upper()}+{best['momentum'].upper()}+"
            f"{best['volatility'].upper()}+{best['volume'].upper()} "
            f"  P&L=${best['total_pnl']:,.2f}  Sharpe={best['sharpe']:.2f}"
        )
        print(
            f"[{seq_no}]  Worst : [M{int(worst['model_id'])}|{worst['variant_key']}] "
            f"{worst['trend'].upper()}+{worst['momentum'].upper()}+"
            f"{worst['volatility'].upper()}+{worst['volume'].upper()} "
            f"  P&L=${worst['total_pnl']:,.2f}  Sharpe={worst['sharpe']:.2f}"
        )

    # ── Per-variant quick summary ─────────────────────────────────────────────
    print(f"\n[{seq_no}]  Per-variant breakdown (active scenarios only):")
    print(f"[{seq_no}]  {'Variant':<12} {'Scenarios':>9} {'Avg PnL':>10} "
          f"{'Win%':>7} {'Avg Sharpe':>11}")
    for expiry_label, strike_label in OPTION_VARIANTS:
        vkey = f"{expiry_label}/{strike_label}"
        sub  = sdf_active[sdf_active['variant_key'] == vkey]
        if sub.empty:
            continue
        print(
            f"[{seq_no}]  "
            f"{vkey:<12} "
            f"{len(sub):>9,} "
            f"${sub['total_pnl'].mean():>9,.2f} "
            f"{sub['win_rate'].mean():>6.1f}% "
            f"{sub['sharpe'].mean():>11.3f}"
        )

    print(f"{'='*60}\n")


# ── top-level worker wrapper (module-level for multiprocessing pickling) ───────

def _run_window(args: tuple) -> int:
    seq_no, run_dir, date_start, date_end, seed, buyback, expiry_filter, strike_filter = args
    S.OPTION_EXIT_PRICE = buyback
    S.OPTION_VARIANTS   = [(ew, sl) for ew in expiry_filter for sl in strike_filter]
    run_model_set(seq_no, run_dir, date_start, date_end, seed)
    return seq_no


# ══════════════════════════════════════════════════════════════════════════════
# OPTION VARIANT ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def analyse_option_variants(df: pd.DataFrame, n_batches: int) -> pd.DataFrame:
    """
    Aggregate performance by option variant (expiry_label + strike_label)
    across all 1,221 combos and all batches.
    Returns one row per variant (18 rows), ranked by consistency_score.
    """
    active = df[df['number_of_trades'] > 0]
    rows   = []
    for (expiry_label, strike_label), grp in active.groupby(['expiry_label', 'strike_label']):
        variant_key  = f"{expiry_label}/{strike_label}"
        pf_capped    = grp['profit_factor'].clip(upper=PF_CAP)
        pnl_hit      = (grp['total_pnl'] > 0).mean()
        sharpe_hit   = (grp['sharpe'] > 0).mean()
        score = min(100.0, (
            0.40 * pnl_hit
            + 0.25 * sharpe_hit
            + 0.20 * grp['win_rate'].mean() / 100
            + 0.15 * pf_capped.mean() / 10
        ) * 100)

        # Best combo for this variant by total_pnl
        best_grp = grp.loc[grp['total_pnl'].idxmax()]
        best_combo = (
            f"{best_grp['trend'].upper()}+"
            f"{best_grp['momentum'].upper()}+"
            f"{best_grp['volatility'].upper()}+"
            f"{best_grp['volume'].upper()}"
            if all(c in best_grp.index for c in ['trend', 'momentum', 'volatility', 'volume'])
            else ''
        )

        rows.append({
            'variant_key':       variant_key,
            'expiry_label':      expiry_label,
            'strike_label':      strike_label,
            'batch_count':       int(grp['batch_no'].nunique()) if 'batch_no' in grp.columns else n_batches,
            'avg_trades':        round(grp['number_of_trades'].mean(), 1),
            'avg_win_rate':      round(grp['win_rate'].mean(), 1),
            'avg_total_pnl':     round(grp['total_pnl'].mean(), 2),
            'pnl_hit_rate':      round(pnl_hit, 3),
            'avg_sharpe':        round(grp['sharpe'].mean(), 3),
            'avg_pf':            round(pf_capped.mean(), 3),
            'consistency_score': round(score, 1),
            'best_combo':        best_combo,
        })
    result = pd.DataFrame(rows).sort_values('consistency_score', ascending=False)
    result.insert(0, 'rank', range(1, len(result) + 1))
    return result.reset_index(drop=True)


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSIS — MARKDOWN REPORT
# ══════════════════════════════════════════════════════════════════════════════

def write_markdown_report(
    models_df:    pd.DataFrame,
    variants_df:  pd.DataFrame,
    category_dfs: dict,
    pair_dfs:     dict,
    n_batches:    int,
    total_rows:   int,
    run_dir:      Path,
    run_params:   dict | None = None,
):
    p    = run_params or {}
    now  = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    path = run_dir / 'batch_run_analysis.md'

    n_variants     = p.get('n_variants',     len(S.OPTION_VARIANTS))
    expiry_labels  = p.get('expiry_labels',  S.EXPIRY_WEEKS)
    strike_labels  = p.get('strike_labels',  S.STRIKE_LABELS)
    n_combos       = len(models_df) if len(models_df) > 0 else 1_221
    rows_per_win   = n_combos * n_variants

    lines = ['# Batch Run Analysis — Covered Call Variants']
    lines.append(f'\n_Generated: {now}_\n')
    lines.append('## Run Parameters\n')
    lines.append('| Parameter | Value |')
    lines.append('|---|---|')
    lines.append(f'| Run directory      | `{run_dir.name}` |')
    lines.append(f'| Date range         | {p.get("date_first", "—")} → {p.get("date_last", "—")} |')
    lines.append(f'| Windows run        | {p.get("n_windows", n_batches)} |')
    lines.append(f'| Windows analysed   | {n_batches} |')
    lines.append(f'| Window length      | {p.get("window_days", "—")} calendar days |')
    lines.append(f'| Random seed        | {p.get("random_seed", "—")} |')
    lines.append(f'| Workers            | {p.get("n_workers", "—")} |')
    lines.append(f'| Sample bars/window | {p.get("n_sample", S.N_SAMPLE):,} |')
    lines.append(f'| Shares per trade   | {p.get("shares", S.SHARES)} |')
    lines.append(f'| Commission         | ${p.get("commission", S.COMMISSION):.2f} per round-trip |')
    lines.append(f'| Buyback threshold  | ${p.get("buyback", S.OPTION_EXIT_PRICE):.2f} |')
    lines.append(f'| Expiry labels      | {", ".join(expiry_labels)} |')
    lines.append(f'| Strike labels      | {", ".join(strike_labels)} |')
    lines.append(f'| Variants tested    | {n_variants} ({" × ".join([str(len(expiry_labels)), str(len(strike_labels))])}) |')
    lines.append(f'| Total scenario-rows| {total_rows:,} |')
    lines.append(f'| Unique combos      | {n_combos:,} |')
    lines.append(f'| Rows per window    | {rows_per_win:,} ({n_combos:,} combos × {n_variants} variants) |')
    lines.append(f'| Min runs threshold | {MIN_BATCHES} |')
    lines.append(f'| Profit factor cap  | {PF_CAP} |')
    if p.get('argv'):
        lines.append(f'| Command            | `{" ".join(p["argv"])}` |')
    lines.append('')
    lines.append('### Consistency Score Formula\n')
    lines.append('```')
    lines.append('score = 0.40 × pnl_hit_rate')
    lines.append('      + 0.25 × sharpe_hit_rate')
    lines.append('      + 0.20 × avg_win_rate / 100')
    lines.append('      + 0.15 × avg_profit_factor / 10')
    lines.append('(all terms normalised to 0–1, score reported 0–100)')
    lines.append('```\n')

    # ── Option variant rankings ────────────────────────────────────────────────
    lines.append('## Option Variant Rankings (Table 2)\n')
    lines.append('_Compares all 12 option variants across all 1,221 combos and all windows._\n')
    variant_display_cols = [
        'rank', 'variant_key', 'expiry_label', 'strike_label',
        'batch_count', 'avg_trades', 'avg_win_rate',
        'avg_total_pnl', 'pnl_hit_rate', 'avg_sharpe', 'avg_pf',
        'consistency_score', 'best_combo',
    ]
    lines.append(md_table(
        variants_df[[c for c in variant_display_cols if c in variants_df.columns]],
        n=len(variants_df)
    ))
    lines.append('')

    # ── Top combos ────────────────────────────────────────────────────────────
    display_model_cols = [
        'rank', 'trend', 'momentum', 'volatility', 'volume',
        'batch_count', 'avg_trades', 'avg_win_rate', 'avg_total_pnl',
        'pnl_hit_rate', 'avg_sharpe', 'avg_pf', 'consistency_score',
    ]
    lines.append(f'## Top {TOP_N} Indicator Combos — Table 1 (all variants combined)\n')
    lines.append(md_table(models_df[[c for c in display_model_cols if c in models_df.columns]]))
    lines.append('')

    # ── Category rankings (skip momentum) ────────────────────────────────────
    for cat, cdf in category_dfs.items():
        if cat == 'momentum':
            continue
        display_cols = [
            'rank', cat, 'batch_count', 'avg_trades', 'avg_win_rate',
            'avg_total_pnl', 'pnl_hit_rate', 'avg_sharpe', 'consistency_score',
        ]
        lines.append(f'## {cat.capitalize()} Indicator Rankings\n')
        lines.append(md_table(
            cdf[[c for c in display_cols if c in cdf.columns]],
            n=len(cdf)
        ))
        lines.append('')

    # ── Pair rankings ──────────────────────────────────────────────────────────
    for (cat_a, cat_b), pdf in pair_dfs.items():
        display_cols = [
            'rank', cat_a, cat_b, 'batch_count', 'avg_trades',
            'avg_win_rate', 'avg_total_pnl', 'pnl_hit_rate',
            'avg_sharpe', 'consistency_score',
        ]
        lines.append(f'## Pair Rankings: {cat_a.capitalize()} × {cat_b.capitalize()}\n')
        lines.append(md_table(
            pdf[[c for c in display_cols if c in pdf.columns]]
        ))
        lines.append('')

    path.write_text('\n'.join(lines), encoding='utf-8')
    print(f"[report]  Markdown  -> {path}")


# ══════════════════════════════════════════════════════════════════════════════
# ALL-RUNS ACCUMULATOR
# ══════════════════════════════════════════════════════════════════════════════

def append_to_all_runs(df: pd.DataFrame, run_dir: Path) -> None:
    """
    Aggregate the full-run summary df by (combo × variant) and append one row
    per combination to REPORTS_DIR/all_runs.csv.

    Columns written
    ───────────────
    run_ts          — folder name of this batch run (mmddhhmi)
    model_id        — sequential model index 1-1221 (deterministic across runs)
    model_detail    — trend_momentum_volatility_volume (e.g. ema_rsi_atr_vwap)
    variant         — w0/s+1 … w2/s-2
    avg_stock_pnl   — weighted-avg per-trade stock P&L across all windows
    avg_option_pnl  — weighted-avg per-trade option P&L across all windows
    avg_total_pnl   — weighted-avg per-trade combined P&L across all windows
    trade_count     — total trades across all windows for this combo+variant
    win_pct         — weighted-avg win rate across all windows
    total_pnl       — sum of total_pnl across all windows
    sharpe          — mean Sharpe ratio across windows
    profit_factor   — mean profit factor across windows (capped at PF_CAP)
    max_drawdown    — worst (min) max_drawdown across windows
    avg_bars_held   — weighted-avg bars held per trade
    consistency_score — composite score: 0.40*pnl_hit + 0.25*sharpe_hit
                        + 0.20*win_rate/100 + 0.15*pf/10  (0-100 scale)
    """
    all_runs_path = REPORTS_DIR / 'all_runs.csv'
    run_ts = run_dir.name

    active = df[df['number_of_trades'] > 0].copy()
    if active.empty:
        print("[all_runs]  No active rows — skipping.")
        return

    group_cols = ['model_id', 'trend', 'momentum', 'volatility', 'volume', 'variant_key']
    # model_id may not be present if summary was built without it; fall back gracefully
    group_cols = [c for c in group_cols if c in active.columns]
    min_group  = ['trend', 'momentum', 'volatility', 'volume', 'variant_key']
    if not all(c in active.columns for c in min_group):
        print("[all_runs]  Required group columns missing — skipping.")
        return

    rows = []
    for keys, grp in active.groupby(group_cols, sort=False):
        key_dict = dict(zip(group_cols, keys if isinstance(keys, tuple) else (keys,)))
        t   = key_dict['trend']
        m   = key_dict['momentum']
        v   = key_dict['volatility']
        vol = key_dict['volume']
        vkey = key_dict['variant_key']

        n_total = int(grp['number_of_trades'].sum())
        if n_total == 0:
            continue

        def _wavg(col: str, fallback: float = 0.0) -> float:
            """Weighted average of col by number_of_trades; falls back to unweighted mean if col missing."""
            if col not in grp.columns:
                return fallback
            return round(float((grp[col] * grp['number_of_trades']).sum() / n_total), 2)

        pf_capped  = grp['profit_factor'].clip(upper=PF_CAP) if 'profit_factor' in grp.columns else pd.Series([0.0])
        pnl_hit    = float((grp['total_pnl'] > 0).mean()) if 'total_pnl' in grp.columns else 0.0
        sharpe_col = grp['sharpe'] if 'sharpe' in grp.columns else pd.Series([0.0])
        sharpe_hit = float((sharpe_col > 0).mean())
        wr_mean    = float(grp['win_rate'].mean()) / 100 if 'win_rate' in grp.columns else 0.0
        score      = min(100.0, (
            0.40 * pnl_hit
            + 0.25 * sharpe_hit
            + 0.20 * wr_mean
            + 0.15 * float(pf_capped.mean()) / 10
        ) * 100)

        rows.append({
            'run_ts':             run_ts,
            'model_id':           int(key_dict.get('model_id', 0)) if 'model_id' in key_dict else '',
            'model_detail':       f"{t}_{m}_{v}_{vol}",
            'variant':            vkey,
            'avg_stock_pnl':      _wavg('avg_stock_pnl'),
            'avg_option_pnl':     _wavg('avg_option_pnl'),
            'avg_total_pnl':      _wavg('avg_pnl'),
            'trade_count':        n_total,
            'win_pct':            _wavg('win_rate'),
            'total_pnl':          round(float(grp['total_pnl'].sum()), 2) if 'total_pnl' in grp.columns else 0.0,
            'sharpe':             round(float(sharpe_col.mean()), 3),
            'profit_factor':      round(float(pf_capped.mean()), 3),
            'max_drawdown':       round(float(grp['max_drawdown'].min()), 2) if 'max_drawdown' in grp.columns else 0.0,
            'avg_bars_held':      _wavg('avg_duration_bars'),
            'consistency_score':  round(score, 1),
        })

    if not rows:
        print("[all_runs]  No rows to append.")
        return

    col_order = [
        'run_ts', 'model_id', 'model_detail', 'variant',
        'avg_stock_pnl', 'avg_option_pnl', 'avg_total_pnl',
        'trade_count', 'win_pct', 'total_pnl',
        'sharpe', 'profit_factor', 'max_drawdown', 'avg_bars_held',
        'consistency_score',
    ]
    new_df = pd.DataFrame(rows)
    new_df = new_df[[c for c in col_order if c in new_df.columns]]

    write_header = not all_runs_path.exists()
    new_df.to_csv(all_runs_path, mode='a', header=write_header, index=False)
    print(f"[all_runs]  Appended {len(new_df):,} rows  ->  {all_runs_path}")
    print(f"[all_runs]  run_ts={run_ts}  "
          f"combos={len(new_df) // len(S.OPTION_VARIANTS)}  "
          f"variants={len(S.OPTION_VARIANTS)}")


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSIS — SUMMARIZE RUN
# ══════════════════════════════════════════════════════════════════════════════

def consolidate_csvs(run_dir: Path) -> None:
    """
    Merge all per-window {n}_summary.csv and {n}_trades.csv into single
    summary.csv and trades.csv, then delete the individual files.
    Run logs ({n}_run.log) are left as-is.
    """
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
        print(f"[consolidate]  {len(files)} files → {out_path.name}  ({out_path.stat().st_size / 1_048_576:.1f} MB)")


def summarize_run(run_dir: Path):
    print(f"\n{'='*60}")
    print(f"  Batch Run Analysis — {run_dir.name}")
    print(f"{'='*60}\n")

    # Load run parameters saved at batch start (falls back to empty dict for old runs)
    import json
    params_path = run_dir / 'run_params.json'
    run_params  = json.loads(params_path.read_text()) if params_path.exists() else {}

    # Load summary data: prefer consolidated summary.csv, fall back to per-window files
    consolidated_path = run_dir / 'summary.csv'
    if consolidated_path.exists():
        combined = pd.read_csv(consolidated_path, low_memory=False)
        combined['profit_factor_capped'] = combined['profit_factor'].clip(upper=PF_CAP)
        n_batches = int(combined['batch_no'].nunique()) if 'batch_no' in combined.columns else len(
            list(run_dir.glob('*_run.log'))
        )
        print(f"[load]  summary.csv  |  {n_batches} runs  |  {len(combined):,} rows")
        df = combined
    else:
        df, n_batches = load_all_summaries(run_dir=run_dir)

    # Force numeric dtypes on key columns.
    # Empty summary files (all rows filtered out in low-trade windows) cause
    # pd.concat to infer object dtype for columns it never sees data for.
    numeric_cols = [
        'number_of_trades', 'win_rate', 'total_pnl', 'avg_pnl',
        'avg_bars_held', 'profit_factor', 'profit_factor_capped',
        'sharpe', 'max_drawdown', 'avg_stock_pnl', 'avg_option_pnl',
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    # Rename columns back to internal names used by aggregate() in utils.py.
    # Summary CSVs on disk use display names; we reverse them for analysis.
    rename_map = {
        'avg_entry_price':  'avg_entry',
        'avg_exit_price':   'avg_exit',
        'avg_bars_held':    'avg_duration_bars',
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    # ── Option variant rankings (Table 2) ────────────────────────────────────
    print("[analyse] Option variant rankings ...")
    variants_df = analyse_option_variants(df, n_batches)
    variants_df.to_csv(run_dir / 'analysis_option_variants.csv', index=False)
    print(f"          {len(variants_df)} variants ranked  ->  analysis_option_variants.csv")

    # ── Full model combinations (Table 1 — collapse across variants) ──────────
    print("[analyse] Full model combinations ...")
    models_df = analyse_full_models(df, n_batches)
    models_df.to_csv(run_dir / 'analysis_combos.csv', index=False)
    print(f"          {len(models_df):,} models ranked  ->  analysis_combos.csv")

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
    write_markdown_report(models_df, variants_df, category_dfs, pair_dfs,
                          n_batches, len(df), run_dir, run_params)

    # ── Append to persistent all_runs.csv ────────────────────────────────────
    print("\n[all_runs] Appending to all_runs.csv ...")
    append_to_all_runs(df, run_dir)

    # ── Console summary ───────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("  OPTION VARIANT RANKINGS")
    print(f"{'='*60}")
    print(f"  {'Rank':<5} {'Variant':<12} {'Avg PnL':>10} {'Win%':>7} "
          f"{'Sharpe':>8} {'Score':>7}")
    for _, r in variants_df.iterrows():
        print(
            f"  #{int(r['rank']):<4} "
            f"{r['variant_key']:<12} "
            f"${r['avg_total_pnl']:>9,.2f} "
            f"{r['avg_win_rate']:>6.1f}% "
            f"{r['avg_sharpe']:>8.3f} "
            f"{r['consistency_score']:>7.1f}"
        )

    print(f"\n{'='*60}")
    print("  TOP 5 CONSISTENT MODELS (all variants combined)")
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
        description='Run all 1,221 CC-variant models (18 variants each) across N windows.'
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
    _parser.add_argument(
        '--windows', type=int, default=100,
        help='Number of test windows to run (default: 100; max is date-range / window-days)',
    )
    _parser.add_argument(
        '--buyback', type=float, default=0.50,
        help='Option ask threshold to trigger a buyback exit (default: 0.50)',
    )
    _parser.add_argument(
        '--expiry-label', nargs='+', default=None,
        metavar='LABEL',
        help='Expiry weeks to include: w0 w1 w2 (default: all three)',
    )
    _parser.add_argument(
        '--strike-label', nargs='+', default=None,
        metavar='LABEL',
        help='Strike labels to include: s-2 s-1 s-0 s+0 s+1 s+2 (default: all six)',
    )
    _args = _parser.parse_args()

    _expiry_filter = _args.expiry_label or S.EXPIRY_WEEKS
    _strike_filter = _args.strike_label or S.STRIKE_LABELS

    _invalid_expiry = [e for e in _expiry_filter if e not in S.EXPIRY_WEEKS]
    _invalid_strike = [s for s in _strike_filter if s not in S.STRIKE_LABELS]
    if _invalid_expiry:
        _parser.error(f"Invalid --expiry-label value(s): {_invalid_expiry}. Choose from {S.EXPIRY_WEEKS}")
    if _invalid_strike:
        _parser.error(f"Invalid --strike-label value(s): {_invalid_strike}. Choose from {S.STRIKE_LABELS}")

    S.OPTION_VARIANTS = [(ew, sl) for ew in _expiry_filter for sl in _strike_filter]
    OPTION_VARIANTS   = S.OPTION_VARIANTS   # keep local alias in sync

    RANDOM_SEED = _args.seed if _args.seed is not None else secrets.randbelow(2**32)
    N_WORKERS   = _args.workers
    DATA_FIRST  = datetime.date.fromisoformat(_args.data_first)
    DATA_LAST   = datetime.date.fromisoformat(_args.data_last)
    WINDOW_DAYS = _args.window_days
    N_WINDOWS   = _args.windows
    S.OPTION_EXIT_PRICE = _args.buyback

    run_ts  = datetime.datetime.now().strftime('%m%d%H%M')
    run_dir = REPORTS_DIR / run_ts
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"[main]  Master seed : {RANDOM_SEED}")
    print(f"[main]  Workers     : {N_WORKERS}")
    print(f"[main]  Windows     : {N_WINDOWS}  ({WINDOW_DAYS}-day each)")
    print(f"[main]  Date range  : {DATA_FIRST} → {DATA_LAST}")
    print(f"[main]  Buyback     : ${S.OPTION_EXIT_PRICE:.2f}")
    print(f"[main]  Expiry      : {_expiry_filter}")
    print(f"[main]  Strike      : {_strike_filter}")
    print(f"[main]  Variants    : {len(S.OPTION_VARIANTS)}")
    print(f"[main]  Output dir  : {run_dir}")

    # Save run parameters so summarize_run can include them in the report,
    # even when called standalone after the batch completes.
    import json
    _run_params = {
        'run_ts':        run_ts,
        'random_seed':   RANDOM_SEED,
        'n_workers':     N_WORKERS,
        'n_windows':     N_WINDOWS,
        'window_days':   WINDOW_DAYS,
        'date_first':    str(DATA_FIRST),
        'date_last':     str(DATA_LAST),
        'buyback':       S.OPTION_EXIT_PRICE,
        'expiry_labels': _expiry_filter,
        'strike_labels': _strike_filter,
        'n_variants':    len(S.OPTION_VARIANTS),
        'n_sample':      S.N_SAMPLE,
        'commission':    S.COMMISSION,
        'shares':        S.SHARES,
        'argv':          sys.argv,
    }
    (run_dir / 'run_params.json').write_text(
        json.dumps(_run_params, indent=2), encoding='utf-8'
    )

    # Build signals CSV in the main process before spawning workers.
    print("[main]  Loading/building signals CSV ...")
    S.load_or_build_signals()
    print("[main]  Signals ready.\n")

    # ── Generate N_WINDOWS window specs ───────────────────────────────────────
    max_start  = DATA_LAST - datetime.timedelta(days=WINDOW_DAYS)
    total_days = (max_start - DATA_FIRST).days

    if N_WINDOWS > total_days:
        raise ValueError(
            f"--windows {N_WINDOWS} exceeds available unique start days ({total_days}) "
            f"for date range {DATA_FIRST} → {DATA_LAST} with --window-days {WINDOW_DAYS}. "
            f"Reduce --windows or extend the date range."
        )

    master_rng = np.random.default_rng(RANDOM_SEED)
    offsets    = master_rng.choice(total_days, size=N_WINDOWS, replace=False).tolist()
    run_seeds  = master_rng.integers(1, 10_000, size=N_WINDOWS).tolist()
    runs       = sorted(zip(offsets, run_seeds), key=lambda x: x[0])

    window_args = []
    for seq_no, (offset, seed) in enumerate(runs, 1):
        date_start = (DATA_FIRST + datetime.timedelta(days=int(offset))).strftime('%Y-%m-%d')
        date_end   = (DATA_FIRST + datetime.timedelta(days=int(offset) + WINDOW_DAYS)).strftime('%Y-%m-%d')
        window_args.append((seq_no, run_dir, date_start, date_end, int(seed),
                            _args.buyback, _expiry_filter, _strike_filter))
        print(f"  [{seq_no:>3}]  {date_start} -> {date_end}  seed={seed}")

    # ── Dispatch to worker pool ────────────────────────────────────────────────
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

    # ── Consolidate per-window CSVs into single files ─────────────────────────
    print("\n[consolidate] Merging per-window CSVs ...")
    consolidate_csvs(run_dir)

    # ── Post-run analysis ─────────────────────────────────────────────────────
    summarize_run(run_dir)
