# paper3/ — arbo703

Live AAPL paper-trading engine = **paper2 + a trailing combo-net stop-loss.**

paper3 forward-tests the in-sample backtest optimum found in
`model/1_cc_with_stoploss/FINDINGS.md`: **w1 / s+0 / k=5**.  Running it on live
unseen bars is the out-of-sample validation of that finding.  Run it alongside
paper2 (which has no stop) on a separate `client_id` to A/B no-stop vs stop live.

## What's different from paper2

Everything is identical to `paper2/arbo702.py` **except**:

1. **A trailing combo-net stop-loss** (`check_cc_stops`, runs each bar *before*
   `check_cc_buybacks`). paper2 has no stop — CCs run to expiry otherwise.
2. **New params** (top-level): `stop_atr_mult`, `stop_basis`, `stop_type`,
   `stale_stop_fallback`.
3. **Two new `position_support.csv` columns:** `hwm_net`, `hwm_stock`
   (high-water marks, persisted for restart-safety).
4. **`client_id = 3`** (paper1=1, paper2=2, paper3=3) so all three can run
   against the same TWS concurrently.

paper3 has its own `data/` and `logs/` (a sibling dir of paper2 under `feb1/`,
so the shared-code imports — `compute_indicators`, `add_buy_signals` — resolve
identically).

## The stop

Each bar, for every open CC (entry filled, no exit pending):

```
net/sh    = (stock_bid − entry_price) − (option_ask − cc_open_price)
hwm_net   = max(hwm_net, net/sh)            # ratchets up, never down
fire when   net/sh ≤ hwm_net − stop_atr_mult × atr_at_entry
```

On fire → submit the **same atomic BAG SELL** the buyback path uses
(reason `stop_loss_combo_net`); `cancel_stale_orders` drops the row when it
fills. `atr_at_entry` is fixed at entry.

**Stale-quote fallback** (`stale_stop_fallback="stock_leg"`): when no live
option ask is available, maintain a stock-leg high-water mark (`hwm_stock`,
updated every bar) and fire when `(stock_bid − entry_price) ≤ hwm_stock − k×ATR`,
pricing the closing BAG off the call's intrinsic value
(`max(0, stock_bid − strike)`).  `"skip"` instead leaves the stop idle until a
fresh ask returns.  Live quotes are usually fresh, so the fallback mainly covers
feed hiccups.

`stop_atr_mult = 0` disables the stop entirely (paper2 parity).

## Params (`params.json`)

Same schema as `paper2/params.json` (see `paper2/CLAUDE.md`) plus the four stop
keys above.  The shipped config is the single optimal strategy:

| Key | Value |
|---|---|
| `expiry_label` / `strike_label` | `w1` / `s+0` |
| `stop_atr_mult` | `5` |
| `stale_stop_fallback` | `stock_leg` |
| `buyback_tv` | `0.25` |
| `cc_tv_min` / `cc_tv_max` | `1.5` / `3.5` |
| `client_id` | `3` |

## Run

```bash
cd paper3
python arbo703.py
```

## Exit priority (per bar)

`stop_loss` → `buyback_tv` → Friday expiry.  (The stop is checked first so a
protective exit pre-empts a buyback.)

## Caveat

`w1/s+0/k=5` is an **in-sample** optimum (one underlier, one window, single-strike
isolated book — not additive to a multi-strategy book). paper3 *is* the forward
test; treat early live results as the validation, not a guarantee.
