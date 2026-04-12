# Model 2: Stop Loss → Covered Call Pivot

## Overview

An extension of Model 1 (Tech Indicators Stock Trade) with a **covered-call exit overlay**. Uses identical entry logic (4-indicator combo + ATR bracket), but when the trailing stop fires instead of closing the position, the model **sells a covered call** at a suitable strike and holds the stock through expiration, earning option premium.

**Strategy Type:** Trend-following entry + tactical option overlay on stop-loss events  
**Asset:** AAPL (Apple Inc.)  
**Timeframe:** 1-minute bars  
**Capital:** $10,000 per trade  
**Commission:** $2.00 per round-trip trade (stock) + option commissions  

---

## Entry Conditions

**Identical to Model 1:** Composite 4-indicator buy signal fires (see Model 1 documentation).

```
BUY = bsig_TREND AND bsig_MOMENTUM AND bsig_VOLATILITY AND bsig_VOLUME
```

All 24 indicator buy signal definitions are the same (EMA crossover, MACD cross, ADX trend gate, etc.).

### Position Sizing
```
shares = floor(TRADE_CAPITAL / entry_price)    # integer shares, no partials
```

---

## Exit Conditions

### Exit Priority

Exits are evaluated in order:

#### 1. **Forced EOD Exit** (Time-based, non-stop events)
- **Trigger:** Current bar time ≥ 3:45 PM ET (15:45) AND no trailing stop has fired yet
- **Exit Price:** `avg_bid` (market price at exit)
- **Exit Reason:** `eod_forced`
- **Note:** Closes both stock and any open short call

#### 2. **Trailing Stop Loss Hit** → **Covered Call Pivot**
- **Trigger:** Price (`average` WAP) drops to or below the trailing stop
- **Trailing Mechanism:** Same as Model 1 — ratchet stop upward each bar, initialized at `entry_price - (atr_14 × 1.5)`

**When trailing stop fires:**

1. **Find a suitable short call:**
   - Strike ≤ (trigger_avg_price − $2)  → cap loss vs stock purchase
   - Expiry ≤ 4 calendar days away (max Friday of that week)
   - Max strike gap: $2 between target strike and actual available strike
   - Must have fresh quotes (< 30 min old)
   
2. **Sell 1 call contract** at `avg_bid` (or best available)
   - **Leg recorded:** `leg='option'` in trades CSV
   - **P&L:** Gross = (option sell price × 100) − option commissions

3. **Continue holding the stock** (no immediate exit)
   - **Leg recorded:** `leg='stock'` in trades CSV
   - **P&L:** Combined with option leg for total position P&L

4. **Monitor through Friday expiry:**

   **a) Option closes early (buyback):**
   - **Trigger:** Option ask price drops below $0.50
   - **Action:** Buy back the call (close short) + sell stock at `avg_bid`
   - **Exit Reason:** `cc_buyback`
   
   **b) Option held to expiry (Friday at or after 3 PM):**
   - **If stock avg_bid > strike (ITM):** Assignment triggers
     - Stock is sold at the **strike price** (not market)
     - Call expires ITM, investor assigned
     - **Exit Reason:** `cc_assigned`
   
   - **If stock avg_bid ≤ strike (OTM):** Option expires worthless
     - Call expires OTM, keep the premium
     - Stock is sold at `avg_bid` (current market)
     - **Exit Reason:** `cc_expired_otm`

   **c) Fallback (no matching option or data gap):**
   - If no suitable call is found or option data is missing
   - **Action:** Exit stock at the trailing stop price
   - **Exit Reason:** `stop_loss` (no call overlay)

### Sell Signal Exit (less common)
- If any of the 4 model indicators' **sell signal** fires before the trailing stop:
  - Exit stock immediately at `avg_bid`
  - Exit Reason: `sell_signal_{indicator}`
  - No covered call is opened
  - (Priority: sell signals check **before** stop is hit each bar)

---

## Variations Tested

### Indicator Combinations
**Identical to Model 1:** 1,221 valid 4-indicator combinations from the 4 categories.

| Category | Indicators |
|----------|---|
| **TREND** (7) | ema, macd, adx, sar, don, arn, vtx |
| **MOMENTUM** (10) | rsi, sto, cci, cmo, tsi, roc, frc, srsi, rmi, macd |
| **VOLATILITY** (3) | atr, bbd, chp |
| **VOLUME** (6) | vwap, obv, mfi, klg, frc, vrc |

(macd in both TREND & MOMENTUM; frc in both MOMENTUM & VOLUME → 1,221 distinct combos)

### Sample Strategy
**Identical to Model 1:** Fixed 10,000-bar sample across all models (seed=42) for fair comparison.

### Standalone Run (single_model.py)
```
python single_model.py --trend ema --momentum rsi --volatility atr --volume vwap
```

- **Windows:** 100 non-overlapping 14-day windows (vs 500 in Model 1)
  - Fewer windows due to option data complexity
- **Seed:** Each window gets a fresh random seed (or `--seed N` for fixed)
- **Output:** `{combo}_runs.csv` + `{combo}_trades.csv`

### Batch Run (all_models.py)
```
python all_models.py [--seed N]
```

- Tests all 1,221 combos
- Outputs: summary CSV + markdown report

---

## Key Implementation Details

### Covered Call Logic

#### Call Selection Algorithm
When a trailing stop fires, the model searches for a short call:

```python
target_strike = floor(trigger_price − 2.0)  # $2 below stop price
found_strikes = [s for s in available_strikes 
                 if abs(s − target_strike) <= MAX_STRIKE_GAP]  # within $2 gap
selected_strike = closest_to_target(found_strikes)
```

#### Quotes Freshness Check
Option data must be recent:
- Quote timestamp must be < 30 minutes old
- On expiry Friday: quote must exist at or after 3 PM (15:00)

#### Risk Capping
- Strike ≤ (trigger_price − $2) ensures stock loss is bounded
- If stock drops further after call is sold, the strike floor limits downside
- Example: Entry $150, stop fires at $148, sell $146 call
  - Worst case: stock assigned at $146 strike (vs. $150 entry) = -$4 loss

### Data Sources

#### Stock Quotes
- `sq_AAPL_extended.csv` and `sq_AAPL_signals.csv`
- Columns: `avg_ask` (entry), `average` (WAP, for bracket/stop), `avg_bid` (exit)

#### Option Data
- Index file: `option_index.csv` — maps (date, strike, expiry) → option data file
- Data directory: `data/options/` — individual CSV files per option (date, expiry, strike, bid/ask by minute)
- Lookup on stop-loss fires to find suitable call; re-query for expiry-day handling

### Constants & Thresholds
```python
TRADE_CAPITAL = 10_000.0            # max per trade
COMMISSION = 2.00                   # stock round-trip
ATR_STOP_MULT = 1.5                 # stop = entry − (atr_14 × 1.5)
ATR_TARGET_RR = 2.0                 # profit_target = entry + atr_14 × 3.0

CC_BUYBACK_THRESHOLD = 0.50         # buyback if call ask < $0.50
CC_MAX_EXPIRY_DAYS = 4              # max days to Friday expiry
MAX_STRIKE_GAP = 2.0                # max $ between target and actual strike
MAX_QUOTE_AGE_MINUTES = 30          # option quote must be < 30 min old
EXPIRY_QUOTE_MIN_HOUR = 15          # expiry-day quote must be ≥ 3 PM
```

### Metrics
Each position (stock + option legs combined) is tracked as a single trade with:
- **Total P&L** = (stock P&L) + (option P&L)
- **Win/loss** classification based on combined P&L
- **Exit type breakdown:** eod_forced, stop_loss, sell_signal, cc_buyback, cc_assigned, cc_expired_otm

### Trades CSV Structure
```
model_id, trend, momentum, volatility, volume,
trade_date, entry_time, entry_price, shares,
stop_loss, profit_target,
exit_price, exit_reason,
bars_held,
cost, proceeds, pnl_dollar, pnl_pct, is_winner,
atr_at_entry, rsi_at_entry, adx_at_entry, vwap_at_entry,
leg                                     ← 'stock' or 'option'
```

**Two rows per closed position:**
1. `leg='stock'` — stock entry/exit (always present)
2. `leg='option'` — call entry/exit (only if CC was opened)

Combined P&L = stock_pnl + option_pnl

---

## Important Notes

1. **Option data availability:** Not all dates/strikes have option quotes. Fallback is to exit stock at stop-loss price if no matching call is found.

2. **Buyback threshold ($0.50):** If the short call drops in value to < $0.50, the model buys it back to lock in profit and exit the position.

3. **Expiry handling:** On Friday, the model checks option assignment status at 3 PM. If still holding, assignment or expiry determines final exit price.

4. **Two-leg reporting:** Each CC position generates two trades CSV rows (stock + option) but is evaluated as a single position. Total P&L is the sum of both legs.

5. **Indicator sell signals still apply:** If any of the 4 model indicators' sell signal fires, the position exits immediately without waiting for the stop loss to trigger. (Lower priority to stop/CC logic, but still exits cleanly.)

6. **Reduced windows (100 vs 500):** Model 2 uses fewer windows due to option data complexity; comparisons within Model 2 are still fair (fixed sample per combo).

---

## Outputs

### single_model.py
- `{combo}_{timestamp}/` directory:
  - `{combo}_runs.csv` — one row per 14-day window
  - `{combo}_trades.csv` — all trades (stock + option legs) across all windows
  - `run.log` — structured log

### all_models.py
- `{timestamp}_summary.md` — markdown report with rankings
- `{timestamp}_trades.csv` — all trades from all 1,221 combos
