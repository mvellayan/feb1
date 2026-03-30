"""
positions.py

Connects to IB, fetches open positions and open orders, and writes:
  data/positions.csv  — one row per open position
  data/trades.csv     — one row per open order

Requires TWS or IB Gateway to be running and accepting API connections.
"""

import csv
import os
import threading
import time

from ibapi.client import EClient
from ibapi.wrapper import EWrapper


HOST      = '127.0.0.1'
PORT      = 7496   # 7497 = TWS paper, 7496 = TWS live, 4002 = Gateway paper
CLIENT_ID = 12     # unique client ID for this script

TIMEOUT   = 30     # seconds to wait for each response

POSITION_COLUMNS = [
    'account', 'symbol', 'localSymbol', 'conId',
    'secType', 'exchange', 'currency', 'position', 'avgCost',
]

TRADE_COLUMNS = [
    'orderId', 'permId', 'account', 'symbol', 'localSymbol', 'conId',
    'secType', 'exchange', 'currency', 'action', 'orderType',
    'totalQty', 'lmtPrice', 'auxPrice', 'status',
]


class PositionsApp(EWrapper, EClient):

    def __init__(self):
        EClient.__init__(self, self)
        self._positions      = []
        self._trades         = []
        self._positions_done = threading.Event()
        self._orders_done    = threading.Event()

    # ------------------------------------------------------------------
    # EWrapper callbacks — positions
    # ------------------------------------------------------------------

    def position(self, account, contract, position, avgCost):
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
        self._positions_done.set()

    # ------------------------------------------------------------------
    # EWrapper callbacks — open orders
    # ------------------------------------------------------------------

    def openOrder(self, orderId, contract, order, orderState):
        self._trades.append({
            'orderId':     orderId,
            'permId':      order.permId,
            'account':     order.account,
            'symbol':      contract.symbol,
            'localSymbol': contract.localSymbol,
            'conId':       contract.conId,
            'secType':     contract.secType,
            'exchange':    contract.exchange,
            'currency':    contract.currency,
            'action':      order.action,
            'orderType':   order.orderType,
            'totalQty':    order.totalQuantity,
            'lmtPrice':    order.lmtPrice,
            'auxPrice':    order.auxPrice,
            'status':      orderState.status,
        })

    def openOrderEnd(self):
        self._orders_done.set()

    # ------------------------------------------------------------------
    # Error handler
    # ------------------------------------------------------------------

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=''):
        if reqId == -1:
            return   # connection-level info
        if errorCode >= 2000:
            return   # warnings, ignore
        print(f"  [IB error] reqId={reqId} code={errorCode}: {errorString}")

    # ------------------------------------------------------------------
    # Fetch helpers
    # ------------------------------------------------------------------

    def fetch_positions(self):
        self.reqPositions()
        if not self._positions_done.wait(timeout=TIMEOUT):
            print("  [!] Timed out waiting for positions")

    def fetch_open_orders(self):
        self.reqAllOpenOrders()
        if not self._orders_done.wait(timeout=TIMEOUT):
            print("  [!] Timed out waiting for open orders")


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main():
    os.makedirs('data', exist_ok=True)

    app = PositionsApp()
    app.connect(HOST, PORT, CLIENT_ID)

    thread = threading.Thread(target=app.run, daemon=True)
    thread.start()
    time.sleep(2)   # allow handshake to complete

    # --- Positions ---
    print("Fetching positions ...", end=' ', flush=True)
    app.fetch_positions()
    print(f"{len(app._positions)} positions")

    with open('data/positions.csv', 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=POSITION_COLUMNS)
        writer.writeheader()
        writer.writerows(app._positions)
    print(f"  → data/positions.csv")

    # --- Open orders ---
    print("Fetching open orders ...", end=' ', flush=True)
    app.fetch_open_orders()
    print(f"{len(app._trades)} orders")

    with open('data/trades.csv', 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=TRADE_COLUMNS)
        writer.writeheader()
        writer.writerows(app._trades)
    print(f"  → data/trades.csv")

    app.disconnect()
    print("\nDone.")


if __name__ == '__main__':
    main()
