import pandas as pd
import os
from tqdm import tqdm

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

    years = ["22", "23", "24", "25", "26"]
    for year in tqdm(years, desc="Years", unit="year"):
        with tqdm(total=4, desc=f"  {year}", unit="step", leave=False) as pbar:

            # Step 1: Read trades
            pbar.set_description(f"  {year} Reading trades")
            trades = quote_file_list(base_directory, f"**/sq-TRADES-AAPL{year}*.csv")
            trades_pd = read_files(base_directory, trades)
            pbar.update(1)

            # Step 2: Read bid/ask
            pbar.set_description(f"  {year} Reading bid/ask")
            bid_ask = quote_file_list(base_directory, f"**/sq-BID_ASK-AAPL{year}*.csv")
            bid_ask_pd = read_files(base_directory, bid_ask)
            bid_ask_pd.rename(columns={'open': 'avg_bid', 'high': 'max_ask', 'low': 'min_bid', 'close': 'avg_ask'}, inplace=True)
            pbar.update(1)

            # Step 3: Merge datasets
            pbar.set_description(f"  {year} Merging datasets")
            trades_pd = pd.merge(trades_pd, bid_ask_pd[['date', 'conId', 'avg_bid', 'max_ask', 'min_bid', 'avg_ask']], on=['date', 'conId'], how='inner')
            trades_pd.dropna(subset=['open', 'close', 'volume', 'barCount', 'conId'], inplace=True)
            trades_pd[['open', 'close', 'volume', 'barCount', 'conId']] = trades_pd[['open', 'close', 'volume', 'barCount', 'conId']].astype(int)
            trades_pd = trades_pd[['date',
                                   'open', 'high', 'low', 'close',
                                   'avg_bid', 'avg_ask', 'max_ask', 'min_bid',
                                   'average',
                                   'barCount', 'volume',
                                   'symbol', 'localSymbol', 'conId']]
            trades_pd.sort_values(by=['conId', 'date'], inplace=True)
            pbar.update(1)

            # Step 4: Write output files
            pbar.set_description(f"  {year} Writing output")
            os.makedirs(f"../data/options/{year}", exist_ok=True)
            file_ctr = 0
            for conId, group in trades_pd.groupby('conId'):
                local_symbol = group['localSymbol'].iloc[0]
                symbol_part = local_symbol[local_symbol.index('2'):]
                group.to_csv(f"../data/options/{year}/oq_{symbol_part}.csv", float_format="%.3f", index=False)
                file_ctr += 1
            pbar.update(1)

        tqdm.write(f"Year {year}: trades={len(trades):,}, bid_ask={len(bid_ask):,}, files written={file_ctr:,}")


if __name__ == "__main__":
    main()
