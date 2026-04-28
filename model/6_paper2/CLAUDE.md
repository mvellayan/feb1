# 6_paper2 — historical replay of paper2

## Purpose
Runs the exact paper2 (live) covered-call logic over historical data.  Same
`params.json` schema, same per-strategy `signal_mode` / cooldown / cash / TV
gates, same BAG combo entry/exit mechanics — but walks `sq_AAPL_signals.csv`
+ the historical option quote data instead of connecting to IB.

Use it to:
- Sanity-check a proposed `params.json` against historical P&L before promoting
  it to live paper2.
- Investigate why a given day's live decisions were made (same bars, same gates).
- Compare alternate strategy configs under identical historical conditions.

## Files
| File | Role |
|---|---|
| `all_models.py` | Entry point. |
| `params.json` | Strategy config (same schema as `paper2/params.json`). |
| `CLAUDE.md` | This file. |
| `reports/{mmddhhmi}/` | Output per run (see **Reports** below). |

## Run
```bash
cd model/6_paper2
python all_models.py --data-first 2026-03-01 --data-last 2026-03-25 [--params PATH]
```

Only three flags:
- `--data-first YYYY-MM-DD` (required) — inclusive lower bound.
- `--data-last  YYYY-MM-DD` (required) — inclusive upper bound.
- `--params PATH` (optional) — defaults to `./params.json`.

No `--windows` / `--batch_size` / `--sample_size` / `--seed`.  One
continuous replay over the date range, deterministic.

## Params schema
Identical to `paper2/params.json` — same keys, same validation.  Missing
`signal_mode` defaults to `none`.  See `paper2/CLAUDE.md` for the full
schema.  A copy of the file used for each run is saved to the run directory
as `reports/{mmddhhmi}/params.json`.

## Semantics vs. paper2
Model 6 reuses paper2's helpers by direct import where possible:

| Concern | Source |
|---|---|
| `signal_mode` gate | `paper2.arbo702._signal_mode_fires` |
| Strategy validation | `paper2.arbo702._DEFAULT_STRATEGY` / `SIGNAL_MODES` |
| BAG LMT pricing buffers | `paper2.arbo702.BAG_LMT_ENTRY_BUFFER` / `BAG_LMT_EXIT_BUFFER` |
| Commission | `paper2.arbo702.COMMISSION` |
| Session entry floor | `paper2.arbo702.ENTRY_EARLIEST_MINUTE` (09:35) |
| Friday expiry cutoff | `paper2.arbo702.EOD_MINUTE` (15:45) |
| Strike/expiry resolution | `model/3_covered_calls/single_model.find_cc_variant` |
| Option price lookups | `model/3_covered_calls/single_model.get_option_price_at` |

## BAG fill mechanics — option (ii)
At a firing bar *t*, compute the BAG LMT:
- **Entry**: `lmt = (stock_ask_t − opt_bid_t) + BAG_LMT_ENTRY_BUFFER` (per share).
- **Exit**:  `lmt = (stock_bid_t − opt_ask_t) − BAG_LMT_EXIT_BUFFER` (per share).

Walk bars `t … t+N` (inclusive):
- **Entry** fills on the first bar *j* where `stock_ask_j − opt_bid_j ≤ lmt`.
  Fill price is the **observed market combo** at bar *j* (conservative IB
  LMT semantics: you fill at the prevailing quote, not the limit).
- **Exit** fills on the first bar *j* where `stock_bid_j − opt_ask_j ≥ lmt`.

Timeouts match `paper2.cancel_stale_orders`:
- `ENTRY_BAG_TIMEOUT_BARS = 2`
- `EXIT_BAG_TIMEOUT_BARS  = 5`

Entry timeouts increment a `bag_timeout` funnel counter and no position
opens.  Exit timeouts leave the position open; the next bar's buyback
check re-tries if TV is still below threshold.

## Quote freshness
`MAX_QUOTE_AGE_MINUTES = 3` — **tight** freshness guard (paper2 gets live
quotes).  The option quote CSVs are event-sparse; if the most recent quote
at or before a bar's timestamp is > 3 minutes old, the lookup returns
`None` and the bar is treated as no-quote.  The first occurrence per
`(tag, day)` pair is logged at WARNING level:

```
[quote] stale/missing avg_bid for w0/s+1 at 2026-03-18 10:14:00 (>3 min old)
```

A per-day summary of missing quotes is embedded in `batch_run_analysis.md`.

## Session filter
- Entries allowed only from **09:35 onward** (no upper cutoff — matches
  paper2's `ENTRY_EARLIEST_MINUTE` post-09:35 change).
- Does **not** use `ses_after_10` / `ses_before_345` (those are models
  3/4/5's filter; paper2 diverges).

## Friday expiry
Same as paper2: at `bar_dt.date() == expiry_date` and session-minute ≥
`EOD_MINUTE` (15:45):
- `stock_bid > strike` → `assigned` (stock sold at strike; call exercised).
- `stock_bid ≤ strike` → `expired_otm` (stock sold at `avg_bid`; call worthless).

## End of replay
Positions still open at `--data-last`:
- `status = 'open_at_end'`, **no P&L booked**.
- Shown in `trades.csv` and `summary` counts but not in P&L aggregates.
- Aligns with user choice (b) — leaves the unclosed risk visible without
  fabricating a synthetic exit price.

## Data inputs
- `../../data/stock/sq_AAPL_signals.csv` — 206-column signals CSV.  Model 6
  reads 12 base cols + the 17 `bsig_*` columns needed for signal_mode
  gates.  `bsig_*` values are pre-computed (same derivation paper2 does
  live on each bar), taken as authoritative — no re-run of
  `compute_indicators` / `add_buy_signals` during replay.
- `../../data/option_index.csv` + `../../data/options/**` — same option-chain
  infrastructure used by models 3/4/5.

## Reports — per run directory (`reports/{mmddhhmi}/`)

### `params.json`
Copy of the input params, so the run is reproducible from the directory alone.

### `trades.csv`
One row per position, columns:
```
position_id, strategy_id, signal_mode, expiry_label, strike_label,
strike, expiry_date, entry_time, exit_time,
entry_stock_price, stock_exit_price,
cc_open_price, cc_tv_at_entry, cc_close_price,
exit_reason (buyback | assigned | expired_otm | open_at_end),
days_held, shares, status (closed | open_at_end),
stock_pnl, option_pnl, combined_pnl, is_winner,
atr_at_entry, rsi_at_entry, adx_at_entry, vwap_at_entry
```

### `summary.csv`
One row per strategy.  Config + per-strategy funnel:
```
strategy_id, signal_mode, expiry_label, strike_label,
cc_tv_min, cc_tv_max, buyback_tv, cooldown_minutes, shares_per_position,
trades, win_rate_pct, total_pnl, avg_pnl_per_trade,
signal_skip, cooldown_skip, cash_skip, no_strike, no_quote,
tv_fail_low, tv_fail_high, bag_timeout, accepted
```

### `transaction.csv` (paper2 schema)
One row **per leg** (stock + option logged separately — two rows per
open, two per close).  Identical schema to `paper2/data/transaction.csv`
for direct `diff` comparison between live runs and historical replays:
```
timestamp, position_id, strategy_id, signal_mode, leg, action, symbol,
local_symbol, sec_type, quantity, price, order_id, reason
```

### `batch_run_analysis.md`
Run parameters, strategy table, summary P&L, per-strategy funnel, top 50
missing-quote occurrences.

### `run.log`
Trade-by-trade INFO log for humans; all `[entry]`, `[exit]`, `[expiry]`,
`[eod]`, `[quote]` events.

## Differences vs. paper2 live
| Aspect | paper2 (live) | model 6 (historical) |
|---|---|---|
| Data feed | IB realtime bars + reqMktData snapshots | `sq_AAPL_signals.csv` + event-sparse option quote files |
| `MAX_QUOTE_AGE_MINUTES` | ~N/A (live) | **3** (tight, warns on stale) |
| BAG fill | IB matching engine | Simulated: peek-ahead within 2/5 bars, fill at observed market combo |
| Pending-order cash reservation | Yes (row in `ps` immediately) | No — entries resolve synchronously; non-fills reserve no cash |
| Startup reconciliation | Yes (`reqPositions` etc.) | N/A |
| Logging | `paper2/logs/YYYYMMDD/{ops,market,trade}.log` | `reports/{mmddhhmi}/run.log` |

## Differences vs. models 3/4/5
| Aspect | 3/4/5 | 6 |
|---|---|---|
| Entry trigger | Random sample (4), composite signal (5) | Per-strategy `signal_mode` (paper2 rule) |
| Strategies | Single `variant` config per run | Array of strategies with shared cash pool |
| Windowing | N random 14-day windows | 1 continuous run over `[data_first, data_last]` |
| Signal gate | Hard-coded or fixed across the run | `none / trend_only / momentum_only / both`, per-strategy |
| Fill | Instant fill at bid/ask | 2-bar / 5-bar peek-ahead (option ii) |
| Exit | TV-based buyback, same formula | Same formula + 5-bar BAG peek-ahead |
| Freshness | `MAX_QUOTE_AGE_MINUTES = 30` | **3**, with WARNING logs |
| Reports | `summary.csv`, `trades.csv`, `analysis_variants.csv` | `summary.csv`, `trades.csv`, `transaction.csv`, `batch_run_analysis.md` |
