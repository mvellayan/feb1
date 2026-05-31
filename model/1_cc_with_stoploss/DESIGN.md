# 1_cc_with_stoploss — covered call with a trailing combo-net stop-loss

## Purpose

Isolate **one variable**: does adding a stop-loss to the paper2 covered-call book
reduce the full-cycle bleed (the live config lost **−$76,741** replayed over
2026-01-02 .. 2026-03-25 with no stop)?

This engine is **identical to `model/6_paper2`** (signal-gated entry, TV buyback,
weekly Friday expiry, shared cash pool, same signals + option data) **except** an
open stock+call position can **stop out** instead of always riding to expiry.
Everything else is held constant so the P&L delta is attributable to the stop alone.

Continuous replay over the **full dataset** (signals CSV: 2022-09-01 .. 2026-03-25,
~3.5 years), not random windows — every regime, including the 2025 selloffs.

## Decisions (locked)

| Aspect | Choice |
|---|---|
| Stop basis | **Combo-net P&L** — mark-to-market of (long stock + short call) |
| Stop type | **Trailing** — `stop = HWM(net_per_share) − ATR×k`; never moves down |
| On stop fire | **Close the entire BAG** (buy back call + sell stock as one combo); `exit_reason='stop_loss'` |
| Stale-quote bar | **Skip the stop check** until a fresh option quote returns (strict combo-net) |
| Late fire | If the quote returns and net is already ≤ stop, fire at the **returning bar's** combo price; flag `late_stop` |
| Exit priority / bar | `stop_loss` → `buyback_tv` → `Friday expiry` |
| Data range | Full data, continuous, deterministic |
| Coupling | **Standalone** — reuse pure signal/option helpers; do NOT import `arbo702`/`ibapi` |
| Capital | `$1M` shared pool (same as live config) for apples-to-apples vs the −$77k baseline |

## Combo-net trailing stop algorithm

Each **valid** bar with a fresh option quote, while a position is open:

```
net_per_share = (stock_bid_now − stock_ask_entry)     # stock leg: proceeds to sell now
              − (call_ask_now  − call_bid_entry)       # call leg: cost to buy back now
HWM           = max(HWM, net_per_share)                # high-water mark of net P&L
stop_level    = HWM − (atr_14_at_entry × stop_atr_mult)
fire if         net_per_share <= stop_level
```

- At entry `HWM` seeds from the entry-bar mark (≈ −spread); initial stop sits ≈ `ATR×k`
  below breakeven.
- As the combo gains, `HWM` ratchets the stop up and eventually locks in profit.
- Units: `net_per_share` and `ATR` are both $/share; total stop distance = `ATR×k × shares`.
- `atr_14` is taken **at entry** and held fixed for the life of the position
  (the stop band does not rebreathe with intraday ATR).

## Bad-data contract ("ignore bad data")

Skipping is core, not an edge case — option quotes are event-sparse and do not span
the whole range.

- **Bar validation** — usable only if `close, avg_bid, avg_ask, average (WAP), atr_14`
  are all present and finite. Bad bar → skipped: no entry, and an open position's
  stop/buyback checks are **skipped that bar** (never force-exit on missing data).
- **Quote freshness** — an option quote is usable only if a real quote exists within
  `max_quote_age_minutes` of the bar. No fresh quote → no entry; and (locked choice)
  **no stop check** until it returns.
- **Entry needs ATR** — no finite `atr_14` → cannot set a stop → skip entry.
- **Gap-through during a stale gap** — handled by the late-fire rule above.
- **End of data** — positions still open → `status='open_at_end'`, no P&L booked.
- Every skip increments a per-strategy **funnel counter**
  (`bad_bar`, `no_quote`, `no_atr`, `stale_stop_skipped`, `late_stop`, …) so ignored
  data is auditable, never silent.

## Params (`params.json`)

paper2 schema (strategy array: `shares_per_position`, `cooldown_minutes`,
`cc_tv_min/max`, `buyback_tv`, `expiry_label`, `strike_label`, `signal_mode`;
top-level `symbol`, `starting_cash`) **plus**:

| Key | Default | Meaning |
|---|---|---|
| `stop_atr_mult` | `1.5` | Trailing-stop distance in ATR multiples |
| `stop_type` | `"trailing"` | Fixed key (only trailing implemented for now) |
| `stop_basis` | `"combo_net"` | Fixed key (only combo_net implemented for now) |
| `max_quote_age_minutes` | `3` | Option-quote freshness guard |
| `stale_stop_fallback` | `"skip"` | `skip` = strict combo_net (no stop check on a stale bar); `stock_leg` = on a stale bar, fall back to a trailing stop on the stock leg alone, closing the call at the best-effort most-recent quote (else intrinsic). A/B switch. |

### Stock-leg fallback (`stale_stop_fallback="stock_leg"`)

A separate stock-only high-water mark (`hwm_stock`) is maintained every bar.
When the option quote is stale (combo-net un-evaluable), fire if
`(stock_bid − stock_ask_entry) ≤ hwm_stock − ATR×k`.  The option leg is closed at
the most-recent ask within `FALLBACK_QUOTE_AGE_MINUTES` (1 trading day); if none
exists all day, at intrinsic `max(0, stock_bid − strike)`.  Trades carry
`stop_basis ∈ {combo_net, stock_fallback}`.

First run sweeps `stop_atr_mult ∈ {1.0, 1.5, 2.0, 3.0}`.

## Run

```bash
cd model/1_cc_with_stoploss
python model.py --data-first 2022-09-01 --data-last 2026-03-25 [--params PATH] [--stop-atr-mult 1.5]
```

## Outputs (`reports/{mmddhhmi}/`)

Mirror model 6, plus stop-specific fields:

- `trades.csv` — one row/position; adds `stop_level_at_exit`, `bars_to_stop`,
  `late_stop`, `hwm_net`, `exit_reason ∈ {stop_loss, buyback, assigned, expired_otm, open_at_end}`.
- `summary.csv` — one row/strategy: config + per-strategy funnel + exit-reason breakdown
  (how many stop vs buyback vs assigned vs expired_otm) + total/avg P&L + win rate.
- `analysis.md` — total P&L and max drawdown **vs the no-stop model-6 baseline**.
- `run.log` — per-trade INFO log (`[entry]`, `[stop]`, `[buyback]`, `[expiry]`, `[eod]`, `[quote]`).
- `params.json` — copy of the input config for reproducibility.

## Differences vs model 6

| Aspect | model 6 | model 1_cc_with_stoploss |
|---|---|---|
| Exit on adverse move | none (rides to expiry) | trailing combo-net stop → close BAG |
| `arbo702`/`ibapi` import | yes (needs stub locally) | no (standalone) |
| New params | — | `stop_atr_mult`, `stop_type`, `stop_basis` |
| New exit reason | — | `stop_loss` (+ `late_stop` flag) |
