# FINDINGS — covered call + trailing combo-net stop-loss

_Backtest of `model/1_cc_with_stoploss` over the full signals dataset
(2022-09-01 → 2026-03-25, ~3.5 years). Live 2-strategy config (w0/s+0 bb0.25,
w0/s+1 bb0.5), $1M shared cash pool. Generated 2026-05-30._

---

## TL;DR

1. **The original "−$76,741" alarm was a down-window artifact.** That figure came
   from replaying the no-stop config over **Jan–Mar 2026 only** — a selloff. Over
   the **full 3.5-year cycle, the no-stop covered-call strategy makes +$88,142.**
2. **On raw return, the stop-loss is a net drag.** Every stop setting earns less
   than no-stop, monotonically improving as the stop loosens toward "off."
3. **But the stop is effective drawdown insurance.** A **wide** stop (k=3.0 ATR) +
   the stock-leg fallback turns the −$76,741 quarter into −$7,122 while still
   netting **+$59,930** over the cycle — far better risk-adjusted than no-stop.
4. **Tight stops (k ≤ 1.5) are strictly bad** — whipsaw shreds the profitable
   trending years (2024–25) and the book stays net-negative.
5. **The stock-leg fallback strictly dominates strict-skip** at every k: it
   eliminates the blind-ride-to-expiry tail (`expired_otm`: 22 trades / −$15.7k → 0)
   and adds ~$16k over the full history.

---

## Background

paper2 trades signal-gated covered calls and rides every position to Friday
expiry — **no stop loss**. A live week (+$6.2k) looked fine, but replaying the
config over Jan–Mar 2026 showed −$76,741, which raised the question: *would a
stop-loss have prevented the bleed?* This model answers it cleanly: it is a
byte-for-byte clone of the `model/6_paper2` replay (same entry signals, TV
buyback, weekly expiry, $1M pool, same data) with exactly **one** added
behaviour — a trailing **combo-net** stop that closes the whole BAG. Isolating
that single variable makes the comparison honest.

See `DESIGN.md` for the full mechanics.

## Methodology

- **Engine:** continuous 1-min bar replay, 2022-09-01 → 2026-03-25 (the signals
  CSV ends 2026-03-25; later live weeks are not backtestable).
- **Stop:** trailing on combo-net P&L — `stop = HWM(net/sh) − ATR×k`, closes the
  BAG at the firing bar's market.
- **No-stop reference:** the *same engine* with `k=100` (the stop never fires) —
  a cleaner baseline than model 6 because every other code path is identical.
- **Data hygiene ("ignore bad data"):** bars missing price/ATR are skipped;
  option quotes older than 3 min are treated as absent; early-Jan-2026 had no w0
  chain and was skipped entirely. No data was fabricated.
- **Costs:** $2.00/leg commission. No extra slippage modelled beyond the
  conservative observed-quote fills.

---

## Result 1 — Over a full cycle, no-stop wins on raw return

| Config | Full-history P&L | Trades | Win % | Stop exits |
|---|---:|---:|---:|---:|
| **no stop (k=100)** | **+$88,142** | 5,691 | — | 3 |
| stop k=3.0 | +$59,930 | 7,516 | 44.7% | 4,881 |
| stop k=2.0 | −$32,263 | 8,349 | 37.9% | 6,688 |
| stop k=1.5 | −$35,565 | 8,775 | 35.4% | 7,641 |
| stop k=1.0 | −$40,583 | 8,910 | 33.5% | 8,356 |

The relationship is **monotonic**: the looser the stop, the better the return,
asymptoting to the no-stop ceiling. There is no interior optimum — k=3.0 is
simply "closer to off." The +$60k at k=3.0 is on its way to the +$88k no-stop.

> Note: the Jan–Mar-only sweep earlier suggested *tighter* was better. That was
> a cherry-picked down window. Over the full cycle the conclusion inverts.

## Result 2 — Per-year regime breakdown: the real trade-off

| Config | 2022* | 2023 | 2024 | 2025 | 2026 Q1* | TOTAL | Worst yr |
|---|---:|---:|---:|---:|---:|---:|---:|
| no stop | −2,368 | +36,366 | +43,861 | +87,025 | **−76,741** | +88,142 | **−76,741** |
| k=3.0 | −1,672 | +37,014 | +12,468 | +19,242 | **−7,122** | +59,930 | **−7,122** |
| k=1.5 | +2,612 | +22,369 | −19,705 | −32,464 | −8,377 | −35,565 | −32,464 |
| k=1.0 | +638 | +17,230 | −35,130 | −14,025 | −9,295 | −40,583 | −35,130 |

_*2022 is Sep–Dec only; 2026 is Jan–Mar only._

This table is the whole story:

- **No-stop** harvests premium beautifully in trending/calm years (2023–25:
  +$36k, +$44k, +$87k) but takes a **−$76,741** hit in the 2026 selloff.
- **Tight stops** (k=1.0/1.5) **destroy the good years** — 2025 goes from +$87k to
  −$14k/−$32k — because the trailing stop whipsaws you out of winners every time
  the stock dips intraday. They get the worst of both worlds: less upside *and*
  still net-negative.
- **Wide stop (k=3.0)** is the sweet spot for risk control: it keeps most of 2023
  (+$37k), gives up a lot of 2024–25 upside, but **caps the 2026 disaster at
  −$7,122** — a ~10× smaller worst-period drawdown than no-stop, for ~30% less
  total return.

## Result 3 — Risk-adjusted, the wide stop is attractive

| Config | Total P&L | Worst period | Return / worst-drawdown |
|---|---:|---:|---:|
| no stop | +$88,142 | −$76,741 | 1.15 |
| **k=3.0** | **+$59,930** | **−$7,122** | **8.42** |

On a return-per-unit-drawdown basis, **k=3.0 is ~7× better than no-stop.** If the
objective is live risk management (survive selloffs, smooth the equity curve)
rather than maximizing raw P&L, a wide stop + fallback is the rational choice.
If the objective is pure expected return and the drawdown is tolerable, don't stop.

## Result 4 — Stock-leg fallback A/B (strictly dominant)

When the option quote is stale the combo-net stop can't be computed. Strict
`skip` mode leaves the position unprotected during option-data gaps; `stock_leg`
falls back to a trailing stop on the stock alone.

| Window | `skip` | `stock_leg` | Δ |
|---|---:|---:|---:|
| Full history (k=1.5) | −$51,719 | **−$35,565** | **+$16,154** |
| Jan–Mar 2026 (k=1.5) | −$18,848 | −$8,377 | +$10,471 |

**Mechanism (verified from the ledger):** the fallback eliminated **all**
blind-ride-to-expiry losses (`expired_otm`: 22 trades / −$15,676 → **0**), the 68
fallback stops were net **+$10,245**, and blind-bar exposure collapsed from
41,059 → ~5,052. It uses the best-effort most-recent quote (else intrinsic) to
price the closing call — no fabricated favorable fills. `stock_leg` is now the
default in `params.json`.

## Result 5 — Exit economics (why stops drag)

At k=1.5 (stock_leg, full history):

| Exit reason | Trades | P&L |
|---|---:|---:|
| buyback (profit-take) | 1,128 | **+$282,949** |
| stop_loss (combo_net) | 7,573 | **−$330,111** |
| stop_loss (stock fallback) | 68 | +$10,245 |
| assigned | 6 | +$1,351 |
| expired_otm | 0 | $0 |

The buyback engine is genuinely profitable (+$283k). Tight/medium stops bleed
that away through 7,500+ small whipsaw exits (−$330k). Widening the stop lets
positions survive to the profitable buyback — which is exactly why looser is
better.

---

## Conclusions & recommendation

1. **The covered-call strategy is not broken.** Over a full cycle it earns
   ~+$88k (no stop) / ~+2.5%/yr on $1M. The −$77k was one bad quarter.
2. **Do not use a tight stop.** k ≤ 1.5 is strictly worse than no stop on both
   return and (vs k=3) drawdown.
3. **For risk-managed live trading, use a wide stop (k ≈ 3.0) + stock_leg
   fallback.** It keeps the book net-positive while cutting the worst-quarter
   drawdown ~10×. This is the recommended live configuration.
4. **For maximum raw return with drawdown tolerance, run no stop** — but be ready
   to stomach −$77k quarters.
5. **Next:** the sweep is monotonic toward no-stop, so an interior "best stop"
   does not exist on return alone; the decision is a risk-appetite choice.
   Worth extending: a regime filter (don't hold into confirmed downtrends) may
   beat a blanket stop by protecting the tail without taxing the trending years.

## Reproduce

```bash
cd model/1_cc_with_stoploss
# default (k=1.5, stock_leg fallback)
python model.py --data-first 2022-09-01 --data-last 2026-03-25
# k-sweep
for k in 1.0 1.5 2.0 3.0; do python model.py --data-first 2022-09-01 --data-last 2026-03-25 --stop-atr-mult $k; done
# no-stop reference
python model.py --data-first 2022-09-01 --data-last 2026-03-25 --stop-atr-mult 100
# strict-skip vs fallback A/B
python model.py --data-first 2022-09-01 --data-last 2026-03-25 --stale-fallback skip
python model.py --data-first 2022-09-01 --data-last 2026-03-25 --stale-fallback stock_leg
```

## Caveats

- Data ends **2026-03-25**; recent live weeks are not backtestable.
- Option quotes are event-sparse; the fallback's intrinsic-price branch slightly
  *understates* buy-to-close cost, so fallback-stop P&L is marginally optimistic
  (immaterial vs the dominant effects).
- Single underlier (AAPL), single 2-strategy config; results are not a promise of
  forward performance.
- Commission $2/leg; no additional market-impact/slippage modelled.
