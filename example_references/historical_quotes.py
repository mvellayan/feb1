"""
historical_quotes.py

Reads companies.csv, fetches 15-minute TRADES bars via IB reqHistoricalData,
and writes hq_{symbol}.csv for each company.

Output columns: date, open, high, low, close, volume, average, barCount,
                symbol, localSymbol, conId

Requires TWS or IB Gateway to be running and accepting API connections.
"""

import csv
import os
import threading
import time
from datetime import datetime

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract


HOST         = '127.0.0.1'
PORT         = 7496   # 7497 = TWS paper trading, 7496 = TWS live, 4002 = Gateway paper
CLIENT_ID    = 11     # different from company_lookup (10) to avoid conflict

DURATION     = '1 Y'     # lookback window (IB allows up to 1 year for 15-min bars)
BAR_SIZE     = '15 mins'
WHAT_TO_SHOW = 'TRADES'
USE_RTH      = 1         # 1 = regular trading hours only
TIMEOUT      = 600       # 10 minutes max wait per symbol

OUTPUT_COLUMNS = ['date', 'open', 'high', 'low', 'close', 'volume',
                  'average', 'barCount', 'symbol', 'localSymbol', 'conId']


class HistoricalQuotesApp(EWrapper, EClient):

    def __init__(self):
        EClient.__init__(self, self)
        self._bars        = {}   # reqId -> [row_dict, ...]
        self._done_events = {}   # reqId -> threading.Event
        self._errors      = {}   # reqId -> (code, msg)

    # ------------------------------------------------------------------
    # EWrapper callbacks
    # ------------------------------------------------------------------

    def historicalData(self, reqId, bar):
        # IB returns intraday dates as "YYYYMMDD HH:MM:SS" when formatDate=1
        dt = datetime.strptime(bar.date, '%Y%m%d  %H:%M:%S')
        row = {
            'date':        dt.strftime('%m/%d/%y %H:%M'),
            'open':        bar.open,
            'high':        bar.high,
            'low':         bar.low,
            'close':       bar.close,
            'volume':      bar.volume,
            'average':     bar.average,
            'barCount':    bar.barCount,
            'symbol':      None,   # tagged after fetch completes
            'localSymbol': None,
            'conId':       None,
        }
        self._bars.setdefault(reqId, []).append(row)

    def historicalDataEnd(self, reqId, start, end):
        if reqId in self._done_events:
            self._done_events[reqId].set()

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=''):
        if reqId == -1:
            return   # connection-level info, not request errors
        # IB codes >= 2000 are warnings — data delivery continues, do not abort
        if errorCode >= 2000:
            print(f"  [warn {errorCode}] {errorString}")
            return
        # True errors: abort the pending request
        if reqId in self._done_events:
            self._errors[reqId] = (errorCode, errorString)
            self._done_events[reqId].set()
        else:
            print(f"  [IB] reqId={reqId} code={errorCode}: {errorString}")

    # ------------------------------------------------------------------
    # Fetch logic
    # ------------------------------------------------------------------

    def fetch(self, req_id, company):
        """
        Request 15-min TRADES bars for one company.
        Blocks until historicalDataEnd fires or TIMEOUT is reached.
        Returns a list of row dicts (may be empty on error/no data).
        """
        symbol    = company['Symbol']
        local_sym = company['LocalSymbol']
        con_id    = company['ConId']
        exchange  = company['Exchange'] or 'SMART'

        event = threading.Event()
        self._done_events[req_id] = event
        self._bars[req_id] = []

        contract = Contract()
        contract.conId    = int(con_id)
        contract.exchange = exchange

        self.reqHistoricalData(
            reqId         = req_id,
            contract      = contract,
            endDateTime   = '20250319 16:00:00 US/Eastern',
            durationStr   = DURATION,
            barSizeSetting= BAR_SIZE,
            whatToShow    = WHAT_TO_SHOW,
            useRTH        = USE_RTH,
            formatDate    = 1,
            keepUpToDate  = False,
            chartOptions  = [],
        )

        completed = event.wait(timeout=TIMEOUT)

        if not completed:
            print(f"  [!] {symbol}: timed out after {TIMEOUT}s")
            return []

        if req_id in self._errors:
            code, msg = self._errors[req_id]
            print(f"  [!] {symbol}: error {code} — {msg}")
            return []

        bars = self._bars.get(req_id, [])

        # Tag each bar with its company identifiers
        for bar in bars:
            bar['symbol']      = symbol
            bar['localSymbol'] = local_sym
            bar['conId']       = con_id

        return bars


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main():
    # Read companies — skip any rows where lookup previously failed (no ConId)
    with open('data/companies.csv', newline='') as f:
        companies = [row for row in csv.DictReader(f) if row.get('ConId')]

    print(f"Loaded {len(companies)} companies from companies.csv")

    app = HistoricalQuotesApp()
    app.connect(HOST, PORT, CLIENT_ID)

    thread = threading.Thread(target=app.run, daemon=True)
    thread.start()
    time.sleep(2)   # allow handshake to complete

    for idx, company in enumerate(companies, start=1):
        symbol = company['Symbol']
        print(f"[{idx}/{len(companies)}] {symbol} ...", end=' ', flush=True)

        bars = app.fetch(idx, company)

        if bars:
            filename = f'data/hq_{symbol}_2.csv'
            with open(filename, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
                writer.writeheader()
                writer.writerows(bars)
            print(f"{len(bars)} bars → {filename}")
        else:
            print("no data, skipped")

        time.sleep(3)   # IB pacing: stay under 60 requests per 10 minutes

    app.disconnect()
    print("\nDone.")


if __name__ == '__main__':
    main()
