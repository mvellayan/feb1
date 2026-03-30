import pandas as pd
import os
from pathlib import Path
from tqdm import tqdm

SCHEMA = """
Index,Column Name,Description
1,date,Quote datetime -- unique sorted ascending
2,vix,CBOE Implied Volatility Index at bar time
3,open,First traded price of the bar
4,high,Highest traded price of the bar
5,low,Lowest traded price of the bar
6,close,Last traded price of the bar
7,avg_bid,Time-averaged bid price over the bar (renamed from open in BID_ASK)
8,avg_ask,Time-averaged ask price over the bar (renamed from close in BID_ASK)
9,max_ask,Maximum ask price observed in the bar (renamed from high in BID_ASK)
10,min_bid,Minimum bid price observed in the bar (renamed from low in BID_ASK)
11,average,Volume-weighted average price (WAP) from IB
12,barCount,Number of individual trades in the bar
13,volume,Total share volume in the bar
14,symbol,Stock symbol (AAPL)
15,localSymbol,Local symbol as reported by IB
16,conId,Interactive Brokers contract ID
"""

# quote_file_list function takes two argument directory and subdirectories and file pattern.
# it returns a list of files matching the pattern in the directory
def quote_file_list(directory, file_pattern):
    import glob
    file_list = glob.glob(file_pattern, root_dir=directory, recursive=True)
    return file_list


# read_files(file_list) and return a single data frame
def read_files(directory, file_list):
    return pd.concat([pd.read_csv(os.path.join(directory, f)) for f in file_list], ignore_index=True)


def main():
    base_directory = "/Users/muthu/Development/OptionList7/data/quotes"

    with tqdm(total=4, desc="Processing", unit="step") as pbar:

        # Step 1: Read trades
        pbar.set_description("Reading trades")
        trades = quote_file_list(base_directory, "**/sq-TRADES-AAPL.csv")
        trades_pd = read_files(base_directory, trades)
        pbar.update(1)

        # Step 2: Read and merge BID_ASK + VIX
        pbar.set_description("Reading bid/ask and VIX")
        bid_ask = quote_file_list(base_directory, "**/sq-BID_ASK-AAPL.csv")
        bid_ask_pd = read_files(base_directory, bid_ask)
        bid_ask_pd.rename(columns={'open': 'avg_bid', 'high': 'max_ask', 'low': 'min_bid', 'close': 'avg_ask'}, inplace=True)
        vix = quote_file_list(base_directory, "**/sq-TRADES-VIX.csv")
        vix_pd = read_files(base_directory, vix)
        vix_pd.rename(columns={'high': 'vix'}, inplace=True)
        pbar.update(1)

        # Step 3: Merge all datasets
        pbar.set_description("Merging datasets")
        trades_pd = pd.merge(trades_pd, bid_ask_pd[['date', 'avg_bid', 'max_ask', 'min_bid', 'avg_ask']], on='date', how='outer')
        trades_pd[['open', 'close', 'volume', 'barCount', 'conId']] = trades_pd[['open', 'close', 'volume', 'barCount', 'conId']].astype(int)
        trades_pd = pd.merge(trades_pd, vix_pd[['date', 'vix']], on='date', how='left')
        trades_pd = trades_pd[['date', 'vix',
                               'open', 'high', 'low', 'close',
                               'avg_bid', 'avg_ask', 'max_ask', 'min_bid',
                               'average',
                               'barCount', 'volume',
                               'symbol', 'localSymbol', 'conId']]
        trades_pd.sort_values(by=['date'], inplace=True)
        pbar.update(1)

        # Step 4: Write output
        pbar.set_description("Writing output")
        trades_pd.to_csv("../data/stock/sq_AAPL.csv", float_format="%.3f", index=False)
        schema_path = Path('../docs/schema/stock_projection.csv')
        schema_path.parent.mkdir(parents=True, exist_ok=True)
        schema_path.write_text(SCHEMA.strip())
        pbar.update(1)

    pd.set_option('display.max_colwidth', 20000)
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', None)
    print(trades_pd.head())
    print(trades_pd.describe())


if __name__ == "__main__":
    main()
