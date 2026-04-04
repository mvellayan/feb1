# 2_indicator/

**Purpose:** Transforms raw stock CSV into indicator-enriched CSV and defines buy/sell signal logic.

## Files

| File | Input | Output | Purpose |
|------|-------|--------|---------|
| `1_compute_indicators.py` | `data/stock/sq_AAPL.csv` | `data/stock/sq_AAPL_extended.csv` | Adds ~142 indicator columns across 26 groups |
| `indicators.md` | — | — | Reference doc for indicator logic and column meanings |

## Key Implementation Notes
- `atr_14` is computed once under the ADX section and reused everywhere — do not recompute
- `fnd_high_14` / `fnd_low_14` shared by Stochastic and Choppiness Index
- VWAP resets per `fnd_trade_date` group via `groupby().cumsum()`
- SAR and Klinger require explicit Python loops (stateful between bars)
- `df.copy()` defrag inserted before the 13 new indicator sections (suppresses PerformanceWarning)
- All indicator columns: float32; binary flags: int8

## Execution
```shell
python 1_compute_indicators.py
# reads  ../data/stock/sq_AAPL.csv
# writes ../data/stock/sq_AAPL_extended.csv
```
Run once. Takes ~5–10 min on 346k rows. Output is cached; signals.py in model/ adds the 48 signal columns on first model run.
