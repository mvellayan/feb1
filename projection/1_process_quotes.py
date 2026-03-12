import pandas as pd
import os

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
    # Read in all trade data
    trades = quote_file_list(base_directory, "**/sq-TRADES-AAPL.csv")
    trades_pd = read_files(base_directory, trades)


    # Read in and merge all BID_ASK data
    bid_ask = quote_file_list(base_directory, "**/sq-BID_ASK-AAPL.csv")
    bid_ask_pd = read_files(base_directory, bid_ask)
    # rename columns ['open', 'high', 'low', 'close'] tp ['avg_bid','max_ask','min_bid','avg_ask']
    bid_ask_pd.rename(columns={'open': 'avg_bid', 'high': 'max_ask', 'low': 'min_bid', 'close': 'avg_ask'}, inplace=True)

    trades_pd = pd.merge(trades_pd, bid_ask_pd[['date', 'avg_bid', 'max_ask', 'min_bid', 'avg_ask']], on='date', how='outer')
    trades_pd[['open', 'close', 'volume', 'barCount', 'conId']] = trades_pd[['open', 'close', 'volume', 'barCount', 'conId']].astype(int)

    # Read in and merge all VIX data
    vix = quote_file_list(base_directory, "**/sq-TRADES-VIX.csv")
    vix_pd = read_files(base_directory, vix)
    vix_pd.rename(columns={'high': 'vix'}, inplace=True)
    trades_pd = pd.merge(trades_pd, vix_pd[['date', 'vix']], on='date', how='left')

    trades_pd = trades_pd[['date', 'vix',
                           'open', 'high', 'low', 'close',
                           'avg_bid', 'avg_ask', 'max_ask', 'min_bid',
                           'average',
                           'barCount', 'volume',
                           'symbol', 'localSymbol', 'conId']]
    print(trades_pd.columns)

    # Sequence column names
    # trades_pd.insert(2, 'vix', trades_pd.pop('vix'))
    trades_pd.to_csv("../data/projection.csv", float_format="%.3f", index=False)

    pd.set_option('display.max_colwidth', 20000)
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', None)
    print(trades_pd.head())
    print(trades_pd.head)
    print(trades_pd.describe())


if __name__ == "__main__":
    main()
