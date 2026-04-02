"""
batch_run_utils.py

Shared constants, scoring, aggregation, and formatting utilities used by
batch_run_all_models.py and batch_run_single_model.py.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

REPORTS_DIR = Path(__file__).parent.parent / 'reports'
PF_CAP      = 10.0   # cap profit_factor before averaging (suppress inf)
MIN_BATCHES = 5      # minimum run appearances to be included in rankings
TOP_N       = 20     # rows shown in each report section
CATEGORIES  = ['trend', 'momentum', 'volatility', 'volume']


# ══════════════════════════════════════════════════════════════════════════════
# LOAD
# ══════════════════════════════════════════════════════════════════════════════

def load_all_summaries(
    reports_dir: Path = REPORTS_DIR,
    run_dir:     Path | None = None,
) -> tuple[pd.DataFrame, int]:
    """
    Reads summary CSVs and returns (combined_df, n_runs).
    - run_dir supplied  → loads only {seq_no}_summary.csv files from that subdirectory
    - run_dir omitted   → loads all */*_summary.csv across every subdirectory
    Adds profit_factor_capped column.
    """
    if run_dir is not None:
        files = sorted(
            run_dir.glob('*_summary.csv'),
            key=lambda p: int(p.stem.split('_')[0]),
        )
        if not files:
            raise FileNotFoundError(f"No *_summary.csv files found in {run_dir}")
    else:
        files = sorted(
            reports_dir.glob('*/*_summary.csv'),
            key=lambda p: (p.parent.name, int(p.stem.split('_')[0])),
        )
        if not files:
            raise FileNotFoundError(f"No *_summary.csv files found in {reports_dir}")

    combined = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    combined['profit_factor_capped'] = combined['profit_factor'].clip(upper=PF_CAP)
    n_batches = len(files)   # each file = one unique (execution, seq_no) run
    print(f"[load]  {len(files)} files  |  {n_batches} runs  |  {len(combined):,} rows")
    return combined, n_batches


# ══════════════════════════════════════════════════════════════════════════════
# SCORING
# ══════════════════════════════════════════════════════════════════════════════

def score(row: pd.Series) -> float:
    """
    Composite consistency score (0–100).
    Favours models that are reliably good, not just occasionally great.

      40% – pnl_hit_rate     (are we profitable more often than not?)
      25% – sharpe_hit_rate  (risk-adjusted reliability)
      20% – avg_win_rate
      15% – avg_pf / PF_CAP  (capped profit factor)
    """
    return (
        0.40 * row['pnl_hit_rate']    * 100 +
        0.25 * row['sharpe_hit_rate'] * 100 +
        0.20 * row['avg_win_rate']          +
        0.15 * (row['avg_pf'] / PF_CAP)    * 100
    )


# ══════════════════════════════════════════════════════════════════════════════
# AGGREGATION
# ══════════════════════════════════════════════════════════════════════════════

def aggregate(
    df:         pd.DataFrame,
    group_cols: list[str],
    n_batches:  int,
    min_count:  int = MIN_BATCHES,
) -> pd.DataFrame:
    """
    Groups df by group_cols, computes consistency metrics across batches/runs,
    applies min_count filter, sorts by consistency_score descending.
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

    agg['appearance_rate']   = (agg['batch_count'] / n_batches).round(4)
    agg['consistency_score'] = agg.apply(score, axis=1).round(2)

    agg = agg[agg['batch_count'] >= min_count]
    agg = agg.sort_values('consistency_score', ascending=False).reset_index(drop=True)
    agg.insert(0, 'rank', range(1, len(agg) + 1))
    return agg


def analyse_full_models(df: pd.DataFrame, n_batches: int) -> pd.DataFrame:
    return aggregate(df, CATEGORIES, n_batches)


def analyse_category(df: pd.DataFrame, n_batches: int, category: str) -> pd.DataFrame:
    return aggregate(df, [category], n_batches)


def analyse_pair(
    df: pd.DataFrame, n_batches: int, cat_a: str, cat_b: str
) -> pd.DataFrame:
    return aggregate(df, [cat_a, cat_b], n_batches)


# ══════════════════════════════════════════════════════════════════════════════
# FORMATTING
# ══════════════════════════════════════════════════════════════════════════════

def md_table(df: pd.DataFrame, n: int = TOP_N) -> str:
    """Convert the first n rows of a DataFrame to a GitHub-flavoured Markdown table."""
    sub    = df.head(n)
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
