# all_runs.csv — Cross-Run Reference

`reports/all_runs.csv` is an **append-only** log.  Every time `all_models.py`
completes, one row per active variant is appended.  Use it to track how
variant performance shifts across runs with different parameters (TV gates,
date ranges, seeds).

---

## Schema

| Column | Type | Description |
|---|---|---|
| `run_ts` | str | Run directory timestamp (`mmddHHMM`), e.g. `04190003` |
| `cc_tv_min` | float | Minimum opening time value gate used in this run |
| `cc_tv_max` | float | Maximum opening time value gate used in this run |
| `buyback_tv` | float | Buyback threshold (ask-side TV) used in this run |
| `expiry_label` | str | `w0` / `w1` / `w2` |
| `strike_label` | str | `s-2` / `s-1` / `s-0` / `s+0` / `s+1` / `s+2` |
| `avg_stock_pnl` | float | Trade-count-weighted mean of per-window `avg_stock_pnl` |
| `avg_option_pnl` | float | Trade-count-weighted mean of per-window `avg_option_pnl` |
| `avg_total_pnl` | float | Trade-count-weighted mean of per-window `avg_pnl` (per-trade average within each window, then weighted) |
| `avg_cc_tv` | float | Trade-count-weighted mean of `avg_cc_tv_at_entry` |
| `trade_count` | int | Total accepted trades across all windows |
| `win_pct` | float | Trade-count-weighted mean win rate (%) |
| `total_pnl` | float | Sum of `total_pnl` across all windows |
| `avg_per_trade_pnl` | float | `total_pnl / trade_count` — overall $/trade for the run |
| `sharpe` | float | Mean of per-window Sharpe ratios |
| `profit_factor` | float | Mean PF across windows; sentinels (≥ 1e8 = no-loss windows) excluded; `inf` if every window was lossless |
| `max_drawdown` | float | Minimum (worst) per-window max drawdown |
| `avg_bars_held` | float | Trade-count-weighted mean of `avg_bars_held` |
| `consistency_score` | float | Blended 0–100 score (see formula below) |

---

## Consistency score formula

```
score = 0.25 × pnl_hit_rate                            (fraction of windows with total_pnl > 0)
      + 0.15 × sharpe_hit_rate                         (fraction of windows with sharpe > 0)
      + 0.15 × avg_win_rate / 100
      + 0.15 × avg_pf / (avg_pf + 5)                   (saturation, PF uncapped)
      + 0.30 × (tanh(avg_per_trade_pnl / 30) + 1) / 2  (profitability term)
(all terms 0–1, score reported 0–100)
```

Constants `PROF_K = 30` and `PF_SAT_K = 5` are tunable in `all_models.py`.

---

## How to use for cross-run comparison

```python
import pandas as pd

df = pd.read_csv('reports/all_runs.csv')

# Compare the same variant across runs with different TV gates
df[df['expiry_label'] == 'w1'][['run_ts','cc_tv_min','cc_tv_max','strike_label',
                                 'total_pnl','avg_per_trade_pnl','consistency_score']]

# Best variant per run
df.loc[df.groupby('run_ts')['consistency_score'].idxmax()]

# Effect of buyback_tv on profitability
df.groupby('buyback_tv')[['total_pnl','avg_per_trade_pnl','win_pct']].mean()
```

---

## Key distinctions

| Column | What it measures |
|---|---|
| `avg_total_pnl` | Per-window mean of that window's average per-trade P&L (weighted by trade count across windows) |
| `avg_per_trade_pnl` | `total_pnl / trade_count` — a single $/trade figure for the entire run |

These can differ when window sizes vary significantly.  `avg_per_trade_pnl`
is what the score formula uses.

---

## Notes

- Rows are **appended**, never updated.  Re-running with the same seed
  produces a second set of rows with the same `run_ts` prefix digit — use
  `run_ts` to distinguish runs.
- Variants with zero accepted trades across all windows are excluded.
- `profit_factor = inf` means every window in that run had no losing trades;
  the saturation term in the score maps this to 1 (maximum).
- `max_drawdown` is the worst single-window drawdown, not a run-level
  portfolio drawdown.
