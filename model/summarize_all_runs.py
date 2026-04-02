"""
summarize_all_runs.py

Reads all {seq_no}_summary.csv files from a timestamped run subdirectory under
../reports/ and rates indicator combinations by consistency across those runs.

Usage
─────
  python summarize_all_runs.py                     # prompts for timestamp
  python summarize_all_runs.py 04011423            # positional argument
  python summarize_all_runs.py --dir 04011423      # named argument

Analysis levels
───────────────
1. Full model  (trend + momentum + volatility + volume)
2. Individual  indicator contribution per category
3. Pairwise    category interactions  (e.g. trend × momentum)

Output  (written into the same run subdirectory)
──────
  {run_dir}/analysis_models.csv
  {run_dir}/analysis_{trend,momentum,volatility,volume}.csv
  {run_dir}/analysis_pair_{cat_a}_{cat_b}.csv  (6 pairs)
  {run_dir}/batch_run_analysis.md
"""

from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

from batch_run_utils import (
    REPORTS_DIR, TOP_N, CATEGORIES, PF_CAP, MIN_BATCHES,
    load_all_summaries, analyse_full_models, analyse_category, analyse_pair,
    md_table,
)


# ══════════════════════════════════════════════════════════════════════════════
# ARGUMENT / PROMPT
# ══════════════════════════════════════════════════════════════════════════════

def resolve_run_dir() -> Path:
    """
    Returns the Path of the run subdirectory to analyse.
    Source priority: positional arg → --dir flag → interactive prompt.
    """
    parser = argparse.ArgumentParser(
        description='Analyse a timestamped batch-run subdirectory.'
    )
    parser.add_argument(
        'timestamp', nargs='?', default=None,
        help='Run subdirectory name, e.g. 04011423  (mmddhhmi)'
    )
    parser.add_argument(
        '--dir', dest='dir_flag', default=None,
        help='Same as the positional argument'
    )
    args = parser.parse_args()

    ts = args.timestamp or args.dir_flag

    if ts is None:
        # List available subdirectories to help the user
        subdirs = sorted(
            [p.name for p in REPORTS_DIR.iterdir()
             if p.is_dir() and list(p.glob('*_summary.csv'))],
            reverse=True,
        )
        if subdirs:
            print('\nAvailable run directories:')
            for d in subdirs:
                print(f'  {d}')
        ts = input('\nEnter run timestamp (mmddhhmi): ').strip()

    run_dir = REPORTS_DIR / ts
    if not run_dir.is_dir():
        sys.exit(f"ERROR: directory not found: {run_dir}")
    return run_dir


# ══════════════════════════════════════════════════════════════════════════════
# MARKDOWN REPORT
# ══════════════════════════════════════════════════════════════════════════════

def write_markdown_report(
    models_df:    'pd.DataFrame',
    category_dfs: dict,
    pair_dfs:     dict,
    n_batches:    int,
    total_rows:   int,
    output_dir:   Path,
) -> Path:
    import pandas as pd  # noqa: F401 – type hint only above

    now  = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    path = output_dir / 'batch_run_analysis.md'

    lines = []
    lines.append('# Batch Run Analysis — Indicator Consistency Report')
    lines.append(f'\n_Generated: {now}_\n')
    lines.append('## Overview\n')
    lines.append('| Metric | Value |')
    lines.append('|---|---|')
    lines.append(f'| Run directory           | {output_dir.name} |')
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
    return path


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main(run_dir: Path | None = None):
    if run_dir is None:
        run_dir = resolve_run_dir()

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


if __name__ == '__main__':
    main()
