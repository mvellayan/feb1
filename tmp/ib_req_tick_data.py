"""
Request real-time tick-by-tick trade data from IB using reqTickByTickData.

Subscribes to AllLast ticks for FB, AMZN, and AAPL, prints incoming trades,
then stops after 120 seconds.

Requires TWS or IB Gateway to be running and accepting API connections.
"""

import threading
import time
from datetime import datetime
from typing import Dict

from ibapi.client import EClient
from ibapi.common import TickAttribLast
from ibapi.contract import Contract
from ibapi.wrapper import EWrapper


HOST = "127.0.0.1"
PORT = 7496
CLIENT_ID = 57
RUN_SECONDS = 120
TICKERS = ["FB", "AMZN", "AAPL"]


def stock_contract(symbol: str) -> Contract:
    contract = Contract()
    contract.symbol = symbol
    contract.secType = "STK"
    contract.exchange = "SMART"
    contract.currency = "USD"
    return contract


class TickByTickApp(EWrapper, EClient):
    def __init__(self) -> None:
        EClient.__init__(self, self)
        self.req_id_to_symbol: Dict[int, str] = {}
        self.connected_event = threading.Event()

    def nextValidId(self, orderId: int) -> None:
        self.connected_event.set()

    def error(self, reqId: int, errorCode: int, errorString: str, advancedOrderRejectJson: str = "") -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"{timestamp} ERROR reqId={reqId} code={errorCode} msg={errorString}")

    def tickByTickAllLast(
        self,
        reqId: int,
        tickType: int,
        time_: int,
        price: float,
        size,
        tickAttribLast: TickAttribLast,
        exchange: str,
        specialConditions: str,
    ) -> None:
        symbol = self.req_id_to_symbol.get(reqId, f"reqId={reqId}")
        timestamp = datetime.fromtimestamp(time_).strftime("%Y-%m-%d %H:%M:%S")
        print(
            f"{timestamp} {symbol} tickType={tickType} "
            f"price={price:.2f} size={size} exchange={exchange} "
            f"pastLimit={tickAttribLast.pastLimit} unreported={tickAttribLast.unreported} "
            f"specialConditions={specialConditions or '-'}"
        )


def main() -> None:
    app = TickByTickApp()
    app.connect(HOST, PORT, CLIENT_ID)

    thread = threading.Thread(target=app.run, daemon=True)
    thread.start()

    if not app.connected_event.wait(timeout=10):
        raise TimeoutError("Timed out waiting for IB API connection handshake")

    for req_id, symbol in enumerate(TICKERS, start=1):
        app.req_id_to_symbol[req_id] = symbol
        app.reqTickByTickData(
            reqId=req_id,
            contract=stock_contract(symbol),
            tickType="AllLast",
            numberOfTicks=0,
            ignoreSize=False,
        )
        print(f"Subscribed to AllLast ticks for {symbol} (reqId={req_id})")

    deadline = time.monotonic() + RUN_SECONDS

    try:
        while time.monotonic() < deadline:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Interrupted, stopping subscriptions early")
    finally:
        for req_id, symbol in app.req_id_to_symbol.items():
            app.cancelTickByTickData(req_id)
            print(f"Cancelled AllLast ticks for {symbol} (reqId={req_id})")

        time.sleep(1)
        app.disconnect()
        print("Disconnected")


if __name__ == "__main__":
    main()
