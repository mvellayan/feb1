import pandas as pd
import glob
import os

def parse_local_symbol(local_symbol: str) -> tuple:
    # e.g. "AAPL  221118C00132000"
    expiration_date = local_symbol[6:12]
    call_put        = local_symbol[12]
    strike_price    = int(local_symbol[14:]) / 1000
    return expiration_date, call_put, strike_price


def main():
    base_directory = os.path.join(os.path.dirname(__file__), '../data/options')
    files = glob.glob(os.path.join(base_directory, '**/*.csv'), recursive=True)
    print(f"Found {len(files):,} files")

    rows = []
    for file in files:
        first_row = pd.read_csv(file, nrows=1)
        local_symbol = first_row['localSymbol'].iloc[0]
        con_id       = first_row['conId'].iloc[0]
        expiration_date, call_put, strike_price = parse_local_symbol(local_symbol)
        rows.append({
            'conId':           con_id,
            'localSymbol':     local_symbol,
            'expiration_date': expiration_date,
            'call_put':        call_put,
            'strike_price':    strike_price,
        })

    index_df = pd.DataFrame(rows)
    index_df.sort_values(by=['expiration_date', 'call_put', 'strike_price'], inplace=True)

    output_path = os.path.join(os.path.dirname(__file__), '../data/option_index.csv')
    index_df.to_csv(output_path, index=False)
    print(f"Wrote {len(index_df):,} rows -> {output_path}")


if __name__ == '__main__':
    main()
