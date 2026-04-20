# paper2/ — arbo702

Live AAPL paper-trading engine.  Signal-driven covered-call opener with
TV-based buyback and auto-expiry handling by IB.

## File structure
```
paper2/
├── arbo702.py                          # main program (single entry point)
├── params.json                         # JSON params (auto-created with defaults)
├── CLAUDE.md                           # this file
├── data/
│   ├── position_support.csv            # live position state (restart-safe)
│   ├── transaction.csv                 # append-only trade log
│   └── ref/
│       ├── contracts_{yymmdd}.csv      # option chain (1× per day)
│       ├── stock_{yymmdd}.csv          # yesterday's 1-min bars (HMDS)
│       └── stock_{yymmdd}_partial.csv  # today's rolling bars (live)
├── logs/
│   ├── arbo702_ops.log                 # hourly-rotating operations log
│   ├── arbo702_market.log              # tick/bar debug log
│   └── arbo702_trade.log               # trade events only
└── ec2/                                # deployment artefacts
```

## Execution
```bash
cd paper2
python arbo702.py
```

The program creates `params.json` on first run with the defaults below.

## Parameters (`params.json`)

| Key | Default | Meaning |
|---|---|---|
| `symbol` | `AAPL` | Underlying |
| `starting_cash` | `500000` | Portfolio cash; limits concurrent positions |
| `shares_per_position` | `100` | Per-entry share count (multiple of 100) |
| `cooldown_minutes` | `60` | Gate between **accepted** entries (global) |
| `cc_tv_min` | `2.5` | Opening time value floor |
| `cc_tv_max` | `3.6` | Opening time value ceiling |
| `buyback_tv` | `0.5` | Exit threshold on ask-side TV |
| `expiry_label` | `w0` | `w0` = this week's Friday, `w1` = next, `w2` = two ahead |
| `strike_label` | `s-2` | Strike position (`s-2..s+2` relative to chain) |
| `host` | `127.0.0.1` | TWS host |
| `port` | `7497` | TWS paper-trading port |
| `client_id` | `2` | IB clientId (paper1 uses 1, test uses 99) |
| `retention_days` | `30` | `data/ref/*.csv` older than this get pruned at startup |

## Entry rule

Evaluated on every completed 1-min bar (before 15:45):
1. `(any bsig_trend) AND (any bsig_momentum)` on the latest extended row
2. `bar_dt − last_entry_time ≥ cooldown_minutes`
3. `cash_on_hand ≥ shares × avg_ask`
4. Option at `(expiry_label, strike_label)` has a live bid
5. `cc_tv = option_bid − max(0, avg_ask − strike)` is in `[cc_tv_min, cc_tv_max]`

On pass: submits sequential MKT orders — BUY stock, SELL 1 call — and
subscribes persistent `reqMktData` on the call for buyback monitoring.

## Exit rule

**Buyback (each bar, per open CC):**
`ask − max(0, stock_bid − strike) < buyback_tv` → MKT BUY call + MKT SELL stock.

**Expiry (Friday ≥ 15:45):**
- `stock_bid > strike` → IB auto-assigns (stock sold at strike)
- `stock_bid ≤ strike` → MKT SELL stock

No stop loss, no profit target.  CCs run to expiry otherwise.

## Restart idempotency

On startup:
1. `reqPositions` → ground truth
2. Cancel stale pending orders tracked in CSV
3. Drop CSV rows whose stock qty is missing from IB
4. For `(stock + CC)` rows where the CC is missing from IB (expired during
   downtime), MKT SELL the stock and drop the row
5. Flatten any untracked IB stock with MKT SELL
6. For each surviving open CC, re-subscribe `reqMktData`
7. Brief sleep + immediate TV check — close any position already below
   `buyback_tv` (synthesised bar from latest bid/ask ticks)
8. Enter the live bar loop

## Historical bootstrap

`ensure_stock_history()` on startup:
- Yesterday: load `data/ref/stock_{yymmdd}.csv` if cached, else
  `reqHistoricalData` 1D ending yesterday 23:59, save.
- Today: load `stock_{yymmdd}_partial.csv` if present.  If missing and the
  market is already open, try `reqHistoricalData` 1D ending *now* for
  today-so-far.  Both calls log-and-continue if HMDS is unavailable.

Today's partial CSV is rewritten every bar (filtered to today's rows only)
so a restart later in the same session resumes with minimal gap.

## Option chain

`data/ref/contracts_{yymmdd}.csv` — built at the first startup of the day
via `reqContractDetails(symbol='AAPL', secType='OPT', right='C')`.  ~once per
day.  All subsequent strike lookups within the day hit the cache.

## Ref-file retention

At startup, files in `data/ref/` older than `retention_days` are deleted.
Default 30 days.

## Threading

Same pattern as arbo701:
- `IBApp.run()` in a daemon thread (IB callbacks)
- Main thread blocks on `bar_queue.get(timeout=120)`
- One `process_bar()` call per completed 1-min bar

## Dependencies on repo code

- `../2_indicator/1_compute_indicators.py` → `compute_indicators(df)`
- `../model/1a_tech_indicators_sock_trade/signals.py` → `add_buy_signals(df)`

Both are imported at startup; no data files from other models are consumed.

## TWS configuration required

- API enabled (Edit → Global Configuration → API → Settings)
- Socket port 7497 (paper)
- Trusted IP `127.0.0.1`
- `clientId=2` free (paper1 uses 1)
- Market-data entitlement for AAPL stock + options, VIX index

## Differences vs arbo701

| Aspect | arbo701 | arbo702 |
|---|---|---|
| Entry signal | AND of 4 specific indicators (per model) | `any(trend) AND any(momentum)` (union) |
| Models config | `buy_signals.csv` with 1,221 combos | none |
| CC overlay | only on trailing-stop trigger | immediately on every entry |
| CC threshold | `ask < $0.50` (dollar) | `ask − intrinsic < buyback_tv` (time-value) |
| Stop/target | ATR trailing stop, 2R target | neither — CC runs to expiry |
| Entry gate | full indicator signal | signal + cash + cooldown + TV range |
| Position size | fixed 100 | parameter (multiple of 100) |
| Cash accounting | none | tracks cash_used per position |
| Concurrent positions | one per model | many, bounded by cash |
