import threading
from decimal import Decimal

from ibapi.account_summary_tags import AccountSummaryTags
from ibapi.client import EClient
from ibapi.common import BarData
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
import time
from datetime import datetime


class MyTWSApp(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.connected = False
        self.contract_received = False

    def connectAck(self):
        self.connected = True
        print("Connection established")

    def contractDetails(self, reqId, contractDetails):
        print("Contract Details: ", reqId, " ", contractDetails)
        self.contract_received = True

    def contractDetailsEnd(self, reqId):
        print("Contract Details End for reqId:", reqId)
        self.contract_received = True

    def error(self, reqId, errorCode, errorString):
        print("Error: ", reqId, " ", errorCode, " ", errorString)

    def historicalData(self, reqId: int, bar: BarData):
        print(f"HistoricalData. ReqId: {reqId}, BarData: {bar}")

    def accountSummary(self, reqId: int, account: str, tag: str, value: str, currency: str):
        super().accountSummary(reqId, account, tag, value, currency)
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"\t{timestamp} {reqId} {account} {tag}: {value} {currency}")

    def accountSummaryEnd(self, reqId: int):
        super().accountSummaryEnd(reqId)
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"\t{timestamp} AccountSummaryEnd. {reqId} -----------------------\n\n")

    def position(self, account: str, contract: Contract, position: Decimal, avgCost: float):
        super().position(account, contract, position, avgCost)
        print(f"\tPosition. {account} {contract.symbol} {contract.secType} {contract.currency} {position}, Avg cost: {avgCost}")

    def positionEnd(self):
        super().positionEnd()
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"\t{timestamp} PositionEnd. ---------------------------------\n\n")

if __name__ == "__main__":
    app = MyTWSApp()
    app.connect("127.0.0.1", 7496, 56)

    # Wait for connection
    time.sleep(1)
    def websocket_connection():
        app.run()

    # Start the websocket connection in a separate thread
    websocket_connection_thread = threading.Thread(target=websocket_connection, daemon=True)
    websocket_connection_thread.start()
    time.sleep(1)

    app.reqAccountSummary(9004, "All", "$LEDGER,CashBalance,StockMarketValue,UnrealizedPnL")
    time.sleep(5)

    app.reqPositions()

    try:
        time.sleep(600)
    except KeyboardInterrupt:
        print("Interrupted")
    
    print(f"Sleep ended at {datetime.now().strftime('%H:%M:%S')}")
    app.cancelAccountSummary(9004)
    app.disconnect()
    print("Disconnected")


