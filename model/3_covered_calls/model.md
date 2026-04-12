# Model 3: Covered Calls Strategy

## Overview

A **covered call income strategy** that immediately pairs stock and option entry. When a 4-indicator buy signal fires, the model simultaneously:
1. **Buys 100 shares** at `avg_ask`
2. **Sells 1 call contract** at a predetermined strike offset

The position holds through Friday expiry (4 days max) with no traditional stop loss. Premium collected from the short call reduces the effective cost basis and provides downside protection. Strike offsets allow testing of different risk/reward profiles.

**Strategy Type:** Income/covered call overlay on technical entry signal  
**Asset:** AAPL (Apple Inc.)  
**Timeframe:** 1-minute bars  
**Position Size:** 100 shares per signal (fixed, not dynamic)  
**Capital:** ~$15,000–$17,000 per trade (varies by stock price)  
**Commission:** $2.00 per round-trip (stock + option legs)  

---

## Entry Conditions

### Buy Signal (Composite)
**Identical to Models 1 & 2:** Composite 4-indicator buy signal fires on the same bar.

```
BUY = bsig_TREND AND bsig_MOMENTUM AND bsig_VOLATILITY AND bsig_VOLUME
```

All 24 indicator buy signal definitions are the same (see Model 1 documentation).

### Position Structure at Entry

When the buy signal fires:

1. **Buy 100 shares** at `avg_ask` (fixed quantity, not dynamic by capital)
   - **Cost:** 100 × entry_ask
   - **Entry P&L row:** `leg='stock'`, entry_price = avg_ask, shares = 100

2. **Sell 1 call contract** at the predetermined strike offset
   - **Strike Calculation:**
     ```
     target_strike = floor(entry_price) + STRIKE_OFFSET
       where STRIKE_OFFSET ∈ [+3, +2, -2, -3]
     ```
   - **Strike Grid:** AAPL quotes on $1 grid; target is floored to nearest dollar
   - **Max Strike Gap:** Must find actual strike within $1 of target (MAX_STRIKE_GAP = 1.0)
   - **Expiry:** Friday of the same week (≤ 4 days from entry)
   - **Entry P&L row:** `leg='option'`, entry_price = option sold price (avg_bid)

   **Strike Offset Interpretation:**
   - **+3 OTM:** Strike = floor(price) + 3 → bullish, limited upside, lower premium
   - **+2 OTM:** Strike = floor(price) + 2 → moderate bullish, moderate premium
   - **-2 ITM:** Strike = floor(price) − 2 → bearish, higher premium, capped loss
   - **-3 ITM:** Strike = floor(price) − 3 → very bearish, maximum premium, max loss cap

---

## Exit Conditions

There is **no traditional stop loss** and **no EOD forced exit on non-expiry days**. The position closes only when the covered call closes.

### Exit Priority

#### 1. **Early Buyback** (Call premium collapse)
- **Trigger:** Option ask price drops below $0.50 (call value approaching $0)
- **Action:**
  - Buy back (close) the short call at `avg_ask`
  - Sell stock at `avg_bid` (exit stock immediately after)
- **Exit Price (stock):** `avg_bid` at buyback time
- **Exit Price (option):** `avg_ask` at buyback time
- **Exit Reason:** `cc_buyback`
- **Timing:** Can happen any day before Friday
- **Rationale:** Locks in option profit and frees capital

#### 2. **Friday Expiry (>= 3 PM)**
The position automatically closes on Friday at or after 3 PM:

**a) Stock ITM (avg_bid > strike) → Assignment**
- Stock is sold at the **strike price** (not market)
- Call expires ITM; investor is automatically assigned
- **Exit Price (stock):** Strike price
- **Exit Price (option):** Expires worthless ($0)
- **Exit Reason:** `cc_assigned`

**b) Stock OTM (avg_bid ≤ strike) → Expiration OTM**
- Call expires worthless; premium is fully kept
- Stock is sold at `avg_bid` (current market)
- **Exit Price (stock):** `avg_bid` on expiry
- **Exit Price (option):** Expires worthless ($0)
- **Exit Reason:** `cc_expired_otm`

---

## Variations Tested

### Strike Offsets
The model tests **4 different strike offset values**:

| Offset | Strike Formula | Character | Expected Premium |
|--------|---|---|---|
| **+3** | floor(price) + 3 | OTM bullish | Lower |
| **+2** | floor(price) + 2 | OTM moderate | Low to mid |
| **-2** | floor(price) − 2 | ITM bearish | Mid to high |
| **-3** | floor(price) − 3 | ITM very bearish | Higher |

**Combined Variations:**
```
1,221 indicator combos × 4 strike offsets = 4,884 total model variations
```

(Unlike Models 1 & 2, Model 3 tests 4× as many configurations due to the strike offset dimension.)

### Indicator Combinations
**Same 1,221 combos as Models 1 & 2:**

| Category | Indicators |
|----------|---|
| **TREND** (7) | ema, macd, adx, sar, don, arn, vtx |
| **MOMENTUM** (10) | rsi, sto, cci, cmo, tsi, roc, frc, srsi, rmi, macd |
| **VOLATILITY** (3) | atr, bbd, chp |
| **VOLUME** (6) | vwap, obv, mfi, klg, frc, vrc |

### Sample Strategy
**Fixed 10,000-bar sample** across all models (seed=42) for fair comparison.

### Standalone Run (single_model.py)
```
python single_model.py --trend ema --momentum rsi --volatility atr --volume vwap --strike-offset -2
```

- **Windows:** 100 non-overlapping 14-day windows
- **Seed:** Fresh per window (or `--seed N` for fixed)
- **Output:** `{combo}_{offset}_runs.csv` + `{combo}_{offset}_trades.csv`

### Batch Run (all_models.py)
```
python all_models.py [--seed N]
```

- Tests all 1,221 × 4 = 4,884 variations
- Outputs: summary CSV + markdown report

---

## Key Implementation Details

### Price Sources
| Situation | Column | Rationale |
|-----------|--------|-----------|
| **Stock entry cost** | `avg_ask` | Actual price paid when buying 100 shares |
| **Call entry premium** | `avg_bid` | Actual price received when selling the call |
| **Stock exit (early buyback)** | `avg_bid` | Market price when closing stock |
| **Stock exit (assignment)** | Strike price | Contractual assignment price |
| **Stock exit (expiry OTM)** | `avg_bid` | Market price on Friday at expiry |
| **Call exit (buyback)** | `avg_ask` | Cost to buy back the short call |

### Position Sizing
- **Stock:** Always 100 shares (fixed, not dynamic)
- **Options:** Always 1 contract (100-share equivalent)

This is different from Models 1 & 2, which size shares dynamically based on capital.

### Call Selection Algorithm
When the buy signal fires, search for the target call:

```python
target_strike = floor(entry_price) + STRIKE_OFFSET
found_strikes = [s for s in available_calls[expiry_friday]
                 if abs(s − target_strike) <= MAX_STRIKE_GAP]  # within $1
selected_strike = closest_to_target(found_strikes)
```

#### Quotes Freshness Check
Option data must be current:
- Quote timestamp < 30 minutes old at entry
- On Friday: quote must exist at or after 3 PM (15:00)

### Effective Cost Basis
```
Net entry cost = (100 × stock_entry_price) − (option_sell_price × 100)
               = cost of stock − premium_collected
```

Example:
- Stock entry: $150 × 100 = $15,000
- Sell $148 call for $2.00 premium = $200
- **Net cost:** $15,000 − $200 = $14,800

### P&L Calculation
```
Total P&L = (stock_exit_price − stock_entry_price) × 100 
          + (option_sell_price − option_buyback_price) × 100
          − commissions
```

For **assignment scenario:**
```
Stock P&L = (strike − entry_price) × 100
Option P&L = (sell_price − 0) × 100  # call expires worthless
```

### Constants & Thresholds
```python
COMMISSION = 2.00                   # round-trip (stock + option)
CC_BUYBACK_THRESHOLD = 0.50         # buyback if call ask < $0.50
CC_MAX_EXPIRY_DAYS = 4              # hold until Friday (≤ 4 days from Monday)
MAX_STRIKE_GAP = 1.00               # max $ between target strike and found strike
MAX_QUOTE_AGE_MINUTES = 30          # option quote must be < 30 min old
EXPIRY_QUOTE_MIN_HOUR = 15          # expiry-day quote must be ≥ 3 PM (15:00)

STRIKE_OFFSETS = [3.0, 2.0, -2.0, -3.0]  # tested variations
```

### Metrics
Each position (stock + option legs combined) is tracked as a single trade with:
- **Total P&L** = (stock P&L) + (option P&L)
- **Win/loss** classification based on combined P&L
- **Hold Duration:** Entry to Friday expiry (typically 2–4 days, measured in bars)
- **Entry Snapshot:** atr_at_entry, rsi_at_entry, adx_at_entry, vwap_at_entry

### Trades CSV Structure
```
model_id, trend, momentum, volatility, volume, strike_offset,
trade_date, entry_time, entry_price, shares,
strike_price,
exit_price, exit_reason,
bars_held,
cost, proceeds, pnl_dollar, pnl_pct, is_winner,
atr_at_entry, rsi_at_entry, adx_at_entry, vwap_at_entry,
leg                                     ← 'stock' or 'option'
```

**Two rows per closed position:**
1. `leg='stock'` — stock entry/exit (always present)
2. `leg='option'` — call entry/exit (always present)

Combined P&L = stock_pnl + option_pnl

---

## Important Notes

1. **No stop loss:** Unlike Models 1 & 2, there is no ATR-based trailing stop. Risk is defined by the strike offset (especially ITM calls which cap loss).

2. **Fixed share quantity (100):** Model 3 always buys 100 shares, regardless of capital. This differs from Models 1 & 2, which size shares by capital/price. Model 3 requires higher capital allocation.

3. **Premium offsets cost basis:** The call premium collected at entry immediately reduces the effective cost basis, providing a buffer against downside moves.

4. **Friday expiry lock:** Position **must** close by Friday at/after 3 PM. No multi-day extension.

5. **Buyback triggers on premium collapse:** If the short call's ask price drops below $0.50, the model buys it back to lock in option profit. This can happen mid-week.

6. **Strike offset dimension:** Model 3 has 4× the configuration space of Models 1 & 2 (4 strike offsets × 1,221 combos = 4,884 variations).

7. **Assignment cost:** When assigned, the stock is sold at the strike price, not market. This is guaranteed but may be sub-optimal if the stock rallies past the strike.

---

## Outputs

### single_model.py
- `{combo}_{offset}_{timestamp}/` directory:
  - `{combo}_{offset}_runs.csv` — one row per 14-day window
  - `{combo}_{offset}_trades.csv` — all trades (stock + option legs) across windows
  - `run.log` — structured log

### all_models.py
- `{timestamp}_summary.md` — markdown report with rankings across all 4,884 variations
- `{timestamp}_trades.csv` — all trades from all variations
- Separate subsections for each strike offset so performance by strike is visible
