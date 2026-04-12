# Model 1: Tech Indicators Stock Trade

## Overview

A systematic intraday equity backtesting engine for AAPL 1-minute bars. Tests **1,221 unique 4-indicator combinations** across a fixed sample of 10,000 bars. Each trade uses ATR-based brackets (stop loss and profit target) with a trailing stop exit mechanism.

**Strategy Type:** Trend-following + momentum confirmation using technical indicators

**Asset:** AAPL (Apple Inc.)  
**Timeframe:** 1-minute bars  
**Capital:** $10,000 per trade  
**Commission:** $2.00 per round-trip trade

---

## Entry Conditions

### Buy Signal (Composite)
A trade enters when **ALL four indicator signals fire simultaneously** on the same bar:

```
BUY = bsig_TREND AND bsig_MOMENTUM AND bsig_VOLATILITY AND bsig_VOLUME
```

Each slot is filled by one indicator from its category (see Variations below).

### Buy Signal Definitions (by indicator)

| Indicator | Buy Condition |
|-----------|---------------|
| **ema** | EMA 9/21 crossover — bullish cross on this exact bar (`ema_cross_event == 1`) |
| **macd** | MACD line crosses above signal line AND histogram > 0 AND histogram growing (`mcd_sig_event==1 AND mcd_histogram>0 AND mcd_hist_growing==1`) |
| **adx** | Trend strength gate active AND ADX rising (`adx_trend_gate==1 AND adx_rising==1`) |
| **sar** | Parabolic SAR flips to bullish (`sar_flip_bull == 1`) |
| **don** | Price breaks above upper Donchian band (`don_breakout_up == 1`) |
| **arn** | Aroon bullish OR crossover (`arn_bull==1 OR arn_cross_up==1`) |
| **vtx** | Vortex crosses bullish (`vtx_cross_up == 1`) |
| **rsi** | RSI crosses 50 OR crosses 30 from below (`rsi_cross_50==1 OR rsi_cross_30==1`) |
| **sto** | Stochastic crosses up AND not overbought (`sto_cross_up==1 AND NOT overbought`) |
| **cci** | CCI crosses -100 OR crosses 0 from below (`cci_cross_m100==1 OR cci_cross_0==1`) |
| **cmo** | Chande Momentum crosses 0 from below (`cmo_cross_0 == 1`) |
| **tsi** | TSI crosses 0 OR crosses signal line (`tsi_cross_0==1 OR tsi_cross_sig==1`) |
| **roc** | Rate of Change crosses 0 from below (`roc_cross_0 == 1`) |
| **frc** | Force Index crosses 0 from below (`frc_cross_0 == 1`) |
| **srsi** | Stochastic RSI crosses up AND not overbought (`srsi_cross_up==1 AND NOT overbought`) |
| **rmi** | RMI crosses 50 from below (`rmi_cross_50 == 1`) |
| **atr** | ATR bar ratio ≥ 0.5 AND no volatility spike (`atr_bar_ratio>=0.5 AND atr_spike==0`) |
| **bbd** | Bollinger Bands expanding AND price above SMA AND %B > 0.5 (`bbd_expanding==1 AND above_sma==1 AND pct_b>0.5`) |
| **chp** | Choppiness Index < 50 (trending regime) (`chp_14 < 50`) |
| **vwap** | Price above VWAP AND distance > 0 (`vwp_above==1 AND vwp_distance>0`) |
| **obv** | OBV rising AND above EMA AND no bearish divergence (`obv_rising==1 AND above_ema==1 AND div_bear==0`) |
| **mfi** | MFI crosses 50 OR bounces (`mfi_cross_50==1 OR mfi_bounce==1`) |
| **klg** | Klinger Volume crosses signal line (`klg_cross_sig == 1`) |
| **vrc** | Volume ROC positive AND no spike (`vrc_pos==1 AND vrc_spike==0`) |

### Pre-Trade Filters
Candidate bars must also pass intraday screening:
- `ses_after_10 == 1` — trading began after 10:00 AM ET
- `ses_before_345 == 1` — still before 3:45 PM ET cutoff
- `atr_spike == 0` — no extreme volatility event
- All required indicator columns contain valid data (not NaN)

### Position Sizing
```
shares = floor(TRADE_CAPITAL / entry_price)    # integer shares, no partials
```

### Stop Loss and Profit Target (ATR Bracket)
```
stop_loss = entry_price - (atr_14 × ATR_STOP_MULT)        # default: atr_14 × 1.5
profit_target = entry_price + (atr_14 × ATR_TARGET_RR × ATR_STOP_MULT)  # atr_14 × 3.0
```

The profit target is locked in at entry; the stop is **trailed upward** each bar (see Exit Conditions).

---

## Exit Conditions

Exits are evaluated in priority order. The first condition that matches determines the exit type.

### 1. **Forced EOD Exit** (Time-based)
- **Trigger:** Current bar time ≥ 3:45 PM ET (15:45)
- **Exit Price:** `avg_bid` (market price at exit)
- **Exit Reason:** `eod_forced`
- **Note:** Locks in position at EOD to avoid overnight risk

### 2. **Trailing Stop Loss Hit** (Dynamic)
- **Trigger:** Price (`average` WAP) drops to or below the trailing stop
- **Trailing Mechanism:** Each bar, update: `trailing_stop = max(trailing_stop, current_bar_high - atr_14 × 1.5)`
  - This allows the stop to ratchet upward but never lower
  - Initialized at entry to `entry_price - (atr_14 × 1.5)`
- **Exit Price:** `avg_bid` (market price at stop level)
- **Exit Reason:** `stop_loss`

### 3. **Profit Target Hit**
- **Trigger:** Price (`average` WAP) reaches or exceeds the profit target
- **Exit Price:** `avg_bid` (market price at target level)
- **Exit Reason:** `profit_target`

### 4. **Sell Signal Fired** (Type B exit)
- **Trigger:** Any of the 4 model indicators' sell signal fires
  - Sell signals are **specific to each indicator** (see Sell Signal Definitions below)
- **Exit Price:** `avg_bid`
- **Exit Reason:** `sell_signal_{indicator}`
- **Note:** Used only if neither stop nor target was hit first

### Sell Signal Definitions (by indicator)

| Indicator | Sell Condition |
|-----------|---|
| **ema** | EMA crossover flips to bearish (`ema_crossover == 0`) |
| **macd** | Histogram becomes negative or shrinking |
| **adx** | Trend gate closes (`adx_trend_gate == 0`) |
| **sar** | SAR flips to bearish (`sar_bull == 0`) |
| **don** | Price closes below lower Donchian band (`close < don_lower_20`) |
| **arn** | Aroon Oscillator < -20 |
| **vtx** | Vortex minus > vortex plus |
| **rsi** | RSI overbought condition fires (`rsi_overbought == 1`) |
| **sto** | Stochastic overbought (`sto_overbought == 1`) |
| **cci** | CCI overbought (`cci_overbought == 1`) |
| **cmo** | CMO > 50 (extreme momentum) |
| **tsi** | TSI drops below 0 |
| **roc** | ROC < 0 |
| **frc** | Force Index EMA < 0 |
| **srsi** | Stochastic RSI overbought |
| **rmi** | RMI overbought |
| **atr** | Never (always False for volatility baseline) |
| **bbd** | Bollinger Bands %B > 0.95 (price near upper band) |
| **chp** | Choppiness Index > 61.8 (ranging regime) |
| **vwap** | Price drops below VWAP (`vwp_above == 0`) |
| **obv** | Bearish divergence fires (`obv_div_bear == 1`) |
| **mfi** | MFI > 80 (money flow overbought) |
| **klg** | Klinger Bull flag off (`klg_bull == 0`) |
| **vrc** | Never (always False for volume baseline) |

---

## Variations Tested

### Indicator Combinations
The model tests **1,221 valid 4-indicator combinations**:

```
product(TREND, MOMENTUM, VOLATILITY, VOLUME)
  where each slot is filled by one indicator from its category
  and all four indicators are distinct (no repeats)
```

**Categories:**

| Category | Indicators (Count) |
|----------|---|
| **TREND** (7) | ema, macd, adx, sar, don, arn, vtx |
| **MOMENTUM** (10) | rsi, sto, cci, cmo, tsi, roc, frc, srsi, rmi, macd |
| **VOLATILITY** (3) | atr, bbd, chp |
| **VOLUME** (6) | vwap, obv, mfi, klg, frc, vrc |

**Constraint:** macd appears in both TREND and MOMENTUM; frc appears in both MOMENTUM and VOLUME. The combination generator filters out any 4-tuple where all four slots are not distinct, reducing 7×10×3×6 = 1,260 to **1,221 valid combinations**.

### Sample Strategy
All 1,221 models test against the **same fixed 10,000-bar sample** drawn from the data:
- **Seed:** `RANDOM_SEED = 42` (deterministic)
- **Filter:** Bars where `ses_after_10==1`, `ses_before_345==1`, `atr_spike==0`, all indicator columns valid
- **Rationale:** Fair comparison — differences in P&L are signal-driven, not sample-driven

### Standalone Run (single_model.py)
When run standalone, a single combo is tested across **500 independent windows**:

```
python single_model.py --trend ema --momentum rsi --volatility atr --volume vwap
```

- **Windows:** 500 non-overlapping 14-day windows
- **Date Range:** 2023-01-01 to 2026-02-28 (3+ years)
- **Seed:** Each window gets a fresh random seed (or `--seed N` for fixed reproducibility)
- **Output:** CSV of per-window metrics + aggregated trades CSV

### Batch Run (all_models.py)
Tests all 1,221 combos in a single execution:

```
python all_models.py [--seed N]
```

- **Output:** Summary CSV + markdown report with rankings

---

## Key Implementation Details

### Price Sources
| Situation | Column | Rationale |
|-----------|--------|-----------|
| **Entry cost** | `avg_ask` | Actual price paid when buying |
| **Stop/target levels** | `average` (WAP) | Fair reference for dynamic brackets |
| **Trade exit** | `avg_bid` | Actual price received when selling |
| **Trailing stop check** | `average` (WAP) | Consistent with bracket computation |

### Data Schema
All 24 indicators are precomputed in `sq_AAPL_extended.csv` and cached into `sq_AAPL_signals.csv` on the first run:

- **Extended CSV:** 158 columns (indicators only)
- **Signals CSV:** 206 columns (extended + 48 buy/sell signal columns: bsig_xxx, ssig_xxx for 24 indicators)

### Constants & Thresholds
```python
TRADE_CAPITAL = 10_000.0        # max per trade (actual depends on share count)
COMMISSION = 2.00               # $2 deducted from final P&L (1 × $1/leg × 2 legs, IBKR rate)
ATR_STOP_MULT = 1.5             # stop = entry − (atr_14 × 1.5)
ATR_TARGET_RR = 2.0             # target = entry + (atr_14 × 1.5 × 2.0) = entry + atr_14 × 3.0
N_SAMPLE = 10_000               # bars per model test
RANDOM_SEED = 42                # for reproducible sampling
```

### Metrics Computed
Per-model summary includes:
- **Total P&L** (dollars)
- **Win Rate** (%)
- **Profit Factor** (gross wins ÷ gross losses, capped at 10.0)
- **Sharpe Ratio** (excess return / volatility)
- **Max Drawdown** (%)
- **Trade Count**
- **Avg Duration** (bars held)
- **Entry / Exit price averages**

### Important Notes
1. **Signal firing is vectorized:** All candidate bars for all 1,221 models are computed at once, then simulations run only on bars where the composite signal fires (massive speed-up).
2. **Trailing stop is bar-by-bar:** On each bar of an open trade, the stop is recalculated; profit target is fixed.
3. **Exit minute (3:45 PM):** Intraday cutoff prevents holding past close; this is **not** a target exit but a hard deadline.
4. **Sell signals are optional:** A trade can exit via stop, target, or forced EOD without any indicator's sell signal firing.
5. **Sample is identical:** All 1,221 models see the same 10,000-bar sample, so rankings reflect signal quality, not luck.

---

## Outputs

### single_model.py
- `{combo}_{timestamp}/` directory:
  - `{combo}_runs.csv` — one row per 14-day window; metrics across all trades in that window
  - `{combo}_trades.csv` — every individual trade; entry/exit prices, duration, P&L, entry snapshot fields
  - `run.log` — structured log of each trade fired

### all_models.py
- `{timestamp}_summary.md` — markdown report with top/bottom performers, full 1,221-row table
- `{timestamp}_trades.csv` — all trades from all 1,221 combos in one file (model_id, indicators, entry/exit, P&L)
