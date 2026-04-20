# Model 5: Signal-Driven Covered Calls (Union Composite)

## Overview

Signal-driven covered-call strategy. Entry fires when **any** trend indicator
is positive AND **any** momentum indicator is positive — a "union" composite
that replaces the rare 2-indicator or 4-indicator AND conjunctions.

This model trades **materially more often** than models 2–4 while still being
filtered by the TV range, cooldown, and variant TV gate.

**Strategy Type:** Signal-driven income overlay with union composite
**Asset:** AAPL
**Timeframe:** 1-minute bars
**Position Size:** 100 shares per entry
**Commission:** $2.00 per round-trip (stock + option pair)

---

## Entry

For each window, the entry universe is bars where **all** of:

1. `ses_after_10 == 1` AND `ses_before_345 == 1`
2. `atr_spike == 0`
3. `avg_ask` and `avg_bid` are valid
4. **Any** trend buy signal is 1:  `bsig_ema OR bsig_macd OR bsig_adx OR
   bsig_sar OR bsig_don OR bsig_arn OR bsig_vtx`
5. **Any** momentum buy signal is 1:  `bsig_rsi OR bsig_sto OR bsig_cci OR
   bsig_cmo OR bsig_tsi OR bsig_roc OR bsig_frc OR bsig_srsi OR bsig_rmi OR
   bsig_macd`

Entry bars are walked in **chronological order** (no random draw).

For every entry timestamp, each active `(expiry_label, strike_label)` variant
is independently evaluated:

```
cc_tv = option_bid(entry_ts) − max(0, avg_ask(entry_ts) − strike)
```

A variant opens a position iff all three are true:

| Gate | Rule |
|---|---|
| Quote freshness | Option has a valid `avg_bid` within `MAX_QUOTE_AGE_MINUTES` (30) |
| TV range | `cc_tv_min ≤ cc_tv ≤ cc_tv_max` |
| Cooldown | At least `COOLDOWN_MINUTES` (60) have elapsed since this variant's last accepted entry |

The entry timestamp is **shared** across all variants. One signal fire can
open positions in 0, 1, or many variants.

### Entry Snapshot

At entry, `atr_14`, `rsi_14`, `adx_14`, and `vwp_vwap` are captured for
post-hoc analysis. These do not affect the trade decision.

---

## Exit

Identical to model 4 — whichever fires first:

| Priority | Rule | cc_close_reason |
|---|---|---|
| 1 | `option_ask(bar) − max(0, avg_bid(bar) − strike) < buyback_tv` | `buyback` |
| 2 | Bar at or after `expiry_date + 15:00` with `avg_bid > strike` | `assigned` (stock sold at strike) |
| 2 | Bar at or after `expiry_date + 15:00` with `avg_bid ≤ strike` | `expired_otm` (stock sold at avg_bid) |
| 3 | Neither fired before data ran out | `window_end` |

A late-data recheck on the final bar may emit `buyback_late_data`.

---

## The 18-Variant Matrix

Same as models 3/4 — 3 expiries × 6 strikes = 18 variants.

### Expiry Labels

| Label | Expiry |
|---|---|
| `w0` | Friday of the entry week |
| `w1` | Friday of the following week |
| `w2` | Friday two weeks after the entry week |

### Strike Labels

| Label | Position in Chain |
|---|---|
| `s-2` | Third strike below entry price |
| `s-1` | Second strike below |
| `s-0` | First strike below |
| `s+0` | First strike above |
| `s+1` | Second strike above |
| `s+2` | Third strike above |

---

## Budget

**`--batch_size`** (default 1,000): per-variant cap on accepted entries. The
window ends when every variant has filled its batch OR when all signal-fire
bars have been exhausted.

There is **no `--sample_size`**: signal-fire bars are walked deterministically.

### Per-Variant Funnel

| Counter | Meaning |
|---|---|
| `draws` | Signal-fire bars where this variant had a valid quote (sum of the below) |
| `no_quote` | Valid-quote gate failed |
| `tv_fail_low` | `cc_tv < cc_tv_min` |
| `tv_fail_high` | `cc_tv > cc_tv_max` |
| `cooldown_skip` | Inside the 60-minute cooldown window |
| `accepted` | Position opened |
| `batch_full_pct` | `accepted / batch_size` |

---

## CLI Flags

```
--seed SEED              Master RNG seed (omit for a fresh random seed each run)
--workers WORKERS        Parallel worker processes (default: cpu_count − 1)
--data-first DATE        Start of data range      (default: 2023-01-01)
--data-last  DATE        End of data range        (default: 2026-02-28)
--window-days N          Calendar days per window (default: 14)
--windows N              Number of windows        (default: 100)
--batch_size  N          Max accepted trades per variant/win  (default: 1000)
--buyback_tv VAL         Buyback threshold on ask-side TV     (default: 0.25)
--cc_tv_min  VAL         Minimum opening TV to open           (default: 1.00)
--cc_tv_max  VAL         Maximum opening TV to open           (default: 3.00)
--expiry-label w0 w1 …   One or more expiry labels (default: all three)
--strike-label s+0 s-0 … One or more strike labels (default: all six)
```

---

## Execution

```bash
cd model/5_covered_calls
python all_models.py [flags]
```

Loads `data/stock/sq_AAPL_signals.csv` (the 206-column signals CSV — needs the
24 `bsig_*` columns) and the option index into memory in the parent process.

---

## Signal Fire Rate (expected trading volume)

On real AAPL data, session-filtered:

| Signal | Fire rate |
|---|---:|
| Any trend signal positive | 37.8% of bars |
| Any momentum signal positive | 25.4% of bars |
| **Both (entry composite)** | **~11% of bars** |

Translates to ~22 potential entry bars per trading day. The 60-minute
cooldown per variant caps it at ~5–6 distinct entries/day per variant in
practice; with 18 variants, expect **~20–30 trade rows/day** after the TV gate.

---

## Reports

Identical structure to model 4:

```
model/5_covered_calls/
├── all_models.py
├── model.md
└── reports/
    ├── all_runs.csv               # one row per variant per run (appended)
    └── {mmddhhmi}/
        ├── run_params.json
        ├── summary.csv            # per-window × per-variant rows
        ├── trades.csv             # per-trade rows
        ├── 1_run.log … N_run.log  # per-window trade-by-trade log
        ├── analysis_variants.csv  # variants ranked by consistency_score
        └── batch_run_analysis.md
```

Scoring formula is the same 5-term consistency + profitability blend as
model 4:

```
score = 0.25 × pnl_hit_rate
      + 0.15 × sharpe_hit_rate
      + 0.15 × avg_win_rate / 100
      + 0.15 × avg_pf / (avg_pf + 5)
      + 0.30 × (tanh(avg_per_trade_pnl / 30) + 1) / 2
```

---

## Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Entry logic | `(any trend) AND (any momentum)` | Too rare with strict `trend AND momentum` conjunction; random-entry (model 4) has no directional bias |
| Chronological walk | No shuffle | Signal fires are time-ordered; cooldown prunes in-order |
| No sample-size budget | Process every signal-fire bar up to batch cap | Signal-driven universe is already finite |
| 60-min cooldown | Ported from model 4 | Prevents back-to-back entries on signal clusters |
| Combo attribution | Dropped | Union composite collapses 69 indicator pairs to 1 model; trace individual indicators via post-hoc analysis of bsig columns on each entry bar if needed |
| Signals CSV as input | Reads `sq_AAPL_signals.csv` | Needs `bsig_*` columns (extended CSV does not have these) |
