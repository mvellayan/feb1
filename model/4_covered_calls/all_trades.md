# Output Reference — 4_covered_calls

All output lands in `reports/{mmddhhmi}/` for the current run and in
`reports/all_runs.csv` for cross-run tracking.

---

## Directory layout

```
reports/
├── all_runs.csv                   # appended across every run
└── {mmddhhmi}/
    ├── run_params.json            # every CLI arg, serialised
    ├── trades.csv                 # one row per accepted trade
    ├── summary.csv                # one row per variant per window
    ├── analysis_variants.csv      # one row per variant, ranked
    ├── batch_run_analysis.md      # human-readable params + rankings + funnel
    └── {n}_run.log                # per-window trade-by-trade log (n = window seq)
```

Per-window `{n}_summary.csv` and `{n}_trades.csv` are written during execution
and **merged** into the top-level files after all windows complete; the
per-window files are then deleted.

---

## trades.csv

One row per accepted (variant × entry).

| Column | Type | Description |
|---|---|---|
| `batch_no` | int | Window sequence number (1 … N_WINDOWS) |
| `trade_no` | int | Sequential trade number within the window |
| `expiry_label` | str | `w0` / `w1` / `w2` |
| `strike_label` | str | `s-2` … `s+2` |
| `strike` | float | Resolved strike price from the option chain |
| `expiry_date` | date | Friday expiry date |
| `entry_time` | datetime | Bar timestamp when position was opened |
| `exit_time` | datetime | Bar timestamp when position was closed |
| `entry_stock_price` | float | `avg_ask` at entry — the price paid for stock |
| `stock_exit_price` | float | Price realised on the stock leg at exit |
| `cc_open_price` | float | Option bid received when the call was sold |
| `cc_tv` | float | Opening time value: `cc_open_price − max(0, entry_stock_price − strike)` |
| `cc_open_days` | float | Calendar days from entry to 16:00 on `expiry_date` |
| `cc_close_price` | float | Ask paid on buyback; 0 on assigned / expired_otm |
| `exit_reason` | str | `buyback` / `assigned` / `expired_otm` / `window_end` / `buyback_late_data` |
| `bars_held` | int | 1-minute bars from entry to exit |
| `days_held` | float | `bars_held / 390` |
| `shares` | int | Always 100 |
| `stock_pnl` | float | `shares × (exit − entry) − COMMISSION` |
| `option_pnl` | float | `(cc_open_price − cc_close_price) × shares − COMMISSION` |
| `combined_pnl` | float | `stock_pnl + option_pnl` (net of both commissions) |

### exit_reason values

| Value | Meaning |
|---|---|
| `buyback` | Option TV fell below `buyback_tv` threshold |
| `assigned` | Expired ITM (`avg_bid > strike`) — stock sold at strike |
| `expired_otm` | Expired OTM — stock sold at `avg_bid` |
| `window_end` | Neither buyback nor expiry fired before window data ran out |
| `buyback_late_data` | No intra-bar buyback, but last available ask would have triggered it |

---

## summary.csv

One row per variant per window.  Funnel counters and window-level metrics.

| Column | Description |
|---|---|
| `batch_no` | Window sequence number |
| `expiry_label` | `w0` / `w1` / `w2` |
| `strike_label` | `s-2` … `s+2` |
| `variant_key` | `{expiry_label}/{strike_label}` |
| `number_of_trades` | Accepted entries in this window |
| `win_rate` | % of trades with `combined_pnl > 0` |
| `avg_entry_price` | Mean `avg_ask` at entry |
| `avg_exit_price` | Mean stock exit price |
| `avg_bars_held` | Mean bars held per trade |
| `avg_cc_tv_at_entry` | Mean opening time value |
| `total_pnl` | Sum of `combined_pnl` for the window |
| `avg_pnl` | `total_pnl / number_of_trades` |
| `profit_factor` | `gross_wins / abs(gross_losses)`; `inf` if no losing trades |
| `sharpe` | Annualised Sharpe on daily P&L (`√252 × mean / std`) |
| `max_drawdown` | Peak-to-trough drawdown of cumulative P&L |
| `avg_stock_pnl` | Mean per-trade stock leg P&L |
| `avg_option_pnl` | Mean per-trade option leg P&L |
| `draws` | Draws where this variant had a valid quote |
| `no_quote` | Draws rejected: option quote stale or missing |
| `tv_fail_low` | Draws rejected: `cc_tv < cc_tv_min` |
| `tv_fail_high` | Draws rejected: `cc_tv > cc_tv_max` |
| `cooldown_skip` | Draws rejected: within 60-min cooldown |
| `accepted` | Draws accepted (= `number_of_trades`) |
| `batch_full_pct` | `accepted / batch_size × 100` |
| `pnl_positive` | `True` if `total_pnl > 0` |
| `status` | `ok` or `no_trades` |

---

## analysis_variants.csv

One row per variant, sorted by `consistency_score` descending.
Aggregates all windows for the run.

| Column | Description |
|---|---|
| `run_ts` | Run directory timestamp, e.g. `04190003` |
| `rank` | 1 = best |
| `variant_key` | `{expiry_label}/{strike_label}` |
| `expiry_label` | `w0` / `w1` / `w2` |
| `strike_label` | `s-2` … `s+2` |
| `batch_count` | Number of windows the variant appeared in |
| `total_trades` | Sum of trades across all windows |
| `avg_trades` | Mean trades per window |
| `avg_cc_tv` | Mean opening time value across windows |
| `avg_win_rate` | Mean per-window win rate (%) |
| `avg_total_pnl` | Per-window mean of `total_pnl` (not per-trade) |
| `avg_per_trade_pnl` | `sum(total_pnl) / sum(trades)` across all windows |
| `avg_days_held` | Weighted average of `avg_bars_held / 390` by trade count |
| `total_pnl` | Sum of `total_pnl` across all windows |
| `pnl_hit_rate` | Fraction of windows with `total_pnl > 0` |
| `avg_sharpe` | Mean per-window Sharpe |
| `avg_pf` | Mean profit factor (sentinels ≥ 1e8 excluded; `inf` if all windows lossless) |
| `consistency_score` | Blended 0–100 score (see formula below) |

**Note:** `avg_total_pnl` and `avg_per_trade_pnl` are different numbers.
`avg_total_pnl` is the per-window mean dollar P&L; `avg_per_trade_pnl` is
total dollars divided by total trades across all windows.

### Consistency score formula

```
score = 0.25 × pnl_hit_rate
      + 0.15 × sharpe_hit_rate
      + 0.15 × avg_win_rate / 100
      + 0.15 × avg_pf / (avg_pf + 5)              # saturation, PF uncapped
      + 0.30 × (tanh(avg_per_trade_pnl / 30) + 1) / 2
(all terms 0–1, score reported 0–100)
```

---

## all_runs.csv

Appended on every run.  One row per variant per run for long-term tracking.

| Column | Description |
|---|---|
| `run_ts` | Run directory timestamp, e.g. `04190003` |
| `cc_tv_min` | Minimum opening TV gate used in this run |
| `cc_tv_max` | Maximum opening TV gate used in this run |
| `buyback_tv` | Buyback threshold used in this run |
| `expiry_label` | `w0` / `w1` / `w2` |
| `strike_label` | `s-2` … `s+2` |
| `avg_stock_pnl` | Trade-count-weighted mean of per-window `avg_stock_pnl` |
| `avg_option_pnl` | Trade-count-weighted mean of per-window `avg_option_pnl` |
| `avg_total_pnl` | Trade-count-weighted mean of per-window `avg_pnl` |
| `avg_cc_tv` | Trade-count-weighted mean of `avg_cc_tv_at_entry` |
| `trade_count` | Total trades across all windows |
| `win_pct` | Trade-count-weighted mean win rate (%) |
| `total_pnl` | Sum of `total_pnl` across all windows |
| `avg_per_trade_pnl` | `total_pnl / trade_count` |
| `sharpe` | Mean per-window Sharpe |
| `profit_factor` | Mean PF (sentinels excluded; `inf` if all windows lossless) |
| `max_drawdown` | Minimum (worst) per-window max drawdown |
| `avg_bars_held` | Trade-count-weighted mean of `avg_bars_held` |
| `consistency_score` | Same 5-term formula as `analysis_variants.csv` |

---

## batch_run_analysis.md

Human-readable report written once per run.  Contains:

1. **Run Parameters** — every CLI flag and constant (seed, windows, date range,
   sample/batch sizes, TV gates, expiry/strike selection, commission, etc.)
2. **Score Formula** — the 5-term blended consistency + profitability formula
3. **Variant Rankings** — same data as `analysis_variants.csv` in a markdown table
4. **Entry Funnel** — aggregate draws / no_quote / tv_fail / cooldown / accepted
   per variant across all windows, with an accept-rate column

---

## {n}_run.log

One file per window.  Human-readable trade-by-trade log.

- Header: window date range, seed, sample/batch sizes, TV gates
- For each shared draw that opened ≥ 1 position: a block showing entry
  snapshot (ATR, RSI, ADX, VWAP) followed by a table row per variant with
  strike, expiry, open bid, `cc_tv`, exit time, exit reason, and P&L
  (stock / option / combined)

---

## run_params.json

JSON object with every CLI argument and derived constant for the run.
Keys: `run_ts`, `random_seed`, `n_workers`, `n_windows`, `window_days`,
`date_first`, `date_last`, `sample_size`, `batch_size`, `cc_tv_min`,
`cc_tv_max`, `buyback_tv`, `cooldown_min`, `expiry_labels`, `strike_labels`,
`n_variants`, `commission`, `shares`, `argv`.
