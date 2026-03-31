"""
batch_run_analysis.py

Reads all {batch_no}_summary.csv files from ../reports/ and rates indicator
combinations by consistency across batches.

Analysis levels
───────────────
1. Full model  (trend + momentum + volatility + volume)
2. Individual  indicator contribution per category
3. Pairwise    category interactions  (e.g. trend × momentum)

Consistency metrics (per group, across batches)
────────────────────────────────────────────────
  appearance_rate   – % of batches where the model had at least 1 trade
  pnl_hit_rate      – % of batches where total_pnl > 0
  sharpe_hit_rate   – % of batches where sharpe  > 0
  avg_win_rate      – mean win_rate (%)
  avg_total_pnl     – mean total_pnl ($)
  avg_sharpe        – mean Sharpe ratio
  avg_pf            – mean profit_factor  (capped at 10 to suppress inf)
  pnl_std           – std of total_pnl  (lower = more stable)
  consistency_score – weighted composite (see _score())

Output
──────
  ../reports/analysis_models.csv       – full model rankings
  ../reports/analysis_trend.csv        – trend indicator rankings
  ../reports/analysis_momentum.csv     – momentum indicator rankings
  ../reports/analysis_volatility.csv   – volatility indicator rankings
  ../reports/analysis_volume.csv       – volume indicator rankings
  ../reports/analysis_pairs_*.csv      – pairwise rankings (6 pairs)
  ../reports/batch_run_analysis.md     – human-readable summary report
"""

from __future__ import annotations

import datetime
from pathlib import Path

import numpy as np
import pandas as pd

REPORTS_DIR = Path('../reports')
PF_CAP      = 10.0   # cap profit_factor before averaging (suppress inf)
MIN_BATCHES = 5      # minimum batch appearances to be included in rankings
TOP_N       = 20     # rows shown in each report section

CATEGORIES  = ['trend', 'momentum', 'volatility', 'volume']


# ══════════════════════════════════════════════════════════════════════════════
# LOAD
# ══════════════════════════════════════════════════════════════════════════════

def load_all_summaries() -> pd.DataFrame:
    files = sorted(REPORTS_DIR.glob('*_summary.csv'),
                   key=lambda p: int(p.stem.split('_')[0]))
    if not files:
        raise FileNotFoundError(f"No *_summary.csv files found in {REPORTS_DIR}")

    frames = []
    for f in files:
        df = pd.read_csv(f)
        frames.append(df)

    combined = pd.concat(frames, ignore_index=True)
    combined['profit_factor_capped'] = combined['profit_factor'].clip(upper=PF_CAP)
    n_batches = combined['batch_no'].nunique()
    print(f"[load]  {len(files)} files  |  {n_batches} batches  |  {len(combined):,} rows")
    return combined, n_batches


# ══════════════════════════════════════════════════════════════════════════════
# SCORING
# ══════════════════════════════════════════════════════════════════════════════

def _score(row: pd.Series) -> float:
    """
    Composite consistency score (0–100).
    Weights chosen to favour models that are *reliably* good, not just occasionally great.

      40% – pnl_hit_rate     (most important: are we profitable more often than not?)
      25% – sharpe_hit_rate  (risk-adjusted reliability)
      20% – avg_win_rate / 100
      15% – avg_pf / PF_CAP  (capped profit factor)
    """
    return (
        0.40 * row['pnl_hit_rate']   * 100 +
        0.25 * row['sharpe_hit_rate'] * 100 +
        0.20 * row['avg_win_rate']         +
        0.15 * (row['avg_pf'] / PF_CAP)   * 100
    )


# ══════════════════════════════════════════════════════════════════════════════
# AGGREGATION HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def aggregate(df: pd.DataFrame, group_cols: list, n_batches: int) -> pd.DataFrame:
    """
    Groups df by group_cols and computes consistency metrics across batches.
    """
    g = df.groupby(group_cols)

    agg = pd.DataFrame({
        'batch_count':      g['batch_no'].nunique(),
        'total_model_runs': g['batch_no'].count(),
        'avg_trades':       g['number_of_trades'].mean().round(1),
        'avg_win_rate':     g['win_rate'].mean().round(2),
        'avg_total_pnl':    g['total_pnl'].mean().round(2),
        'pnl_std':          g['total_pnl'].std().round(2),
        'avg_sharpe':       g['sharpe'].mean().round(3),
        'avg_pf':           g['profit_factor_capped'].mean().round(3),
        'pnl_hit_rate':     g['total_pnl'].apply(lambda s: (s > 0).mean()).round(4),
        'sharpe_hit_rate':  g['sharpe'].apply(lambda s: (s > 0).mean()).round(4),
    }).reset_index()

    agg['appearance_rate'] = (agg['batch_count'] / n_batches).round(4)
    agg['consistency_score'] = agg.apply(_score, axis=1).round(2)

    agg = agg[agg['batch_count'] >= MIN_BATCHES]
    agg = agg.sort_values('consistency_score', ascending=False).reset_index(drop=True)
    agg.insert(0, 'rank', range(1, len(agg) + 1))
    return agg


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSIS FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def analyse_full_models(df: pd.DataFrame, n_batches: int) -> pd.DataFrame:
    return aggregate(df, CATEGORIES, n_batches)


def analyse_category(df: pd.DataFrame, n_batches: int, category: str) -> pd.DataFrame:
    return aggregate(df, [category], n_batches)


def analyse_pair(df: pd.DataFrame, n_batches: int, cat_a: str, cat_b: str) -> pd.DataFrame:
    return aggregate(df, [cat_a, cat_b], n_batches)


# ══════════════════════════════════════════════════════════════════════════════
# MARKDOWN REPORT
# ══════════════════════════════════════════════════════════════════════════════

def _md_table(df: pd.DataFrame, n: int = TOP_N) -> str:
    sub = df.head(n)
    header = '| ' + ' | '.join(str(c) for c in sub.columns) + ' |'
    sep    = '| ' + ' | '.join(['---'] * len(sub.columns)) + ' |'
    rows   = []
    for _, row in sub.iterrows():
        cells = []
        for v in row.values:
            if isinstance(v, float):
                cells.append(f'{v:.3f}' if abs(v) < 1000 else f'{v:,.0f}')
            else:
                cells.append(str(v))
        rows.append('| ' + ' | '.join(cells) + ' |')
    return '\n'.join([header, sep] + rows)


def write_markdown_report(
    models_df:      pd.DataFrame,
    category_dfs:   dict,
    pair_dfs:       dict,
    n_batches:      int,
    total_rows:     int,
) -> Path:
    now  = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    path = REPORTS_DIR / 'batch_run_analysis.md'

    lines = []
    lines.append('# Batch Run Analysis — Indicator Consistency Report')
    lines.append(f'\n_Generated: {now}_\n')
    lines.append('## Overview\n')
    lines.append('| Metric | Value |')
    lines.append('|---|---|')
    lines.append(f'| Batches analysed       | {n_batches} |')
    lines.append(f'| Total model-runs       | {total_rows:,} |')
    lines.append(f'| Unique models (4-tuple)| {len(models_df):,} |')
    lines.append(f'| Min batches threshold  | {MIN_BATCHES} |')
    lines.append(f'| Profit factor cap      | {PF_CAP} |')
    lines.append('')

    lines.append('### Consistency Score Formula\n')
    lines.append('```')
    lines.append('score = 0.40 × pnl_hit_rate')
    lines.append('      + 0.25 × sharpe_hit_rate')
    lines.append('      + 0.20 × avg_win_rate / 100')
    lines.append('      + 0.15 × avg_profit_factor / 10')
    lines.append('(all terms normalised to 0–1, score reported 0–100)')
    lines.append('```\n')

    # Full model top/bottom
    display_model_cols = [
        'rank', 'trend', 'momentum', 'volatility', 'volume',
        'batch_count', 'avg_trades', 'avg_win_rate', 'avg_total_pnl',
        'pnl_hit_rate', 'avg_sharpe', 'avg_pf', 'consistency_score',
    ]
    lines.append(f'## Top {TOP_N} Full Model Combinations\n')
    lines.append(_md_table(models_df[display_model_cols]))
    lines.append('')

    bottom = models_df[display_model_cols].tail(TOP_N).iloc[::-1].copy()
    bottom['rank'] = range(1, len(bottom) + 1)
    lines.append(f'## Bottom {TOP_N} Full Model Combinations\n')
    lines.append(_md_table(bottom))
    lines.append('')

    # Individual categories
    for cat, cdf in category_dfs.items():
        display_cols = [
            'rank', cat, 'batch_count', 'avg_trades', 'avg_win_rate',
            'avg_total_pnl', 'pnl_hit_rate', 'avg_sharpe', 'consistency_score',
        ]
        lines.append(f'## {cat.capitalize()} Indicator Rankings\n')
        lines.append(_md_table(cdf[display_cols], n=len(cdf)))
        lines.append('')

    # Pairs
    for (cat_a, cat_b), pdf in pair_dfs.items():
        display_cols = [
            'rank', cat_a, cat_b, 'batch_count', 'avg_trades',
            'avg_win_rate', 'avg_total_pnl', 'pnl_hit_rate',
            'avg_sharpe', 'consistency_score',
        ]
        lines.append(f'## Pair Rankings: {cat_a.capitalize()} × {cat_b.capitalize()}\n')
        lines.append(_md_table(pdf[display_cols]))
        lines.append('')

    path.write_text('\n'.join(lines), encoding='utf-8')
    print(f"[report]  Markdown  -> {path}")
    return path


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print(f"\n{'='*60}")
    print("  Batch Run Analysis")
    print(f"{'='*60}\n")

    df, n_batches = load_all_summaries()

    # -- Full model combinations -------------------------------------------
    print("[analyse] Full model combinations ...")
    models_df = analyse_full_models(df, n_batches)
    models_df.to_csv(REPORTS_DIR / 'analysis_models.csv', index=False)
    print(f"          {len(models_df):,} models ranked  ->  analysis_models.csv")

    # -- Individual categories ---------------------------------------------
    category_dfs = {}
    for cat in CATEGORIES:
        print(f"[analyse] {cat} indicators ...")
        cdf = analyse_category(df, n_batches, cat)
        cdf.to_csv(REPORTS_DIR / f'analysis_{cat}.csv', index=False)
        print(f"          {len(cdf)} indicators  ->  analysis_{cat}.csv")
        category_dfs[cat] = cdf

    # -- Pairwise combinations ---------------------------------------------
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
        pdf.to_csv(REPORTS_DIR / out_name, index=False)
        print(f"          {len(pdf)} pairs ranked  ->  {out_name}")
        pair_dfs[(cat_a, cat_b)] = pdf

    # -- Markdown report ---------------------------------------------------
    print("\n[report]  Writing summary ...")
    write_markdown_report(models_df, category_dfs, pair_dfs, n_batches, len(df))

    # -- Console highlights ------------------------------------------------
    print(f"\n{'='*60}")
    print("  TOP 5 CONSISTENT MODELS")
    print(f"{'='*60}")
    top5 = models_df.head(5)
    for _, r in top5.iterrows():
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


if __name__ == '__main__':
    main()
