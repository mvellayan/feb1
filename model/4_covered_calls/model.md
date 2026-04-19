# Model 4: Random-Entry Covered Calls

## Overview

This model **sells covered calls at random entry points** and evaluates the result per
(expiry, strike) variant. There is **no indicator signal** — entries are drawn at
random from the eligible bar universe and gated only by the opening time value of the
sold call.

The goal is to isolate the effect of *when* (calendar timing and volatility regime)
and *which variant* (expiry × strike) from any edge a technical entry signal would
supply. Everything that makes money here is attributable to the option premium and
the strike/expiry choice.

**Strategy Type:** Random-entry income overlay
**Asset:** AAPL
**Timeframe:** 1-minute bars
**Position Size:** 100 shares per entry
**Commission:** $2.00 per round-trip (stock + option pair)

---

## Entry

No indicator composite. For each window the model:

1. Filters bars to the eligible universe:
   - `ses_after_10 == 1`
   - `ses_before_345 == 1`
   - `atr_spike == 0`
   - `avg_ask` and `avg_bid` are valid
2. Shuffles the eligible indices with a per-window seed derived from the master seed.
3. Walks the shuffled list, proposing one random entry timestamp at a time.

For every proposed entry timestamp, each active **(expiry_label, strike_label)**
variant is independently evaluated:

```
cc_tv = option_bid(entry_ts) − max(0, avg_ask(entry_ts) − strike)
```

A variant opens a position iff all three are true:

| Gate | Rule |
|---|---|
| Quote freshness | Option has a valid `avg_bid` within `MAX_QUOTE_AGE_MINUTES` (30) |
| TV range | `cc_tv_min ≤ cc_tv ≤ cc_tv_max` |
| Cooldown | At least `COOLDOWN_MINUTES` (60) have elapsed since this variant's last accepted entry |

The candidate timestamp is **shared** across all variants. One random draw can open
positions in 0, 1, or many variants.

### Entry Snapshot

At entry, `atr_14`, `rsi_14`, `adx_14`, and `vwp_vwap` are captured for post-hoc
analysis. These do not affect the trade decision.

---

## Exit

Each open position is closed by whichever rule fires first on the forward walk:

| Priority | Rule | cc_close_reason |
|---|---|---|
| 1 | `option_ask(bar) − max(0, avg_bid(bar) − strike) < buyback_tv` | `buyback` |
| 2 | Bar at or after `expiry_date + 15:00` with `avg_bid > strike` | `assigned` (stock sold at strike) |
| 2 | Bar at or after `expiry_date + 15:00` with `avg_bid ≤ strike` | `expired_otm` (stock sold at avg_bid) |
| 3 | Neither fired before data ran out | `window_end` |

A late-data recheck on the final bar may emit `buyback_late_data` if the last
available ask would have triggered buyback.

**Note:** the buyback rule is **time-value based**, not dollar-based. A dollar-cheap
call still has meaningful TV if it's far OTM; a dollar-expensive call can be almost
pure intrinsic. The TV test keeps the buyback semantically consistent across strikes.

---

## The 18-Variant Matrix

3 expiry weeks × 6 strike labels = 18 variants (when all are selected).

### Expiry Labels

| Label | Expiry |
|---|---|
| `w0` | Friday of the entry week |
| `w1` | Friday of the following week |
| `w2` | Friday two weeks after the entry week |

### Strike Labels

Strikes are selected by walking the actual option chain — not by `floor(price) ± N`:

| Label | Position in Chain |
|---|---|
| `s-2` | Third strike below entry price |
| `s-1` | Second strike below |
| `s-0` | First strike below |
| `s+0` | First strike above |
| `s+1` | Second strike above |
| `s+2` | Third strike above |

Both `--expiry-label` and `--strike-label` accept one or more values. Omit either
to select all of that dimension. Examples:

```bash
# Default — full 18-way matrix
python all_models.py

# Just w1 and w2, all six strikes = 12 variants
python all_models.py --expiry-label w1 w2

# Single variant
python all_models.py --expiry-label w0 --strike-label s+0
```

---

## Sample / Batch Budget

Two independent caps control how much a window runs:

**`--sample_size`** (default 10,000): total random draws per window. A draw counts
toward this budget iff at least one active variant has a valid option quote at that
timestamp. Stale-data draws are free.

**`--batch_size`** (default 1,000): per-variant cap on accepted entries. A variant
whose batch is full is skipped on further draws.

The window ends when **either** sample_size is exhausted **or** every variant has
filled its batch.

### Per-Variant Funnel

The summary CSV records, per variant per window:

| Counter | Meaning |
|---|---|
| `draws` | Draws where this variant had a valid quote (sum of all below) |
| `no_quote` | Valid-quote gate failed |
| `tv_fail_low` | `cc_tv < cc_tv_min` |
| `tv_fail_high` | `cc_tv > cc_tv_max` |
| `cooldown_skip` | Inside the 60-minute cooldown window |
| `accepted` | Position opened |
| `batch_full_pct` | `accepted / batch_size` |

---

## CLI Flags

```
--seed SEED              Master RNG seed (omit for a fresh random seed each run)
--workers WORKERS        Parallel worker processes (default: cpu_count − 1)
--data-first DATE        Start of data range      (default: 2023-01-01)
--data-last  DATE        End of data range        (default: 2026-02-28)
--window-days N          Calendar days per window (default: 14)
--windows N              Number of windows        (default: 100)
--sample_size N          Random quotes tested per window      (default: 10000)
--batch_size  N          Max accepted trades per variant/win  (default: 1000)
--buyback_tv VAL         Buyback threshold on ask-side TV     (default: 0.25)
--cc_tv_min  VAL         Minimum opening TV to open           (default: 1.00)
--cc_tv_max  VAL         Maximum opening TV to open           (default: 3.00)
--expiry-label w0 w1 …   One or more expiry labels (default: all three)
--strike-label s+0 s-0 … One or more strike labels (default: all six)
```

---

## File Structure

```
model/4_covered_calls/
├── __init__.py
├── all_models.py
├── model.md                       # this file
└── reports/
    ├── all_runs.csv               # one row per variant per run (appended)
    └── {mmddhhmi}/
        ├── run_params.json
        ├── summary.csv            # consolidated per-window summary rows
        ├── trades.csv             # consolidated per-trade rows
        ├── 1_run.log … N_run.log  # per-window trade-by-trade log
        ├── analysis_variants.csv  # variants ranked by consistency score
        └── batch_run_analysis.md
```

Per-window `{n}_summary.csv` and `{n}_trades.csv` are written during the run and
then **consolidated** into the top-level `summary.csv` and `trades.csv` (the
individual files are deleted after merge).

---

## trades.csv Schema

One row per accepted (variant × entry).

| Column | Description |
|---|---|
| `batch_no` | Window sequence number (1..N_WINDOWS) |
| `trade_no` | Sequential trade number within the window |
| `expiry_label`, `strike_label` | Variant identifiers |
| `strike`, `expiry_date` | Resolved option chain values |
| `entry_time`, `exit_time` | Timestamps |
| `entry_stock_price` | `avg_ask` at entry |
| `stock_exit_price` | Stock price used to realise stock leg |
| `cc_open_price` | Sold-call bid at entry |
| **`cc_tv`** | `cc_open_price − max(0, entry_stock_price − strike)` |
| `cc_open_days` | Calendar days from entry to 16:00 on expiry_date |
| `cc_close_price` | Ask paid on buyback, 0 on assigned/expired_otm |
| `exit_reason` | `buyback` / `assigned` / `expired_otm` / `window_end` / `buyback_late_data` |
| `bars_held`, `days_held` | Duration |
| `shares` | 100 |
| `stock_pnl`, `option_pnl`, `combined_pnl` | Dollar P&L (combined is net of commission) |

Dropped vs. Model 3: `model_id`, `trend`, `momentum`, `volatility`, `volume`.
Added: `cc_tv` (positioned right after `cc_open_price`).

---

## Reports

### `analysis_variants.csv` (per run)

One row per variant with `consistency_score` ranking across all windows.
The score blends **consistency** (70%) and **profitability** (30%):

```
score = 0.25 × pnl_hit_rate                            (windows with total_pnl > 0)
      + 0.15 × sharpe_hit_rate                         (windows with sharpe > 0)
      + 0.15 × avg_win_rate / 100
      + 0.15 × avg_pf / (avg_pf + 5)                   (saturation, PF uncapped)
      + 0.30 × (tanh(avg_per_trade_pnl / 30) + 1) / 2  (profitability term)
(all terms bounded to 0–1, score reported 0–100)
```

- `avg_pf` reports the raw mean profit factor. Windows with zero losses
  (sentinel PF `≥ 1e8`) are excluded from the mean; if every window is a
  sentinel the column shows `inf` and the saturated term in the score
  evaluates to 1.
- `avg_per_trade_pnl` = `total_pnl / total_trades` across all windows.
- `total_pnl` = sum of per-window `total_pnl`.
- `avg_total_pnl` is the **per-window** mean of `total_pnl` (not per-trade —
  distinct from `avg_per_trade_pnl`).

### `batch_run_analysis.md` (per run)

Contains:
- Run parameters block (every CLI arg)
- Variant rankings table
- **Entry funnel** — aggregate draws / no_quote / tv_fail / cooldown / accepted per variant across all windows

### `all_runs.csv` (cross-run, appended)

One row per (`run_ts`, variant) for long-term tracking. Columns include trade count,
weighted-avg P&L, `avg_per_trade_pnl`, avg cc_tv, Sharpe, profit factor (uncapped),
max drawdown, and `consistency_score` (same 5-term formula as above).

---

## Execution

```bash
cd model/4_covered_calls
python all_models.py [flags]
```

First invocation loads the 346K-row extended CSV and the option index into memory
in the parent process before forking workers. Each worker builds its own option-data
cache on demand.

---

## Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Entry logic | Random draw, no indicators | Isolate variant and regime effects from signal edge |
| Time value for entry gate | `bid − max(0, avg_ask − strike)` | Measures what premium the short call actually offers |
| Time value for buyback gate | `ask − max(0, avg_bid − strike)` | Measures the real cost to close, net of intrinsic |
| Cooldown | 60 min between accepted entries | Avoids clustering entries on a single anomaly moment |
| Shared candidate draw | One timestamp tested against all variants | Keeps variants directly comparable on the same market moments |
| Per-variant batch cap | `batch_size` per variant | One rare-quote variant can't starve the others |
| No indicator data loaded | Uses extended CSV, not signals CSV | Model 4 has no need for the 48 signal columns |
| Session filters | After 10:00, before 15:45, no atr_spike | Consistent with Models 1–3 for intraday sanity |
| Worker warm-up | Extended CSV + option index loaded in parent | Saves N repeated CSV parses across workers |
