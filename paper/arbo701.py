"""
arbo701.py — AAPL paper trading monitor.

Connects to IB TWS paper account (port 7497), pulls live 1-minute AAPL + VIX
bars, recomputes indicators on a rolling 1000-bar window, checks buy signals
from buy_signals.csv, and manages positions with ATR trailing stops and a
covered-call overlay when the trailing stop fires.

Architecture
────────────
  Single program, single IB connection (CLIENT_ID=1).
  IBApp (EWrapper+EClient) runs in a daemon thread via EClient.run().
  Main thread blocks on bar_queue, processes one completed bar per iteration.
  All state is persisted to CSV files so the program can restart seamlessly.

Bar sources (three keepUpToDate streams)
  REQ_AAPL_TRADES (1) — AAPL TRADES  → open/high/low/close/average/barCount/volume
  REQ_AAPL_BIDASK (2) — AAPL BID_ASK → avg_bid / avg_ask / max_ask / min_bid
  REQ_VIX_TRADES  (3) — VIX  TRADES  → vix (= bar.high)

Column mapping from IB BID_ASK bar  (matches 1_projection/1_process_stock_quotes.py):
  avg_bid  = bar.open   (time-avg bid)
  avg_ask  = bar.close  (time-avg ask)
  max_ask  = bar.high
  min_bid  = bar.low

Exit logic
──────────
  Profit target hit         → limit sell at mid-price
  Trailing stop hit         → sell covered call (best ITM offset $1-$5, nearest Friday ≤4 days)
  Covered call ask < $0.50  → buy back call (limit mid-price) + sell stock (limit mid-price)
  3:45 PM (no covered call) → market sell stock
  Friday close (with CC)    → assigned at strike (ITM) or stock sold at market (OTM)

Files
─────
  paper/data/stock/aapl.csv           last 1000 raw 1-min bars
  paper/data/stock/aapl_extended.csv  last 1000 rows + indicators (recomputed each bar)
  paper/data/buy_signals.csv          model combos (model_no, trend, momentum, volatility, volume)
  paper/data/position_support.csv     open position state — one row per active model
  paper/data/transaction.csv          append-only trade log — one row per leg
  paper/logs/arbo701_ops.log          hourly rotating operations log
  paper/logs/arbo701_market.log       hourly rotating market/tick log
  paper/logs/arbo701_trade.log        hourly rotating trade/execution log

Restart idempotency
───────────────────
  On startup: query IB positions as ground truth, sync position_support.csv,
  cancel any stale orders, backfill missing bars, then resume the bar loop.
"""

from __future__ import annotations

import csv
import importlib.util
import logging
import math
import queue
import sys
import threading
import time
from datetime import datetime, date, timedelta
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import numpy as np
import pandas as pd
from ibapi.client import EClient
from ibapi.contract import Contract
from ibapi.order import Order
from ibapi.wrapper import EWrapper

# ── path setup ─────────────────────────────────────────────────────────────────
_HERE   = Path(__file__).parent
_BASE   = _HERE.parent
_INDIC  = _BASE / '2_indicator'
_MODEL1 = _BASE / 'model' / '1_tech_indicators_sock_trade'
sys.path.insert(0, str(_MODEL1))

# 1_compute_indicators.py starts with a digit — import via importlib
_spec = importlib.util.spec_from_file_location(
    'compute_indicators_mod', _INDIC / '1_compute_indicators.py'
)
_cmod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cmod)
compute_indicators = _cmod.compute_indicators

from signals import add_buy_signals  # noqa: E402  (after sys.path setup)

# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

HOST      = '127.0.0.1'
PORT      = 7497          # TWS paper trading
CLIENT_ID = 1

SYMBOL    = 'AAPL'
SHARES    = 100           # fixed position size; allows selling exactly 1 call

ATR_STOP_MULT        = 1.5
ATR_TARGET_RR        = 2.0
CC_BUYBACK_THRESHOLD = 0.50   # buy back short call when ask drops below this
CC_MAX_EXPIRY_DAYS   = 4      # max calendar days to Friday expiry
MAX_STRIKE_GAP       = 2.0    # max $ from target strike to rounded-down strike
EOD_MINUTE           = 15 * 60 + 45   # 3:45 PM expressed as minutes-since-midnight

COMMISSION           = 2.00   # round-trip per trade
MAX_BARS             = 1000   # rows retained in rolling CSV files
HISTORY_DURATION     = '5 D'  # IB lookback window on startup (covers weekends)
CONN_TIMEOUT         = 20     # seconds to wait for TWS handshake
OPT_QUOTE_TIMEOUT    = 5      # seconds to wait for option bid/ask ticks

# ── reqId layout ──────────────────────────────────────────────────────────────
# Live subscriptions (persistent for the session)
REQ_AAPL_TRADES = 1   # reqHistoricalData keepUpToDate=True  → TRADES bars (bar driver)
REQ_AAPL_MKTDATA = 2  # reqMktData streaming                 → live AAPL bid / ask ticks
REQ_VIX_MKTDATA  = 3  # reqMktData streaming                 → live VIX last-price tick

# One-time historical backfill (keepUpToDate=False, fired once on startup)
_HIST_TRADES_REQ = 10
_HIST_BIDASK_REQ = 11
_HIST_VIX_REQ    = 12

_DYN_REQ_START   = 100   # dynamic reqIds for option quote queries

# ══════════════════════════════════════════════════════════════════════════════
# FILE PATHS
# ══════════════════════════════════════════════════════════════════════════════

PAPER_DATA      = _HERE / 'data'
STOCK_DIR       = PAPER_DATA / 'stock'
LOGS_DIR        = _HERE / 'logs'

AAPL_CSV        = STOCK_DIR / 'aapl.csv'
AAPL_EXT_CSV    = STOCK_DIR / 'aapl_extended.csv'
BUY_SIGNALS_CSV = PAPER_DATA / 'buy_signals.csv'
POS_SUPPORT_CSV = PAPER_DATA / 'position_support.csv'
TRANSACTION_CSV = PAPER_DATA / 'transaction.csv'

SRC_AAPL_CSV    = _BASE / 'data' / 'stock' / 'sq_AAPL.csv'
SRC_AAPL_EXT    = _BASE / 'data' / 'stock' / 'sq_AAPL_extended.csv'

# ══════════════════════════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════════════════════════

def _setup_logging() -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(
        '%(asctime)s  %(levelname)-7s  %(message)s', datefmt='%H:%M:%S'
    )

    trade_prefixes = (
        '[BUY]', '[fill]', '[orders]', '[stop]', '[CC open]', '[stop exit]',
        '[target]', '[CC buyback]', '[CC expiry]', '[EOD]', '[txn]',
    )
    market_prefixes = ('[tick]', '[bar]', '[trailing]', '[cc monitor]')

    class _PrefixFilter(logging.Filter):
        def __init__(self, prefixes: tuple[str, ...], include: bool):
            super().__init__()
            self.prefixes = prefixes
            self.include = include

        def filter(self, record: logging.LogRecord) -> bool:
            msg = record.getMessage()
            matched = any(msg.startswith(prefix) for prefix in self.prefixes)
            return matched if self.include else not matched

    def _hourly_handler(name: str, level: int) -> TimedRotatingFileHandler:
        handler = TimedRotatingFileHandler(
            LOGS_DIR / name,
            when='H',
            interval=1,
            backupCount=24 * 14,
            encoding='utf-8',
        )
        handler.suffix = '%Y%m%d_%H'
        handler.setLevel(level)
        handler.setFormatter(fmt)
        return handler

    ops = _hourly_handler('arbo701_ops.log', logging.INFO)
    ops.addFilter(_PrefixFilter(trade_prefixes + market_prefixes, include=False))

    market = _hourly_handler('arbo701_market.log', logging.DEBUG)
    market.addFilter(_PrefixFilter(market_prefixes, include=True))

    trade = _hourly_handler('arbo701_trade.log', logging.INFO)
    trade.addFilter(_PrefixFilter(trade_prefixes, include=True))

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    ch.addFilter(_PrefixFilter(market_prefixes, include=False))

    lg = logging.getLogger('arbo701')
    lg.setLevel(logging.DEBUG)
    if not lg.handlers:
        lg.addHandler(ops)
        lg.addHandler(market)
        lg.addHandler(trade)
        lg.addHandler(ch)
    return lg

log: logging.Logger = _setup_logging()

# ══════════════════════════════════════════════════════════════════════════════
# IB APP  (EWrapper + EClient)
# ══════════════════════════════════════════════════════════════════════════════

class IBApp(EWrapper, EClient):
    """
    Combined EWrapper / EClient.  Runs in its own daemon thread.
    Main thread communicates via threading.Event and queue.Queue objects.
    """

    def __init__(self):
        EClient.__init__(self, self)

        # ── connection ───────────────────────────────────────────────────────
        self._connected_event = threading.Event()
        self._next_order_id   = 1000
        self._oid_lock        = threading.Lock()

        # ── dynamic reqId counter ────────────────────────────────────────────
        self._dyn_req  = _DYN_REQ_START
        self._req_lock = threading.Lock()

        # ── one-time historical backfill collections ─────────────────────────────
        # Keyed by the _HIST_* reqIds (10, 11, 12), not the live-stream reqIds.
        self._hist_bars: dict[int, list] = {
            _HIST_TRADES_REQ: [], _HIST_BIDASK_REQ: [], _HIST_VIX_REQ: []
        }
        self._hist_done: dict[int, threading.Event] = {
            _HIST_TRADES_REQ: threading.Event(),
            _HIST_BIDASK_REQ: threading.Event(),
            _HIST_VIX_REQ:    threading.Event(),
        }

        # ── live bar assembly ─────────────────────────────────────────────────
        # Only the TRADES keepUpToDate stream drives bar completion.
        # Bid/ask and VIX come from reqMktData tick subscriptions below.
        self._live_trades_ts  : str | None  = None
        self._live_trades_bar : object      = None
        self._bar_lock                      = threading.Lock()
        self.bar_queue        : queue.Queue = queue.Queue()

        # ── live tick state (from reqMktData subscriptions) ───────────────────
        self._current_bid : float | None = None   # AAPL best bid (tickType 1)
        self._current_ask : float | None = None   # AAPL best ask (tickType 2)
        self._current_vix : float | None = None   # VIX last price (tickType 4)

        # ── positions ────────────────────────────────────────────────────────
        self._positions : list[dict] = []
        self._pos_done  = threading.Event()

        # ── open orders ──────────────────────────────────────────────────────
        self._open_orders : dict[int, dict] = {}
        self._orders_done = threading.Event()

        # ── order status (updated by orderStatus callback) ───────────────────
        self.order_status : dict[int, dict] = {}  # orderId → {status, filled, avg_fill}

        # ── option tick collection ────────────────────────────────────────────
        self._opt_bid  : dict[int, float] = {}   # reqId → last bid
        self._opt_ask  : dict[int, float] = {}   # reqId → last ask
        self._opt_done : dict[int, threading.Event] = {}

    # ── connection ─────────────────────────────────────────────────────────────

    def nextValidId(self, orderId: int):
        with self._oid_lock:
            self._next_order_id = orderId
        self._connected_event.set()
        log.info(f"[IB] Connected — next order ID: {orderId}")

    def error(self, reqId: int, errorCode: int, errorString: str,
              advancedOrderRejectJson: str = ''):
        if reqId == -1:
            # Connection-level informational messages
            if errorCode not in (2104, 2106, 2158):
                log.info(f"[IB] info {errorCode}: {errorString}")
            return
        if errorCode >= 2000:
            log.debug(f"[IB] warning reqId={reqId} {errorCode}: {errorString}")
            return
        log.error(f"[IB] error reqId={reqId} code={errorCode}: {errorString}")
        # Unblock waiting events so callers don't hang
        if reqId in self._hist_done:
            self._hist_done[reqId].set()
        if reqId in self._opt_done:
            self._opt_done[reqId].set()

    # ── historical data (one-time backfill, keepUpToDate=False) ──────────────────

    def historicalData(self, reqId: int, bar):
        if reqId in self._hist_bars:
            self._hist_bars[reqId].append(bar)

    def historicalDataEnd(self, reqId: int, start: str, end: str):
        if reqId in self._hist_done:
            n = len(self._hist_bars.get(reqId, []))
            log.info(f"[IB] historicalDataEnd reqId={reqId} ({n} bars)")
            self._hist_done[reqId].set()

    # ── live bar updates  (TRADES keepUpToDate=True only) ────────────────────────

    def historicalDataUpdate(self, reqId: int, bar):
        """
        Called when the current incomplete TRADES bar is updated by IB.
        When the minute timestamp advances the previous bar is complete —
        emit it immediately using the latest bid/ask/vix from reqMktData ticks.
        """
        if reqId != REQ_AAPL_TRADES:
            return
        ts = _bar_minute_key(bar.date)
        with self._bar_lock:
            prev_ts  = self._live_trades_ts
            prev_bar = self._live_trades_bar
            if prev_ts is not None and ts != prev_ts:
                # Previous bar is complete — emit it
                bid = self._current_bid if self._current_bid else prev_bar.average
                ask = self._current_ask if self._current_ask else prev_bar.average
                vix = self._current_vix if self._current_vix else float('nan')
                row = _assemble_bar(prev_bar, bid, ask, vix)
                self.bar_queue.put(row)
                log.debug(f"[bar] {prev_ts}  close={row['close']}  wap={row['average']}  "
                          f"bid={row['avg_bid']}  ask={row['avg_ask']}  vix={row['vix']}")
            self._live_trades_ts  = ts
            self._live_trades_bar = bar

    # ── positions ───────────────────────────────────────────────────────────────

    def position(self, account: str, contract, position: float, avgCost: float):
        self._positions.append({
            'account':     account,
            'symbol':      contract.symbol,
            'localSymbol': contract.localSymbol,
            'conId':       contract.conId,
            'secType':     contract.secType,
            'exchange':    contract.exchange,
            'currency':    contract.currency,
            'position':    position,
            'avgCost':     avgCost,
        })

    def positionEnd(self):
        self._pos_done.set()

    # ── open orders ─────────────────────────────────────────────────────────────

    def openOrder(self, orderId: int, contract, order, orderState):
        self._open_orders[orderId] = {
            'orderId':   orderId,
            'symbol':    contract.symbol,
            'localSymbol': contract.localSymbol,
            'secType':   contract.secType,
            'action':    order.action,
            'orderType': order.orderType,
            'totalQty':  order.totalQuantity,
            'lmtPrice':  order.lmtPrice,
            'status':    orderState.status,
        }

    def openOrderEnd(self):
        self._orders_done.set()

    # ── order status ────────────────────────────────────────────────────────────

    def orderStatus(self, orderId: int, status: str, filled: float,
                    remaining: float, avgFillPrice: float, permId: int,
                    parentId: int, lastFillPrice: float, clientId: int,
                    whyHeld: str, mktCapPrice: float):
        self.order_status[orderId] = {
            'status':   status,
            'filled':   filled,
            'avg_fill': avgFillPrice,
        }
        log.info(f"[order] id={orderId}  status={status}  filled={filled}  avg={avgFillPrice:.4f}")

    # ── tick data ────────────────────────────────────────────────────────────────

    def tickPrice(self, reqId: int, tickType: int, price: float, attrib):
        """
        Handles tick prices from three sources:
          REQ_AAPL_MKTDATA (2) — live AAPL bid (1) / ask (2)
          REQ_VIX_MKTDATA  (3) — live VIX last price (4) or close (9)
          Dynamic reqIds ≥ 100 — option quote collection
        tickType reference: 1=Bid, 2=Ask, 4=Last, 9=Close, 66=DelayedBid, 67=DelayedAsk
        """
        if price <= 0:
            return

        if reqId == REQ_AAPL_MKTDATA:
            if tickType in (1, 66):    # Bid or DelayedBid
                self._current_bid = price
                log.debug(f"[tick] AAPL bid={price}")
            elif tickType in (2, 67):  # Ask or DelayedAsk
                self._current_ask = price
                log.debug(f"[tick] AAPL ask={price}")

        elif reqId == REQ_VIX_MKTDATA:
            if tickType in (4, 9, 68): # Last, Close, or DelayedLast
                self._current_vix = price
                log.debug(f"[tick] VIX={price}")

        elif reqId in self._opt_done:
            # Option quote collection for CC selection
            if tickType in (1, 66):
                self._opt_bid[reqId] = price
            elif tickType in (2, 67):
                self._opt_ask[reqId] = price
            if reqId in self._opt_bid and reqId in self._opt_ask:
                self._opt_done[reqId].set()

    def tickSize(self, reqId: int, tickType: int, size):
        pass   # not needed

    # ── helper methods ──────────────────────────────────────────────────────────

    def next_req_id(self) -> int:
        with self._req_lock:
            rid = self._dyn_req
            self._dyn_req += 1
            return rid

    def next_order_id(self) -> int:
        with self._oid_lock:
            oid = self._next_order_id
            self._next_order_id += 1
            return oid

    def fetch_positions(self, timeout: float = 15.0) -> list[dict]:
        """Synchronously request all IB positions. Returns list of position dicts."""
        self._positions = []
        self._pos_done.clear()
        self.reqPositions()
        if not self._pos_done.wait(timeout=timeout):
            log.warning("[IB] Timed out waiting for positions")
        return list(self._positions)

    def fetch_open_orders(self, timeout: float = 15.0) -> dict[int, dict]:
        """Synchronously request all open orders. Returns {orderId: dict}."""
        self._open_orders = {}
        self._orders_done.clear()
        self.reqAllOpenOrders()
        if not self._orders_done.wait(timeout=timeout):
            log.warning("[IB] Timed out waiting for open orders")
        return dict(self._open_orders)

    def get_option_quote(
        self, contract: Contract, timeout: float = OPT_QUOTE_TIMEOUT
    ) -> tuple[float | None, float | None]:
        """
        Request a real-time streaming quote for one option contract.
        Blocks until bid AND ask arrive or timeout expires.
        Cancels the subscription before returning.
        Returns (bid, ask) or (None, None).
        """
        rid = self.next_req_id()
        evt = threading.Event()
        self._opt_done[rid] = evt
        self._opt_bid.pop(rid, None)
        self._opt_ask.pop(rid, None)

        self.reqMktData(rid, contract, '', False, False, [])
        got = evt.wait(timeout=timeout)
        self.cancelMktData(rid)

        bid = self._opt_bid.pop(rid, None)
        ask = self._opt_ask.pop(rid, None)
        del self._opt_done[rid]

        label = (contract.localSymbol or
                 f"{contract.symbol}{contract.lastTradeDateOrContractMonth}"
                 f"{contract.right}{int(contract.strike)}")
        if got and bid and ask:
            log.info(f"[opt quote] {label}  bid={bid:.4f}  ask={ask:.4f}")
        else:
            log.info(f"[opt quote] {label}  no quote  timeout={not got}")
        return bid, ask


# ══════════════════════════════════════════════════════════════════════════════
# BAR UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

def _bar_minute_key(date_str: str) -> str:
    """Return 'YYYYMMDD HH:MM' from IB's 'YYYYMMDD  HH:MM:SS' bar.date."""
    return date_str.strip().replace('  ', ' ')[:15]


def _parse_bar_dt(date_str: str) -> datetime:
    s = date_str.strip().replace('  ', ' ')
    for fmt in ('%Y%m%d %H:%M:%S', '%Y%m%d %H:%M', '%Y%m%d'):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse bar date: {date_str!r}")


def _assemble_bar(
    trades,
    bid:      float,
    ask:      float,
    vix_high: float,
) -> dict:
    """
    Build one aapl.csv row from a completed TRADES bar plus live tick values.

    bid / ask come from the REQ_AAPL_MKTDATA reqMktData subscription.
    For historical backfill, bid/ask come from the BID_ASK historical bars
    (open=avg_bid, close=avg_ask per 1_projection/1_process_stock_quotes.py).
    max_ask and min_bid are set to ask/bid respectively (best available for live).
    """
    dt = _parse_bar_dt(trades.date)
    return {
        'date':        dt.strftime('%Y-%m-%d %H:%M:%S'),
        'vix':         round(float(vix_high), 4) if not math.isnan(vix_high) else None,
        'open':        trades.open,
        'high':        trades.high,
        'low':         trades.low,
        'close':       trades.close,
        'avg_bid':     round(float(bid), 4),
        'avg_ask':     round(float(ask), 4),
        'max_ask':     round(float(ask), 4),   # best available for live bars
        'min_bid':     round(float(bid), 4),
        'average':     trades.average,
        'barCount':    trades.barCount,
        'volume':      trades.volume,
        'symbol':      SYMBOL,
        'localSymbol': SYMBOL,
        'conId':       '',
    }


# ══════════════════════════════════════════════════════════════════════════════
# DATA FILE MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

_RAW_COLS = [
    'date', 'vix', 'open', 'high', 'low', 'close',
    'avg_bid', 'avg_ask', 'max_ask', 'min_bid',
    'average', 'barCount', 'volume', 'symbol', 'localSymbol', 'conId',
]


def bootstrap_data():
    """
    Create paper/data/stock/{aapl,aapl_extended}.csv from the last MAX_BARS rows
    of the historical source files if they don't exist yet.
    """
    STOCK_DIR.mkdir(parents=True, exist_ok=True)

    if not AAPL_CSV.exists():
        if SRC_AAPL_CSV.exists():
            df = pd.read_csv(SRC_AAPL_CSV, low_memory=False)
            df.tail(MAX_BARS).to_csv(AAPL_CSV, index=False)
            log.info(f"[bootstrap] {AAPL_CSV.name} created from last {MAX_BARS} source rows")
        else:
            pd.DataFrame(columns=_RAW_COLS).to_csv(AAPL_CSV, index=False)
            log.warning(f"[bootstrap] Source not found — created empty {AAPL_CSV.name}")

    if not AAPL_EXT_CSV.exists():
        if SRC_AAPL_EXT.exists():
            df = pd.read_csv(SRC_AAPL_EXT, low_memory=False)
            df.tail(MAX_BARS).to_csv(AAPL_EXT_CSV, index=False)
            log.info(f"[bootstrap] {AAPL_EXT_CSV.name} created from last {MAX_BARS} source rows")
        else:
            log.warning(f"[bootstrap] Source not found — {AAPL_EXT_CSV.name} will be built on first bar")


def _load_raw() -> pd.DataFrame:
    df = pd.read_csv(AAPL_CSV, parse_dates=['date'], low_memory=False)
    return df.sort_values('date').reset_index(drop=True)


def append_and_save(new_row: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Append new_row to aapl.csv, recompute all indicators on the rolling window,
    write both files, and return (df_raw, df_ext).
    Called once per completed bar.
    """
    df_raw = _load_raw()
    new_df = pd.DataFrame([new_row])
    new_df['date'] = pd.to_datetime(new_df['date'])

    df_raw = (pd.concat([df_raw, new_df], ignore_index=True)
                .drop_duplicates(subset='date')
                .sort_values('date')
                .reset_index(drop=True)
                .tail(MAX_BARS)
                .reset_index(drop=True))

    df_ext = compute_indicators(df_raw.copy()).tail(MAX_BARS).reset_index(drop=True)

    df_raw.to_csv(AAPL_CSV, index=False)
    df_ext.to_csv(AAPL_EXT_CSV, index=False)
    return df_raw, df_ext


def backfill_and_save(app: IBApp) -> pd.DataFrame:
    """
    Wait for the three one-time historical requests (_HIST_* reqIds), merge by
    minute timestamp, insert missing bars into aapl.csv, recompute indicators.

    BID_ASK historical bars use the same column mapping as the live projection:
      avg_bid = bar.open, avg_ask = bar.close  (max_ask=high, min_bid=low)

    If a historical stream fails (e.g. outside market hours), it is skipped
    gracefully — existing CSV data is used as-is.
    """
    log.info("[backfill] Waiting for historical data ...")
    for rid, label in [(_HIST_TRADES_REQ, 'TRADES'),
                       (_HIST_BIDASK_REQ, 'BID_ASK'),
                       (_HIST_VIX_REQ,   'VIX')]:
        if not app._hist_done[rid].wait(timeout=60):
            log.warning(f"[backfill] {label} (reqId={rid}) timed out — skipping")

    trades_map = {_bar_minute_key(b.date): b for b in app._hist_bars[_HIST_TRADES_REQ]}
    bidask_map = {_bar_minute_key(b.date): b for b in app._hist_bars[_HIST_BIDASK_REQ]}
    vix_map    = {_bar_minute_key(b.date): b for b in app._hist_bars[_HIST_VIX_REQ]}

    # Require TRADES; BID_ASK is optional (fall back to WAP if empty)
    all_ts = sorted(trades_map.keys())
    log.info(f"[backfill] {len(all_ts)} TRADES bars  |  "
             f"{len(bidask_map)} BID_ASK bars  |  {len(vix_map)} VIX bars")

    df_raw = _load_raw()
    existing_ts = set(df_raw['date'].dt.strftime('%Y%m%d %H:%M').tolist())

    last_vix = None
    new_rows = []
    for ts in all_ts:
        try:
            dt = datetime.strptime(ts, '%Y%m%d %H:%M')
        except ValueError:
            continue
        if dt.strftime('%Y%m%d %H:%M') in existing_ts:
            continue

        tb  = trades_map[ts]
        bb  = bidask_map.get(ts)
        vb  = vix_map.get(ts)

        if vb:
            last_vix = float(vb.high)
        vix_val = last_vix if last_vix is not None else float('nan')

        # BID_ASK mapping: open=avg_bid, close=avg_ask (matches 1_projection)
        bid = float(bb.open)  if bb else float(tb.average)
        ask = float(bb.close) if bb else float(tb.average)

        new_rows.append(_assemble_bar(tb, bid, ask, vix_val))

    if new_rows:
        log.info(f"[backfill] Inserting {len(new_rows)} missing bars")
        new_df = pd.DataFrame(new_rows)
        new_df['date'] = pd.to_datetime(new_df['date'])
        df_raw = (pd.concat([df_raw, new_df], ignore_index=True)
                    .drop_duplicates(subset='date')
                    .sort_values('date')
                    .reset_index(drop=True)
                    .tail(MAX_BARS)
                    .reset_index(drop=True))
        df_raw.to_csv(AAPL_CSV, index=False)
    else:
        log.info("[backfill] No missing bars — CSV is up to date")

    df_ext = compute_indicators(df_raw.copy()).tail(MAX_BARS).reset_index(drop=True)
    df_ext.to_csv(AAPL_EXT_CSV, index=False)
    log.info(f"[backfill] aapl_extended.csv ready ({len(df_ext)} rows)")
    return df_ext


# ══════════════════════════════════════════════════════════════════════════════
# POSITION SUPPORT  &  TRANSACTIONS
# ══════════════════════════════════════════════════════════════════════════════

# Columns for position_support.csv
_PS_COLS = [
    'model_no', 'symbol', 'local_symbol',
    'entry_time', 'entry_price', 'entry_wap',
    'stop_loss', 'profit_target',
    'atr_at_entry', 'rsi_at_entry', 'adx_at_entry', 'vwap_at_entry',
    'high_water',             # WAP high-water mark — used to ratchet trailing stop
    'current_trailing_stop',  # updated every bar
    'pending_order_id',       # set when a limit buy is waiting to fill
    # Covered call fields (populated when CC is opened)
    'cc_symbol', 'cc_local_symbol', 'cc_strike', 'cc_expiry',
    'cc_open_price', 'cc_open_time',
]

# Columns for transaction.csv
_TX_COLS = [
    'timestamp', 'model_no', 'leg', 'action',
    'symbol', 'local_symbol', 'sec_type', 'quantity',
    'price', 'order_id', 'reason',
]


def load_ps() -> pd.DataFrame:
    if not POS_SUPPORT_CSV.exists():
        return pd.DataFrame(columns=_PS_COLS)
    return pd.read_csv(
        POS_SUPPORT_CSV,
        dtype={'model_no': str, 'pending_order_id': str},
        low_memory=False,
    )


def save_ps(df: pd.DataFrame):
    df.to_csv(POS_SUPPORT_CSV, index=False)


def _init_transaction_file():
    TRANSACTION_CSV.parent.mkdir(parents=True, exist_ok=True)
    if not TRANSACTION_CSV.exists():
        with open(TRANSACTION_CSV, 'w', newline='') as f:
            csv.DictWriter(f, fieldnames=_TX_COLS).writeheader()


def _log_txn(row: dict):
    """Append one row to transaction.csv and write to the log."""
    with open(TRANSACTION_CSV, 'a', newline='') as f:
        csv.DictWriter(f, fieldnames=_TX_COLS, extrasaction='ignore').writerow(row)
    log.info(f"[txn] {row}")


# ══════════════════════════════════════════════════════════════════════════════
# CONTRACT  &  ORDER HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _stk_contract() -> Contract:
    c = Contract()
    c.symbol      = SYMBOL
    c.secType     = 'STK'
    c.exchange    = 'SMART'
    c.primaryExch = 'NASDAQ'
    c.currency    = 'USD'
    return c


def _vix_contract() -> Contract:
    c = Contract()
    c.symbol   = 'VIX'
    c.secType  = 'IND'
    c.exchange = 'CBOE'
    c.currency = 'USD'
    return c


def _option_contract(strike: float, expiry: date, right: str = 'C') -> Contract:
    c = Contract()
    c.symbol     = SYMBOL
    c.secType    = 'OPT'
    c.exchange   = 'SMART'
    c.currency   = 'USD'
    c.lastTradeDateOrContractMonth = expiry.strftime('%Y%m%d')
    c.right      = right
    c.strike     = strike
    c.multiplier = '100'
    return c


def _limit_order(action: str, qty: int, price: float) -> Order:
    o = Order()
    o.action        = action
    o.orderType     = 'LMT'
    o.totalQuantity = qty
    o.lmtPrice      = round(price, 2)
    o.tif           = 'DAY'
    return o


def _market_order(action: str, qty: int) -> Order:
    o = Order()
    o.action        = action
    o.orderType     = 'MKT'
    o.totalQuantity = qty
    o.tif           = 'DAY'
    return o


def _mid(bid: float, ask: float) -> float:
    return round((bid + ask) / 2, 2)


# ══════════════════════════════════════════════════════════════════════════════
# COVERED CALL  —  LIVE OPTION SELECTION
# ══════════════════════════════════════════════════════════════════════════════

def _nearest_friday(from_date: date) -> date | None:
    """Nearest Friday on or after from_date, or None if > CC_MAX_EXPIRY_DAYS away."""
    days_ahead = (4 - from_date.weekday()) % 7   # Mon=0…Fri=4; 0 if already Friday
    friday = from_date + timedelta(days=days_ahead)
    return friday if (friday - from_date).days <= CC_MAX_EXPIRY_DAYS else None


def find_best_cc_live(
    app:          IBApp,
    trigger_date: date,
    trigger_avg:  float,
    entry_price:  float,
) -> dict | None:
    """
    Query IB live for each of offsets $1–$5 ITM, pick the option with the
    highest ITM P&L assuming assignment at expiry.

    itm_pnl = (strike - entry_price) * SHARES + premium * SHARES - 2 * COMMISSION

    Returns a dict with keys:
        strike, expiry, expiry_ib, local_symbol,
        bid, ask, premium, itm_pnl
    or None if no viable option found.
    Logs the full candidate table.
    """
    friday = _nearest_friday(trigger_date)
    if friday is None:
        log.info(f"[cc select] No Friday ≤ {CC_MAX_EXPIRY_DAYS} days from {trigger_date} — fallback")
        return None

    log.info(f"[cc select] trigger_avg={trigger_avg:.4f}  entry={entry_price:.4f}  "
             f"expiry={friday.isoformat()}")
    log.info(f"[cc select] {'offset':>6}  {'strike':>7}  {'bid':>7}  {'ask':>7}  "
             f"{'premium':>8}  {'itm_pnl':>9}  note")

    best     = None
    best_pnl = float('-inf')
    seen_strikes: set[float] = set()

    for offset in (1.0, 2.0, 3.0, 4.0, 5.0):
        target = trigger_avg - offset
        strike = math.floor(target)          # floor to nearest $1 (AAPL strike grid)
        gap    = target - strike

        def _note(msg: str):
            log.info(f"[cc select]  ${offset:4.0f}   {strike:7.2f}  {'':>7}  {'':>7}  "
                     f"{'':>8}  {'':>9}  {msg}")

        if gap > MAX_STRIKE_GAP:
            _note(f"skip — gap ${gap:.1f} > MAX_STRIKE_GAP")
            continue
        if strike in seen_strikes:
            _note("skip — duplicate strike")
            continue
        seen_strikes.add(strike)

        contract = _option_contract(strike, friday)
        bid, ask = app.get_option_quote(contract)

        if bid is None or ask is None:
            _note("skip — no live quote")
            continue

        premium = bid   # we sell at bid (market buys from us)
        itm_pnl = (strike - entry_price) * SHARES + premium * SHARES - 2 * COMMISSION
        c_label = f"AAPL {friday.strftime('%y%m%d')}C{int(strike * 1000):08d}"

        log.info(f"[cc select]  ${offset:4.0f}   {strike:7.2f}  {bid:7.4f}  {ask:7.4f}  "
                 f"{premium:8.4f}  {itm_pnl:9.2f}  "
                 f"{'<<< best' if itm_pnl > best_pnl else ''}")

        if itm_pnl > best_pnl:
            best_pnl = itm_pnl
            best = {
                'strike':      strike,
                'expiry':      friday,
                'expiry_ib':   friday.strftime('%Y%m%d'),
                'local_symbol': c_label,
                'bid':         bid,
                'ask':         ask,
                'premium':     round(premium, 4),
                'itm_pnl':     round(itm_pnl, 2),
                'contract':    contract,
            }

    if best:
        log.info(f"[cc select] Best: strike={best['strike']}  premium={best['premium']}  "
                 f"itm_pnl={best['itm_pnl']}")
    else:
        log.info("[cc select] No viable option found — fallback to stop_loss exit")
    return best


# ══════════════════════════════════════════════════════════════════════════════
# STARTUP SYNC
# ══════════════════════════════════════════════════════════════════════════════

def sync_on_startup(app: IBApp) -> pd.DataFrame:
    """
    Reconcile IB positions (ground truth) with position_support.csv.

    Rules:
      1. Cancel all pending buy orders tracked in position_support.
      2. Remove rows whose stock position no longer exists in IB.
      3. Close IB stock positions that have no matching row in position_support.
    Returns the cleaned position_support DataFrame.
    """
    log.info("[startup] Fetching IB positions ...")
    ib_positions = app.fetch_positions()
    log.info(f"[startup] {len(ib_positions)} IB position(s):")
    for p in ib_positions:
        log.info(f"  {p['secType']:3s}  {p['localSymbol']:25s}  "
                 f"qty={p['position']:6.0f}  avgCost={p['avgCost']:.4f}")

    ps = load_ps()

    # 1. Cancel stale pending buy orders
    if not ps.empty:
        for _, row in ps.iterrows():
            oid_str = str(row.get('pending_order_id', ''))
            if oid_str and oid_str not in ('nan', '', 'None'):
                try:
                    oid = int(float(oid_str))
                    log.info(f"[startup] Cancelling stale order {oid} (model {row['model_no']})")
                    app.cancelOrder(oid)
                except (ValueError, TypeError):
                    pass
        ps['pending_order_id'] = None

    # Build lookup of AAPL stock positions in IB (qty > 0)
    ib_stk = {p['localSymbol']: p for p in ib_positions
              if p['symbol'] == SYMBOL and p['secType'] == 'STK' and p['position'] > 0}

    # 2. Remove position_support rows with no matching IB stock position
    if not ps.empty:
        valid = ps['local_symbol'].isin(set(ib_stk.keys()))
        for _, row in ps[~valid].iterrows():
            log.info(f"[startup] Removing stale support row model={row['model_no']} "
                     f"sym={row['local_symbol']} (no IB position)")
        ps = ps[valid].reset_index(drop=True)

    # 3. Close IB positions not tracked in position_support
    tracked = set(ps['local_symbol'].tolist()) if not ps.empty else set()
    for sym, p in ib_stk.items():
        if sym in tracked:
            continue
        qty = int(p['position'])
        log.warning(f"[startup] Untracked IB position {sym} qty={qty} — closing at market")
        oid = app.next_order_id()
        app.placeOrder(oid, _stk_contract(), _market_order('SELL', qty))
        _log_txn({
            'timestamp':    datetime.now().isoformat(), 'model_no':  'UNKNOWN',
            'leg': 'stock',  'action': 'SELL',          'symbol':    SYMBOL,
            'local_symbol': sym, 'sec_type': 'STK',     'quantity':  qty,
            'price': 0,      'order_id': oid,            'reason':    'startup_close_untracked',
        })

    save_ps(ps)
    log.info(f"[startup] position_support.csv: {len(ps)} active row(s)")
    return ps


# ══════════════════════════════════════════════════════════════════════════════
# TRADING CYCLE  —  one call per completed 1-minute bar
# ══════════════════════════════════════════════════════════════════════════════

def _ses_minute(dt: datetime) -> int:
    """Minutes since midnight (e.g. 10:30 → 630)."""
    return dt.hour * 60 + dt.minute


def _has_pending(row) -> bool:
    oid = str(row.get('pending_order_id', ''))
    return oid not in ('', 'nan', 'None')


def _has_cc(row) -> bool:
    return str(row.get('cc_symbol', '')) not in ('', 'nan', 'None')


# ── step 1: cancel unfilled buy orders from the prior bar ─────────────────────

def cancel_stale_orders(app: IBApp, ps: pd.DataFrame) -> pd.DataFrame:
    """
    Confirm fills and drop any unfilled buy orders from the prior bar.
    Rows with a pending_order_id that is Filled get entry_price updated
    from avg_fill and pending_order_id cleared.
    Rows whose order did not fill (still pending or unexpected status) are
    REMOVED from position_support — no ghost positions that could trigger
    spurious sells.
    """
    ps = ps.copy()
    rows_to_drop = []

    for i, row in ps.iterrows():
        if not _has_pending(row):
            continue
        try:
            oid = int(float(row['pending_order_id']))
        except (ValueError, TypeError):
            rows_to_drop.append(i)
            continue

        st     = app.order_status.get(oid, {})
        status = st.get('status', '')

        if status == 'Filled':
            fill = float(st.get('avg_fill', row['entry_price']))
            log.info(f"[fill] model={row['model_no']}  order {oid} filled @ {fill:.4f}")
            atr = float(row['atr_at_entry'])
            new_stop   = round(fill - atr * ATR_STOP_MULT, 4)
            new_target = round(fill + atr * ATR_STOP_MULT * ATR_TARGET_RR, 4)
            ps.at[i, 'entry_price']           = fill
            ps.at[i, 'entry_wap']             = fill
            ps.at[i, 'stop_loss']             = new_stop
            ps.at[i, 'profit_target']         = new_target
            ps.at[i, 'high_water']            = fill
            ps.at[i, 'current_trailing_stop'] = new_stop
            ps.at[i, 'pending_order_id']      = None
        elif status in ('PreSubmitted', 'Submitted', ''):
            # Market order still in flight — cancel and drop; signal can re-fire next bar
            log.info(f"[orders] Cancelling unfilled buy order {oid} model={row['model_no']} — dropping row")
            app.cancelOrder(oid)
            rows_to_drop.append(i)
        else:
            # Cancelled, Inactive, or other terminal non-fill status — drop
            log.info(f"[orders] order {oid} status={status} model={row['model_no']} — dropping row")
            rows_to_drop.append(i)

    if rows_to_drop:
        ps = ps.drop(index=rows_to_drop).reset_index(drop=True)

    return ps


# ── step 2: check buy signals ─────────────────────────────────────────────────

def check_buy_signals(
    app:     IBApp,
    df_ext:  pd.DataFrame,
    models:  pd.DataFrame,
    ps:      pd.DataFrame,
    bar:     dict,
    bar_dt:  datetime,
) -> pd.DataFrame:
    """
    For each model not already active, check composite buy signal on latest bar.
    Places a limit buy at mid-price if signal fires.
    Does not enter after 3:45 PM.
    """
    if _ses_minute(bar_dt) >= EOD_MINUTE or df_ext.empty:
        return ps

    ps           = ps.copy()
    active_set   = set(str(m) for m in ps['model_no'].tolist()) if not ps.empty else set()
    df_sig       = add_buy_signals(df_ext.tail(10).copy())
    last         = df_sig.iloc[-1]
    last_ext     = df_ext.iloc[-1]

    for _, model in models.iterrows():
        mn  = str(model['model_no'])
        if mn in active_set:
            continue

        t, m, v, vol = (str(model['trend']),   str(model['momentum']),
                        str(model['volatility']), str(model['volume']))
        try:
            fired = (int(last[f'bsig_{t}']) == 1 and
                     int(last[f'bsig_{m}']) == 1 and
                     int(last[f'bsig_{v}']) == 1 and
                     int(last[f'bsig_{vol}']) == 1)
        except KeyError as e:
            log.warning(f"[signals] Missing column {e} for model {mn}")
            continue

        if not fired:
            continue

        atr = float(last_ext.get('atr_14', float('nan')))
        if math.isnan(atr) or atr <= 0:
            log.info(f"[signals] Model {mn} fired — ATR invalid, skip")
            continue

        bar_wap = float(bar['average'])
        stop    = round(bar_wap - atr * ATR_STOP_MULT, 4)
        target  = round(bar_wap + atr * ATR_STOP_MULT * ATR_TARGET_RR, 4)
        oid     = app.next_order_id()

        app.placeOrder(oid, _stk_contract(), _market_order('BUY', SHARES))
        log.info(f"[BUY] model={mn} ({t}/{m}/{v}/{vol})  "
                 f"market  wap={bar_wap:.4f}  stop={stop}  target={target}  oid={oid}  "
                 f"atr={atr:.4f}  rsi={last_ext.get('rsi_14','?')}  "
                 f"adx={last_ext.get('adx_14','?')}  vwap={last_ext.get('vwp_vwap','?')}")

        ps_row = {
            'model_no':             mn,
            'symbol':               SYMBOL,
            'local_symbol':         SYMBOL,
            'entry_time':           bar_dt.isoformat(),
            'entry_price':          bar_wap,   # estimated; updated to avg_fill on confirm
            'entry_wap':            bar_wap,
            'stop_loss':            stop,
            'profit_target':        target,
            'atr_at_entry':         round(atr, 4),
            'rsi_at_entry':         round(float(last_ext.get('rsi_14', float('nan'))), 4),
            'adx_at_entry':         round(float(last_ext.get('adx_14', float('nan'))), 4),
            'vwap_at_entry':        round(float(last_ext.get('vwp_vwap', float('nan'))), 4),
            'high_water':           bar_wap,
            'current_trailing_stop': stop,
            'pending_order_id':     oid,
            'cc_symbol':            None, 'cc_local_symbol': None,
            'cc_strike':            None, 'cc_expiry':        None,
            'cc_open_price':        None, 'cc_open_time':     None,
        }
        ps = pd.concat([ps, pd.DataFrame([ps_row])], ignore_index=True)
        _log_txn({
            'timestamp': bar_dt.isoformat(), 'model_no': mn,   'leg': 'stock',
            'action': 'BUY',                 'symbol': SYMBOL, 'local_symbol': SYMBOL,
            'sec_type': 'STK',               'quantity': SHARES, 'price': bar_wap,
            'order_id': oid,                 'reason': f'signal_{t}_{m}_{v}_{vol}',
        })
        active_set.add(mn)

    return ps


# ── step 3: update trailing stops ─────────────────────────────────────────────

def update_trailing_stops(ps: pd.DataFrame, bar_wap: float) -> pd.DataFrame:
    """
    For every filled stock-only position (no covered call, no pending order),
    ratchet the trailing stop up if WAP has moved above the high-water mark.
    """
    ps = ps.copy()
    for i, row in ps.iterrows():
        if _has_pending(row) or _has_cc(row):
            continue
        hw  = float(row['high_water'])
        cur = float(row['current_trailing_stop'])
        if bar_wap > hw:
            gain = bar_wap - hw
            ps.at[i, 'high_water']            = bar_wap
            ps.at[i, 'current_trailing_stop'] = round(cur + gain, 4)
            log.debug(f"[trailing] model={row['model_no']}  hw {hw:.4f}→{bar_wap:.4f}  "
                      f"stop {cur:.4f}→{ps.at[i,'current_trailing_stop']:.4f}")
    return ps


# ── step 4: check stop losses → covered call pivot ────────────────────────────

def check_stop_losses(
    app:    IBApp,
    ps:     pd.DataFrame,
    bar:    dict,
    bar_dt: datetime,
) -> pd.DataFrame:
    """
    For filled stock-only positions: if bar WAP ≤ trailing stop, either:
      a) sell a covered call (best ITM option) — hold the stock, or
      b) fall back to a normal stop-loss sell if no option is available.
    """
    bar_wap  = float(bar['average'])
    bar_bid  = float(bar['avg_bid'])
    bar_ask  = float(bar['avg_ask'])
    bar_date = bar_dt.date()
    ps = ps.copy()

    for i, row in ps.iterrows():
        if _has_pending(row) or _has_cc(row):
            continue
        ts = float(row['current_trailing_stop'])
        if bar_wap > ts:
            continue

        mn = row['model_no']
        log.info(f"[stop] model={mn}  wap={bar_wap:.4f} ≤ trailing_stop={ts:.4f}  "
                 f"— searching for covered call ...")

        best_cc = find_best_cc_live(app, bar_date, bar_wap, float(row['entry_price']))

        if best_cc:
            # ── Covered call pivot ──────────────────────────────────────────
            oid = app.next_order_id()
            app.placeOrder(oid, best_cc['contract'],
                           _limit_order('SELL', 1, _mid(best_cc['bid'], best_cc['ask'])))
            log.info(f"[CC open] model={mn}  strike={best_cc['strike']}  "
                     f"expiry={best_cc['expiry'].isoformat()}  "
                     f"premium={best_cc['premium']:.4f}  oid={oid}")

            ps.at[i, 'cc_symbol']       = SYMBOL
            ps.at[i, 'cc_local_symbol'] = best_cc['local_symbol']
            ps.at[i, 'cc_strike']       = best_cc['strike']
            ps.at[i, 'cc_expiry']       = best_cc['expiry'].isoformat()
            ps.at[i, 'cc_open_price']   = best_cc['premium']
            ps.at[i, 'cc_open_time']    = bar_dt.isoformat()

            _log_txn({
                'timestamp': bar_dt.isoformat(),   'model_no': mn,
                'leg': 'option',                   'action': 'SELL',
                'symbol': SYMBOL,                  'local_symbol': best_cc['local_symbol'],
                'sec_type': 'OPT',                 'quantity': 1,
                'price': best_cc['premium'],        'order_id': oid,
                'reason': f'cc_open_stop_at_{ts:.4f}',
            })
        else:
            # ── Fallback: normal stop-loss exit ────────────────────────────
            oid = app.next_order_id()
            app.placeOrder(oid, _stk_contract(),
                           _limit_order('SELL', SHARES, _mid(bar_bid, bar_ask)))
            log.info(f"[stop exit] model={mn}  limit={_mid(bar_bid, bar_ask):.2f}  oid={oid}")

            _log_txn({
                'timestamp': bar_dt.isoformat(),   'model_no': mn,
                'leg': 'stock',                    'action': 'SELL',
                'symbol': SYMBOL,                  'local_symbol': SYMBOL,
                'sec_type': 'STK',                 'quantity': SHARES,
                'price': _mid(bar_bid, bar_ask),    'order_id': oid,
                'reason': f'stop_loss_at_{ts:.4f}',
            })
            ps = ps.drop(index=i).reset_index(drop=True)

    return ps


# ── step 5: check profit targets ──────────────────────────────────────────────

def check_profit_targets(
    app:    IBApp,
    ps:     pd.DataFrame,
    bar:    dict,
    bar_dt: datetime,
) -> pd.DataFrame:
    """
    For filled stock-only positions: if WAP ≥ profit_target, place a limit sell.
    """
    bar_wap = float(bar['average'])
    bar_bid = float(bar['avg_bid'])
    bar_ask = float(bar['avg_ask'])
    ps = ps.copy()
    rows_to_drop = []

    for i, row in ps.iterrows():
        if _has_pending(row) or _has_cc(row):
            continue
        tgt = float(row['profit_target'])
        if bar_wap < tgt:
            continue

        mn  = row['model_no']
        mid = _mid(bar_bid, bar_ask)
        oid = app.next_order_id()
        app.placeOrder(oid, _stk_contract(), _limit_order('SELL', SHARES, mid))
        log.info(f"[target] model={mn}  wap={bar_wap:.4f} ≥ target={tgt:.4f}  "
                 f"sell limit={mid}  oid={oid}")
        _log_txn({
            'timestamp': bar_dt.isoformat(), 'model_no': mn,
            'leg': 'stock',                  'action': 'SELL',
            'symbol': SYMBOL,                'local_symbol': SYMBOL,
            'sec_type': 'STK',               'quantity': SHARES,
            'price': mid,                    'order_id': oid,
            'reason': f'profit_target_{tgt:.4f}',
        })
        rows_to_drop.append(i)

    if rows_to_drop:
        ps = ps.drop(index=rows_to_drop).reset_index(drop=True)
    return ps


# ── step 6: check covered-call buyback ($0.50 threshold) ─────────────────────

def check_cc_buybacks(
    app:    IBApp,
    ps:     pd.DataFrame,
    bar:    dict,
    bar_dt: datetime,
) -> pd.DataFrame:
    """
    For positions with an open covered call: query the live option ask.
    If ask < CC_BUYBACK_THRESHOLD, buy back the call and sell the stock.
    """
    bar_bid = float(bar['avg_bid'])
    bar_ask = float(bar['avg_ask'])
    ps = ps.copy()
    rows_to_drop = []

    for i, row in ps.iterrows():
        if not _has_cc(row):
            continue

        cc_sym = str(row['cc_local_symbol'])
        try:
            expiry_date = date.fromisoformat(str(row['cc_expiry']))
        except (ValueError, TypeError):
            continue

        # Re-build the option contract from position_support fields
        cc_contract = _option_contract(
            float(row['cc_strike']), expiry_date, right='C'
        )
        cc_contract.localSymbol = cc_sym

        bid, ask = app.get_option_quote(cc_contract)
        if ask is None:
            log.debug(f"[cc monitor] model={row['model_no']}  {cc_sym}  no quote")
            continue

        log.debug(f"[cc monitor] model={row['model_no']}  {cc_sym}  ask={ask:.4f}")

        if ask >= CC_BUYBACK_THRESHOLD:
            continue

        # ── Buyback triggered ──────────────────────────────────────────────
        mn   = row['model_no']
        log.info(f"[CC buyback] model={mn}  {cc_sym}  ask={ask:.4f} < threshold")

        # Buy back the call at mid-price (bid = None is possible; fall back to ask)
        cc_mid  = _mid(bid, ask) if bid else ask
        cc_oid  = app.next_order_id()
        app.placeOrder(cc_oid, cc_contract, _limit_order('BUY', 1, cc_mid))
        _log_txn({
            'timestamp': bar_dt.isoformat(), 'model_no': mn,
            'leg': 'option',                 'action': 'BUY',
            'symbol': SYMBOL,                'local_symbol': cc_sym,
            'sec_type': 'OPT',               'quantity': 1,
            'price': cc_mid,                 'order_id': cc_oid,
            'reason': f'cc_buyback_ask_{ask:.4f}',
        })

        # Sell stock at mid-price
        stk_mid = _mid(bar_bid, bar_ask)
        stk_oid = app.next_order_id()
        app.placeOrder(stk_oid, _stk_contract(), _limit_order('SELL', SHARES, stk_mid))
        log.info(f"[CC buyback] model={mn}  sell stock limit={stk_mid}  oid={stk_oid}")
        _log_txn({
            'timestamp': bar_dt.isoformat(), 'model_no': mn,
            'leg': 'stock',                  'action': 'SELL',
            'symbol': SYMBOL,                'local_symbol': SYMBOL,
            'sec_type': 'STK',               'quantity': SHARES,
            'price': stk_mid,                'order_id': stk_oid,
            'reason': 'cc_buyback_close_stock',
        })
        rows_to_drop.append(i)

    if rows_to_drop:
        ps = ps.drop(index=rows_to_drop).reset_index(drop=True)
    return ps


# ── step 7: handle CC positions at Friday expiry ──────────────────────────────

def check_cc_expiry(
    app:    IBApp,
    ps:     pd.DataFrame,
    bar:    dict,
    bar_dt: datetime,
) -> pd.DataFrame:
    """
    On the expiry Friday at or after 3:45 PM, resolve covered call positions:
      - Stock bid > strike (ITM)  → log assignment (IB will handle; remove row)
      - Stock bid ≤ strike (OTM)  → sell stock at market
    """
    if _ses_minute(bar_dt) < EOD_MINUTE:
        return ps

    bar_bid = float(bar['avg_bid'])
    ps = ps.copy()
    rows_to_drop = []

    for i, row in ps.iterrows():
        if not _has_cc(row):
            continue
        try:
            expiry_date = date.fromisoformat(str(row['cc_expiry']))
        except (ValueError, TypeError):
            continue

        if bar_dt.date() != expiry_date:
            continue   # not expiry day yet

        mn     = row['model_no']
        strike = float(row['cc_strike'])

        if bar_bid > strike:
            # ITM — stock will be called away at strike by IB; nothing to do
            log.info(f"[CC expiry] model={mn}  ITM  bar_bid={bar_bid:.4f} > strike={strike}  "
                     f"— assignment expected, removing row")
            _log_txn({
                'timestamp': bar_dt.isoformat(), 'model_no': mn,
                'leg': 'stock',                  'action': 'ASSIGNED',
                'symbol': SYMBOL,                'local_symbol': SYMBOL,
                'sec_type': 'STK',               'quantity': SHARES,
                'price': strike,                 'order_id': 0,
                'reason': f'cc_assigned_strike_{strike}',
            })
        else:
            # OTM — sell stock at market
            oid = app.next_order_id()
            app.placeOrder(oid, _stk_contract(), _market_order('SELL', SHARES))
            log.info(f"[CC expiry] model={mn}  OTM  bar_bid={bar_bid:.4f} ≤ strike={strike}  "
                     f"— selling stock oid={oid}")
            _log_txn({
                'timestamp': bar_dt.isoformat(), 'model_no': mn,
                'leg': 'stock',                  'action': 'SELL',
                'symbol': SYMBOL,                'local_symbol': SYMBOL,
                'sec_type': 'STK',               'quantity': SHARES,
                'price': bar_bid,                'order_id': oid,
                'reason': 'cc_expired_otm',
            })
        rows_to_drop.append(i)

    if rows_to_drop:
        ps = ps.drop(index=rows_to_drop).reset_index(drop=True)
    return ps


# ── step 8: EOD close for uncovered stock positions ───────────────────────────

def check_eod(
    app:    IBApp,
    ps:     pd.DataFrame,
    bar:    dict,
    bar_dt: datetime,
) -> pd.DataFrame:
    """At 3:45 PM, market-sell any filled stock position that has no covered call."""
    if _ses_minute(bar_dt) < EOD_MINUTE:
        return ps

    ps = ps.copy()
    rows_to_drop = []

    for i, row in ps.iterrows():
        if _has_pending(row) or _has_cc(row):
            continue
        mn  = row['model_no']
        oid = app.next_order_id()
        app.placeOrder(oid, _stk_contract(), _market_order('SELL', SHARES))
        log.info(f"[EOD] model={mn}  market sell oid={oid}")
        _log_txn({
            'timestamp': bar_dt.isoformat(), 'model_no': mn,
            'leg': 'stock',                  'action': 'SELL',
            'symbol': SYMBOL,                'local_symbol': SYMBOL,
            'sec_type': 'STK',               'quantity': SHARES,
            'price': float(bar['avg_bid']),   'order_id': oid,
            'reason': 'eod_forced',
        })
        rows_to_drop.append(i)

    if rows_to_drop:
        ps = ps.drop(index=rows_to_drop).reset_index(drop=True)
    return ps


# ── full bar processing cycle ─────────────────────────────────────────────────

def process_bar(
    app:    IBApp,
    bar:    dict,
    models: pd.DataFrame,
    ps:     pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Orchestrate one full minute cycle.
    Returns (df_raw, df_ext, updated_ps).
    """
    bar_dt  = datetime.strptime(bar['date'], '%Y-%m-%d %H:%M:%S')
    bar_wap = float(bar['average'])
    log.info(f"━━━ bar {bar_dt.strftime('%Y-%m-%d %H:%M')}  "
             f"close={bar['close']}  wap={bar_wap:.4f}  "
             f"bid={bar['avg_bid']}  ask={bar['avg_ask']}  vix={bar['vix']} ━━━")

    # 1. Persist new bar + recompute indicators
    df_raw, df_ext = append_and_save(bar)

    # 2. Confirm fills / cancel stale buy orders
    ps = cancel_stale_orders(app, ps)

    # 3. Update trailing stops (filled, stock-only, no CC)
    ps = update_trailing_stops(ps, bar_wap)

    # 4. Check stop-loss triggers → CC pivot or exit
    ps = check_stop_losses(app, ps, bar, bar_dt)

    # 5. Check profit targets
    ps = check_profit_targets(app, ps, bar, bar_dt)

    # 6. Check covered-call buyback ($0.50)
    ps = check_cc_buybacks(app, ps, bar, bar_dt)

    # 7. Check covered-call expiry (Friday 3:45 PM)
    ps = check_cc_expiry(app, ps, bar, bar_dt)

    # 8. EOD close for uncovered stock positions
    ps = check_eod(app, ps, bar, bar_dt)

    # 9. Check new buy signals (last, so position management takes priority)
    ps = check_buy_signals(app, df_ext, models, ps, bar, bar_dt)

    # 10. Persist updated position support
    save_ps(ps)

    return df_raw, df_ext, ps


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    log.info("=" * 72)
    log.info("arbo701  starting up")
    log.info("=" * 72)

    # ── directory + file setup ────────────────────────────────────────────────
    PAPER_DATA.mkdir(parents=True, exist_ok=True)
    bootstrap_data()
    _init_transaction_file()

    # ── load static config ────────────────────────────────────────────────────
    if not BUY_SIGNALS_CSV.exists():
        log.error(f"buy_signals.csv not found: {BUY_SIGNALS_CSV}")
        sys.exit(1)
    models = pd.read_csv(BUY_SIGNALS_CSV, dtype={'model_no': str})
    log.info(f"[config] {len(models)} buy signal model(s) loaded")

    # ── connect to IB ─────────────────────────────────────────────────────────
    app = IBApp()
    app.connect(HOST, PORT, CLIENT_ID)
    ib_thread = threading.Thread(target=app.run, daemon=True, name='ib-api')
    ib_thread.start()

    if not app._connected_event.wait(timeout=CONN_TIMEOUT):
        log.error(f"Could not connect to TWS at {HOST}:{PORT} within {CONN_TIMEOUT}s")
        sys.exit(1)

    time.sleep(1)   # allow server-side handshake to complete

    # ── startup sync ─────────────────────────────────────────────────────────
    ps = sync_on_startup(app)

    aapl = _stk_contract()
    vix  = _vix_contract()

    # ── one-time historical backfill (keepUpToDate=False) ────────────────────
    # IB does NOT support keepUpToDate=True for BID_ASK (error 321).
    # Request all three as one-shot queries; live bid/ask comes from reqMktData.
    for req_id, contract, what in [
        (_HIST_TRADES_REQ, aapl, 'TRADES'),
        (_HIST_BIDASK_REQ, aapl, 'BID_ASK'),
        (_HIST_VIX_REQ,    vix,  'TRADES'),
    ]:
        app.reqHistoricalData(
            reqId          = req_id,
            contract       = contract,
            endDateTime    = '',
            durationStr    = HISTORY_DURATION,
            barSizeSetting = '1 min',
            whatToShow     = what,
            useRTH         = 1,
            formatDate     = 1,
            keepUpToDate   = False,
            chartOptions   = [],
        )
        log.info(f"[IB] Backfill request reqId={req_id}  {contract.symbol}  {what}")
        time.sleep(0.5)   # IB pacing: avoid bursting multiple requests simultaneously

    # ── live TRADES stream (bar driver, keepUpToDate=True) ────────────────────
    app.reqHistoricalData(
        reqId          = REQ_AAPL_TRADES,
        contract       = aapl,
        endDateTime    = '',
        durationStr    = '1 D',           # minimal history; backfill already handled above
        barSizeSetting = '1 min',
        whatToShow     = 'TRADES',
        useRTH         = 1,
        formatDate     = 1,
        keepUpToDate   = True,
        chartOptions   = [],
    )
    log.info(f"[IB] Live TRADES stream reqId={REQ_AAPL_TRADES} keepUpToDate=True")

    # ── live bid/ask ticks via reqMktData ────────────────────────────────────
    # IB streams tickType 1 (Bid) and 2 (Ask) continuously for stock data.
    app.reqMktData(REQ_AAPL_MKTDATA, aapl, '', False, False, [])
    log.info(f"[IB] reqMktData AAPL bid/ask  reqId={REQ_AAPL_MKTDATA}")

    # ── live VIX via reqMktData ──────────────────────────────────────────────
    # tickType 4 (Last) gives the current VIX index value.
    app.reqMktData(REQ_VIX_MKTDATA, vix, '', False, False, [])
    log.info(f"[IB] reqMktData VIX            reqId={REQ_VIX_MKTDATA}")

    # ── backfill gap from historical data ─────────────────────────────────────
    df_ext = backfill_and_save(app)

    # ── main bar loop ─────────────────────────────────────────────────────────
    log.info("[loop] Entering live bar loop — waiting for completed 1-min bars ...")
    while True:
        try:
            bar = app.bar_queue.get(timeout=120)
        except queue.Empty:
            log.warning("[loop] No bar received in 120s — market closed or IB issue?")
            continue

        try:
            _, df_ext, ps = process_bar(app, bar, models, ps)
        except Exception as exc:
            log.exception(f"[loop] Unhandled error processing bar {bar.get('date')}: {exc}")


if __name__ == '__main__':
    main()
