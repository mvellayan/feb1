# Model 3: Covered Calls Strategy

## Overview

On a 4-indicator composite buy signal the model **buys 100 shares and simultaneously
simulates selling 12 different call option contracts** — one for each combination of
3 expiry weeks × 4 strike offsets. Each of the 12 option variants is an independent
simulation sharing the same stock entry price and entry time.

There is no stop loss. A position closes only when its covered call closes (buyback,
expiry, or window end). Stock exits at the same time as the option to keep P&L clean.

**Strategy Type:** Income / covered-call overlay on technical entry signal  
**Asset:** AAPL (Apple Inc.)  
**Timeframe:** 1-minute bars  
**Position Size:** 100 shares per signal (fixed — not capital-based)  
**Commission:** $2.00 per round-trip (each stock + option pair)  

---

## Entry Conditions

**Identical to Models 1a, 1b, 2.** Composite 4-indicator buy signal fires on the same bar:

```
BUY = bsig_TREND AND bsig_MOMENTUM AND bsig_VOLATILITY AND bsig_VOLUME
```

All 24 indicator buy/sell signal definitions are unchanged. See `1a/model.md` for the
full table.

### Pre-Trade Filters
Same entry bar eligibility as all other models:
- `ses_after_10 == 1` — after 10:00 AM ET
- `ses_before_345 == 1` — before 3:45 PM ET
- `atr_spike == 0` — no extreme volatility event
- Key indicator columns contain valid data

### Position Structure at Entry

When the buy signal fires:

1. **Buy 100 shares** at `avg_ask`
2. **Simulate selling 12 call contracts**, one per option variant:
   - Entry premium = `avg_bid` of that option at entry time (≤ 30 min old)
   - If no fresh quote exists: `open_price = N/A` — variant is still tracked but
     P&L is not computable

---

## The 12 Option Variants

3 expiry weeks × 4 strike offsets:

### Expiry Labels

| Label | Expiry Date |
|-------|------------|
| **w0** | Friday of the **same** week as entry (1–5 calendar days away) |
| **w1** | Friday of the **following** week (~8–12 days away) |
| **w2** | Friday **two weeks** after the entry week (~15–19 days away) |

A 1-day w0 (entry on Thursday, expires next day) is valid.

### Strike Offsets

| Label | Strike Formula | Character |
|-------|---------------|-----------|
| **s+1** | `floor(entry_price) + 1` | OTM +1 — bullish bias, lowest premium |
| **s+2** | `floor(entry_price) + 2` | OTM +2 — bullish bias, even lower premium |
| **s-1** | `floor(entry_price) − 1` | ITM −1 — bearish bias, higher premium |
| **s-2** | `floor(entry_price) − 2` | ITM −2 — bearish bias, highest premium |

For AAPL at $175.42: floor = $175, so strikes are $176, $177, $174, $173.

Strike grid: AAPL options trade on a $1 grid. If the exact target strike is not in
the option index, the nearest available strike within $1 (`MAX_STRIKE_GAP = 1.0`) is
used. If no strike is found within tolerance, that variant is marked `no_contract`.

### Full Variant Matrix

```
         s+1    s+2    s-1    s-2
w0       ✓      ✓      ✓      ✓
w1       ✓      ✓      ✓      ✓
w2       ✓      ✓      ✓      ✓
```

Variant key string: `"{expiry_label}/{strike_label}"` e.g. `"w1/s-1"`

---

## Exit Conditions (per variant)

Each of the 12 variants exits independently. The stock exits at the same moment as
its option.

### 1. Buyback — option ask collapses
- **Trigger:** Option `avg_ask` drops below `OPTION_EXIT_PRICE` ($0.50)
- **Stock exit price:** `avg_bid` at that bar
- **Option exit price:** `avg_ask` at that bar (cost to buy back)
- **Stock exit reason:** `cc_buyback`
- **Option exit reason:** `buyback`
- Can fire any time from the bar after entry through the window end

### 2. Expiry — at or after 3 PM on expiry Friday
- **Trigger:** Current bar time ≥ 15:00 on `expiry_date`

  **a) Stock ITM (`avg_bid > strike`) → Assignment**
  - Stock sold at the **strike price** (not market)
  - Option expires worthless (P&L = full premium collected)
  - Stock exit reason: `cc_assigned` / Option: `assigned`

  **b) Stock OTM (`avg_bid ≤ strike`) → Expiration**
  - Stock sold at `avg_bid`
  - Option expires worthless (P&L = full premium collected)
  - Stock exit reason: `cc_expired_otm` / Option: `expired_otm`

### 3. Late buyback data — window end with stale low option ask
- **Trigger:** At window end, the last available option `avg_ask` (any age) < $0.50
- Used when the option likely dropped below threshold but no fresh quote was captured
- **Stock exit reason:** `cc_buyback` / **Option exit reason:** `buyback_late_data`

### 4. Window end — no close before window expires
- **Trigger:** Window runs out (14 days) before the option expiry or buyback fires
  - This is common for w2 options when entry is late in the window
- **Option exit price:** Last available `avg_bid` (may be None if no data)
- **Stock exit reason / Option exit reason:** `window_end`

### Data missing — no contract or no data
- `no_contract` — strike/expiry combination not in option index or gap > $1
- `no_data` — contract exists but no option data file found

In both cases: `open_price = N/A`, `combined_pnl = N/A`, row appears in trades CSV
with `data_status` flag.

---

## Variations Tested

### Indicator Combinations
Same 1,221 combos as all other models:

| Category | Indicators |
|----------|---|
| **TREND** (7) | ema, macd, adx, sar, don, arn, vtx |
| **MOMENTUM** (10) | rsi, sto, cci, cmo, tsi, roc, frc, srsi, rmi, macd |
| **VOLATILITY** (3) | atr, bbd, chp |
| **VOLUME** (6) | vwap, obv, mfi, klg, frc, vrc |

### Total configuration space
```
1,221 indicator combos × 12 option variants = 14,652 scenario rows per window
```

Unlike the old model 3 (4,884 rows), each entry now generates 12 simultaneous
option simulations rather than running 12 separate passes over the data.

### Sample Strategy
Fixed 10,000-bar sample per window (seed=42) for fair comparison across combos.

### Standalone Run
```bash
cd model/3_covered_calls
python single_model.py --trend ema --momentum rsi --volatility atr --volume vwap
```
- 100 non-overlapping 14-day windows
- All 12 option variants simulated per entry signal
- No `--strike-offset` argument — all 12 are always run

### Batch Run
```bash
python all_models.py [--seed N]
```
- 1,221 combos, each producing 12 variant rows per window
- Two separate analysis files written after all windows complete

---

## Key Implementation Details

### Simulation Architecture

The key difference from the old model 3: previously `run_combo()` was called once
per (combo, offset) = 4,884 calls. Now it is called once per combo = 1,221 calls,
and a single forward walk through the window data handles all 12 variants
simultaneously via `simulate_all_variants()`.

```
run_combo(df, sample_idx, ..., indicators)
  └─ for each fired bar:
       find_all_cc_variants()     ← look up all 12 contracts
       _build_cc_context()        ← slice from entry day to end of df
       simulate_all_variants()    ← one walk, 12 variants tracked in parallel
```

Inside `simulate_all_variants()`:
- All open variants are checked on each bar
- A variant is removed from the active set as soon as it closes
- Walk terminates when all 12 variants are closed or df_slice ends

### Data Handling Policy

| Situation | Behaviour |
|-----------|-----------|
| No fresh entry quote (>30 min old or missing) | `open_price = N/A`; variant still simulated if contract exists; P&L not computable |
| No option data at all | `data_status = 'no_data'`; variant appears in output as N/A |
| Missing mid-life bars | `get_option_price_at` returns last known price ≤ bar time — gaps handled silently |
| Expiry day data missing (no 3 PM bar) | Simulation continues past expiry date; first bar after expiry triggers assignment/OTM check |
| Late data shows ask < $0.50 | `buyback_late_data` exit used at window end |
| Contract not in option index | `data_status = 'no_contract'`; all fields N/A |

### Price Sources
| Situation | Column | Rationale |
|-----------|--------|-----------|
| Stock entry | `avg_ask` | Actual price paid |
| Option entry premium | `avg_bid` | Premium received when selling the call |
| Stock exit (buyback) | `avg_bid` | Market price at buyback bar |
| Stock exit (assignment) | Strike price | Contractual assignment |
| Stock exit (OTM expiry) | `avg_bid` at expiry bar | Market price at expiry |
| Option exit (buyback) | `avg_ask` at buyback bar | Cost to close the short |
| Option exit (expiry) | $0.00 | Expires worthless |

### P&L Calculation
```
stock_pnl   = (stock_exit_price − entry_price) × 100 − $2.00 commission
option_pnl  = (open_price − cc_close_price)    × 100 − $2.00 commission
combined_pnl = stock_pnl + option_pnl
```

If `open_price` is N/A or `cc_close_price` is N/A: `combined_pnl = N/A`.

### Constants
```python
OPTION_EXIT_PRICE     = 0.50    # buyback threshold (was CC_BUYBACK_THRESHOLD)
MAX_STRIKE_GAP        = 1.0     # max $ between target strike and found strike
MAX_QUOTE_AGE_MINUTES = 30      # entry quote must be < 30 min old
EXPIRY_QUOTE_MIN_HOUR = 15      # trigger expiry logic at/after 3 PM
SHARES                = 100     # fixed, not capital-scaled
COMMISSION            = 2.00    # per round-trip (stock + option pair)
```

---

## Run Log Format

The `{seq_no}_run.log` is written in a **table format** designed for readability.
Each trade entry contains three sections:

**Section 1 — Trade header + entry snapshot:**
```
================================================================================
 TRADE #3  |  EMA+TSI+BBD+FRC  |  Entry: 2024-03-13 10:45:00  |  $175.42 x 100sh
================================================================================
 ATR: 1.23   RSI: 52.3   ADX: 28.1   VWAP: $175.10
```

**Section 2 — Option matrix at entry + price evals (first 5 / last 5 bars):**
```
 OPTION MATRIX AT ENTRY:
 +----------+--------+--------+-----------+
 | Variant  | Expiry | Strike | Open Bid  |
 +----------+--------+--------+-----------+
 | w0/s+1   | 240315 | 176.00 |      1.10 |
 | w0/s+2   | 240315 | 177.00 |      0.65 |
 | w0/s-1   | 240315 | 174.00 |      2.15 |
 | w0/s-2   | 240315 | 173.00 |      3.20 |
 | w1/s+1   | 240322 | 176.00 |      2.80 |
 | ...      |   ...  |   ...  |       ... |
 | w2/s-2   | 240329 |   ---  |       N/A |
 +----------+--------+--------+-----------+

 PRICE EVALS (first 5 / last 5 bars):
 +---------------------+--------+--------+--------+--------+...
 | Time                | Stock  | w0/s+1 | w0/s+2 | w0/s-1 |...
 +---------------------+--------+--------+--------+--------+...
 | 10:46:00            | 175.55 |   1.05 |   0.62 |   2.10 |...
 | ... N rows skipped  |        |        |        |        |...
 +---------------------+--------+--------+--------+--------+...
```

**Section 3 — Results table (one row per variant):**
```
 RESULTS:
 +----------+---------------------+--------------+---------+---------+---------+-------+
 | Variant  | Exit Time           | Exit Reason  | Stock$  | Option$ | Total$  |  Win? |
 +----------+---------------------+--------------+---------+---------+---------+-------+
 | w0/s+1   | 2024-03-15 15:59:00 | expired_otm  |  +38.00 |  +62.70 | +100.70 |  YES  |
 | w0/s-2   | 2024-03-13 11:00:00 | buyback      | -137.00 | +182.40 |  +45.40 |  YES  |
 | w2/s-2   |         N/A         | no_contract  |     N/A |     N/A |     N/A |  N/A  |
 +----------+---------------------+--------------+---------+---------+---------+-------+
```

---

## Outputs

### Per-window files (in `reports/{timestamp}/`)

| File | Rows | Description |
|------|------|-------------|
| `{seq_no}_run.log` | — | Table-format trade log |
| `{seq_no}_trades.csv` | 2 legs × 12 variants × N trades | Stock + option legs; all 12 variants per entry |
| `{seq_no}_summary.csv` | 14,652 per window | One row per (combo × variant); per-window metrics |

### Post-run analysis files (written by `summarize_run` after all windows)

| File | Rows | Description |
|------|------|-------------|
| `analysis_combos.csv` | 1,221 | **Table 1** — indicator combos ranked by consistency score (aggregated across all 12 variants) |
| `analysis_option_variants.csv` | 12 | **Table 2** — option variants ranked by performance (aggregated across all 1,221 combos); includes `best_combo` column |
| `analysis_{cat}.csv` | varies | Per indicator-category rankings (trend/momentum/volatility/volume) |
| `analysis_pair_{a}_{b}.csv` | varies | Cross-category pair rankings (6 files) |
| `batch_run_analysis.md` | — | Markdown report referencing both Table 1 and Table 2 |

### Trades CSV key columns
```
batch_no, model_id, trade_no, leg,
trend, momentum, volatility, volume,
variant_key, expiry_label, strike_offset,
trade_date, entry_time, entry_price,
strike, expiry_date, cc_open_price,
exit_time, exit_price, exit_reason,
bars_held, shares, cost, proceeds,
pnl_dollar, option_pnl, combined_pnl, is_winner,
atr_at_entry, rsi_at_entry, adx_at_entry, vwap_at_entry,
data_status
```

Rows: `leg='stock'` (always present) + `leg='option'` (only if `open_price` is not N/A).

---

## Important Notes

1. **No stop loss, no EOD exit.** Position is held until the covered call resolves
   (buyback, expiry, or window end).

2. **12 simultaneous simulations per entry.** One stock purchase, 12 independent option
   tracks. The stock P&L differs per variant because each variant's stock exits at a
   different time.

3. **w2 options frequently hit `window_end`** because the 14-day window often ends
   before the w2 Friday expiry. This is expected and recorded cleanly.

4. **N/A is not a skip.** Missing entry quotes are recorded as N/A rather than
   excluding the variant. This preserves the full 12-variant structure even when data
   is incomplete.

5. **Assignment is contractual.** When assigned, stock is sold at the strike price
   regardless of the current market price — this can be beneficial (if stock fell
   below strike) or costly (if stock rallied well past strike).

6. **Analysis is two-dimensional.** `analysis_combos.csv` answers "which indicator
   combo works best for covered calls?" `analysis_option_variants.csv` answers "which
   expiry/strike structure extracts the most premium?"
