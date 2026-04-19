# paper/

Live AAPL paper-trading engine. Connects to IB TWS, processes 1-minute bars, evaluates
buy signals from pre-built model combos, and manages positions with ATR trailing stops
and a covered-call overlay.

---

## File Structure

```
paper/
├── arbo701.py                    # main trading engine (single entry point)
├── ib_connection_test.py         # standalone IB connectivity smoke-test
├── data/
│   ├── buy_signals.csv           # model combos loaded at startup (model_no, trend, momentum, volatility, volume)
│   ├── position_support.csv      # live position state — one row per active model
│   ├── transaction.csv           # append-only trade log — one row per order leg
│   └── stock/
│       ├── aapl.csv              # rolling last-1000 raw 1-min bars
│       └── aapl_extended.csv     # rolling last-1000 bars + all indicators (recomputed each bar)
└── logs/
    ├── arbo701_ops.log           # INFO: startup, bars processed, order events (hourly rotating)
    ├── arbo701_market.log        # DEBUG: tick data, bar assembly, trailing stops (hourly rotating)
    └── arbo701_trade.log         # INFO: BUY/SELL/CC events only (hourly rotating)
```

## Execution

```shell
cd paper
python arbo701.py              # start live trading

python ib_connection_test.py   # verify IB connectivity (clientId=99, safe to run alongside arbo701)
```

---

## Architecture

### Threading model
- **IB thread**: `EClient.run()` in a daemon thread — handles all IB callbacks (`realtimeBar`, `tickPrice`, `orderStatus`, etc.)
- **Main thread**: blocks on `bar_queue.get(timeout=120)`, runs one full trading cycle per completed bar

### Live data sources
IB paper accounts do not support `reqHistoricalData` (HMDS unavailable). All data comes from realtime quote lines:

| reqId | Call | Data |
|-------|------|------|
| 2 | `reqMktData` | AAPL bid/ask ticks (tickType 1/2) |
| 3 | `reqMktData` | VIX last-price ticks (tickType 4/9) |
| 4 | `reqRealTimeBars` | AAPL 5-sec OHLCV+WAP bars → accumulated to 1-min |
| 100+ | `reqMktData` (dynamic) | Option bid/ask for CC monitoring (one per open CC position) |

### Bar assembly
`realtimeBar` fires every 5 seconds. The callback accumulates into `_rtbar_acc`:
- OHLCV updated each 5-sec bar; WAP = Σ(wap×vol)/Σ(vol)
- When the Unix minute bucket changes → emit completed bar dict to `bar_queue`
- bid/ask/VIX are snapshotted from `_current_bid/ask/vix` at emission time

### reqId allocation
- `1` — reserved (unused, kept clear to avoid IB session conflicts)
- `2` — `REQ_AAPL_MKTDATA` (static, persistent)
- `3` — `REQ_VIX_MKTDATA` (static, persistent)
- `4` — `REQ_AAPL_RTBARS` (static, persistent)
- `100+` — dynamic option subscriptions (`_DYN_REQ_START = 100`)

---

## Trading Cycle (per bar)

`process_bar` runs these 8 steps in order every minute:

| Step | Function | What it does |
|------|----------|--------------|
| 1 | `cancel_stale_orders` | Confirm fills; cancel unfilled buys (2-bar grace for status=`''`) |
| 2 | `check_buy_signals` | Evaluate composite signal; place market BUY if fired |
| 3 | `update_trailing_stops` | Ratchet `current_trailing_stop` up with new WAP high-water |
| 4 | `check_stop_losses` | WAP ≤ trailing stop → sell covered call (or limit sell if no CC available) |
| 5 | `check_profit_targets` | WAP ≥ profit target → limit sell stock at mid |
| 6 | `check_cc_buybacks` | CC ask < $0.50 → buy back call + sell stock |
| 7 | `check_cc_expiry` | Friday ≥ 3:45 PM → assign (ITM) or sell stock (OTM) |
| 8 | `check_eod` | 3:45 PM, no CC → market sell stock |

### Position entry
- Signal: composite AND of 4 indicators (trend ∧ momentum ∧ volatility ∧ volume)
- Order: `MKT BUY 100 shares DAY` — `eTradeOnly=False, firmQuoteOnly=False` required to avoid TWS warning 10268
- Entry price: estimated from WAP; updated to actual `avgFillPrice` from `orderStatus` callback

### Stop / target levels
```
entry_wap = WAP at entry bar
stop      = entry_wap − atr_14 × 1.5
target    = entry_wap + atr_14 × 1.5 × 2.0
trailing  = ratchets up as WAP makes new highs, never down
```

### Covered call selection (at stop trigger)
`find_best_cc_live` scans ITM strikes $1–$5 below WAP, nearest Friday ≤ 4 days:
- Requires a fresh bid/ask quote (< `MAX_QUOTE_AGE_MINUTES` old)
- `MAX_STRIKE_GAP = 2.0` — skip if nearest valid strike is > $2 away from target
- Prefers highest premium; falls back to limit stock sell if no valid CC found
- On open: subscribes `reqMktData(cc_rid)` for persistent bid/ask monitoring

---

## State — position_support.csv

One row per active model. Written to disk after every bar.

| Column | Description |
|--------|-------------|
| `model_no` | model identifier from buy_signals.csv |
| `entry_time` / `entry_price` / `entry_wap` | fill time and prices |
| `stop_loss` / `profit_target` | initial bracket levels |
| `atr_at_entry` / `rsi_at_entry` / `adx_at_entry` / `vwap_at_entry` | snapshot at entry for analysis |
| `high_water` | WAP high-water mark used to ratchet trailing stop |
| `current_trailing_stop` | updated each bar |
| `pending_order_id` | set while market BUY is unconfirmed; cleared on fill |
| `pending_bars` | incremented each bar while fill unconfirmed; row dropped after 2 |
| `cc_symbol` / `cc_local_symbol` / `cc_strike` / `cc_expiry` | set when CC is opened |
| `cc_open_price` / `cc_open_time` | CC leg entry |
| `cc_mktdata_req_id` | reqId of the persistent CC option subscription |

---

## Startup Sequence

```
connect to TWS (port 7497, clientId=1)
  └─ nextValidId → _connected_event.set()

sync_on_startup(app)
  └─ reqPositions → ground truth from IB
  └─ cancel stale pending orders from prior session
  └─ remove position_support rows with no matching IB position
  └─ market-sell any IB positions not tracked in position_support

reqMktData(2, AAPL)          → start bid/ask ticks
reqMktData(3, VIX)           → start VIX ticks
reqRealTimeBars(4, AAPL, 5)  → start 5-sec bar stream

backfill_and_save(app)
  └─ load aapl_extended.csv as-is (no reqHistoricalData)

→ enter bar_queue loop
```

---

## Logging Architecture

Three log files, one `arbo701` logger, three handlers with prefix filters:

| File | Level | Prefixes included |
|------|-------|-------------------|
| `arbo701_ops.log` | INFO | everything **except** market + trade prefixes |
| `arbo701_market.log` | DEBUG | `[tick]` `[bar]` `[trailing]` `[cc monitor]` |
| `arbo701_trade.log` | INFO | `[BUY]` `[fill]` `[orders]` `[stop]` `[CC open]` `[stop exit]` `[target]` `[CC buyback]` `[CC expiry]` `[EOD]` `[txn]` |

Console output: INFO, excludes market prefixes (no tick noise on screen).

---

## Key Design Decisions

| Decision | Choice | Reason |
|----------|--------|--------|
| Bar driver | `reqRealTimeBars` (5-sec) | `reqHistoricalData` unavailable on IB paper accounts (HMDS) |
| Bid/ask source | `reqMktData` ticks | Separate from bar stream; always fresh |
| CC monitoring | Persistent `reqMktData` per open CC | Avoids per-bar open/close hitting 100-line limit |
| Order type | `MKT` for BUY | Limit orders expire unfilled on IB paper; market fills immediately |
| Order attributes | `eTradeOnly=False`, `firmQuoteOnly=False` | Prevents TWS warning 10268 which blocks `orderStatus` callbacks |
| Fill confirmation | `orderStatus` callback | Status stored in `app.order_status[orderId]` |
| Ghost position guard | Drop row if status `''` for 2+ bars | One-bar grace period handles delayed callbacks; hard cancel on bar 2 |
| Trailing stop | WAP-based (not close) | More reliable than wick-based triggers on 1-min bars |
| Position size | Fixed 100 shares | Ensures exactly 1 covered call contract can be written |
| CC strike selection | ITM $1–$5 below WAP | ITM for premium; avoids OTM that expires worthless too easily |
| State persistence | CSV after every bar | Restart-safe; `sync_on_startup` reconciles against IB ground truth |

## External Dependencies

```
ibapi          IB TWS Python API (ibapi package)
pandas         DataFrame operations
numpy          Array math for indicators
```

Indicators imported from `../2_indicator/1_compute_indicators.py` (via importlib — filename starts with digit).
Buy signals imported from `../model/1_tech_indicators_sock_trade/signals.py`.

## TWS Configuration Required

- API enabled: Edit → Global Configuration → API → Settings
- Socket port: `7497` (paper trading)
- Trusted IP: `127.0.0.1`
- `clientId=1` used by arbo701; `clientId=99` used by `ib_connection_test.py`
- Market data subscriptions: AAPL (US stocks) + VIX index
