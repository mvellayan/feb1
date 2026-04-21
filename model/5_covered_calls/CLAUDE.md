# 5_covered_calls — signal-driven covered-call backtest

## Purpose
Covered-call backtest with a **per-variant signal_mode** gate.  Every
variant is a triple `(signal_mode, expiry_label, strike_label)`; the four
signal modes map to:

| signal_mode | Condition to consider a bar |
|---|---|
| `none`           | always (no signal filter — mirrors model 4's random entry) |
| `trend_only`     | `(any bsig_trend == 1)` |
| `momentum_only`  | `(any bsig_momentum == 1)` |
| `both`           | `(any bsig_trend == 1) AND (any bsig_momentum == 1)` |

TREND = ema macd adx sar don arn vtx
MOMENTUM = rsi sto cci cmo tsi roc frc srsi rmi macd

Running all four modes side-by-side lets you measure the marginal value of
each signal layer against the no-signal baseline within one run.

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
buyback_tv 0.25.  Variant matrix = 4 × 3 × 6 = **72 variants**
(signal_modes × expiry_labels × strike_labels).  `--expiry-label` and
`--strike-label` can trim the last two dimensions; the signal-mode
dimension always includes all four modes.

## Entry rule (non-obvious bits)
- Data input is `data/stock/sq_AAPL_signals.csv` (206 cols — needs the 24
  `bsig_*` columns). **Not** the extended CSV.
- `prepare_window` returns all session-eligible bars in chronological order
  and attaches two scratch columns, `_trend_any` / `_mom_any`, so each
  variant can cheaply check its signal-mode gate per-bar.
- **Cooldown is per full variant** — `(signal_mode, expiry, strike)`.  Each
  full variant has an independent 60-min clock that only advances after an
  *accepted* entry (signal-skips, TV fails, cooldown skips, and no-quotes
  do not start the clock).
- Option-chain lookups are cached per `(expiry_label, strike_label)` within
  each bar so all four signal modes share the same find-contract call.

## Exit rule (same as model 4)
- Buyback is TV-based: `option_ask − max(0, avg_bid − strike) < buyback_tv`
- Expiry: ITM → `assigned` at strike; OTM → `expired_otm` at `avg_bid`.

## Batch accounting
- `batch_size` is **per full variant**.  Window ends when every variant
  has filled its batch OR all eligible bars have been walked.
- Modes with looser gates (`none`) fill faster than `both`; the window
  continues until the last variant either fills or runs out of bars.

## trades.csv schema
One row per accepted (variant × entry).  Column order:
`batch_no, trade_no, signal_mode, expiry_label, strike_label, strike,
expiry_date, entry_time, exit_time, entry_stock_price, stock_exit_price,
cc_open_price, cc_tv, cc_open_days, cc_close_price, exit_reason,
bars_held, days_held, shares, stock_pnl, option_pnl, combined_pnl`.

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
- `summary.csv` — per-window × per-variant rows (`signal_mode` column
  included) with metrics + funnel counters: `draws`, `signal_skip`,
  `no_quote`, `tv_fail_low`, `tv_fail_high`, `cooldown_skip`, `accepted`,
  `batch_full_pct`.
- `analysis_variants.csv` — 72-row ranking by 5-term `consistency_score`,
  keyed on `(signal_mode, expiry_label, strike_label)`.
- `batch_run_analysis.md` — run params + rankings + aggregate funnel.
- `{n}_run.log` — human-readable trade tables (signal_mode is the first
  column in each trade's multi-variant block).
- `all_runs.csv` (cross-run) — rolling append, one row per full variant
  per run.  Column order: `run_ts, cc_tv_min, cc_tv_max, buyback_tv,
  signal_mode, expiry_label, strike_label, …`.

## Scoring formula
Same as model 4:
```
score = 0.25 × pnl_hit_rate
      + 0.15 × sharpe_hit_rate
      + 0.15 × avg_win_rate / 100
      + 0.15 × avg_pf / (avg_pf + 5)
      + 0.30 × (tanh(avg_per_trade_pnl / 30) + 1)/2
```

## Expected fire rate per mode
From session-filtered AAPL data (2023–2025):

| signal_mode | Bars qualifying | Pre-cooldown rate |
|---|---|---|
| `none`          | 100% (all eligible bars) | ~390/day |
| `trend_only`    | 37.8% | ~150/day |
| `momentum_only` | 25.4% | ~100/day |
| `both`          | ~11%  | ~22/day |

The 60-min cooldown caps every mode at ~5–6 accepted entries per day per
full variant.  Batch imbalance across modes is expected — `none` typically
fills its batch far earlier than `both` within a given window.
