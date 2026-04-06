"""
ib_connection_test.py
─────────────────────
Standalone IB connection test.  Uses CLIENT_ID=99 so it does not conflict
with arbo701 (CLIENT_ID=1) when both are running simultaneously.

Test sequence
  1. Connect to TWS
  2. Fetch open positions
  3. Fetch open orders
  4. Subscribe to AAPL market data and record a mid-price quote
  5. Place a BUY LIMIT order at 0.5% below the mid
  6. Wait 30 seconds
  7. Cancel that order
  8. Fetch open positions again
  9. Fetch open orders again
  10. Report PASS / FAIL

Usage
  python ib_connection_test.py [--host 127.0.0.1] [--port 7497]
"""

import argparse
import logging
import queue
import threading
import time
from datetime import datetime

from ibapi.client import EClient
from ibapi.contract import Contract
from ibapi.order import Order
from ibapi.wrapper import EWrapper

# ── config ────────────────────────────────────────────────────────────────────
HOST      = '127.0.0.1'
PORT      = 7497       # TWS paper trading
CLIENT_ID = 99         # distinct from arbo701 (1) to allow concurrent use
SYMBOL    = 'AAPL'
QUOTE_WAIT   = 10      # seconds to wait for a quote
ORDER_WAIT   = 30      # seconds to hold the order before cancelling

# ── logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-7s  %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('ib_test')

# Suppress IB API's own verbose REQUEST/ANSWER/SENDING messages
logging.getLogger('ibapi').setLevel(logging.WARNING)
logging.getLogger('ibapi.client').setLevel(logging.WARNING)
logging.getLogger('ibapi.decoder').setLevel(logging.WARNING)
logging.getLogger('ibapi.comm').setLevel(logging.WARNING)


# ── IB app ────────────────────────────────────────────────────────────────────

class TestApp(EWrapper, EClient):

    REQ_QUOTE  = 1
    REQ_ORDERS = 2

    def __init__(self):
        EWrapper.__init__(self)
        EClient.__init__(self, self)

        self._connected      = threading.Event()
        self._next_oid       = None

        # positions
        self._positions      : list[dict] = []
        self._pos_done       = threading.Event()

        # open orders
        self._open_orders    : list[dict] = []
        self._orders_done    = threading.Event()

        # quote
        self._bid : float | None = None
        self._ask : float | None = None
        self._quote_ready    = threading.Event()

        # order status tracking  {orderId: status_str}
        self._order_status   : dict[int, str] = {}
        self._oid_lock       = threading.Lock()
        self._next_order_id  = None

    # ── connection ────────────────────────────────────────────────────────────

    def nextValidId(self, orderId: int):
        self._next_order_id = orderId
        self._connected.set()
        log.info(f"[IB] Connected — next order ID: {orderId}")

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=''):
        if errorCode in (2104, 2106, 2158, 2107, 2103, 2105):
            return   # routine farm-connection info
        level = logging.WARNING if errorCode >= 2000 else logging.ERROR
        log.log(level, f"[IB] reqId={reqId}  code={errorCode}  {errorString}")

    # ── positions ─────────────────────────────────────────────────────────────

    def position(self, account, contract, position, avgCost):
        self._positions.append({
            'account':     account,
            'symbol':      contract.symbol,
            'localSymbol': contract.localSymbol,
            'secType':     contract.secType,
            'position':    position,
            'avgCost':     avgCost,
        })

    def positionEnd(self):
        self._pos_done.set()

    # ── open orders ───────────────────────────────────────────────────────────

    def openOrder(self, orderId, contract, order, orderState):
        self._open_orders.append({
            'orderId':   orderId,
            'symbol':    contract.symbol,
            'secType':   contract.secType,
            'action':    order.action,
            'orderType': order.orderType,
            'qty':       order.totalQuantity,
            'lmtPrice':  order.lmtPrice,
            'status':    orderState.status,
        })

    def openOrderEnd(self):
        self._orders_done.set()

    # ── market data (quotes) ──────────────────────────────────────────────────

    def tickPrice(self, reqId, tickType, price, attrib):
        if reqId != self.REQ_QUOTE or price <= 0:
            return
        if tickType in (1, 66):    # Bid / DelayedBid
            self._bid = price
        elif tickType in (2, 67):  # Ask / DelayedAsk
            self._ask = price
        if self._bid is not None and self._ask is not None:
            self._quote_ready.set()

    # ── order status ──────────────────────────────────────────────────────────

    def orderStatus(self, orderId, status, filled, remaining,
                    avgFillPrice, permId, parentId, lastFillPrice,
                    clientId, whyHeld, mktCapPrice):
        with self._oid_lock:
            self._order_status[orderId] = status
        log.info(f"[order] orderId={orderId}  status={status}  "
                 f"filled={filled}  remaining={remaining}")

    # ── helpers ───────────────────────────────────────────────────────────────

    def next_order_id(self) -> int:
        with self._oid_lock:
            oid = self._next_order_id
            self._next_order_id += 1
            return oid


# ── contract / order builders ─────────────────────────────────────────────────

def _aapl_contract() -> Contract:
    c = Contract()
    c.symbol   = SYMBOL
    c.secType  = 'STK'
    c.exchange = 'SMART'
    c.currency = 'USD'
    return c


def _limit_order(action: str, qty: int, price: float) -> Order:
    o = Order()
    o.action           = action
    o.orderType        = 'LMT'
    o.totalQuantity    = qty
    o.lmtPrice         = round(price, 2)
    o.tif              = 'DAY'
    o.outsideRth       = False
    o.eTradeOnly       = False   # avoid warning 10268 in newer TWS builds
    o.firmQuoteOnly    = False
    return o


# ── report helpers ────────────────────────────────────────────────────────────

def _print_positions(positions: list[dict], label: str):
    log.info(f"── Positions ({label}) ─────────────────────────────────────────")
    if not positions:
        log.info("  (none)")
    for p in positions:
        log.info(f"  {p['secType']:3s}  {p['localSymbol'] or p['symbol']:25s}  "
                 f"qty={p['position']:8.0f}  avgCost={p['avgCost']:.4f}")


def _print_orders(orders: list[dict], label: str):
    log.info(f"── Open orders ({label}) ────────────────────────────────────────")
    if not orders:
        log.info("  (none)")
    for o in orders:
        log.info(f"  orderId={o['orderId']:6d}  {o['action']:4s} {o['qty']:.0f} "
                 f"{o['symbol']}  {o['orderType']}@{o['lmtPrice']:.2f}  "
                 f"status={o['status']}")


# ── main test ─────────────────────────────────────────────────────────────────

def run_test(host: str, port: int) -> bool:
    passed = True

    # ── 1. Connect ────────────────────────────────────────────────────────────
    log.info("=" * 60)
    log.info("IB CONNECTION TEST")
    log.info("=" * 60)
    log.info(f"[1] Connecting to {host}:{port}  clientId={CLIENT_ID}")

    app = TestApp()
    app.connect(host, port, CLIENT_ID)
    ib_thread = threading.Thread(target=app.run, daemon=True, name='ib-api')
    ib_thread.start()

    if not app._connected.wait(timeout=15):
        log.error("[1] FAIL — could not connect within 15s")
        return False
    log.info("[1] PASS — connected")

    time.sleep(0.5)   # allow API handshake to settle

    # ── 2. Fetch open positions ───────────────────────────────────────────────
    log.info("[2] Fetching open positions ...")
    app.reqPositions()
    if not app._pos_done.wait(timeout=10):
        log.warning("[2] WARN — positionEnd not received within 10s")
        passed = False
    _print_positions(app._positions, "before")
    log.info(f"[2] {len(app._positions)} position(s) found")

    # ── 3. Fetch open orders ──────────────────────────────────────────────────
    log.info("[3] Fetching open orders ...")
    app.reqAllOpenOrders()
    if not app._orders_done.wait(timeout=10):
        log.warning("[3] WARN — openOrderEnd not received within 10s")
        passed = False
    _print_orders(app._open_orders, "before")
    log.info(f"[3] {len(app._open_orders)} open order(s) found")

    # ── 4. Get AAPL quote ─────────────────────────────────────────────────────
    log.info(f"[4] Requesting {SYMBOL} quote (reqId={TestApp.REQ_QUOTE}) ...")
    app.reqMktData(TestApp.REQ_QUOTE, _aapl_contract(), '', False, False, [])
    if not app._quote_ready.wait(timeout=QUOTE_WAIT):
        log.error(f"[4] FAIL — no bid/ask within {QUOTE_WAIT}s")
        app.cancelMktData(TestApp.REQ_QUOTE)
        return False
    app.cancelMktData(TestApp.REQ_QUOTE)
    mid = round((app._bid + app._ask) / 2, 2)
    log.info(f"[4] PASS — bid={app._bid}  ask={app._ask}  mid={mid}")

    # ── 5. Place BUY LIMIT 0.5% below mid ────────────────────────────────────
    limit_price = round(mid * 0.995, 2)
    oid = app.next_order_id()
    log.info(f"[5] Placing BUY LIMIT {SYMBOL}  1 share @ {limit_price}  "
             f"(mid={mid} − 0.5%)  orderId={oid}")
    app.placeOrder(oid, _aapl_contract(), _limit_order('BUY', 1, limit_price))

    # give IB a moment to acknowledge
    time.sleep(2)
    status = app._order_status.get(oid, 'unknown')
    if status in ('Filled', 'PreSubmitted', 'Submitted'):
        log.info(f"[5] PASS — order acknowledged  status={status}")
    else:
        log.warning(f"[5] WARN — unexpected status={status} (may still be in flight)")

    # ── 6. Wait 30 seconds ────────────────────────────────────────────────────
    log.info(f"[6] Holding order for {ORDER_WAIT}s ...")
    for remaining in range(ORDER_WAIT, 0, -5):
        time.sleep(5)
        current = app._order_status.get(oid, 'unknown')
        log.info(f"    {remaining:2d}s remaining — order status: {current}")
        if current == 'Filled':
            log.warning("    Order filled unexpectedly — cancellation skipped")
            break

    # ── 7. Cancel the order ───────────────────────────────────────────────────
    current = app._order_status.get(oid, 'unknown')
    if current != 'Filled':
        log.info(f"[7] Cancelling orderId={oid} ...")
        app.cancelOrder(oid)
        time.sleep(3)
        final = app._order_status.get(oid, 'unknown')
        if final in ('Cancelled', 'Inactive'):
            log.info(f"[7] PASS — order cancelled  status={final}")
        else:
            log.warning(f"[7] WARN — status after cancel={final}")
    else:
        log.warning(f"[7] SKIP — order was filled; manual close required")
        passed = False

    # ── 8. Fetch positions again ──────────────────────────────────────────────
    log.info("[8] Fetching positions after cancel ...")
    app._positions = []
    app._pos_done.clear()
    app.reqPositions()
    if not app._pos_done.wait(timeout=10):
        log.warning("[8] WARN — positionEnd timeout")
    _print_positions(app._positions, "after")

    # ── 9. Fetch open orders again ────────────────────────────────────────────
    log.info("[9] Fetching open orders after cancel ...")
    app._open_orders = []
    app._orders_done.clear()
    app.reqAllOpenOrders()
    if not app._orders_done.wait(timeout=10):
        log.warning("[9] WARN — openOrderEnd timeout")
    _print_orders(app._open_orders, "after")

    # ── 10. Result ────────────────────────────────────────────────────────────
    log.info("=" * 60)
    if passed:
        log.info("RESULT: PASS — all steps completed successfully")
    else:
        log.info("RESULT: FAIL — one or more steps had warnings/errors")
    log.info("=" * 60)

    app.disconnect()
    return passed


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='IB TWS connection test')
    parser.add_argument('--host', default=HOST)
    parser.add_argument('--port', default=PORT, type=int)
    args = parser.parse_args()

    ok = run_test(args.host, args.port)
    raise SystemExit(0 if ok else 1)
