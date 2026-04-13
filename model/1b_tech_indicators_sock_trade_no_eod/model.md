# Model 1b: Tech Indicators Stock Trade — No EOD Exit

## Overview

Identical to **Model 1a** in every respect except the exit rules. In 1a a position is
forced out at 3:45 PM if neither stop nor profit target has fired. In 1b **there is no
intraday time-box** — the position carries across day boundaries and only closes when the
ATR-based trailing stop is finally triggered. If the stop has not fired by the last bar of
the 14-day window the trade exits with `exit_reason = 'window_end'`.

**Strategy Type:** Trend-following + momentum confirmation; trailing stop only  
**Asset:** AAPL (Apple Inc.)  
**Timeframe:** 1-minute bars  
**Capital:** $10,000 per trade  
**Commission:** $2.00 per round-trip trade

---

## Entry Conditions

**Identical to Model 1a.** Composite 4-indicator buy signal fires on the same bar:

```
BUY = bsig_TREND AND bsig_MOMENTUM AND bsig_VOLATILITY AND bsig_VOLUME
```

All 24 indicator buy signal definitions are unchanged. See `1a/model.md` for the full table.

### Pre-Trade Filters
Same as 1a — candidate bars must pass:
- `ses_after_10 == 1` — after 10:00 AM ET
- `ses_before_345 == 1` — before 3:45 PM ET (entry filter only; does not force exit)
- `atr_spike == 0` — no extreme volatility event
- All required indicator columns contain valid data

### Position Sizing
```
shares = floor(TRADE_CAPITAL / entry_price)    # integer shares, no partials
```

### Stop and Target (ATR Bracket)
```
stop_loss      = entry_avg − (atr_14 × 1.5)
profit_target  = entry_avg + (atr_14 × 3.0)    # kept for reference; not used as exit
```

The trailing stop ratchets upward as price rises; the profit target is **not used** as an exit
trigger in either 1a or 1b.

---

## Exit Conditions

### Only exit: Trailing Stop Loss

- **Trigger:** `average` (WAP) on any bar ≤ `trailing_stop`
- **Trailing Mechanism:** Each bar, if `current_avg > high_water`:
  ```
  gain          = current_avg − high_water
  trailing_stop = trailing_stop + gain      # ratchet up, never down
  high_water    = current_avg
  ```
  The stop is initialized at `entry_avg − (atr_14 × 1.5)` at entry.
- **Exit Price:** `avg_bid` at the bar where the stop triggers
- **Exit Reason:** `stop_loss`
- **Scope:** Evaluated across **all bars from entry day through end of the 14-day window** —
  not just the bars remaining on the entry day.

### Fallback: Window End

If the trailing stop never fires before the window expires:

- **Exit Price:** `avg_bid` on the last bar of the window
- **Exit Reason:** `window_end`
- **Note:** Unlike 1a's `eod_forced`, `window_end` can occur days after entry. The
  trade has been held multi-day and the window simply ran out.

### What is removed vs 1a

| Exit type in 1a | Status in 1b |
|-----------------|---|
| `eod_forced` (3:45 PM time-box) | **Removed** — position carries to next day |
| `stop_loss` (trailing stop) | **Unchanged** |
| `profit_target` | Already commented out in 1a — not present |
| `sell_signal` | Already commented out in 1a — not present |
| `eow_forced` (--end-of-week-exit flag) | **Removed** — flag does not exist in 1b |

---

## Variations Tested

### Indicator Combinations
**Identical to Models 1a/2/3:** 1,221 valid 4-indicator combinations.

| Category | Indicators |
|----------|---|
| **TREND** (7) | ema, macd, adx, sar, don, arn, vtx |
| **MOMENTUM** (10) | rsi, sto, cci, cmo, tsi, roc, frc, srsi, rmi, macd |
| **VOLATILITY** (3) | atr, bbd, chp |
| **VOLUME** (6) | vwap, obv, mfi, klg, frc, vrc |

### Sample Strategy
Fixed 10,000-bar sample per window (seed=42) — all 1,221 models see the same bars.

### Standalone Run
```bash
cd model/1b_tech_indicators_sock_trade_no_eod
python single_model.py --trend ema --momentum rsi --volatility atr --volume vwap
```

- **Windows:** 100 non-overlapping 14-day windows
- **Seed:** Fresh per window (or `--seed N` for fixed)
- **No `--end-of-week-exit` flag** — it does not exist in 1b

### Batch Run
```bash
python all_models.py [--seed N]
```

---

## Key Implementation Details

### Context Builder: `_build_from_entry_context(df, entry_global_idx)`

This is the core difference from 1a. In 1a, each trade receives `df_sim = day_dict[trade_date]`
(a single day's bars). In 1b, each trade receives a slice from the entry bar's trade date
through the last row of the window DataFrame:

```python
# 1a — single day only
df_sim = day_dict.get(trade_date)

# 1b — entry day through end of window
df_sim, entry_iloc = _build_from_entry_context(df, entry_global_idx)
```

`_build_from_entry_context` filters `df` to rows where `fnd_trade_date >= entry_dt` and
locates the entry bar by its exact `date` timestamp. The resulting slice includes all bars
from that point to the window boundary — spanning multiple days.

`simulate_trade` itself is unchanged; it simply walks forward through whatever slice it
receives, so multi-day behaviour falls out naturally.

### Price Sources
Identical to 1a:

| Situation | Column | Rationale |
|-----------|--------|-----------|
| Entry cost | `avg_ask` | Actual price paid |
| Stop/target bracket | `average` (WAP) | Fair reference |
| Trailing stop check | `average` (WAP) | Consistent |
| Exit price | `avg_bid` | Actual price received |

### Constants
```python
TRADE_CAPITAL = 10_000.0
COMMISSION    = 2.00
ATR_STOP_MULT = 1.5     # stop = entry_avg − atr × 1.5
ATR_TARGET_RR = 2.0     # target computed but not used as exit
N_SAMPLE      = 10_000
RANDOM_SEED   = 42
```

### Validate date range before running
`generate_test_runs` now raises a `ValueError` with a descriptive message if the date range
is too narrow to accommodate the requested number of windows:

```
ValueError: Date range 2025-06-20 → 2025-07-04 only spans 0 usable start days,
but 100 non-overlapping 14-day windows were requested.
```

This fix is also present in 1b's `single_model.py` (it was not in 1a).

---

## Expected Behavioural Differences vs 1a

| Dimension | 1a | 1b |
|-----------|----|----|
| **Exit mechanism** | EOD forced + trailing stop | Trailing stop only |
| **Position duration** | Max 1 day (same-day entry→3:45 PM) | Max 14 days (window boundary) |
| **Trade frequency** | Higher (more trades per signal; can re-enter next day) | Lower (long-running trades block re-entry on same position) |
| **Avg bars held** | Low (~tens of bars) | High (potentially hundreds of bars) |
| **Drawdown exposure** | Limited by intraday time-box | Unlimited intraday; multi-day gaps possible |
| **Overnight risk** | None | Present — position held through close and into next open |
| **`exit_reason` values** | `stop_loss`, `eod_forced` | `stop_loss`, `window_end` |

---

## Outputs

### single_model.py
- `reports/single_{combo}_{timestamp}/`:
  - `single_{combo}_runs.csv` — one row per 14-day window
  - `single_{combo}_trades.csv` — all trades; note `bars_held` will be much larger than 1a
  - `run.log` — structured log

### all_models.py
- `reports/{timestamp}/`:
  - `{seq_no}_summary.csv`, `{seq_no}_trades.csv`, `{seq_no}_run.log`
  - `analysis_models.csv`, `analysis_{cat}.csv`, `analysis_pair_*.csv`
  - `batch_run_analysis.md`
