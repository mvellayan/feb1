# 5_covered_calls — signal-driven covered-call backtest

## Purpose
Like model 4 but with a **union-composite buy signal** replacing random entry:

```
entry = (any bsig_trend == 1) AND (any bsig_momentum == 1)
```

TREND = ema macd adx sar don arn vtx
MOMENTUM = rsi sto cci cmo tsi roc frc srsi rmi macd

Single unified model — no per-combo attribution. Trade volume is two
orders of magnitude above the rare `trend AND momentum` conjunctions of
models 3.

See `model.md` for the full spec.

## Files
| File | Role |
|---|---|
| `all_models.py` | Self-contained entry point. |
| `model.md` | Human-facing spec. |
| `reports/{mmddhhmi}/` | Output per run. |
| `reports/all_runs.csv` | Cross-run append log (one row per variant per run). |

## Run
```bash
cd model/5_covered_calls
python all_models.py [flags]
```

Defaults: 100 windows × 14 days, `--batch_size 1000`, cc_tv ∈ [1.00, 3.00],
buyback_tv 0.25, all 18 variants. No `--sample_size` — signal-fire bars are
walked chronologically until the batch caps fill or the window is exhausted.

## Entry rule (non-obvious bits)
- Data input is `data/stock/sq_AAPL_signals.csv` (206 cols — needs the 24
  `bsig_*` columns). **Not** the extended CSV.
- Entry timestamps come from the chronological list of signal-fire bars;
  `prepare_window` returns them in time order (not shuffled).
- Each entry bar evaluates all active variants.  `cc_tv` gate and 60-min
  cooldown per variant filter which variants actually open.
- MACD appears in both TREND and MOMENTUM — `bsig_macd` is counted once
  (union) in each direction.

## Exit rule (same as model 4)
- Buyback is TV-based: `option_ask − max(0, avg_bid − strike) < buyback_tv`
- Expiry: ITM → `assigned` at strike; OTM → `expired_otm` at `avg_bid`.

## Batch accounting
- `batch_size` is **per-variant**. Window ends when every variant has filled
  its batch OR the signal-fire bar list is exhausted.
- No `sample_size` concept.

## trades.csv schema
Same as model 4: one row per accepted (variant × entry). `cc_tv` is inserted
right after `cc_open_price`. No `model_id` / `trend` / `momentum` columns
(the union composite fires regardless of which specific indicators lit).

## Dependencies on other models
`all_models.py` imports from `../3_covered_calls/single_model.py`:
- `EXPIRY_WEEKS`, `STRIKE_LABELS`, `MAX_QUOTE_AGE_MINUTES`,
  `EXPIRY_QUOTE_MIN_HOUR`, `SHARES`, `COMMISSION`
- `load_option_index`, `load_option_data`, `get_option_price_at`,
  `_lookup_prices_vectorized`, `find_cc_variant`

And from `../1a_tech_indicators_sock_trade/utils.py`:
- `md_table`

## Data inputs
- `data/stock/sq_AAPL_signals.csv` — 206-col signals CSV. Model 5 loads a
  ~28-col subset (12 base cols + 16 `bsig_*` columns).
- `data/option_index.csv` + `data/options/**` — option chain + per-contract
  quotes. Loaded lazily; each worker builds its own cache.

## Reports
Identical to model 4:
- `summary.csv` — per-window × per-variant rows with metrics + funnel
  counters (`draws`, `no_quote`, `tv_fail_low`, `tv_fail_high`,
  `cooldown_skip`, `accepted`, `batch_full_pct`).
- `analysis_variants.csv` — variants ranked by 5-term `consistency_score`.
- `batch_run_analysis.md` — run params + rankings + aggregate funnel.
- `{n}_run.log` — human-readable trade tables.

## Scoring formula
Same as model 4:
```
score = 0.25 × pnl_hit_rate
      + 0.15 × sharpe_hit_rate
      + 0.15 × avg_win_rate / 100
      + 0.15 × avg_pf / (avg_pf + 5)
      + 0.30 × (tanh(avg_per_trade_pnl / 30) + 1)/2
```

## Expected fire rate
From session-filtered AAPL data (2023–2025):
- Any trend buy signal positive: 37.8% of bars
- Any momentum buy signal positive: 25.4% of bars
- Both (entry composite): ~11% of bars ≈ **22 potential entries/trading day**

60-min cooldown per variant caps this to ~5–6 distinct entries/day per
variant. With 18 variants, expect ~20–30 trade rows/day after the TV gate.
