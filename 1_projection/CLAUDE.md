# 1_projection/

**Purpose:** Data ingestion pipeline. Reads raw quote files from IBKR exports and writes
consolidated CSVs into `data/`.

## Files

| File | Input | Output | Purpose |
|------|-------|--------|---------|
| `1_process_stock_quotes.py` | Raw AAPL 1-min quote files | `data/stock/sq_AAPL.csv` | Concatenates stock bar files into one CSV |
| `2_process_option_quotes.py` | Raw option quote files | `data/options/{YY}/oq_*.csv` | Processes and writes per-contract option bar CSVs |
| `3_options_index.py` | `data/options/{YY}/oq_*.csv` | `data/option_index.csv` | Builds the contract metadata index (conId, localSymbol, expiration_date, call_put, strike_price) |

## Execution Order
```shell
python 1_process_stock_quotes.py
python 2_process_option_quotes.py
python 3_options_index.py
```

Run once when new raw data is added. Outputs are stable; do not re-run unless source data changes.
