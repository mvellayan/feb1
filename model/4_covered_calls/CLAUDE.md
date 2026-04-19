# 4_covered_calls — random-entry covered-call backtest

## Purpose
Sells covered calls at **random entry points** (no indicator signals) and evaluates
each (expiry_label × strike_label) variant. Isolates variant/regime effects from
any edge a technical entry would supply.

See `model.md` for the full specification.

## Files
| File | Role |
|---|---|
| `all_models.py` | Self-contained entry point. No `single_model.py` in this model. |
| `model.md` | Human-facing spec. |
| `reports/{mmddhhmi}/` | Output per run. |
| `reports/all_runs.csv` | Cross-run append log (one row per variant per run). |

## Run
```bash
cd model/4_covered_calls
python all_models.py [flags]
```

Full flag list is in `model.md`. Defaults: 100 windows × 14 days, sample 10k /
batch 1k, cc_tv ∈ [1.00, 3.00], buyback_tv 0.25, all 18 variants.

## Entry rule (non-obvious bits)
- Entry price = `avg_ask` at the drawn bar.
- `cc_tv = option_bid(entry_ts) − max(0, avg_ask − strike)` — uses the **bid**
  (premium received) and `avg_ask` (stock paid).
- Gates: valid quote ≤ 30 min old, cc_tv_min ≤ cc_tv ≤ cc_tv_max, ≥ 60 min since
  this variant's last accepted entry. **All three** must pass.
- One random draw is a **shared** candidate across variants — each variant
  independently accepts or rejects it.

## Exit rule (non-obvious bits)
- Buyback is **time-value-based**: `option_ask − max(0, avg_bid − strike) < buyback_tv`
  (uses `avg_bid` for the intrinsic calc, because that's the price you'd realise
  selling the stock).
- Expiry: ITM (`avg_bid > strike`) → `assigned` at strike; OTM → `expired_otm` at
  `avg_bid`.

## Sample / batch accounting
- `sample_size` counts draws that had a valid quote **for at least one** variant.
  Stale-data draws are free.
- `batch_size` is **per-variant**. Window ends when sample is exhausted OR every
  variant has filled its batch.

## trades.csv schema
One row per accepted (variant × entry). `cc_tv` is inserted right after
`cc_open_price`. No `model_id` / `trend` / `momentum` / `volatility` / `volume`
columns (there are no indicators).

## Dependencies on other models
`all_models.py` imports from `../3_covered_calls/single_model.py`:
- Option chain / option data helpers (`load_option_index`, `load_option_data`,
  `get_option_price_at`, `_lookup_prices_vectorized`, `find_cc_variant`)
- Shared constants (`EXPIRY_WEEKS`, `STRIKE_LABELS`, `MAX_QUOTE_AGE_MINUTES`,
  `EXPIRY_QUOTE_MIN_HOUR`, `SHARES`, `COMMISSION`)

And from `../1a_tech_indicators_sock_trade/utils.py`:
- `PF_CAP`, `md_table`

Changing those functions in their source packages affects this model.

## Data inputs
- `data/stock/sq_AAPL_extended.csv` — extended CSV (only 12 columns loaded; the
  48 signal columns from `sq_AAPL_signals.csv` are **not** used).
- `data/option_index.csv` + `data/options/**` — option chain and per-contract
  quotes. Loaded lazily; each worker builds its own cache.

## Reports
- `summary.csv` — per-window × per-variant rows with metrics + funnel counters
  (`draws`, `no_quote`, `tv_fail_low`, `tv_fail_high`, `cooldown_skip`,
  `accepted`, `batch_full_pct`).
- `analysis_variants.csv` — variants ranked by a blended 5-term
  `consistency_score` (70% consistency + 30% profitability).
- `batch_run_analysis.md` — run params + rankings + aggregate funnel.
- `{n}_run.log` — human-readable trade tables.

## Scoring formula (non-obvious)
```
score = 0.25 × pnl_hit_rate
      + 0.15 × sharpe_hit_rate
      + 0.15 × avg_win_rate / 100
      + 0.15 × avg_pf / (avg_pf + PF_SAT_K)        # PF_SAT_K=5, uncapped
      + 0.30 × (tanh(avg_per_trade_pnl / PROF_K) + 1)/2   # PROF_K=30
```
- `PROF_K` and `PF_SAT_K` are module constants in `all_models.py`; tune there.
- Profit factor is **not capped**. "No-losses" sentinel windows
  (PF ≥ 1e8) are excluded from the mean; all-sentinel variants show `inf`.
- `analysis_variants.csv` has two distinct per-trade-ish columns:
  - `avg_total_pnl` = per-window mean of `total_pnl`
  - `avg_per_trade_pnl` = `sum(total_pnl) / sum(trades)`
  Different numbers — don't conflate.
