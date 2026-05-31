"""
arbo703.py — AAPL signal-driven covered-call paper trader WITH a trailing
combo-net stop-loss (paper3).

Identical to paper2/arbo702.py except for one added exit: each bar, before the
buyback check, an open CC is marked to combo-net P&L and closed (via the same
atomic BAG SELL) if it falls stop_atr_mult×ATR below its high-water mark.  When
the option ask is missing, stale_stop_fallback='stock_leg' stops on the stock
leg alone.  stop_atr_mult=0 disables the stop (paper2 parity).  Uses client_id 3.
This config forward-tests the in-sample backtest optimum: w1 / s+0 / k=5
(see model/1_cc_with_stoploss/FINDINGS.md).

Entry rule (per completed 1-min bar, while market is open):
  composite = (any bsig_trend) AND (any bsig_momentum)
  and  cooldown_minutes since last accepted entry has elapsed
  and  cash on hand >= shares × entry_price
  and  the chosen (expiry_label, strike_label) option has
       cc_tv = option_bid − max(0, entry_stock − strike) in [cc_tv_min, cc_tv_max]

On entry fire, submit sequentially:
   1.  MKT BUY  shares_per_position AAPL
   2.  MKT SELL 1 call (cc contract)

Exit rule (per open position, every bar):
  if option_ask − max(0, stock_bid − strike) < buyback_tv:
      MKT BUY  1 call (buy-to-close)
      MKT SELL shares_per_position AAPL
  CCs otherwise run to IB's auto-expiry handling (assigned or expired OTM).
  On startup, for every open CC, TV is re-checked immediately; closes if below.

External dependencies
─────────────────────
  - IB TWS / Gateway (paper port 7497) with API enabled, trusted IP 127.0.0.1,
    clientId open for this program (default 2)
  - Python: ibapi, pandas, numpy
  - ../2_indicator/1_compute_indicators.py      → compute_indicators(df)
  - ../model/1a_tech_indicators_sock_trade/signals.py → add_buy_signals(df)

Files created by this program
  paper2/params.json                                    parameters (this file)
  paper2/data/ref/contracts_{yymmdd}.csv                option chain (once/day)
  paper2/data/ref/stock_{yymmdd}.csv                    yesterday's 1-min bars
  paper2/data/ref/stock_{yymmdd}_partial.csv            today's rolling 1-min bars
  paper2/data/position_support.csv                      live position state
  paper2/data/transaction.csv                           append-only trade log
  paper2/logs/{YYYYMMDD}/{ops,market,trade}.log         one dir per trading day

Restart idempotency
───────────────────
On startup: reqPositions → ground truth; flatten orphan IB positions with MKT
SELL; drop orphan CSV rows.  For each surviving open CC, resubscribe
reqMktData and immediately compute TV — if below buyback_tv, close now.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import logging
import math
import queue
import sys
import threading
import time
from datetime import datetime, date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from ibapi.client import EClient
from ibapi.contract import ComboLeg, Contract
from ibapi.order import Order
from ibapi.wrapper import EWrapper


# ──────────────────────────────────────────────────────────────────────────────
# ibapi 9.81 monkey-patch: tolerate decimal-string volume in real-time bars.
# IB servers have started emitting fractional-share volume like
# b'42.0000000000000000' on certain contracts; the stock decoder calls
# `int(s)` directly and raises ValueError, killing the daemon thread and
# silently stalling the bar queue.  Replace processRealTimeBarMsg with a
# version that parses volume/count via float first.
#
# NOTE: Decoder dispatches through `msgId2handleInfo`, a class-level dict
# that captures direct function references at class-body eval time.  So
# rewriting `Decoder.processRealTimeBarMsg` alone is ignored — we also
# have to swap the HandleInfo entry for REAL_TIME_BARS.
# ──────────────────────────────────────────────────────────────────────────────
def _patch_ibapi_realtimebar():
    from ibapi.common import RealTimeBar
    from ibapi.decoder import Decoder, HandleInfo
    from ibapi.message import IN
    from ibapi.utils import decode

    def processRealTimeBarMsg(self, fields):
        next(fields)              # version
        decode(int, fields)       # reserved field (ibapi discards too)
        reqId = decode(int, fields)

        bar = RealTimeBar()
        bar.time   = decode(int, fields)
        bar.open   = decode(float, fields)
        bar.high   = decode(float, fields)
        bar.low    = decode(float, fields)
        bar.close  = decode(float, fields)
        bar.volume = int(decode(float, fields))   # robust: "42.000..." → 42
        bar.wap    = decode(float, fields)
        bar.count  = int(decode(float, fields))   # same defensive parse

        self.wrapper.realtimeBar(
            reqId, bar.time, bar.open, bar.high, bar.low,
            bar.close, bar.volume, bar.wap, bar.count,
        )

    Decoder.processRealTimeBarMsg = processRealTimeBarMsg
    Decoder.msgId2handleInfo[IN.REAL_TIME_BARS] = HandleInfo(proc=processRealTimeBarMsg)


_patch_ibapi_realtimebar()

# ══════════════════════════════════════════════════════════════════════════════
# PATHS & EXTERNAL IMPORTS
# ══════════════════════════════════════════════════════════════════════════════

_HERE    = Path(__file__).parent
_BASE    = _HERE.parent
_INDIC   = _BASE / '2_indicator'
_MODEL1A = _BASE / 'model' / '1a_tech_indicators_sock_trade'
sys.path.insert(0, str(_MODEL1A))

_spec = importlib.util.spec_from_file_location(
    'compute_indicators_mod', _INDIC / '1_compute_indicators.py'
)
_cmod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cmod)
compute_indicators = _cmod.compute_indicators

from signals import add_buy_signals  # noqa: E402

# ══════════════════════════════════════════════════════════════════════════════
# PARAMS
# ══════════════════════════════════════════════════════════════════════════════

PARAMS_FILE = _HERE / 'params.json'

# Per-strategy signal-gate modes.  Each strategy picks one:
#   none          — no signal filter (always fires; default when omitted)
#   trend_only    — any bsig_trend == 1
#   momentum_only — any bsig_momentum == 1
#   both          — (any trend) AND (any momentum)   (the old fixed behavior)
SIGNAL_MODES = ('none', 'trend_only', 'momentum_only', 'both')

_STRATEGY_KEYS = (
    'shares_per_position', 'cooldown_minutes',
    'cc_tv_min', 'cc_tv_max', 'buyback_tv',
    'expiry_label', 'strike_label', 'signal_mode',
)

_DEFAULT_STRATEGY = {
    'shares_per_position': 100,
    'cooldown_minutes':    60,
    'cc_tv_min':           2.5,
    'cc_tv_max':           3.6,
    'buyback_tv':          0.5,
    'expiry_label':        'w0',
    'strike_label':        's-2',
    'signal_mode':         'none',   # default when omitted from params.json
}

_DEFAULT_PARAMS = {
    'symbol':         'AAPL',
    'starting_cash':  500_000,
    'strategies':     [dict(_DEFAULT_STRATEGY)],
    'host':           '127.0.0.1',
    'port':           7497,
    'client_id':      3,                 # paper3 — distinct from paper1(1)/paper2(2)
    'retention_days': 30,
    # paper3 additions: trailing combo-net stop-loss (paper2 has none).
    'stop_atr_mult':       0.0,          # 0 = stop disabled (paper2 parity); >0 to enable
    'stop_basis':          'combo_net',  # only combo_net implemented
    'stop_type':           'trailing',   # only trailing implemented
    'stale_stop_fallback': 'skip',       # 'skip' | 'stock_leg' (stop on stock when option ask missing)
}


def load_params() -> dict:
    if not PARAMS_FILE.exists():
        PARAMS_FILE.write_text(json.dumps(_DEFAULT_PARAMS, indent=2))
    p = json.loads(PARAMS_FILE.read_text())

    # Fill top-level defaults (but NOT strategies — that's handled below)
    for k, v in _DEFAULT_PARAMS.items():
        if k != 'strategies':
            p.setdefault(k, v)

    # Legacy / single-strategy fallback: a flat params file gets wrapped
    strategies = p.get('strategies')
    if not strategies:
        flat = {k: p[k] for k in _STRATEGY_KEYS if k in p}
        if flat:
            print("[params] legacy single-strategy format — wrapping in strategies[]")
            strategies = [flat]
        else:
            strategies = [dict(_DEFAULT_STRATEGY)]

    # Validate & fill defaults on each strategy
    cleaned = []
    for i, s in enumerate(strategies):
        if not isinstance(s, dict):
            raise ValueError(f"strategies[{i}] is not an object: {s!r}")
        merged = {**_DEFAULT_STRATEGY, **s}
        shares = int(merged['shares_per_position'])
        if shares % 100 != 0 or shares <= 0:
            raise ValueError(f"strategies[{i}].shares_per_position must be positive multiple of 100: {shares}")
        merged['shares_per_position'] = shares
        merged['cooldown_minutes']    = int(merged['cooldown_minutes'])
        merged['cc_tv_min']           = float(merged['cc_tv_min'])
        merged['cc_tv_max']           = float(merged['cc_tv_max'])
        merged['buyback_tv']          = float(merged['buyback_tv'])
        if merged['cc_tv_min'] > merged['cc_tv_max']:
            raise ValueError(f"strategies[{i}] cc_tv_min > cc_tv_max")
        merged['signal_mode'] = str(merged['signal_mode'])
        if merged['signal_mode'] not in SIGNAL_MODES:
            raise ValueError(
                f"strategies[{i}].signal_mode must be one of "
                f"{list(SIGNAL_MODES)}: got {merged['signal_mode']!r}"
            )
        cleaned.append(merged)
    p['strategies'] = cleaned

    # paper3 stop-loss config (top-level, applies to all strategies)
    p['stop_atr_mult']       = float(p.get('stop_atr_mult', 0) or 0)
    p['stop_basis']          = str(p.get('stop_basis', 'combo_net'))
    p['stop_type']           = str(p.get('stop_type', 'trailing'))
    p['stale_stop_fallback'] = str(p.get('stale_stop_fallback', 'skip'))
    if p['stop_atr_mult'] < 0:
        raise ValueError(f"stop_atr_mult must be >= 0: {p['stop_atr_mult']}")
    if p['stop_basis'] != 'combo_net':
        raise ValueError(f"stop_basis only supports 'combo_net': {p['stop_basis']!r}")
    if p['stop_type'] != 'trailing':
        raise ValueError(f"stop_type only supports 'trailing': {p['stop_type']!r}")
    if p['stale_stop_fallback'] not in ('skip', 'stock_leg'):
        raise ValueError(
            f"stale_stop_fallback must be 'skip' or 'stock_leg': {p['stale_stop_fallback']!r}")
    return p


PARAMS = load_params()
SYMBOL = PARAMS['symbol']

# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

# Buy-signal buckets (trend / momentum — per-strategy signal_mode picks the gate)
TREND    = ['ema', 'macd', 'adx', 'sar', 'don', 'arn', 'vtx']
MOMENTUM = ['rsi', 'sto', 'cci', 'cmo', 'tsi', 'roc', 'frc', 'srsi', 'rmi', 'macd']
BSIG_TREND_COLS    = [f'bsig_{k}' for k in TREND]
BSIG_MOMENTUM_COLS = [f'bsig_{k}' for k in MOMENTUM]


def _signal_mode_fires(mode: str, trend_any: bool, mom_any: bool) -> bool:
    if mode == 'none':          return True
    if mode == 'trend_only':    return trend_any
    if mode == 'momentum_only': return mom_any
    if mode == 'both':          return trend_any and mom_any
    raise ValueError(f"unknown signal_mode: {mode!r}")

COMMISSION        = 2.00
CONN_TIMEOUT      = 20
OPT_QUOTE_TIMEOUT = 5
MAX_BARS          = 1000        # rolling bar history kept in today-partial file
EOD_MINUTE        = 15 * 60 + 45  # 15:45 — Friday CC expiry handling cutoff
ENTRY_EARLIEST_MINUTE = 9 * 60 + 35  # 09:35 — no entries considered before this
REQ_STK_MKTDATA   = 2
REQ_VIX_MKTDATA   = 3
REQ_STK_RTBARS    = 4
REQ_HIST          = 5
_DYN_REQ_START    = 100

# BAG (combo) order pricing — per-share buffers on the net combo price
BAG_LMT_ENTRY_BUFFER = 0.10   # entry combo = BUY  → LMT = net_debit  + buffer
BAG_LMT_EXIT_BUFFER  = 0.10   # exit  combo = SELL → LMT = net_credit − buffer

# ══════════════════════════════════════════════════════════════════════════════
# FILE PATHS
# ══════════════════════════════════════════════════════════════════════════════

DATA_DIR        = _HERE / 'data'
REF_DIR         = DATA_DIR / 'ref'
LOGS_DIR        = _HERE / 'logs'
POS_SUPPORT_CSV = DATA_DIR / 'position_support.csv'
TRANSACTION_CSV = DATA_DIR / 'transaction.csv'


def _yymmdd(d: date) -> str:
    return d.strftime('%y%m%d')


def contracts_file(d: date) -> Path:
    return REF_DIR / f'contracts_{_yymmdd(d)}.csv'


def stock_file(d: date, partial: bool = False) -> Path:
    stem = f'stock_{_yymmdd(d)}' + ('_partial' if partial else '')
    return REF_DIR / f'{stem}.csv'


# ══════════════════════════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════════════════════════

class _DailyDirFileHandler(logging.FileHandler):
    """
    FileHandler whose output path is `<root>/<YYYYMMDD>/<name>`.  The
    date component is re-checked on each emit; if the local date has
    advanced, we close the current file and open a new one under the new
    date directory.  This keeps an overnight-running session's log lines
    segregated by calendar day without needing a rotation handler.
    """

    def __init__(self, root: Path, name: str, encoding: str = 'utf-8'):
        self._root = Path(root)
        self._fname = name
        self._current_day: str | None = None
        self._ensure_day()
        super().__init__(self.baseFilename, mode='a', encoding=encoding, delay=False)

    def _ensure_day(self) -> bool:
        today = datetime.now().strftime('%Y%m%d')
        if today != self._current_day:
            day_dir = self._root / today
            day_dir.mkdir(parents=True, exist_ok=True)
            self.baseFilename = str(day_dir / self._fname)
            self._current_day = today
            return True
        return False

    def emit(self, record):
        if self._ensure_day() and self.stream is not None:
            try:
                self.stream.close()
            except Exception:
                pass
            self.stream = None   # FileHandler.emit will _open() on next write
        super().emit(record)


def _setup_logging() -> logging.Logger:
    """
    Logs land in `paper2/logs/YYYYMMDD/<name>.log`.  Three streams per day:
      ops.log     — INFO and above, everything EXCEPT trade/market prefixes
      market.log  — DEBUG and above, only [tick] / [bar] / [cc monitor] lines
      trade.log   — INFO and above, only trade-lifecycle-tagged lines
    Plus a stdout stream (INFO, no market-prefix noise).
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter('%(asctime)s  %(levelname)-7s  %(message)s',
                            datefmt='%H:%M:%S')

    trade_prefixes = ('[BUY]', '[fill]', '[orders]', '[CC open]', '[CC buyback]',
                      '[CC expiry]', '[EOD]', '[txn]', '[exit]')
    market_prefixes = ('[tick]', '[bar]', '[cc monitor]')

    class _Pref(logging.Filter):
        def __init__(self, pfx, include):
            super().__init__()
            self.pfx, self.include = pfx, include
        def filter(self, r):
            msg = r.getMessage()
            m = any(msg.startswith(p) for p in self.pfx)
            return m if self.include else not m

    def _h(name: str, level: int) -> _DailyDirFileHandler:
        h = _DailyDirFileHandler(LOGS_DIR, name)
        h.setLevel(level)
        h.setFormatter(fmt)
        return h

    ops = _h('ops.log', logging.INFO)
    ops.addFilter(_Pref(trade_prefixes + market_prefixes, include=False))

    mkt = _h('market.log', logging.DEBUG)
    mkt.addFilter(_Pref(market_prefixes, include=True))

    tr = _h('trade.log', logging.INFO)
    tr.addFilter(_Pref(trade_prefixes, include=True))

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    ch.addFilter(_Pref(market_prefixes, include=False))

    lg = logging.getLogger('arbo702')
    lg.setLevel(logging.DEBUG)
    if not lg.handlers:
        for handler in (ops, mkt, tr, ch):
            lg.addHandler(handler)
    return lg


log: logging.Logger = _setup_logging()


# ══════════════════════════════════════════════════════════════════════════════
# IB APP (EWrapper + EClient)
# ══════════════════════════════════════════════════════════════════════════════

class IBApp(EWrapper, EClient):
    """
    Single IB connection; runs in a daemon thread.  Main thread pulls
    completed 1-min bars from bar_queue.
    """

    def __init__(self):
        EClient.__init__(self, self)
        self._connected_event = threading.Event()
        self._next_order_id   = 1000
        self._oid_lock        = threading.Lock()
        self._dyn_req         = _DYN_REQ_START
        self._req_lock        = threading.Lock()

        # 5-sec rtbar accumulator → 1-min bar queue
        self._rtbar_acc: dict = {}
        self._bar_lock        = threading.Lock()
        self.bar_queue: queue.Queue = queue.Queue()

        # Live tick state
        self._current_bid: float | None = None
        self._current_ask: float | None = None
        self._current_vix: float | None = None

        # Positions
        self._positions: list[dict] = []
        self._pos_done = threading.Event()

        # Order status (orderId → {status, filled, avg_fill})
        self.order_status: dict[int, dict] = {}

        # Option single-shot quote collection (used by get_option_quote)
        self._opt_bid : dict[int, float] = {}
        self._opt_ask : dict[int, float] = {}
        self._opt_done: dict[int, threading.Event] = {}

        # Persistent CC option subscriptions keyed by reqId
        self._cc_prices: dict[int, dict] = {}

        # Contract details collection (reqContractDetails)
        self._cd_rows : dict[int, list[dict]] = {}
        self._cd_done : dict[int, threading.Event] = {}

        # Historical data collection (reqHistoricalData)
        self._hist_rows: dict[int, list[dict]] = {}
        self._hist_done: dict[int, threading.Event] = {}
        self._hist_err : dict[int, str] = {}

    # ── connection ────────────────────────────────────────────────────────────
    def nextValidId(self, orderId: int):
        with self._oid_lock:
            self._next_order_id = orderId
        self._connected_event.set()
        log.info(f"[IB] Connected — next order ID: {orderId}")

    def error(self, reqId: int, errorCode: int, errorString: str,
              advancedOrderRejectJson: str = ''):
        if reqId == -1:
            if errorCode not in (2104, 2106, 2158):
                log.info(f"[IB] info {errorCode}: {errorString}")
            return
        # Warnings (code ≥ 2000) are not failures — they arrive on arbitrary
        # reqIds and must NOT trip the historical / option / contract-details
        # fall-back paths.
        if errorCode >= 2000:
            log.debug(f"[IB] warning reqId={reqId} {errorCode}: {errorString}")
            return
        log.error(f"[IB] error reqId={reqId} code={errorCode}: {errorString}")
        # Real errors: unblock any waiting synchronous callers with err msg
        if reqId in self._hist_done:
            self._hist_err[reqId] = f"{errorCode}:{errorString}"
            self._hist_done[reqId].set()
        if reqId in self._opt_done:
            self._opt_done[reqId].set()
        if reqId in self._cd_done:
            self._cd_done[reqId].set()

    # ── id helpers ────────────────────────────────────────────────────────────
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

    # ── 5-sec rtbars → 1-min queue ────────────────────────────────────────────
    def realtimeBar(self, reqId: int, time_: int, open_: float, high: float,
                    low: float, close: float, volume: int, wap: float, count: int):
        if reqId != REQ_STK_RTBARS:
            return
        minute_ts = (time_ // 60) * 60
        with self._bar_lock:
            acc = self._rtbar_acc
            if not acc:
                log.info(f"[bar] RealTimeBars first update minute_ts={minute_ts}")
            elif acc['minute_ts'] != minute_ts:
                vol = acc['volume']
                avg = acc['sum_wap_vol'] / vol if vol > 0 else acc['close']
                bid = self._current_bid if self._current_bid else avg
                ask = self._current_ask if self._current_ask else avg
                vix = self._current_vix if self._current_vix else float('nan')
                dt_str = datetime.fromtimestamp(acc['minute_ts']).strftime('%Y-%m-%d %H:%M:%S')
                row = {
                    'date':        dt_str,
                    'vix':         round(float(vix), 4) if not math.isnan(vix) else None,
                    'open':        acc['open'],
                    'high':        acc['high'],
                    'low':         acc['low'],
                    'close':       acc['close'],
                    'avg_bid':     round(float(bid), 4),
                    'avg_ask':     round(float(ask), 4),
                    'max_ask':     round(float(ask), 4),
                    'min_bid':     round(float(bid), 4),
                    'average':     round(float(avg), 4),
                    'barCount':    acc['count'],
                    'volume':      acc['volume'],
                    'symbol':      SYMBOL,
                    'localSymbol': SYMBOL,
                    'conId':       '',
                }
                self.bar_queue.put(row)
                log.info(f"[bar] queued {dt_str}  close={row['close']}  wap={row['average']}  "
                         f"bid={row['avg_bid']}  ask={row['avg_ask']}  vix={row['vix']}")
                self._rtbar_acc = {}
                acc = {}
            if not acc:
                self._rtbar_acc = {
                    'minute_ts':   minute_ts,
                    'open':        open_,
                    'high':        high,
                    'low':         low,
                    'close':       close,
                    'volume':      volume,
                    'sum_wap_vol': wap * volume,
                    'count':       count,
                }
            else:
                self._rtbar_acc['high']        = max(acc['high'], high)
                self._rtbar_acc['low']         = min(acc['low'],  low)
                self._rtbar_acc['close']       = close
                self._rtbar_acc['volume']      += volume
                self._rtbar_acc['sum_wap_vol'] += wap * volume
                self._rtbar_acc['count']       += count

    # ── tick prices ───────────────────────────────────────────────────────────
    def tickPrice(self, reqId: int, tickType: int, price: float, attrib):
        if price <= 0:
            return
        if reqId == REQ_STK_MKTDATA:
            if tickType in (1, 66):
                self._current_bid = price
                log.debug(f"[tick] {SYMBOL} bid={price}")
            elif tickType in (2, 67):
                self._current_ask = price
                log.debug(f"[tick] {SYMBOL} ask={price}")
        elif reqId == REQ_VIX_MKTDATA:
            if tickType in (4, 9, 68):
                self._current_vix = price
                log.debug(f"[tick] VIX={price}")
        elif reqId in self._cc_prices:
            if tickType in (1, 66):
                self._cc_prices[reqId]['bid'] = price
            elif tickType in (2, 67):
                self._cc_prices[reqId]['ask'] = price
        elif reqId in self._opt_done:
            if tickType in (1, 66):
                self._opt_bid[reqId] = price
            elif tickType in (2, 67):
                self._opt_ask[reqId] = price
            if reqId in self._opt_bid and reqId in self._opt_ask:
                self._opt_done[reqId].set()

    def tickSize(self, reqId, tickType, size): pass

    # ── positions ─────────────────────────────────────────────────────────────
    def position(self, account, contract, position, avgCost):
        self._positions.append({
            'account':     account,
            'symbol':      contract.symbol,
            'localSymbol': contract.localSymbol,
            'conId':       contract.conId,
            'secType':     contract.secType,
            'position':    position,
            'avgCost':     avgCost,
            'strike':      getattr(contract, 'strike', 0) or 0,
            'right':       getattr(contract, 'right', '') or '',
            'expiry':      getattr(contract, 'lastTradeDateOrContractMonth', '') or '',
        })

    def positionEnd(self):
        self._pos_done.set()

    # ── order status ──────────────────────────────────────────────────────────
    def orderStatus(self, orderId: int, status: str, filled: float,
                    remaining: float, avgFillPrice: float, permId: int,
                    parentId: int, lastFillPrice: float, clientId: int,
                    whyHeld: str, mktCapPrice: float):
        self.order_status[orderId] = {
            'status': status, 'filled': filled, 'avg_fill': avgFillPrice,
        }
        log.info(f"[order] id={orderId}  status={status}  filled={filled}  avg={avgFillPrice:.4f}")

    # ── contract details ──────────────────────────────────────────────────────
    def contractDetails(self, reqId: int, contractDetails):
        c = contractDetails.contract
        self._cd_rows.setdefault(reqId, []).append({
            'symbol':       c.symbol,
            'secType':      c.secType,
            'expiry':       c.lastTradeDateOrContractMonth,
            'strike':       float(c.strike) if c.strike else 0.0,
            'right':        c.right,
            'exchange':     c.exchange,
            'currency':     c.currency,
            'localSymbol':  c.localSymbol,
            'multiplier':   c.multiplier,
            'conId':        c.conId,
        })

    def contractDetailsEnd(self, reqId: int):
        if reqId in self._cd_done:
            self._cd_done[reqId].set()

    # ── historical data ───────────────────────────────────────────────────────
    def historicalData(self, reqId: int, bar):
        self._hist_rows.setdefault(reqId, []).append({
            'date':   bar.date,
            'open':   bar.open,
            'high':   bar.high,
            'low':    bar.low,
            'close':  bar.close,
            'volume': bar.volume,
            'wap':    getattr(bar, 'wap', bar.close),
            'barCount': getattr(bar, 'barCount', 0),
        })

    def historicalDataEnd(self, reqId: int, start: str, end: str):
        if reqId in self._hist_done:
            self._hist_done[reqId].set()

    # ── synchronous RPC helpers ───────────────────────────────────────────────
    def fetch_positions(self, timeout: float = 15.0) -> list[dict]:
        self._positions = []
        self._pos_done.clear()
        self.reqPositions()
        if not self._pos_done.wait(timeout=timeout):
            log.warning("[IB] Timed out waiting for positions")
        return list(self._positions)

    def get_option_quote(self, contract: Contract,
                         timeout: float = OPT_QUOTE_TIMEOUT) -> tuple[float | None, float | None]:
        """
        One-shot option quote.  Uses snapshot=True so IB returns the last-known
        quote (even if stale / outside market hours) and auto-unsubscribes.
        The waiter fires when BOTH bid and ask are present; after a partial
        wait (half the timeout) it settles for whichever side(s) arrived.
        """
        rid = self.next_req_id()
        evt = threading.Event()
        self._opt_done[rid] = evt
        self._opt_bid.pop(rid, None)
        self._opt_ask.pop(rid, None)
        # snapshot=True returns the cached last quote and unsubscribes on its own
        self.reqMktData(rid, contract, '', True, False, [])
        got_both = evt.wait(timeout=timeout)
        if not got_both:
            # Give a partial answer if at least one side came in
            time.sleep(0.3)
        try:
            self.cancelMktData(rid)
        except Exception:
            pass
        bid = self._opt_bid.pop(rid, None)
        ask = self._opt_ask.pop(rid, None)
        self._opt_done.pop(rid, None)
        # Identify the contract we queried (localSymbol is not set on our
        # construction; show strike/expiry/right so concurrent calls for
        # different strikes can be told apart in the log)
        desc = (contract.localSymbol
                or f"{contract.symbol} {contract.lastTradeDateOrContractMonth} "
                   f"{contract.right}{contract.strike}")
        if bid or ask:
            log.debug(f"[opt quote] {desc}  bid={bid}  ask={ask}  "
                      f"({'both' if (bid and ask) else 'partial'})")
        else:
            log.debug(f"[opt quote] {desc}  no quote")
        return bid, ask

    def fetch_option_chain(self, symbol: str,
                           timeout: float = 30.0) -> list[dict]:
        """
        reqContractDetails with right='C' and no strike/expiry — returns every
        listed call on the symbol.  Used to build contracts_{yymmdd}.csv.
        """
        c = Contract()
        c.symbol     = symbol
        c.secType    = 'OPT'
        c.exchange   = 'SMART'
        c.currency   = 'USD'
        c.right      = 'C'
        c.multiplier = '100'
        rid = self.next_req_id()
        self._cd_rows[rid]  = []
        self._cd_done[rid]  = threading.Event()
        self.reqContractDetails(rid, c)
        if not self._cd_done[rid].wait(timeout=timeout):
            log.warning(f"[chain] Timed out waiting for contract details rid={rid}")
        rows = self._cd_rows.pop(rid, [])
        self._cd_done.pop(rid, None)
        return rows

    def fetch_historical_minute_bars(self, contract: Contract, end_dt: datetime,
                                     duration: str, timeout: float = 30.0
                                     ) -> tuple[list[dict], str | None]:
        """
        reqHistoricalData for 1-minute TRADES bars.  Returns (rows, err_msg).
        end_dt in local time; IB takes 'YYYYMMDD HH:MM:SS' format.
        """
        rid = self.next_req_id()
        self._hist_rows[rid] = []
        self._hist_done[rid] = threading.Event()
        self._hist_err.pop(rid, None)
        end_str = end_dt.strftime('%Y%m%d %H:%M:%S US/Eastern')
        self.reqHistoricalData(
            rid, contract, end_str, duration, '1 min', 'TRADES',
            1, 1, False, [],
        )
        got = self._hist_done[rid].wait(timeout=timeout)
        rows = self._hist_rows.pop(rid, [])
        err  = self._hist_err.pop(rid, None)
        self._hist_done.pop(rid, None)
        if not got:
            err = err or 'timeout'
        return rows, err


# ══════════════════════════════════════════════════════════════════════════════
# CONTRACT / ORDER HELPERS
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


def _option_contract(strike: float, expiry_ib: str, right: str = 'C') -> Contract:
    c = Contract()
    c.symbol     = SYMBOL
    c.secType    = 'OPT'
    c.exchange   = 'SMART'
    c.currency   = 'USD'
    c.lastTradeDateOrContractMonth = expiry_ib
    c.right      = right
    c.strike     = strike
    c.multiplier = '100'
    return c


def _market_order(action: str, qty: int) -> Order:
    o = Order()
    o.action        = action
    o.orderType     = 'MKT'
    o.totalQuantity = qty
    o.tif           = 'DAY'
    o.eTradeOnly    = False
    o.firmQuoteOnly = False
    return o


# ══════════════════════════════════════════════════════════════════════════════
# BAG (combo) ORDER HELPERS — atomic buy-write / close-buy-write
# ══════════════════════════════════════════════════════════════════════════════

_stock_conid_cache: int | None = None


def get_stock_conid(app, symbol: str = None,
                    timeout: float = 10.0) -> int | None:
    """One-shot stock conId lookup via reqContractDetails; cached for the session."""
    global _stock_conid_cache
    if _stock_conid_cache is not None:
        return _stock_conid_cache
    sym = symbol or SYMBOL
    c = Contract()
    c.symbol      = sym
    c.secType     = 'STK'
    c.exchange    = 'SMART'
    c.primaryExch = 'NASDAQ'
    c.currency    = 'USD'
    rid = app.next_req_id()
    app._cd_rows[rid] = []
    app._cd_done[rid] = threading.Event()
    app.reqContractDetails(rid, c)
    if not app._cd_done[rid].wait(timeout=timeout):
        log.warning(f"reqContractDetails timed out for stock {sym}")
    rows = app._cd_rows.pop(rid, [])
    app._cd_done.pop(rid, None)
    if rows:
        _stock_conid_cache = int(rows[0]['conId'])
        log.info(f"[conid] {sym} stock conId = {_stock_conid_cache}")
    return _stock_conid_cache


def option_conid_from_chain(chain_df: pd.DataFrame, strike: float,
                            expiry_ib: str) -> int | None:
    """Look up the cached option conId in contracts_{yymmdd}.csv."""
    sub = chain_df[(chain_df['expiry'] == expiry_ib) &
                   (chain_df['strike'].astype(float) == float(strike)) &
                   (chain_df['right'] == 'C')]
    if sub.empty:
        return None
    return int(sub.iloc[0]['conId'])


def _buywrite_bag(stock_conid: int, option_conid: int) -> Contract:
    """
    BAG contract for a covered-call buy-write:
      leg 1: 100 shares stock   (BUY for entry, SELL for exit via combo action)
      leg 2:   1 call option    (SELL for entry, BUY  for exit via combo action)
    Ratio 100:1 → 1 combo unit = 1 covered-call pair.
    """
    bag = Contract()
    bag.symbol   = SYMBOL
    bag.secType  = 'BAG'
    bag.currency = 'USD'
    bag.exchange = 'SMART'

    stk_leg = ComboLeg()
    stk_leg.conId     = stock_conid
    stk_leg.ratio     = 100
    stk_leg.action    = 'BUY'     # BAG action=BUY/SELL flips this effectively
    stk_leg.exchange  = 'SMART'
    stk_leg.openClose = 0

    opt_leg = ComboLeg()
    opt_leg.conId     = option_conid
    opt_leg.ratio     = 1
    opt_leg.action    = 'SELL'
    opt_leg.exchange  = 'SMART'
    opt_leg.openClose = 0

    bag.comboLegs = [stk_leg, opt_leg]
    return bag


def _bag_limit_order(action: str, quantity: int, lmt_price: float) -> Order:
    """
    LMT order for a BAG combo.  quantity = number of 100-share combo units.
      action='BUY'  → pay up to lmt_price per share (net debit for entry)
      action='SELL' → accept at least lmt_price per share (net credit for exit)
    """
    o = Order()
    o.action        = action
    o.orderType     = 'LMT'
    o.totalQuantity = quantity
    o.lmtPrice      = round(lmt_price, 2)
    o.tif           = 'DAY'
    o.eTradeOnly    = False
    o.firmQuoteOnly = False
    return o


# ══════════════════════════════════════════════════════════════════════════════
# EXPIRY / STRIKE RESOLUTION
# ══════════════════════════════════════════════════════════════════════════════

def resolve_expiry_friday(entry_date: date, expiry_label: str) -> date:
    """w0/w1/w2 → Friday of the entry week + N weeks ahead."""
    if not expiry_label.startswith('w') or not expiry_label[1:].isdigit():
        raise ValueError(f"Invalid expiry_label: {expiry_label}")
    weeks_ahead = int(expiry_label[1:])
    # Monday=0 … Friday=4
    days_to_fri = (4 - entry_date.weekday()) % 7
    # If today is already Friday, w0 is today
    if entry_date.weekday() == 4:
        base_fri = entry_date
    else:
        base_fri = entry_date + timedelta(days=days_to_fri)
    return base_fri + timedelta(weeks=weeks_ahead)


def resolve_strike(chain_df: pd.DataFrame, expiry_ib: str,
                   entry_price: float, strike_label: str) -> float | None:
    """
    Walk the option chain for the given expiry and return the strike that
    corresponds to the requested position label (s-2 … s+2).
    """
    if len(strike_label) < 3 or strike_label[:2] not in ('s-', 's+'):
        raise ValueError(f"Invalid strike_label: {strike_label}")
    direction = strike_label[1]
    step      = int(strike_label[2:])

    sub = chain_df[chain_df['expiry'] == expiry_ib]
    strikes = sorted(set(float(s) for s in sub['strike'].tolist() if s > 0))
    if direction == '-':
        cands = sorted([s for s in strikes if s < entry_price], reverse=True)
    else:
        cands = sorted([s for s in strikes if s > entry_price])
    return float(cands[step]) if len(cands) > step else None


# ══════════════════════════════════════════════════════════════════════════════
# REFERENCE DATA — contracts + stock history
# ══════════════════════════════════════════════════════════════════════════════

_chain_cache: tuple[date, pd.DataFrame] | None = None


CHAIN_FETCH_ATTEMPTS       = 3           # total tries before giving up on IB
CHAIN_FETCH_RETRY_INTERVAL = 180          # seconds between retries (3 min)


def _most_recent_cached_chain(before: date) -> Path | None:
    """Most recent contracts_{yymmdd}.csv strictly earlier than `before`, or None."""
    if not REF_DIR.exists():
        return None
    candidates: list[tuple[date, Path]] = []
    for p in REF_DIR.glob('contracts_*.csv'):
        stem = p.stem.split('_', 1)[1]   # "260420"
        if len(stem) != 6 or not stem.isdigit():
            continue
        try:
            d = datetime.strptime(stem, '%y%m%d').date()
        except ValueError:
            continue
        if d < before:
            candidates.append((d, p))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0], reverse=True)
    return candidates[0][1]


def ensure_contracts(app: IBApp, today: date) -> pd.DataFrame:
    """
    Return the option chain for SYMBOL as of `today`.

    Resolution order:
      1. In-process cache (`_chain_cache`) if same date
      2. `data/ref/contracts_{today}.csv` from disk
      3. IB `reqContractDetails`, retried up to CHAIN_FETCH_ATTEMPTS times
         with CHAIN_FETCH_RETRY_INTERVAL seconds between attempts.  On
         success the result is persisted to disk as today's chain.
      4. Fallback: the most recent `contracts_{yymmdd}.csv` on disk from
         an earlier date.  Loaded with a loud warning and NOT saved as
         today's file, so tomorrow's startup tries IB fresh.

    Raises RuntimeError only if both IB and any earlier cached chain are
    unavailable.
    """
    global _chain_cache
    if _chain_cache and _chain_cache[0] == today:
        return _chain_cache[1]

    REF_DIR.mkdir(parents=True, exist_ok=True)
    path = contracts_file(today)
    if path.exists():
        df = pd.read_csv(path, dtype={'expiry': str, 'conId': 'Int64'})
        log.info(f"[chain] loaded {len(df):,} rows from {path.name}")
        _chain_cache = (today, df)
        return df

    log.info(f"[chain] {path.name} missing — reqContractDetails for {SYMBOL} calls")
    rows: list = []
    for attempt in range(1, CHAIN_FETCH_ATTEMPTS + 1):
        try:
            rows = app.fetch_option_chain(SYMBOL)
        except Exception as exc:
            log.warning(f"[chain] attempt {attempt}/{CHAIN_FETCH_ATTEMPTS} "
                        f"raised {type(exc).__name__}: {exc}")
            rows = []
        if rows:
            break
        if attempt < CHAIN_FETCH_ATTEMPTS:
            log.warning(f"[chain] attempt {attempt}/{CHAIN_FETCH_ATTEMPTS} "
                        f"returned 0 rows; retrying in "
                        f"{CHAIN_FETCH_RETRY_INTERVAL}s ...")
            time.sleep(CHAIN_FETCH_RETRY_INTERVAL)
        else:
            log.warning(f"[chain] attempt {attempt}/{CHAIN_FETCH_ATTEMPTS} "
                        f"returned 0 rows; giving up on IB for this bootstrap")

    if rows:
        df = pd.DataFrame(rows)
        df.to_csv(path, index=False)
        log.info(f"[chain] wrote {len(df):,} rows → {path.name}")
        _chain_cache = (today, df)
        return df

    # Fallback: most recent earlier cached chain
    fallback = _most_recent_cached_chain(today)
    if fallback is None:
        raise RuntimeError(
            f"reqContractDetails returned 0 rows for {SYMBOL} and no earlier "
            f"cached chain in {REF_DIR}"
        )
    df = pd.read_csv(fallback, dtype={'expiry': str, 'conId': 'Int64'})
    age_days = (today - datetime.strptime(
        fallback.stem.split('_', 1)[1], '%y%m%d').date()).days
    log.warning(
        f"[chain] !! IB unavailable — falling back to {fallback.name} "
        f"({age_days} day(s) old, {len(df):,} rows).  "
        f"Strike coverage may be stale; tomorrow will retry IB fresh."
    )
    _chain_cache = (today, df)
    return df


def ensure_stock_history(app: IBApp, today: date) -> pd.DataFrame:
    """
    Return a DataFrame of 1-minute bars covering up through ~now.  Combines:
      - yesterday's  full stock_{yymmdd}.csv (from cache or HMDS)
      - today's partial stock_{yymmdd}_partial.csv (if exists)
      - if partial is empty/stale, try HMDS for today-so-far; on error, return what we have
    """
    REF_DIR.mkdir(parents=True, exist_ok=True)
    frames: list[pd.DataFrame] = []

    # Yesterday
    y = today - timedelta(days=1)
    # Step back to previous trading day (skip weekends)
    while y.weekday() > 4:
        y -= timedelta(days=1)
    y_path = stock_file(y)
    if y_path.exists():
        df_y = pd.read_csv(y_path, parse_dates=['date'], low_memory=False)
        frames.append(df_y)
        log.info(f"[history] yesterday from cache: {len(df_y):,} rows  {y_path.name}")
    else:
        log.info(f"[history] yesterday cache missing — trying HMDS for {y}")
        # 1 trading day ending at tomorrow-midnight of yesterday
        end = datetime.combine(y + timedelta(days=1), datetime.min.time())
        rows, err = app.fetch_historical_minute_bars(
            _stk_contract(), end, '1 D', timeout=30.0,
        )
        if err:
            log.warning(f"[history] HMDS yesterday failed: {err} — continuing without")
        elif rows:
            df_y = _hist_rows_to_df(rows)
            df_y.to_csv(y_path, index=False)
            frames.append(df_y)
            log.info(f"[history] yesterday from HMDS: {len(df_y):,} rows saved → {y_path.name}")

    # Today partial
    t_partial = stock_file(today, partial=True)
    if t_partial.exists():
        df_t = pd.read_csv(t_partial, parse_dates=['date'], low_memory=False)
        frames.append(df_t)
        log.info(f"[history] today partial: {len(df_t):,} rows  {t_partial.name}")
    else:
        # Attempt to bootstrap today's morning bars via HMDS
        now = datetime.now()
        if now.time() > datetime.min.time().replace(hour=9, minute=30):
            log.info(f"[history] today partial missing — trying HMDS for today-so-far")
            rows, err = app.fetch_historical_minute_bars(
                _stk_contract(), now, '1 D', timeout=30.0,
            )
            if err:
                log.warning(f"[history] HMDS today failed: {err} — realtime warmup only")
            elif rows:
                df_t = _hist_rows_to_df(rows)
                df_t = df_t[df_t['date'].dt.date == today].copy()
                df_t.to_csv(t_partial, index=False)
                frames.append(df_t)
                log.info(f"[history] today from HMDS: {len(df_t):,} rows → {t_partial.name}")

    if frames:
        df = (pd.concat(frames, ignore_index=True)
                .drop_duplicates(subset='date')
                .sort_values('date')
                .reset_index(drop=True))
    else:
        df = pd.DataFrame(columns=[
            'date', 'vix', 'open', 'high', 'low', 'close',
            'avg_bid', 'avg_ask', 'max_ask', 'min_bid',
            'average', 'barCount', 'volume', 'symbol', 'localSymbol', 'conId',
        ])
    log.info(f"[history] combined total: {len(df):,} rows")
    return df


def _hist_rows_to_df(rows: list[dict]) -> pd.DataFrame:
    """Normalise reqHistoricalData rows into the raw-CSV schema."""
    df = pd.DataFrame(rows)
    # IB returns date as 'YYYYMMDD  HH:MM:SS' (regular) or unix-ish; parse flexibly
    def _parse(s: str) -> datetime:
        s = str(s).strip().replace('  ', ' ')
        for fmt in ('%Y%m%d %H:%M:%S', '%Y%m%d'):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        return pd.to_datetime(s)
    df['date'] = df['date'].apply(_parse)
    df['avg_bid']     = df.get('low',  df.get('close'))
    df['avg_ask']     = df.get('high', df.get('close'))
    df['max_ask']     = df['avg_ask']
    df['min_bid']     = df['avg_bid']
    df['average']     = df.get('wap', df.get('close'))
    df['vix']         = None
    df['symbol']      = SYMBOL
    df['localSymbol'] = SYMBOL
    df['conId']       = ''
    df['barCount']    = df.get('barCount', 0)
    keep = ['date', 'vix', 'open', 'high', 'low', 'close',
            'avg_bid', 'avg_ask', 'max_ask', 'min_bid',
            'average', 'barCount', 'volume', 'symbol', 'localSymbol', 'conId']
    return df[[c for c in keep if c in df.columns]]


def prune_old_ref_files(retention_days: int):
    """Delete ref files older than retention_days (by modification time)."""
    if retention_days <= 0:
        return
    cutoff = time.time() - retention_days * 86400
    for p in REF_DIR.glob('*.csv'):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                log.info(f"[retention] removed old file {p.name}")
        except OSError:
            pass


def update_today_partial(df_raw: pd.DataFrame, today: date):
    """Persist today's rolling bars to stock_{yymmdd}_partial.csv."""
    mask = pd.to_datetime(df_raw['date']).dt.date == today
    df_raw.loc[mask].to_csv(stock_file(today, partial=True), index=False)


# ══════════════════════════════════════════════════════════════════════════════
# STATE FILES — position_support.csv + transaction.csv
# ══════════════════════════════════════════════════════════════════════════════

_PS_COLS = [
    'position_id', 'strategy_id', 'signal_mode',
    'entry_time', 'entry_price', 'entry_wap', 'shares', 'cash_used',
    'buyback_tv',          # copied from strategy at entry; governs exit
    'atr_at_entry', 'rsi_at_entry', 'adx_at_entry', 'vwap_at_entry',
    'pending_order_id',    # entry BAG order (cleared once filled)
    'pending_bars',
    'cc_symbol', 'cc_local_symbol', 'cc_strike', 'cc_expiry', 'cc_right',
    'cc_conid',            # option conId — needed to rebuild exit BAG
    'cc_open_price', 'cc_open_time', 'cc_tv_at_entry',
    'cc_mktdata_req_id',
    'cc_pending_order_id', # exit BAG order (cleared once filled → row drops)
    'hwm_net',             # paper3: high-water mark of combo-net P&L/share (trailing stop)
    'hwm_stock',           # paper3: high-water mark of stock-leg P&L/share (stale-quote fallback)
]

_TX_COLS = [
    'timestamp', 'position_id', 'strategy_id', 'signal_mode',
    'leg', 'action',
    'symbol', 'local_symbol', 'sec_type', 'quantity',
    'price', 'order_id', 'reason',
]


def load_ps() -> pd.DataFrame:
    if not POS_SUPPORT_CSV.exists():
        return pd.DataFrame(columns=_PS_COLS)
    df = pd.read_csv(
        POS_SUPPORT_CSV,
        dtype={'position_id': str, 'pending_order_id': str,
               'cc_mktdata_req_id': str, 'cc_pending_order_id': str,
               'cc_expiry': str},
        low_memory=False,
    )
    # Backfill any columns added in later releases
    for c in _PS_COLS:
        if c not in df.columns:
            df[c] = None
    # Legacy rows written before per-strategy signal_mode existed ran under
    # the old fixed (trend AND momentum) composite → treat missing as 'both'.
    df['signal_mode'] = df['signal_mode'].fillna('both').replace('', 'both')
    return df[_PS_COLS]


def save_ps(df: pd.DataFrame):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(POS_SUPPORT_CSV, index=False)


def _init_tx_file():
    TRANSACTION_CSV.parent.mkdir(parents=True, exist_ok=True)
    if not TRANSACTION_CSV.exists():
        with open(TRANSACTION_CSV, 'w', newline='') as f:
            csv.DictWriter(f, fieldnames=_TX_COLS).writeheader()
        return

    # Migrate legacy transaction.csv that lacks the signal_mode column.
    # Rewrites once: new header + 'both' back-filled on historical rows (those
    # all ran under the fixed pre-feature composite gate).
    with open(TRANSACTION_CSV, newline='') as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or 'signal_mode' in reader.fieldnames:
            return
        old_rows = list(reader)

    log.info("[txn] migrating transaction.csv — injecting signal_mode column")
    with open(TRANSACTION_CSV, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=_TX_COLS, extrasaction='ignore')
        w.writeheader()
        for row in old_rows:
            row.setdefault('signal_mode', 'both')
            w.writerow(row)


def _log_txn(row: dict):
    with open(TRANSACTION_CSV, 'a', newline='') as f:
        csv.DictWriter(f, fieldnames=_TX_COLS, extrasaction='ignore').writerow(row)
    log.info(f"[txn] {row}")


def _cancel_cc_sub(app: IBApp, row):
    try:
        rid = int(float(row.get('cc_mktdata_req_id') or 0))
    except (ValueError, TypeError):
        return
    if rid > 0:
        app.cancelMktData(rid)
        app._cc_prices.pop(rid, None)
        log.debug(f"[cc sub] cancelled reqId={rid}")


def _new_position_id() -> str:
    return f"pos_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"


# ══════════════════════════════════════════════════════════════════════════════
# STARTUP SYNC
# ══════════════════════════════════════════════════════════════════════════════

def sync_on_startup(app: IBApp, params: dict) -> pd.DataFrame:
    """
    Reconcile IB positions against position_support.csv.
      - Cancel lingering pending orders tracked in CSV.
      - Drop CSV rows whose stock position no longer exists in IB.
      - Flatten IB stock positions not tracked in CSV (MKT SELL).
      - If a CSV row has a cc_* but IB has no matching call option (CC expired
        during downtime), flatten the stock and drop the row.
      - For surviving CSV rows with an open CC: re-subscribe reqMktData on the
        option contract so TV monitoring resumes.
    """
    log.info("[startup] Fetching IB positions ...")
    ibpos = app.fetch_positions()
    log.info(f"[startup] {len(ibpos)} IB position(s):")
    for p in ibpos:
        log.info(f"  {p['secType']:3s}  {p['localSymbol']:25s}  qty={p['position']}  "
                 f"avgCost={p['avgCost']}")

    ps = load_ps()

    # Cancel any lingering pending stock-buy orders from last run
    if not ps.empty:
        for _, row in ps.iterrows():
            for col in ('pending_order_id', 'cc_pending_order_id'):
                oid_str = str(row.get(col, ''))
                if oid_str and oid_str not in ('nan', '', 'None'):
                    try:
                        oid = int(float(oid_str))
                        log.info(f"[startup] cancelling stale order {oid} "
                                 f"({col} pos={row['position_id']})")
                        app.cancelOrder(oid)
                    except (ValueError, TypeError):
                        pass
        ps['pending_order_id']    = None
        ps['cc_pending_order_id'] = None

    # Build ground truth: stock qty + short calls keyed by (strike, expiry)
    # (localSymbol strings from IB use padded OSI format; matching on the
    # structured strike+expiry is more robust than string comparison.)
    ib_stk_qty = sum(p['position'] for p in ibpos
                     if p['symbol'] == SYMBOL and p['secType'] == 'STK'
                     and p['position'] > 0)
    ib_calls = {
        (round(float(p['strike']), 4), str(p['expiry'])): p
        for p in ibpos
        if p['symbol'] == SYMBOL and p['secType'] == 'OPT'
        and p['right'] == 'C' and p['position'] < 0
    }

    # Drop CSV rows with no matching stock or CC coverage in IB
    keep_rows = []
    # Default shares = first strategy's shares (only used for rows missing the field)
    default_shares = int(params['strategies'][0]['shares_per_position'])
    for i, row in ps.iterrows():
        shares = int(float(row.get('shares') or default_shares))
        cc_sym = str(row.get('cc_local_symbol', '') or '')
        has_cc_in_csv = cc_sym not in ('', 'nan', 'None')
        if has_cc_in_csv:
            cc_key = (round(float(row['cc_strike']), 4), str(row['cc_expiry']))
            cc_present_in_ib = cc_key in ib_calls
        else:
            cc_present_in_ib = False
        has_stk_in_ib = ib_stk_qty >= shares

        if not has_stk_in_ib:
            log.info(f"[startup] pos={row['position_id']} — no stock in IB, dropping row")
            _cancel_cc_sub(app, row)
            continue

        if has_cc_in_csv and not cc_present_in_ib:
            # CC expired/closed during downtime.  Flatten the stock.
            log.info(f"[startup] pos={row['position_id']} — CC {cc_sym} missing in IB "
                     f"(expired?); MKT SELL stock, dropping row")
            oid = app.next_order_id()
            app.placeOrder(oid, _stk_contract(), _market_order('SELL', shares))
            _log_txn({
                'timestamp': datetime.now().isoformat(),
                'position_id': row['position_id'], 'leg': 'stock', 'action': 'SELL',
                'symbol': SYMBOL, 'local_symbol': SYMBOL, 'sec_type': 'STK',
                'quantity': shares, 'price': 0, 'order_id': oid,
                'reason': 'startup_cc_missing',
            })
            _cancel_cc_sub(app, row)
            ib_stk_qty -= shares
            continue

        # Good row — re-subscribe CC mktdata if present (build contract from
        # strike+expiry; do NOT pass localSymbol — IB would require canonical form)
        if has_cc_in_csv:
            cc_rid = app.next_req_id()
            app._cc_prices[cc_rid] = {'bid': None, 'ask': None}
            opt = _option_contract(float(row['cc_strike']), str(row['cc_expiry']))
            app.reqMktData(cc_rid, opt, '', False, False, [])
            row['cc_mktdata_req_id'] = cc_rid
            log.info(f"[startup] resubscribed CC {cc_sym} rid={cc_rid} pos={row['position_id']}")
        keep_rows.append(row)
        ib_stk_qty -= shares

    ps = pd.DataFrame(keep_rows, columns=_PS_COLS) if keep_rows else pd.DataFrame(columns=_PS_COLS)

    # Flatten any remaining untracked IB stock qty
    if ib_stk_qty > 0:
        log.warning(f"[startup] {ib_stk_qty} untracked {SYMBOL} shares in IB — MKT SELL")
        oid = app.next_order_id()
        app.placeOrder(oid, _stk_contract(), _market_order('SELL', int(ib_stk_qty)))
        _log_txn({
            'timestamp': datetime.now().isoformat(),
            'position_id': 'UNKNOWN', 'leg': 'stock', 'action': 'SELL',
            'symbol': SYMBOL, 'local_symbol': SYMBOL, 'sec_type': 'STK',
            'quantity': int(ib_stk_qty), 'price': 0, 'order_id': oid,
            'reason': 'startup_close_untracked',
        })

    save_ps(ps)
    log.info(f"[startup] position_support.csv: {len(ps)} active row(s)")
    return ps


# ══════════════════════════════════════════════════════════════════════════════
# CASH ACCOUNTING
# ══════════════════════════════════════════════════════════════════════════════

def current_cash(ps: pd.DataFrame, starting_cash: float) -> float:
    """Rough cash on hand = starting − sum(cash_used for open positions)."""
    if ps.empty:
        return starting_cash
    used = pd.to_numeric(ps['cash_used'], errors='coerce').fillna(0).sum()
    return float(starting_cash - used)


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY / EXIT LOGIC
# ══════════════════════════════════════════════════════════════════════════════

def _ses_minute(dt: datetime) -> int:
    return dt.hour * 60 + dt.minute


def _has_pending(row) -> bool:
    oid = str(row.get('pending_order_id', ''))
    return oid not in ('', 'nan', 'None')


def _has_cc(row) -> bool:
    return str(row.get('cc_symbol', '')) not in ('', 'nan', 'None')


def _has_exit_pending(row) -> bool:
    oid = str(row.get('cc_pending_order_id', ''))
    return oid not in ('', 'nan', 'None')


def _composite_fires(last_row: pd.Series) -> bool:
    """(any bsig_trend) AND (any bsig_momentum) on the given row."""
    try:
        trend_hit = any(int(last_row.get(c, 0)) == 1 for c in BSIG_TREND_COLS)
        mom_hit   = any(int(last_row.get(c, 0)) == 1 for c in BSIG_MOMENTUM_COLS)
    except (TypeError, ValueError):
        return False
    return trend_hit and mom_hit


def _last_entry_time(ps: pd.DataFrame) -> datetime | None:
    if ps.empty:
        return None
    times = pd.to_datetime(ps['entry_time'], errors='coerce').dropna()
    return times.max() if not times.empty else None


def _last_entry_time_for_strategy(ps: pd.DataFrame, strategy_id: int) -> datetime | None:
    """Latest entry_time across positions that belong to the given strategy."""
    if ps.empty:
        return None
    sub = ps[pd.to_numeric(ps['strategy_id'], errors='coerce') == strategy_id]
    if sub.empty:
        return None
    times = pd.to_datetime(sub['entry_time'], errors='coerce').dropna()
    return times.max() if not times.empty else None


def cancel_stale_orders(app: IBApp, ps: pd.DataFrame) -> pd.DataFrame:
    """
    Track BAG order lifecycles:
      • Entry BAG (pending_order_id):  on fill → clear pending, mark position
        as fully open.  If unfilled after 2 bars → cancel + drop row (nothing
        to close, BAG is atomic).
      • Exit BAG (cc_pending_order_id): on fill → drop row (position closed).
        If unfilled after 5 bars → cancel, clear cc_pending_order_id, and the
        next buyback check will re-submit with fresh prices.
    Entry and exit pending states are mutually exclusive on a given row.
    """
    if ps.empty:
        return ps
    ps = ps.copy()
    drops: list[int] = []

    for i, row in ps.iterrows():
        # ── Exit BAG pending ──────────────────────────────────────────────
        if _has_exit_pending(row):
            try:
                oid = int(float(row['cc_pending_order_id']))
            except (ValueError, TypeError):
                ps.at[i, 'cc_pending_order_id'] = None
                continue
            st     = app.order_status.get(oid, {})
            status = st.get('status', '')
            pos_id = row['position_id']

            if status == 'Filled':
                fill = float(st.get('avg_fill', 0.0))
                log.info(f"[exit fill] pos={pos_id} exit BAG oid={oid} filled "
                         f"@ net_credit={fill:.4f}/share")
                _cancel_cc_sub(app, row)
                drops.append(i)
            elif status in ('', 'PreSubmitted', 'Submitted'):
                bars = int(float(row.get('pending_bars') or 0)) + 1
                ps.at[i, 'pending_bars'] = bars
                if bars >= 5:
                    log.warning(f"[orders] exit BAG oid={oid} unfilled {status or '(no status)'} "
                                f"after {bars} bars — cancel, will retry next buyback check")
                    try:
                        app.cancelOrder(oid)
                    except Exception:
                        pass
                    ps.at[i, 'cc_pending_order_id'] = None
                    ps.at[i, 'pending_bars']        = 0
            else:
                # Cancelled / Inactive / etc. — clear pending, let next bar retry
                log.info(f"[orders] exit BAG oid={oid} status={status} — "
                         f"clearing, next buyback check will retry")
                ps.at[i, 'cc_pending_order_id'] = None
                ps.at[i, 'pending_bars']        = 0
            continue

        # ── Entry BAG pending ─────────────────────────────────────────────
        if _has_pending(row):
            try:
                oid = int(float(row['pending_order_id']))
            except (ValueError, TypeError):
                drops.append(i)
                continue
            st     = app.order_status.get(oid, {})
            status = st.get('status', '')
            pos_id = row['position_id']

            if status == 'Filled':
                fill = float(st.get('avg_fill', 0.0))
                log.info(f"[fill] pos={pos_id} entry BAG oid={oid} filled "
                         f"@ net_debit={fill:.4f}/share")
                ps.at[i, 'pending_order_id'] = None
                ps.at[i, 'pending_bars']     = 0
                # Update cash_used from the actual net debit per share × shares.
                # BAG fill avg is the per-share net debit (stock − premium).
                if fill > 0:
                    ps.at[i, 'cash_used'] = fill * float(row['shares'])
            elif status == '':
                bars = int(float(row.get('pending_bars') or 0)) + 1
                ps.at[i, 'pending_bars'] = bars
                if bars >= 2:
                    log.warning(f"[orders] entry BAG oid={oid} no status after {bars} bars "
                                f"— cancel + drop row (atomic, nothing to unwind)")
                    try:
                        app.cancelOrder(oid)
                    except Exception:
                        pass
                    drops.append(i)
            elif status in ('PreSubmitted', 'Submitted'):
                bars = int(float(row.get('pending_bars') or 0)) + 1
                ps.at[i, 'pending_bars'] = bars
                if bars >= 2:
                    log.info(f"[orders] entry BAG oid={oid} {status} after {bars} bars "
                             f"— cancel + drop row")
                    try:
                        app.cancelOrder(oid)
                    except Exception:
                        pass
                    drops.append(i)
            else:
                log.info(f"[orders] entry BAG oid={oid} status={status} — dropping row")
                drops.append(i)

    if drops:
        ps = ps.drop(index=drops).reset_index(drop=True)
    return ps


def check_cc_stops(app: IBApp, ps: pd.DataFrame, bar: dict,
                   bar_dt: datetime, params: dict) -> pd.DataFrame:
    """
    paper3 ONLY — trailing combo-net stop-loss (paper2 has no stop).

    Each bar, mark every open CC to market:
        net/sh = (stock_bid − entry_price) − (option_ask − cc_open_price)
    Track a high-water mark (hwm_net, persisted on the row).  Fire when
        net/sh ≤ hwm_net − stop_atr_mult × atr_at_entry
    and close the whole position with the SAME atomic BAG SELL the buyback path
    uses (reason 'stop_loss_combo_net').  cancel_stale_orders drops the row when
    the exit BAG fills, exactly as for a buyback.

    Stale-quote fallback: when the option ask is unavailable (no live tick) and
    stale_stop_fallback='stock_leg', fire on a trailing stop of the stock leg
    alone (hwm_stock, maintained every bar) and price the closing BAG off the
    call's intrinsic value.

    Runs BEFORE check_cc_buybacks so the protective exit takes priority.  A no-op
    when stop_atr_mult == 0 (paper2-parity).
    """
    if ps.empty:
        return ps
    k = float(params.get('stop_atr_mult', 0) or 0)
    if k <= 0:
        return ps
    fallback = str(params.get('stale_stop_fallback', 'skip'))
    bar_bid  = float(bar['avg_bid'])
    ps = ps.copy()

    for i, row in ps.iterrows():
        if not _has_cc(row):           continue
        if _has_exit_pending(row):     continue   # exit BAG already outstanding
        if _has_pending(row):          continue   # entry BAG not filled yet
        try:
            atr      = float(row['atr_at_entry'])
            entry_px = float(row['entry_price'])
            cc_open  = float(row['cc_open_price'])
        except (ValueError, TypeError, KeyError):
            continue
        if not (atr > 0):              continue   # no ATR band → cannot stop
        band = k * atr

        # live option ask from the persistent cc subscription (may be absent)
        try:
            cc_rid = int(float(row.get('cc_mktdata_req_id') or 0))
        except (ValueError, TypeError):
            cc_rid = 0
        ask = (app._cc_prices.get(cc_rid, {}) if cc_rid > 0 else {}).get('ask')

        # maintain the stock-leg HWM every bar (stock price is always present)
        stock_net = bar_bid - entry_px
        hs = row.get('hwm_stock')
        hwm_stock = stock_net if (hs is None or pd.isna(hs)) else max(float(hs), stock_net)
        ps.at[i, 'hwm_stock'] = hwm_stock

        fire = False
        basis = None
        opt_close = None
        if ask is not None:
            net = stock_net - (float(ask) - cc_open)
            hn  = row.get('hwm_net')
            hwm_net = net if (hn is None or pd.isna(hn)) else max(float(hn), net)
            ps.at[i, 'hwm_net'] = hwm_net
            if net <= hwm_net - band:
                fire, basis, opt_close = True, 'combo_net', float(ask)
        elif fallback == 'stock_leg':
            if stock_net <= hwm_stock - band:
                # no fresh option price — close at the call's intrinsic value
                opt_close = max(0.0, bar_bid - float(row['cc_strike']))
                fire, basis = True, 'stock_fallback'

        if not fire:
            continue

        # submit the atomic BAG SELL (identical mechanism to a buyback exit)
        shares   = int(float(row['shares']))
        pos_id   = row['position_id']
        strat_id = row.get('strategy_id', '')
        try:
            option_conid = int(float(row.get('cc_conid') or 0))
        except (ValueError, TypeError):
            option_conid = 0
        if not option_conid:
            log.warning(f"[CC stop] pos={pos_id} — no cc_conid; cannot build exit BAG")
            continue
        stock_conid = get_stock_conid(app)
        if stock_conid is None:
            log.warning(f"[CC stop] pos={pos_id} — no stock conId; skip")
            continue

        quantity             = shares // 100
        net_credit_per_share = bar_bid - float(opt_close)
        lmt_price            = net_credit_per_share - BAG_LMT_EXIT_BUFFER
        cc_sym               = str(row['cc_local_symbol'])

        bag     = _buywrite_bag(stock_conid, option_conid)
        order   = _bag_limit_order('SELL', quantity, lmt_price)
        bag_oid = app.next_order_id()
        app.placeOrder(bag_oid, bag, order)
        log.info(f"[CC stop] pos={pos_id} strat={strat_id} {cc_sym}  "
                 f"basis={basis} stk_bid={bar_bid} opt_close={opt_close:.4f} "
                 f"hwm_net={ps.at[i, 'hwm_net'] if basis=='combo_net' else 'n/a'} "
                 f"band={band:.3f}  BAG SELL qty={quantity} "
                 f"lmt_net_credit={lmt_price:.2f}/share  bag_oid={bag_oid}")
        _log_txn({
            'timestamp': bar_dt.isoformat(), 'position_id': pos_id,
            'strategy_id': strat_id,
            'leg': 'combo', 'action': 'SELL', 'symbol': SYMBOL,
            'local_symbol': f'BAG {SYMBOL}+{cc_sym}',
            'sec_type': 'BAG', 'quantity': quantity,
            'price': lmt_price, 'order_id': bag_oid,
            'reason': f'stop_loss_{basis}',
        })
        ps.at[i, 'cc_pending_order_id'] = bag_oid
        ps.at[i, 'pending_bars']        = 0

    return ps


def check_cc_buybacks(app: IBApp, ps: pd.DataFrame, bar: dict,
                      bar_dt: datetime, params: dict) -> pd.DataFrame:
    """
    TV-based buyback per open CC: option_ask − max(0, stock_bid − strike) < buyback_tv.
    Each position uses its own buyback_tv (copied from its strategy at entry time).
    On trigger: submit a single atomic BAG order (SELL combo) — buy the call
    back AND sell the stock in one exchange-side round.  The row is NOT
    dropped here; cancel_stale_orders drops it when the exit BAG fills.
    """
    if ps.empty:
        return ps
    bar_bid = float(bar['avg_bid'])
    ps = ps.copy()

    for i, row in ps.iterrows():
        if not _has_cc(row):
            continue
        # Skip if an exit BAG is already outstanding (prevent duplicate orders)
        if _has_exit_pending(row):
            continue
        # Skip if entry BAG hasn't filled yet (no CC to buy back)
        if _has_pending(row):
            continue

        cc_sym = str(row['cc_local_symbol'])
        try:
            cc_rid = int(float(row.get('cc_mktdata_req_id') or 0))
        except (ValueError, TypeError):
            cc_rid = 0
        prices = app._cc_prices.get(cc_rid, {}) if cc_rid > 0 else {}
        ask = prices.get('ask')
        if ask is None:
            log.debug(f"[cc monitor] pos={row['position_id']} {cc_sym} no ask yet")
            continue

        strike    = float(row['cc_strike'])
        intrinsic = max(0.0, bar_bid - strike)
        tv        = ask - intrinsic
        try:
            buyback_tv = float(row['buyback_tv'])
        except (ValueError, TypeError):
            buyback_tv = float(params['strategies'][0]['buyback_tv'])
        log.debug(f"[cc monitor] pos={row['position_id']} {cc_sym} ask={ask} "
                  f"tv={tv:.4f}  threshold={buyback_tv}")

        if tv >= buyback_tv:
            continue

        shares   = int(float(row['shares']))
        pos_id   = row['position_id']
        strat_id = row.get('strategy_id', '')

        # Resolve conIds for exit BAG
        try:
            option_conid = int(float(row.get('cc_conid') or 0))
        except (ValueError, TypeError):
            option_conid = 0
        if not option_conid:
            log.warning(f"[CC buyback] pos={pos_id} — no cc_conid on row; "
                        f"cannot build exit BAG this bar")
            continue
        stock_conid = get_stock_conid(app)
        if stock_conid is None:
            log.warning(f"[CC buyback] pos={pos_id} — no stock conId; skip")
            continue

        # Atomic BAG: SELL the combo.  Net credit per share = stock_bid − option_ask.
        quantity             = shares // 100
        net_credit_per_share = float(bar_bid) - float(ask)
        lmt_price            = net_credit_per_share - BAG_LMT_EXIT_BUFFER

        bag     = _buywrite_bag(stock_conid, option_conid)
        order   = _bag_limit_order('SELL', quantity, lmt_price)
        bag_oid = app.next_order_id()
        app.placeOrder(bag_oid, bag, order)
        log.info(f"[CC buyback] pos={pos_id} strat={strat_id} {cc_sym}  "
                 f"tv={tv:.4f} < {buyback_tv}  "
                 f"BAG SELL qty={quantity} stk_bid={bar_bid} opt_ask={ask} "
                 f"lmt_net_credit={lmt_price:.2f}/share  bag_oid={bag_oid}")
        _log_txn({
            'timestamp': bar_dt.isoformat(), 'position_id': pos_id,
            'strategy_id': strat_id,
            'leg': 'combo', 'action': 'SELL', 'symbol': SYMBOL,
            'local_symbol': f'BAG {SYMBOL}+{cc_sym}',
            'sec_type': 'BAG', 'quantity': quantity,
            'price': lmt_price, 'order_id': bag_oid,
            'reason': f'buyback_tv_{tv:.4f}',
        })

        # Mark exit as pending — row is kept until the BAG fills.
        # pending_bars is reused to count bars waiting for the exit to fill.
        ps.at[i, 'cc_pending_order_id'] = bag_oid
        ps.at[i, 'pending_bars']        = 0

    return ps


def check_cc_expiry(app: IBApp, ps: pd.DataFrame, bar: dict,
                    bar_dt: datetime) -> pd.DataFrame:
    """
    On Friday ≥ 15:45: drop rows whose CC is at expiry.
      - Stock bid > strike → IB will auto-assign, nothing to place
      - Stock bid ≤ strike → sell stock at market (CC expires worthless)
    """
    if _ses_minute(bar_dt) < EOD_MINUTE or ps.empty:
        return ps
    bar_bid = float(bar['avg_bid'])
    ps = ps.copy()
    drops: list[int] = []

    for i, row in ps.iterrows():
        if not _has_cc(row):
            continue
        exp_ib = str(row['cc_expiry'])
        try:
            exp_d = datetime.strptime(exp_ib, '%Y%m%d').date()
        except ValueError:
            continue
        if bar_dt.date() != exp_d:
            continue
        strike = float(row['cc_strike'])
        shares = int(float(row['shares']))
        pos_id = row['position_id']
        if bar_bid > strike:
            log.info(f"[CC expiry] pos={pos_id} ITM bid={bar_bid} > strike={strike} — IB will assign")
            _log_txn({
                'timestamp': bar_dt.isoformat(), 'position_id': pos_id,
                'leg': 'stock', 'action': 'ASSIGNED', 'symbol': SYMBOL,
                'local_symbol': SYMBOL, 'sec_type': 'STK', 'quantity': shares,
                'price': strike, 'order_id': 0,
                'reason': f'cc_assigned_strike_{strike}',
            })
        else:
            oid = app.next_order_id()
            app.placeOrder(oid, _stk_contract(), _market_order('SELL', shares))
            log.info(f"[CC expiry] pos={pos_id} OTM bid={bar_bid} ≤ strike={strike} — MKT SELL stock")
            _log_txn({
                'timestamp': bar_dt.isoformat(), 'position_id': pos_id,
                'leg': 'stock', 'action': 'SELL', 'symbol': SYMBOL,
                'local_symbol': SYMBOL, 'sec_type': 'STK', 'quantity': shares,
                'price': bar_bid, 'order_id': oid,
                'reason': 'cc_expired_otm',
            })
        _cancel_cc_sub(app, row)
        drops.append(i)

    if drops:
        ps = ps.drop(index=drops).reset_index(drop=True)
    return ps


def check_entry_signal(
    app:       IBApp,
    df_ext:    pd.DataFrame,
    ps:        pd.DataFrame,
    bar:       dict,
    bar_dt:    datetime,
    params:    dict,
    chain_df:  pd.DataFrame,
) -> pd.DataFrame:
    """
    Every bar: evaluate the buy-signal bitfield once, log a per-strategy
    snapshot, and try to open a position for each strategy whose
    signal_mode gate passes.  Strategies with signal_mode='none' fire on
    every bar.  Entries are considered from ENTRY_EARLIEST_MINUTE (09:35)
    onward with no upper cutoff.
    """
    if _ses_minute(bar_dt) < ENTRY_EARLIEST_MINUTE or df_ext.empty:
        return ps

    df_sig = add_buy_signals(df_ext.tail(10).copy())
    if df_sig.empty:
        return ps
    last = df_sig.iloc[-1]
    trend_hits = [c.replace('bsig_', '')
                  for c in BSIG_TREND_COLS if int(last.get(c, 0)) == 1]
    mom_hits   = [c.replace('bsig_', '')
                  for c in BSIG_MOMENTUM_COLS if int(last.get(c, 0)) == 1]
    trend_any  = bool(trend_hits)
    mom_any    = bool(mom_hits)

    last_ext = df_ext.iloc[-1]
    entry_px = float(bar['avg_ask'])

    # Per-bar snapshot — logs quote + TV + decision for every strategy,
    # using each strategy's own signal_mode gate.  Returns a cache so
    # _try_open_for_strategy can reuse the fetched quote.
    quote_cache = _log_strategy_snapshot(
        app, ps, bar_dt, entry_px, params, chain_df,
        trend_hits, mom_hits, trend_any, mom_any,
    )

    for strategy_id, strat in enumerate(params['strategies']):
        if not _signal_mode_fires(strat['signal_mode'], trend_any, mom_any):
            continue
        ps = _try_open_for_strategy(
            app, strategy_id, strat, ps, bar, bar_dt,
            entry_px, last_ext, params, chain_df,
            precomputed=quote_cache.get(strategy_id),
        )
    return ps


def _log_strategy_snapshot(
    app:         IBApp,
    ps:          pd.DataFrame,
    bar_dt:      datetime,
    entry_px:    float,
    params:      dict,
    chain_df:    pd.DataFrame,
    trend_hits:  list,
    mom_hits:    list,
    trend_any:   bool,
    mom_any:     bool,
) -> dict:
    """
    For every configured strategy, evaluate its signal_mode gate, resolve
    (expiry, strike), fetch the live call-option quote, compute cc_tv,
    evaluate every entry gate, and log a one-line verdict.  Runs on every
    bar — each strategy's `signal` gate is decided by its own signal_mode.

    Returns {strategy_id: {'strike','exp_ib','bid','ask'}} so the subsequent
    entry attempt can reuse the same quote without a refetch.
    """
    log.info(
        f"[snap] signals: "
        f"trend={len(trend_hits)}/{len(BSIG_TREND_COLS)} "
        f"({','.join(trend_hits) if trend_hits else '-'})  "
        f"mom={len(mom_hits)}/{len(BSIG_MOMENTUM_COLS)} "
        f"({','.join(mom_hits) if mom_hits else '-'})"
    )

    cache: dict = {}
    cash = current_cash(ps, float(params['starting_cash']))

    for strategy_id, strat in enumerate(params['strategies']):
        exp_label  = strat['expiry_label']
        strk_label = strat['strike_label']
        mode       = strat['signal_mode']
        sig_fires  = _signal_mode_fires(mode, trend_any, mom_any)

        try:
            friday = resolve_expiry_friday(bar_dt.date(), exp_label)
        except ValueError as e:
            log.info(f"[snap] strat={strategy_id} mode={mode} "
                     f"{exp_label}/{strk_label}  bad expiry_label: {e}")
            continue
        exp_ib = friday.strftime('%Y%m%d')

        strike = resolve_strike(chain_df, exp_ib, entry_px, strk_label)
        if strike is None:
            log.info(f"[snap] strat={strategy_id} mode={mode} "
                     f"{exp_label}/{strk_label}  "
                     f"no strike in chain at ${entry_px:.2f} exp={exp_ib}")
            continue

        opt      = _option_contract(strike, exp_ib)
        bid, ask = app.get_option_quote(opt)
        cache[strategy_id] = {
            'strike': strike, 'exp_ib': exp_ib, 'bid': bid, 'ask': ask,
        }

        tv_min = float(strat['cc_tv_min'])
        tv_max = float(strat['cc_tv_max'])
        if bid is not None:
            cc_tv     = bid - max(0.0, entry_px - strike)
            tv_in_rng = tv_min <= cc_tv <= tv_max
            tv_str    = f'{cc_tv:.4f}'
        else:
            cc_tv     = None
            tv_in_rng = False
            tv_str    = 'N/A'

        cd = timedelta(minutes=int(strat['cooldown_minutes']))
        last_entry = _last_entry_time_for_strategy(ps, strategy_id)
        cooldown_ok = last_entry is None or (bar_dt - last_entry) >= cd

        shares  = int(strat['shares_per_position'])
        cost    = entry_px * shares
        cash_ok = cash >= cost

        gates_failed = []
        if not sig_fires:   gates_failed.append(f'signal({mode})')
        if not cooldown_ok: gates_failed.append('cooldown')
        if not cash_ok:     gates_failed.append('cash')
        if bid is None:     gates_failed.append('no_quote')
        elif not tv_in_rng:
            gates_failed.append('tv_low' if cc_tv < tv_min else 'tv_high')

        decision = 'BUY' if not gates_failed else f"SKIP [{','.join(gates_failed)}]"

        bid_s = f'{bid:.2f}' if bid is not None else 'N/A'
        ask_s = f'{ask:.2f}' if ask is not None else 'N/A'
        log.info(
            f"[snap] strat={strategy_id} mode={mode} "
            f"{exp_label}/{strk_label} K={strike:g} exp={exp_ib}  "
            f"bid={bid_s} ask={ask_s}  "
            f"cc_tv={tv_str} in [{tv_min:g},{tv_max:g}]  "
            f"cash=${cash:,.0f}  → {decision}"
        )
    return cache


def _try_open_for_strategy(
    app:          IBApp,
    strategy_id:  int,
    strat:        dict,
    ps:           pd.DataFrame,
    bar:          dict,
    bar_dt:       datetime,
    entry_px:     float,
    last_ext:     pd.Series,
    params:       dict,
    chain_df:     pd.DataFrame,
    precomputed:  dict | None = None,
) -> pd.DataFrame:
    """Per-strategy entry evaluation; opens one position iff all gates pass.

    `precomputed` — if supplied by the snapshot pass — contains
    {'strike','exp_ib','bid','ask'} so we skip the redundant chain/quote
    lookup.  The gates below still run end-to-end to keep this function
    self-contained; only the IB round-trips are elided.
    """
    # Cooldown — per strategy
    cd = timedelta(minutes=int(strat['cooldown_minutes']))
    last_entry = _last_entry_time_for_strategy(ps, strategy_id)
    if last_entry is not None and (bar_dt - last_entry) < cd:
        log.debug(f"[entry] strat={strategy_id} skip — cooldown "
                  f"{bar_dt - last_entry} < {cd}")
        return ps

    # Cash — shared pool
    shares = int(strat['shares_per_position'])
    cost   = entry_px * shares
    cash   = current_cash(ps, float(params['starting_cash']))
    if cash < cost:
        log.info(f"[entry] strat={strategy_id} — insufficient cash "
                 f"${cash:.2f} < ${cost:.2f}; skip")
        return ps

    # Resolve expiry (needed for opt_display, even when we have the cache)
    try:
        friday = resolve_expiry_friday(bar_dt.date(), strat['expiry_label'])
    except ValueError as e:
        log.warning(f"[entry] strat={strategy_id} bad expiry_label: {e}")
        return ps

    if precomputed:
        exp_ib = precomputed['exp_ib']
        strike = precomputed['strike']
        bid    = precomputed['bid']
        ask    = precomputed['ask']
    else:
        exp_ib = friday.strftime('%Y%m%d')
        strike = resolve_strike(chain_df, exp_ib, entry_px, strat['strike_label'])
        if strike is None:
            log.info(f"[entry] strat={strategy_id} — no {strat['strike_label']} "
                     f"strike available for {exp_ib} at entry ${entry_px:.2f}")
            return ps
        opt = _option_contract(strike, exp_ib)
        bid, ask = app.get_option_quote(opt)

    # Build contract from structured fields (no localSymbol) — needed for the
    # persistent mktData subscription after the BAG fills.
    opt = _option_contract(strike, exp_ib)
    opt_display = f"{SYMBOL} {friday.strftime('%y%m%d')}C{int(strike * 1000):08d}"

    if bid is None:
        log.info(f"[entry] strat={strategy_id} — no option quote for "
                 f"strike={strike} exp={exp_ib}")
        return ps
    cc_tv = bid - max(0.0, entry_px - strike)
    if cc_tv < float(strat['cc_tv_min']):
        log.info(f"[entry] strat={strategy_id} cc_tv {cc_tv:.4f} < "
                 f"{strat['cc_tv_min']}; skip")
        return ps
    if cc_tv > float(strat['cc_tv_max']):
        log.info(f"[entry] strat={strategy_id} cc_tv {cc_tv:.4f} > "
                 f"{strat['cc_tv_max']}; skip")
        return ps

    # Resolve conIds required for BAG legs
    option_conid = option_conid_from_chain(chain_df, strike, exp_ib)
    if option_conid is None:
        log.warning(f"[entry] strat={strategy_id} — no option conId for "
                    f"strike={strike} exp={exp_ib}; skip")
        return ps
    stock_conid = get_stock_conid(app)
    if stock_conid is None:
        log.warning(f"[entry] strat={strategy_id} — could not resolve stock conId; skip")
        return ps

    # Atomic BAG: BUY the combo (stock + short call).  quantity = 100-share units.
    quantity            = shares // 100
    net_debit_per_share = float(entry_px) - float(bid)
    lmt_price           = net_debit_per_share + BAG_LMT_ENTRY_BUFFER

    pos_id  = _new_position_id()
    bag     = _buywrite_bag(stock_conid, option_conid)
    order   = _bag_limit_order('BUY', quantity, lmt_price)
    bag_oid = app.next_order_id()
    app.placeOrder(bag_oid, bag, order)
    log.info(f"[BUY] pos={pos_id} strat={strategy_id}  BAG BUY qty={quantity}  "
             f"stk_ask={entry_px}  opt_bid={bid}  "
             f"lmt_net_debit={lmt_price:.2f}/share (total ≈ ${lmt_price * 100 * quantity:,.2f})  "
             f"bag_oid={bag_oid}")
    _log_txn({
        'timestamp': bar_dt.isoformat(), 'position_id': pos_id,
        'strategy_id': strategy_id,
        'leg': 'combo', 'action': 'BUY', 'symbol': SYMBOL,
        'local_symbol': f'BAG {SYMBOL}+{opt_display}',
        'sec_type': 'BAG', 'quantity': quantity,
        'price': lmt_price, 'order_id': bag_oid,
        'reason': f'composite_signal_strat{strategy_id}_tv{cc_tv:.4f}',
    })

    # Persistent option quote subscription for buyback monitoring
    cc_rid = app.next_req_id()
    app._cc_prices[cc_rid] = {'bid': None, 'ask': None}
    app.reqMktData(cc_rid, opt, '', False, False, [])

    new_row = {
        'position_id':          pos_id,
        'strategy_id':          strategy_id,
        'entry_time':           bar_dt.isoformat(),
        'entry_price':          entry_px,
        'entry_wap':            float(bar['average']),
        'shares':               shares,
        'cash_used':            cost,
        'buyback_tv':           float(strat['buyback_tv']),
        'atr_at_entry':         float(last_ext.get('atr_14', float('nan'))),
        'rsi_at_entry':         float(last_ext.get('rsi_14', float('nan'))),
        'adx_at_entry':         float(last_ext.get('adx_14', float('nan'))),
        'vwap_at_entry':        float(last_ext.get('vwp_vwap', float('nan'))),
        'pending_order_id':     bag_oid,       # entry BAG (atomic)
        'pending_bars':         0,
        'cc_symbol':            SYMBOL,
        'cc_local_symbol':      opt_display,
        'cc_strike':            strike,
        'cc_expiry':            exp_ib,
        'cc_right':             'C',
        'cc_conid':             option_conid,  # needed to rebuild exit BAG
        'cc_open_price':        bid,
        'cc_open_time':         bar_dt.isoformat(),
        'cc_tv_at_entry':       round(cc_tv, 4),
        'cc_mktdata_req_id':    cc_rid,
        'cc_pending_order_id':  None,           # set when buyback BAG is submitted
        'hwm_net':              float('nan'),   # seeded on first stop mark
        'hwm_stock':            float('nan'),
    }
    ps = pd.concat([ps, pd.DataFrame([new_row])], ignore_index=True)
    return ps


# ══════════════════════════════════════════════════════════════════════════════
# BAR LOOP
# ══════════════════════════════════════════════════════════════════════════════

def process_bar(
    app:      IBApp,
    bar:      dict,
    ps:       pd.DataFrame,
    df_raw:   pd.DataFrame,
    params:   dict,
    chain_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    bar_dt = datetime.strptime(bar['date'], '%Y-%m-%d %H:%M:%S')
    log.info(f"━━━ bar {bar_dt.strftime('%Y-%m-%d %H:%M')}  "
             f"close={bar['close']}  wap={bar['average']}  "
             f"bid={bar['avg_bid']}  ask={bar['avg_ask']}  vix={bar['vix']} ━━━")

    # 1. Append new bar to rolling raw df
    new_df = pd.DataFrame([bar])
    new_df['date'] = pd.to_datetime(new_df['date'])
    df_raw = (pd.concat([df_raw, new_df], ignore_index=True)
                .drop_duplicates(subset='date')
                .sort_values('date')
                .reset_index(drop=True)
                .tail(MAX_BARS)
                .reset_index(drop=True))

    # 2. Recompute indicators + buy signals
    try:
        df_ext = compute_indicators(df_raw.copy())
    except Exception as exc:
        log.exception(f"[bar] compute_indicators failed: {exc}")
        df_ext = df_raw.copy()

    # 3. Persist today's partial CSV (one file per session day)
    try:
        update_today_partial(df_raw, bar_dt.date())
    except Exception as exc:
        log.warning(f"[bar] update_today_partial failed: {exc}")

    # 4. Order lifecycle + exit checks (stop runs FIRST — protective exit has priority)
    ps = cancel_stale_orders(app, ps)
    ps = check_cc_stops(app, ps, bar, bar_dt, params)
    ps = check_cc_buybacks(app, ps, bar, bar_dt, params)
    ps = check_cc_expiry(app, ps, bar, bar_dt)

    # 5. Entry evaluation (last so exits run first)
    ps = check_entry_signal(app, df_ext, ps, bar, bar_dt, params, chain_df)

    save_ps(ps)
    return ps, df_raw, df_ext


# ══════════════════════════════════════════════════════════════════════════════
# IMMEDIATE TV CHECK ON STARTUP
# ══════════════════════════════════════════════════════════════════════════════

def immediate_tv_check(app: IBApp, ps: pd.DataFrame, params: dict,
                       wait_secs: float = 6.0) -> pd.DataFrame:
    """
    After startup resubscribe, wait briefly for option asks to arrive, then
    check TV for every open CC.  Close any position already below threshold.
    """
    if ps.empty:
        return ps
    if app._current_bid is None:
        log.info(f"[startup] waiting {wait_secs}s for bid/ask ticks ...")
        time.sleep(wait_secs)
    else:
        time.sleep(2.0)   # give CC subs a moment
    stk_bid = app._current_bid or 0.0
    if stk_bid <= 0:
        log.info("[startup] no stock bid yet — deferring TV check to next bar")
        return ps
    synthetic_bar = {
        'avg_bid': stk_bid,
        'avg_ask': app._current_ask or stk_bid,
        'average': stk_bid,
    }
    ps = check_cc_stops(app, ps, synthetic_bar, datetime.now(), params)
    return check_cc_buybacks(app, ps, synthetic_bar, datetime.now(), params)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    log.info("=" * 72)
    log.info(f"arbo702 starting up")
    log.info(f"  symbol={PARAMS['symbol']}  starting_cash=${PARAMS['starting_cash']:,}  "
             f"clientId={PARAMS['client_id']}")
    log.info(f"  strategies: {len(PARAMS['strategies'])}")
    for i, s in enumerate(PARAMS['strategies']):
        log.info(f"    [{i}] shares={s['shares_per_position']}  "
                 f"cooldown={s['cooldown_minutes']}m  "
                 f"cc_tv=[{s['cc_tv_min']},{s['cc_tv_max']}]  "
                 f"buyback_tv={s['buyback_tv']}  "
                 f"{s['expiry_label']}/{s['strike_label']}")
    log.info("=" * 72)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REF_DIR.mkdir(parents=True, exist_ok=True)
    _init_tx_file()
    prune_old_ref_files(int(PARAMS['retention_days']))

    # ── IB connect ────────────────────────────────────────────────────────────
    app = IBApp()
    app.connect(PARAMS['host'], int(PARAMS['port']), int(PARAMS['client_id']))
    ib_thread = threading.Thread(target=app.run, daemon=True, name='ib-api')
    ib_thread.start()
    if not app._connected_event.wait(timeout=CONN_TIMEOUT):
        log.error(f"Could not connect to TWS at {PARAMS['host']}:{PARAMS['port']}")
        sys.exit(1)
    time.sleep(1)

    # ── Reference data ────────────────────────────────────────────────────────
    today = date.today()
    chain_df = ensure_contracts(app, today)

    # ── Startup reconcile ─────────────────────────────────────────────────────
    ps = sync_on_startup(app, PARAMS)

    # ── Historical bootstrap ──────────────────────────────────────────────────
    df_raw = ensure_stock_history(app, today)

    # ── Live subscriptions (stock, VIX, 5-sec bars) ───────────────────────────
    app.reqMktData(REQ_STK_MKTDATA, _stk_contract(), '', False, False, [])
    log.info(f"[IB] reqMktData {SYMBOL} bid/ask  rid={REQ_STK_MKTDATA}")
    app.reqMktData(REQ_VIX_MKTDATA, _vix_contract(), '', False, False, [])
    log.info(f"[IB] reqMktData VIX               rid={REQ_VIX_MKTDATA}")
    app.reqRealTimeBars(REQ_STK_RTBARS, _stk_contract(), 5, 'TRADES', True, [])
    log.info(f"[IB] reqRealTimeBars {SYMBOL}     rid={REQ_STK_RTBARS}")

    # ── Immediate TV check on any resumed CCs ─────────────────────────────────
    ps = immediate_tv_check(app, ps, PARAMS)
    save_ps(ps)

    # ── Main loop ─────────────────────────────────────────────────────────────
    log.info("[loop] entering bar loop")
    df_ext = df_raw.copy()
    while True:
        try:
            bar = app.bar_queue.get(timeout=120)
        except queue.Empty:
            log.warning("[loop] no bar in 120s — market closed or IB stall")
            continue
        try:
            ps, df_raw, df_ext = process_bar(app, bar, ps, df_raw, PARAMS, chain_df)
        except Exception as exc:
            log.exception(f"[loop] bar {bar.get('date')} failed: {exc}")


if __name__ == '__main__':
    main()
