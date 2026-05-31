# FINDINGS — covered call + trailing combo-net stop-loss

_Backtest of `model/1_cc_with_stoploss` over the full signals dataset
(2022-09-01 → 2026-03-25, ~3.5 years). Live 2-strategy config (w0/s+0 bb0.25,
w0/s+1 bb0.5), $1M shared cash pool, stock-leg stale-quote fallback.
Generated 2026-05-30._

> **Correction note.** An earlier version of this file (committed 95c673d)
> concluded the stop was "monotonically a return drag with no interior optimum,
> no-stop best on return." That was wrong — it interpolated between k=3 and a
> no-stop reference and missed the peak. Sweeping k = 4…10 revealed a clear
> **interior optimum at k≈8 that beats no-stop on both return and drawdown.**
> This version supersedes it.

---

## TL;DR

1. **A properly-tuned stop genuinely improves the strategy** — it is *not* just
   insurance. The full k-sweep has an **interior peak at k≈8 (+$141,119)** that
   beats the no-stop strategy (+$88,142) on **both** return **and** drawdown.
2. **There's a sharp threshold at k≈3.** Below it the stop sits inside the combo's
   noise and whipsaws constantly (net loss). Above it the stop only fires on real
   adverse moves; return jumps from −$32k (k=2) → +$60k (k=3) → +$141k (k=8).
3. **Two sensible operating points, both dominate no-stop:**
   - **k≈8 — max return** (+$141k), worst quarter −$25k.
   - **k≈3 — min drawdown** (worst quarter −$7k), return +$60k.
4. **Tight stops (k ≤ 2) are net-negative** — whipsaw shreds the trending years.
5. **The stock-leg fallback strictly dominates strict-skip** at every k.

---

## Background

paper2 trades signal-gated covered calls and rides every position to Friday
expiry — **no stop loss**. Replaying the config over Jan–Mar 2026 showed
−$76,741, raising the question: *would a stop-loss have prevented the bleed?*
This model answers it cleanly — a byte-for-byte clone of the `model/6_paper2`
replay (same entry signals, TV buyback, weekly expiry, $1M pool, same data) with
exactly **one** added behaviour: a trailing **combo-net** stop that closes the
whole BAG. See `DESIGN.md` for mechanics.

## Methodology

- **Engine:** continuous 1-min bar replay, 2022-09-01 → 2026-03-25 (signals CSV
  ends 2026-03-25; later live weeks aren't backtestable).
- **Stop:** trailing on combo-net P&L — `stop = HWM(net/sh) − ATR×k`, closes the
  BAG at the firing bar's market.
- **Sweep:** k ∈ {1.0, 1.5, 2.0, 3, 4, 5, 6, 7, 8, 10}, plus a **no-stop
  reference** (k=100, stop never fires) in the *same engine* — the cleanest
  baseline because every other path is identical.
- **Data hygiene:** bars missing price/ATR skipped; option quotes >3 min old
  treated as absent (stock-leg fallback keeps the stop live); early-Jan-2026 had
  no w0 chain and was skipped. No data fabricated.
- **Costs:** $2.00/leg commission; conservative observed-quote fills.

---

## Result 1 — The k-sweep has an interior optimum at k≈8

| k (ATR mult) | Total P&L | Trades | Win % | Worst quarter | Stop exits |
|---|---:|---:|---:|---:|---:|
| 1.0 | −$40,583 | 8,910 | 33.5% | −$35,130 | 8,356 |
| 1.5 | −$35,565 | 8,775 | 35.4% | −$32,464 | 7,641 |
| 2.0 | −$32,263 | 8,349 | 37.9% | — | 6,688 |
| 3.0 | +$59,930 | 7,516 | 44.7% | **−$7,122** | 4,881 |
| 4.0 | +$111,121 | 6,883 | 50.2% | −$16,957 | 3,470 |
| 5.0 | +$120,842 | 6,485 | 53.0% | −$24,940 | 2,524 |
| 6.0 | +$122,848 | 6,265 | 55.0% | −$26,433 | 1,847 |
| 7.0 | +$134,610 | 6,098 | 56.7% | −$24,430 | 1,320 |
| **8.0** | **+$141,119** | 5,995 | **57.6%** | −$24,778 | 958 |
| 10.0 | +$136,468 | 5,898 | 58.4% | −$24,627 | 564 |
| no stop (k=100) | +$88,142 | 5,691 | — | −$76,741 | 3 |

The curve rises from deeply negative (tight) through a steep threshold at k≈3,
**peaks at k=8 (+$141,119)**, then declines toward the no-stop asymptote
(+$88,142). Critically, **k=8 beats no-stop on both axes**: +$141k vs +$88k
return, and a −$25k worst quarter vs −$77k.

## Result 2 — Mechanism: threshold + peak

- **Below k≈3 (whipsaw zone):** the stop band (≈ k×ATR ≈ $3–6/sh) is inside the
  combo's normal intraday noise, so it fires constantly (8,356 stops at k=1.0),
  cutting winners before they reach the profitable buyback. Net-negative.
- **Above k≈3 (value zone):** the band clears the noise (~$9–24/sh) and fires only
  on genuine adverse moves, cutting left-tail losers while leaving winners alone.
- **The peak (k≈8):** widest band that still catches the catastrophic losers. Past
  it (k=10→100), the stop catches fewer losers and regresses to no-stop — which is
  why no-stop's worst quarter balloons to −$77k.

## Result 3 — Per-year regime breakdown

| Config | 2022* | 2023 | 2024 | 2025 | 2026 Q1* | TOTAL |
|---|---:|---:|---:|---:|---:|---:|
| no stop | −2,368 | +36,366 | +43,861 | +87,025 | **−76,741** | +88,142 |
| **k=8 (max return)** | −4,862 | **+44,128** | **+46,339** | +80,292 | **−24,778** | **+141,119** |
| k=3 (min drawdown) | −1,672 | +37,014 | +12,468 | +19,242 | **−7,122** | +59,930 |
| k=1.5 (whipsaw) | +2,612 | +22,369 | −19,705 | −32,464 | −8,377 | −35,565 |

_*2022 is Sep–Dec only; 2026 is Jan–Mar only._

**k=8 beats no-stop in almost every regime** — higher in 2023 (+$44k vs +$36k) and
2024 (+$46k vs +$44k) because it trims left-tail losers even in good years, only
slightly lower in 2025 (+$80k vs +$87k), and far better in the 2026 selloff (−$25k
vs −$77k). The stop, well-tuned, adds value broadly — not just in down markets.

## Result 4 — Risk-adjusted: two operating points

| Config | Total P&L | Worst quarter | Return / worst-DD |
|---|---:|---:|---:|
| no stop | +$88,142 | −$76,741 | 1.15 |
| **k=8 (max return)** | **+$141,119** | −$24,778 | 5.70 |
| **k=3 (min drawdown)** | +$59,930 | **−$7,122** | **8.42** |

- **k=8** — highest absolute return, drawdown 1/3 of no-stop. Default choice for an
  income book that can tolerate a ~−$25k quarter.
- **k=3** — best return-per-drawdown (8.4×); worst quarter only −$7k. Choose if
  capital preservation dominates and you'll accept ~half the return.
- **no-stop is dominated** by k=8 on both axes and should not be run.

## Result 5 — Stock-leg fallback A/B (strictly dominant)

When the option quote is stale the combo-net stop can't be computed. Strict `skip`
leaves the position unprotected during option-data gaps; `stock_leg` falls back to
a trailing stop on the stock alone.

| Window | `skip` | `stock_leg` | Δ |
|---|---:|---:|---:|
| Full history (k=1.5) | −$51,719 | −$35,565 | +$16,154 |
| Jan–Mar 2026 (k=1.5) | −$18,848 | −$8,377 | +$10,471 |

It eliminated all blind-ride-to-expiry losses (`expired_otm` 22/−$15.7k → 0 at
k=1.5), the fallback stops were net positive, and blind-bar exposure collapsed
41,059 → ~5,052. Best-effort most-recent quote (else intrinsic) prices the close —
no fabricated favorable fills. `stock_leg` is the `params.json` default.

## Result 6 — Exit economics at the optimum (k=8)

| Exit reason | Trades | P&L |
|---|---:|---:|
| buyback (profit-take) | 4,962 | **+$503,132** |
| stop_loss | 958 | **−$375,493** |
| assigned | 45 | +$10,236 |
| expired_otm | 30 | +$3,244 |

At k=8 the buyback engine harvests +$503k; the few wide stops (958) cost −$375k but
they're cutting genuine losers, not whipsawing winners — so the net is strongly
positive. Contrast k=1.5, where 7,573 whipsaw stops (−$330k) erased an equal-sized
buyback book. The difference between the two is entirely **stop width**.

---

## Conclusions & recommendation

1. **A tuned stop enhances this strategy** — k≈8 returns +$141k vs +$88k no-stop,
   and dominates it on drawdown too. The earlier "stop is a drag" conclusion was an
   artifact of testing only k ≤ 3.
2. **Never run a tight stop (k ≤ 2)** — strictly the worst configurations.
3. **Default to k≈8** for an income book (set in `params.json`); **drop to k≈3** if
   minimizing drawdown matters more than return.
4. **Don't run no-stop** — it is dominated by k=8 on both return and drawdown.
5. **Next:** the optimum (k≈8) is located on a single underlier / single 2-strategy
   config over one 3.5-yr window — validate stability via walk-forward (e.g. fit k
   on 2022–24, test 2025–26) before trusting it live.

## Reproduce

```bash
cd model/1_cc_with_stoploss
# default (k=8, stock_leg fallback)
python model.py --data-first 2022-09-01 --data-last 2026-03-25
# full k-sweep
for k in 1.0 1.5 2.0 3 4 5 6 7 8 10; do \
  python model.py --data-first 2022-09-01 --data-last 2026-03-25 --stop-atr-mult $k; done
# no-stop reference
python model.py --data-first 2022-09-01 --data-last 2026-03-25 --stop-atr-mult 100
```

## Caveats

- Data ends **2026-03-25**; recent live weeks aren't backtestable.
- The k≈8 optimum is fit on one window / one config — **not validated out-of-sample**
  (see conclusion 5). Treat as in-sample.
- Option quotes are event-sparse; the fallback's intrinsic-price branch slightly
  understates buy-to-close cost (immaterial vs the dominant effects).
- AAPL only; commission $2/leg; no extra market-impact/slippage modelled.
