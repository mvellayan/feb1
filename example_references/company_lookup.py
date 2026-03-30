"""
company_lookup.py

Reads input_company.csv (columns: industry, symbol), looks up each symbol
in Interactive Brokers via reqContractDetails, and writes companies.csv with
columns: Industry, Symbol, Description, ConId, SecType, Exchange, Currency, LocalSymbol.

Requires TWS or IB Gateway to be running and accepting API connections.
"""

import csv
import threading
import time

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract


HOST      = '127.0.0.1'
PORT      = 7496   # 7497 = TWS paper trading, 7496 = TWS live, 4002 = Gateway paper
CLIENT_ID = 10

TIMEOUT   = 10     # seconds to wait per symbol before giving up

OUTPUT_COLUMNS = ['Industry', 'Symbol', 'Description', 'ConId',
                  'SecType', 'Exchange', 'Currency', 'LocalSymbol']

# Preferred primary exchanges for picking the best result when IB returns multiples
PREFERRED_EXCHANGES = {'NASDAQ', 'NYSE', 'ARCA', 'BATS', 'AMEX'}


class ContractLookupApp(EWrapper, EClient):

    def __init__(self):
        EClient.__init__(self, self)
        self._results     = {}   # reqId -> [row_dict, ...]
        self._done_events = {}   # reqId -> threading.Event
        self._errors      = {}   # reqId -> (code, msg)

    # ------------------------------------------------------------------
    # EWrapper callbacks
    # ------------------------------------------------------------------

    def contractDetails(self, reqId, contractDetails):
        cd = contractDetails
        row = {
            'Industry':    cd.industry,
            'Symbol':      cd.contract.symbol,
            'Description': cd.longName,
            'ConId':       cd.contract.conId,
            'SecType':     cd.contract.secType,
            'Exchange':    cd.contract.primaryExchange,
            'Currency':    cd.contract.currency,
            'LocalSymbol': cd.contract.localSymbol,
        }
        self._results.setdefault(reqId, []).append(row)

    def contractDetailsEnd(self, reqId):
        if reqId in self._done_events:
            self._done_events[reqId].set()

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=''):
        # Suppress informational messages (codes < 2000 are warnings/info)
        if reqId == -1:
            return
        if reqId in self._done_events:
            self._errors[reqId] = (errorCode, errorString)
            self._done_events[reqId].set()
        else:
            print(f"  [IB error] reqId={reqId} code={errorCode}: {errorString}")

    # ------------------------------------------------------------------
    # Lookup logic
    # ------------------------------------------------------------------

    def lookup(self, req_id, symbol, industry):
        """
        Send reqContractDetails for symbol, wait for response, return best row dict.
        Falls back to a stub row on error or timeout.
        """
        event = threading.Event()
        self._done_events[req_id] = event
        self._results[req_id] = []

        contract = Contract()
        contract.symbol   = symbol
        contract.secType  = 'STK'
        contract.currency = 'USD'
        contract.exchange = 'SMART'

        self.reqContractDetails(req_id, contract)
        event.wait(timeout=TIMEOUT)

        # --- error from IB ---
        if req_id in self._errors:
            code, msg = self._errors[req_id]
            print(f"  [!] {symbol}: error {code} — {msg}")
            return _stub_row(industry, symbol, f'ERROR {code}')

        rows = self._results.get(req_id, [])

        # --- timeout ---
        if not rows:
            print(f"  [!] {symbol}: no response (timeout)")
            return _stub_row(industry, symbol, 'TIMEOUT')

        # --- pick best result ---
        # Prefer a known primary exchange; otherwise take the first returned.
        best = rows[0]
        for row in rows:
            if row['Exchange'] in PREFERRED_EXCHANGES:
                best = row
                break

        # Use CSV industry if IB returns an empty one
        if not best['Industry']:
            best['Industry'] = industry

        return best


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _stub_row(industry, symbol, description):
    return {
        'Industry': industry, 'Symbol': symbol, 'Description': description,
        'ConId': '', 'SecType': '', 'Exchange': '', 'Currency': '', 'LocalSymbol': '',
    }


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main():
    # Read input
    with open('data/input_company.csv', newline='') as f:
        symbols = [(row['industry'], row['symbol']) for row in csv.DictReader(f)]

    print(f"Loaded {len(symbols)} symbols from input_company.csv")

    # Connect to IB
    app = ContractLookupApp()
    app.connect(HOST, PORT, CLIENT_ID)

    thread = threading.Thread(target=app.run, daemon=True)
    thread.start()
    time.sleep(2)   # allow handshake to complete

    rows_out = []
    for req_id, (industry, symbol) in enumerate(symbols, start=1):
        print(f"Requesting [{req_id}/{len(symbols)}] {symbol} ...", end=' ', flush=True)
        row = app.lookup(req_id, symbol, industry)
        print(row['Description'] or row['Symbol'])
        rows_out.append(row)
        time.sleep(3)   # stay within IB pacing limits

    app.disconnect()

    # Write output
    with open('data/companies.csv', 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows_out)

    print(f"\nDone — {len(rows_out)} rows written to companies.csv")


if __name__ == '__main__':
    main()
