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

Top-level keys (shared across the whole engine):

| Key | Default | Meaning |
|---|---|---|
| `symbol` | `AAPL` | Underlying |
| `starting_cash` | `500000` | Shared cash pool for all strategies |
| `strategies` | `[{…}]` | Array of strategy configs (see below) |
| `host` | `127.0.0.1` | TWS host |
| `port` | `7497` | TWS paper-trading port |
| `client_id` | `2` | IB clientId |
| `retention_days` | `30` | `data/ref/*.csv` older than this get pruned at startup |

Each element of `strategies[]`:

| Key | Meaning |
|---|---|
| `shares_per_position` | Share count for *this* strategy's entries (multiple of 100) |
| `cooldown_minutes` | Minimum minutes between accepted entries **for this strategy** |
| `cc_tv_min` / `cc_tv_max` | Opening-TV gate specific to this strategy |
| `buyback_tv` | Exit threshold; copied to each position row at entry |
| `expiry_label` | `w0` / `w1` / `w2` |
| `strike_label` | `s-2` … `s+2` |

Example — 3 strategies running concurrently:
```json
{
  "symbol":        "AAPL",
  "starting_cash": 500000,
  "strategies": [
    {"shares_per_position": 200, "cooldown_minutes": 15,
     "cc_tv_min": 2.5, "cc_tv_max": 3.5, "buyback_tv": 0.5,
     "expiry_label": "w0", "strike_label": "s-2"},
    {"shares_per_position": 100, "cooldown_minutes": 60,
     "cc_tv_min": 2.5, "cc_tv_max": 3.5, "buyback_tv": 0.5,
     "expiry_label": "w0", "strike_label": "s+2"},
    {"shares_per_position": 100, "cooldown_minutes": 60,
     "cc_tv_min": 2.5, "cc_tv_max": 3.5, "buyback_tv": 0.1,
     "expiry_label": "w0", "strike_label": "s-1"}
  ],
  "host": "127.0.0.1", "port": 7497, "client_id": 2, "retention_days": 30
}
```

Legacy flat `params.json` (one strategy inlined at top level) is auto-wrapped
into `strategies: [ { … } ]` at load time.

## Entry rule

On every completed 1-min bar (before 15:45):

1. Evaluate the composite signal once: `(any bsig_trend) AND (any bsig_momentum)`
2. If it fired, walk every strategy in `strategies[]` and independently check:
   - `bar_dt − last_entry_time_for_strategy_i ≥ strategy_i.cooldown_minutes`
   - `cash_on_hand ≥ strategy_i.shares_per_position × avg_ask`
   - Option at `(strategy_i.expiry_label, strategy_i.strike_label)` has a live bid
   - `cc_tv` is in `[strategy_i.cc_tv_min, strategy_i.cc_tv_max]`
3. For each strategy that passes: submit a **single atomic BAG LMT order**
   (BUY stock + SELL 1 call as one exchange-side combo unit).  LMT =
   `stock_ask − option_bid + BAG_LMT_ENTRY_BUFFER` per share.  Subscribe
   persistent `reqMktData` on the call for buyback monitoring.  BAG
   guarantees atomic fill — no naked stock possible.

Multiple strategies can open on the same bar.  Cash is a shared pool — if
strategy 0 consumes the available cash, later strategies get skipped with a
`log-and-skip`.

## Exit rule

**Buyback (each bar, per open CC):**
`ask − max(0, stock_bid − strike) < buyback_tv` → submit a **single atomic
BAG LMT order** (SELL combo = BUY call + SELL stock).  LMT =
`stock_bid − option_ask − BAG_LMT_EXIT_BUFFER` per share.  Row stays in
`position_support.csv` (pending exit state) until the BAG fills; then the
row is dropped.  `cancel_stale_orders()` re-issues after 5 bars if the exit
LMT hasn't filled.

**Expiry (Friday ≥ 15:45):**
- `stock_bid > strike` → IB auto-assigns (stock sold at strike)
- `stock_bid ≤ strike` → MKT SELL stock

No stop loss, no profit target.  CCs run to expiry otherwise.

**Atomicity guarantee:** every stock+call pair is opened and closed as a
single BAG combo order — no intermediate "stock only" or "call only" state.
If a BAG is rejected or doesn't fill, the position simply doesn't open (on
entry) or stays open (on exit) and is retried next bar.

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
