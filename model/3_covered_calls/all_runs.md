# Column Summary table

| Column | Notes|
| - | - |
| run_ts   | Batch run identifier (e.g. 04131530) — distinguishes runs |
| model_id | 1–1221, deterministic (same combo = same ID every run) |
| model_detail   | ema_rsi_atr_vwap format  |
| variant  | w0/s+1 … w2/s-2 |
| avg_stock_pnl  | Weighted-avg per-trade stock P&L  |
| avg_option_pnl | Weighted-avg per-trade option P&L   |
| avg_total_pnl  | Weighted-avg combined P&L per trade   |
| trade_count | Total trades across all windows   |
| win_pct  | Weighted-avg win rate   |
| total_pnl   | Sum across all windows |
| sharpe   | Mean Sharpe across windows  |
| profit_factor  | Mean profit factor (capped)   |
| max_drawdown   | Worst drawdown across windows   |
| avg_bars_held  | Weighted-avg bars held per trade  |
| consistency_score | Same formula as analysis_combos.csv — directly comparable |


