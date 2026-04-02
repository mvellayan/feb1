# AAPL Intraday Backtest System
------
## Project Overview

A systematic intraday backtesting engine for AAPL 1-minute bars using a combinatorial indicator framework. The system evaluates 1,221 unique 4-indicator models across a fixed sample of 10,000 bars and produces ranked performance reports.

## File Structure
```
project/
├── data/
│   └── stock/
│       ├── sq_AAPL.csv                  # raw input (350k rows)
│       ├── sq_AAPL_extended.csv         # 158 columns with all indicators
│       └── sq_AAPL_signals.csv          # 206 columns: extended + 48 bsig/ssig
├── docs/
│   └── schema/
│       └── stock_extended.csv           # schema documentation
├── reports/
│   ├── model_summary_DDMMHHMMSS.md
│   ├── model_detailed_DDMMHHMMSS.md
│   └── trades_DDMMHHMMSS.csv
└── src/
    ├── 1_compute_indicators.py
    ├── indicator_signals.py
    └── model.py
```

## Data Schema

Raw Input (sq_AAPL.csv) — 16 columns
```
date, vix, open, high, low, close,
avg_bid, avg_ask, max_ask, min_bid,
average (WAP), barCount, volume,
symbol, localSymbol, conId
```

### Extended (sq_AAPL_extended.csv) — 158 columns

Column prefix conventions:

| Prefix | Indicator Group       | Key Columns                                             | 
|--------|-----------------------|---------------------------------------------------------|
 | fnd_   | Foundation primitives | true_range, typical_price, trade_date                   | 
 | ema_   | EMA Crossover         | ema_9, ema_21, ema_crossover, ema_cross_event           | 
 | mcd_   | MACD                  | mcd_line, mcd_signal, mcd_histogram, mcd_sig_event      | 
 | adx_   | ADX/DMI               | adx_14, adx_plus_di, adx_minus_di, adx_trend_gate       | 
 | atr_   | ATR                   | atr_14, atr_stop_15x, atr_tgt_2rr, atr_spike            | 
 | rsi_   | RSI                   | rsi_14, rsi_cross_50, rsi_cross_30                      | 
 | sto_   | Stochastic          [batch_run_analysis.md](reports/batch_run_analysis.md)  | sto_k, sto_d, sto_cross_up                              | 
 | cci_   | CCI                   | cci_20, cci_cross_0, cci_cross_m100                     | 
 | bbd_   | Bollinger Bands       | bbd_width, bbd_squeeze, bbd_pct_b, bbd_upper, bbd_lower | 
 | chp_   | Choppiness Index      | chp_14, chp_trending, chp_ranging, chp_regime           | 
 | vwp_   | VWAP (session-reset)  | vwp_vwap, vwp_above, vwp_cross_up, vwp_distance         | 
 | obv_   | OBV                   | obv_raw, obv_rising, obv_above_ema, obv_div_bear        | 
 | mfi_   | MFI                   | mfi_14, mfi_cross_50, mfi_bounce                        | 
 | ses_   | Session flags         | ses_after_10, ses_before_345, ses_minute                | 
 | sar_   | Parabolic SAR         | sar_value, sar_bull, sar_flip_bull                      | 
 | don_   | Donchian Channels     | don_upper_20, don_lower_20, don_breakout_up, don_bull   | 
 | arn_   | Aroon                 | arn_up, arn_dn, arn_osc, arn_bull, arn_cross_up         | 
 | vtx_   | Vortex                | vtx_plus, vtx_minus, vtx_bull, vtx_cross_up             | 
 | cmo_   | Chande Momentum       | cmo_14, cmo_bull, cmo_cross_0                           | 
 | tsi_   | True Strength Index   | tsi_val, tsi_signal, tsi_cross_0, tsi_cross_sig         | 
 | roc_   | Rate of Change        | roc_12, roc_bull, roc_cross_0                           | 
 | frc_   | Force Index           | frc_raw, frc_ema_13, frc_bull, frc_cross_0s             | 
 | rsi_   | Stochastic RSI        | srsi_k, srsi_d, srsi_cross_up                           | 
 | rmi_   | Relative Momentum     | rmi_14, rmi_cross_50                                    | 
 | klg_   | Klinger Volume        | klg_line, klg_signal, klg_bull, klg_cross_sig           | 
 | vrc_   | Volume ROC            | vrc_14, vrc_pos, vrc_spike                              | 

### Signals (sq_AAPL_signals.csv) — 206 columns
Extended + 48 signal columns for each of 24 indices: 
    * bsig_xxx (buy, int8) 
    * ssig_xxx (sell, int8)


## File 1: 1_compute_indicators.py

Purpose: Transforms raw AAPL 1-minute CSV into the 158-column extended CSV.

Key functions:

* compute_indicators(df) — main pipeline, returns extended DataFrame
* _wilder_smooth(series, period) — Wilder/RMA smoothing (used by RSI, ATR, ADX, RMI)
* _rolling_apply_mean_dev(series, window) — mean absolute deviation (used by CCI)
* _compute_parabolic_sar(high, low) — full iterative SAR (requires explicit loop, cannot vectorize)
* _compute_klinger(high, low, close, volume) — Klinger Volume Oscillator (requires stateful CM loop)

Run:
```
bashpython 1_compute_indicators.py
# reads  ../data/stock/sq_AAPL.csv
# writes ../data/stock/sq_AAPL_extended.csv
# writes ../docs/schema/stock_extended.csv
```
Important implementation notes:

* atr_14 is computed once under the ADX section and reused everywhere — do not recompute
* fnd_high_14 and fnd_low_14 are shared by Stochastic and Choppiness Index
* VWAP resets per fnd_trade_date group using groupby().cumsum() — critical for intraday correctness
* A df = df.copy() defrag call is inserted before the 13 new indicator sections to suppress pandas PerformanceWarning
* All indicator columns are downcast to float32; binary flags remain int8

## File 2: indicator_signals.py

Purpose: Defines buy and sell signal logic for all 24 indicators. Provides both row-level functions and vectorized DataFrame builders.

24 indicators and their signal logic:

| Key  | Buy Signal                                                    | Sell Signal                  |
|------|---------------------------------------------------------------|------------------------------|
| ema  | ema_cross_event == 1                                          | ema_crossover == 0           | 
| macd | mcd_sig_event==1 AND histogram>0 AND growingmcd_histogram < 0 | 
| adx  | adx_trend_gate==1 AND adx_rising==1                           | adx_trend_gate == 0          | 
| sar  | sar_flip_bull == 1                                            | sar_bull == 0                | 
| don  | don_breakout_up == 1                                          | close < don_lower_20         | 
| arn  | arn_bull==1 OR arn_cross_up==1                                | arn_osc < -20                | 
| vtx  | vtx_cross_up == 1                                             | vtx_minus > vtx_plus         | 
| rsi  | rsi_cross_50==1 OR rsi_cross_30==1                            | rsi_overbought == 1          | 
| sto  | sto_cross_up==1 AND NOT overbought                            | sto_overbought == 1          | 
| cci  | cci_cross_m100==1 OR cci_cross_0==1                           | cci_overbought == 1          | 
| cmo  | cmo_cross_0 == 1                                              | cmo_14 > 50                  | 
| tsi  | tsi_cross_0==1 OR tsi_cross_sig==1                            | tsi_val < 0                  | 
| roc  | roc_cross_0 == 1                                              | roc_12 < 0                   | 
| frc  | frc_cross_0 == 1                                              | frc_ema_13 < 0               | 
| srsi | srsi_cross_up==1 AND NOT overbought                           | srsi_overbought == 1         | 
| rmi  | rmi_cross_50 == 1                                             | rmi_overbought == 1          | 
| atr  | atr_bar_ratio>=0.5 AND atr_spike==0                           | always False                 | 
| bbd  | bbd_expanding==1 AND above_sma==1 AND pct_b>0.5               | bbd_pct_b > 0.95             | 
| chp  | chp_14 < 50                                                   | chp_ranging == 1 (CI > 61.8) | 
| vwap | vwp_above==1 AND vwp_distance>0                               | vwp_above == 0               | 
| obv  | obv_rising==1 AND above_ema==1 AND div_bear==0                | obv_div_bear == 1            | 
| mfi  | mfi_cross_50==1 OR mfi_bounce==1                              | mfi_14 > 80                  | 
| klg  | klg_cross_sig == 1                                            | klg_bull == 0                | 
| vrc  | vrc_pos==1 AND vrc_spike==0                                   | always False                 | 

### Key functions:

* add_buy_signals(df) — adds 24 bsig_xxx columns, fully vectorized
* add_sell_signals(df) — adds 24 ssig_xxx columns, fully vectorized
* BUY_SIGNAL_FUNCS dict — maps name → row-level function
* SELL_SIGNAL_FUNCS dict — maps name → row-level function
* SIGNAL_NAMES list — ordered list of all 24 indicator keys

## File 3: model.py
Purpose: Backtest engine. Runs all 1,221 models, simulates trades, writes reports.
Configuration constants (top of file):
```python
EXTENDED_CSV  = Path('../data/stock/sq_AAPL_extended.csv')
SIGNALS_CSV   = Path('../data/stock/sq_AAPL_signals.csv')
REPORTS_DIR   = Path('../reports')
DATE_START    = '2022-01-01'
DATE_END      = '2023-12-31'
N_SAMPLE      = 10_000
RANDOM_SEED   = 42
TRADE_CAPITAL = 10_000.0      # dollars per trade
COMMISSION    = 2.00          # round-trip $ per trade
ATR_STOP_MULT = 1.5
ATR_TARGET_RR = 2.0
EXIT_MINUTE   = 15 * 60 + 45  # 3:45 PM
TOP_N_DETAIL  = 20
```
Indicator category membership:
```python
TREND      = ['ema', 'macd', 'adx', 'sar', 'don', 'arn', 'vtx']        # 7
MOMENTUM   = ['rsi', 'sto', 'cci', 'cmo', 'tsi', 'roc', 'frc',
               'srsi', 'rmi', 'macd']                                    # 10
VOLATILITY = ['atr', 'bbd', 'chp']                                       # 3
VOLUME     = ['vwap', 'obv', 'mfi', 'klg', 'frc', 'vrc']                # 6
```

**Note:** `macd` appears in both TREND and MOMENTUM; `frc` appears in both MOMENTUM and VOLUME. The combination generator skips any 4-tuple where all four slots are not distinct, reducing 1,260 to **1,221 valid combinations**.

**Pipeline stages:**
```
load_or_build_signals()
  └─ if sq_AAPL_signals.csv exists → read directly
  └─ else → read extended CSV, call add_buy_signals(), add_sell_signals(), save

draw_sample(df)
  └─ filters: ses_after_10==1, ses_before_345==1, atr_spike==0, key cols notna
  └─ random.seed(42), sample 10,000 indices, same for all models

generate_combos()
  └─ product(TREND, MOMENTUM, VOLATILITY, VOLUME) → filter len(set)==4 → 1,221

run_all_models(df, sample_idx, day_dict, day_pos_map)
  └─ for each combo:
       composite = bsig_t & bsig_m & bsig_v & bsig_vol  (vectorized on sample)
       for each fired bar → simulate_trade()
       accumulate summary stats

simulate_trade(df_day, entry_iloc, stop, target, sell_cols, entry_price, shares)
  └─ exit priority: time_box → stop_loss → profit_target → sell_signal
  └─ P&L = (shares × exit_price) - (shares × entry_price) - COMMISSION
```

Position sizing:

```python
shares = floor(10_000 / entry_price)   # integer, no partial shares
stop   = entry_price - atr_14 * 1.5
target = entry_price + atr_14 * 1.5 * 2.0
```

### model_summary_TIMESTAMP.md contains:

* Configuration block
* Aggregate stats across all active models
* Top 50 by Sharpe
* Top 50 by Total P&L
* Bottom 20 by Total P&L
* Full 1,221-row ranked table

### model_detailed_TIMESTAMP.md contains:

* Top 20 models by Total P&L
* Per-model stats (trades, win rate, avg entry/exit, duration, P&L, profit factor, Sharpe, max drawdown, exit breakdown)
* Full trade-by-trade table with cumulative P&L column

### trades_TIMESTAMP.csv contains:

* Every trade from every model
* Columns: model_id, trend, momentum, volatility, volume, trade_date, entry_time, entry_price, shares, stop_loss, profit_target, exit_price, exit_reason, bars_held, cost, proceeds, pnl_dollar, pnl_pct, is_winner, atr_at_entry, rsi_at_entry, adx_at_entry, vwap_at_entry


## Execution Order
```shell
# Step 1 — build extended CSV (run once, ~5-10 min on 350k rows)
python 1_compute_indicators.py

# Step 2 — run all 1,221 models (run as needed; signals CSV cached after first run)
python model.py
```

On second and subsequent model.py runs, the signals CSV is loaded directly (skipping the add_buy/sell_signals step), saving several minutes.


## Key Design Decisions
| Decision                | Choice                                 | Rationale                                                                       |
|-------------------------|----------------------------------------|---------------------------------------------------------------------------------|
| Combination constraint  | No indicator in two slots              | MACD and FRC appear in two categories; prevents double-counting                 |
| Sample strategy         | Fixed 10,000 rows across all models    | Enables fair model comparison — differences are signal-driven not sample-driven |
| Buy screening           | Vectorized bitwise AND                 | Eliminates 99%+ of candidate bars before simulation loop                        |
| Sell exit scope         | Only the 4 model indicators            | Keeps exit logic coupled to entry logic                                         |
| VWAP reset              | Per fnd_trade_date groupby             | Intraday VWAP must restart each session                                         |
| SAR/Klinger             | Explicit Python loops in helpers       | State dependencies between bars prevent vectorization                           |
| Memory                  | float32 for indicators, int8 for flags | Halves memory on 350k × 158 column DataFrame                                    |
| Commission              | $2.00 deducted from pnl_dollar         | $1.00/leg × 2 legs, IBKR Lite/Pro single-contract rate                          |

## Entry Snapshot Fields
These four fields are captured at the moment of trade entry for post-hoc analysis. They do not affect the trade decision (the buy signal has already fired). They are stored in the trades CSV and log file.

| Field | Source Column | What it measures at entry |
|---|---|---|
| `atr_at_entry` | `atr_14` | Average True Range over 14 bars — the volatility "width" at entry; used to compute stop distance and profit target |
| `rsi_at_entry` | `rsi_14` | Relative Strength Index (0–100) — momentum reading; >70 = overbought, <30 = oversold |
| `adx_at_entry` | `adx_14` | Average Directional Index — trend strength (not direction); >25 = trending, <20 = choppy/ranging |
| `vwap_at_entry` | `vwp_vwap` | Volume-Weighted Average Price for the session — whether entry is above or below the day's fair value |

Useful for post-trade filtering: e.g. "did trades entered when RSI > 80 perform worse?" or "did high-ADX entries have better follow-through?"

## Trade Price Sources
| Situation | Column Used | Why |
|---|---|---|
| Entry cost | `avg_ask` | Actual price paid to buy |
| Bracket computation (stop/target levels) | `average` (WAP) | WAP is the fair reference for stop/target levels |
| High-water mark seed | `average` (WAP) at entry | Must match the same series being tracked each bar |
| Trailing stop update each bar | `bar['average']` | Consistent WAP-based decision making |
| Stop-loss trigger | `bar['average'] <= trailing_stop` | WAP crossing stop is more reliable than a wick |
| Exit price (stop hit) | `avg_bid` | Actual price the market pays on exit |
| Exit price (EOD forced) | `avg_bid` | Actual price the market pays on exit |




------

## Project Purpose
[one paragraph description]

## File Structure
[the directory tree]

## Execution Order
[the two commands]

## Key Design Decisions
[the decisions table]

## Data Schema
[column prefixes and key columns]

## Configuration Constants
[the constants block from model.py]

## Indicator Categories
[TREND, MOMENTUM, VOLATILITY, VOLUME lists]

## Critical Implementation Notes
- VWAP must reset per fnd_trade_date group
- atr_14 computed once under ADX, reused everywhere
- SAR and Klinger require explicit Python loops (stateful)
- df.copy() defrag before new indicator sections
- 1,260 combinations - 39 duplicates = 1,221 valid models
- Same 10,000 sample fixed across all models (seed=42)
- $2.00 commission deducted per round-trip trade
```

---

## Claude Code Session Lifecycle

Understanding this will save you a lot of frustration:
```
New session starts
      │
      ▼
Claude Code reads CLAUDE.md         ← your persistent context lives here
      │
      ▼
Claude Code reads files you show it  ← or that it finds via file tools
      │
      ▼
Work happens
      │
      ▼
Session ends → everything in the context window is gone
      │
      ▼
Next session starts → reads CLAUDE.md again