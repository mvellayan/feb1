import pandas as pd
import numpy as np

def MACD(df,a=12,b=26,c=9):
    """function to calculate MACD
       typical values a(fast moving average) = 12;
                      b(slow moving average) =26;
                      c(signal line ma window) =9"""
    df["MACD_fast"]=df["average"].ewm(span=a,min_periods=a).mean()
    df["MACD_slow"]=df["average"].ewm(span=b,min_periods=b).mean()
    df["MACD_diff"]=df["MACD_fast"]-df["MACD_slow"]
    df["MACD_signal"]=df["MACD_diff"].ewm(span=c,min_periods=c).mean()
    df.drop(columns=["MACD_diff"], inplace=True)
    # df.dropna(inplace=True)
    return df

def bollBnd(df,n=20):
    "function to calculate Bollinger Band"
    #df["MA"] = df['close'].rolling(n).mean()
    df["BB_close"] = df['close'].ewm(span=n,min_periods=n).mean()
    df["BB_up"] = df["BB_close"] + 2*df['close'].rolling(n).std(ddof=0) #ddof=0 is required since we want to take the standard deviation of the population and not sample
    df["BB_dn"] = df["BB_close"] - 2*df['close'].rolling(n).std(ddof=0) #ddof=0 is required since we want to take the standard deviation of the population and not sample
    df["BB_width"] = df["BB_up"] - df["BB_dn"]
    df.drop(columns=["BB_close"], inplace=True)
    return df


def atr(df,n=20):
    "function to calculate True Range and Average True Range"
    df['ATR_high-low_']=abs(df['high']-df['low'])
    df['ATR_high-pd_close_']=abs(df['high']-df['close'].shift(1))
    df['ATR_low-pd_close_']=abs(df['low']-df['close'].shift(1))
    df['ATR_range']=df[['ATR_high-low_','ATR_high-pd_close_','ATR_low-pd_close_']].max(axis=1,skipna=False)
    #df['ATR'] = df['TR'].rolling(n).mean()
    df['ATR'] = df['ATR_range'].ewm(com=n,min_periods=n).mean()
    df.drop(columns=["ATR_high-low_", "ATR_high-pd_close_", "ATR_low-pd_close_", "ATR_range"], inplace=True)
    return df


def rsi(df,n=20):
    "function to calculate RSI"
    df['delta']=df['close'] - df['close'].shift(1)
    df['gain']=np.where(df['delta']>=0,df['delta'],0)
    df['loss']=np.where(df['delta']<0,abs(df['delta']),0)
    avg_gain = []
    avg_loss = []
    gain = df['gain'].tolist()
    loss = df['loss'].tolist()
    for i in range(len(df)):
        if i < n:
            avg_gain.append(np.nan)
            avg_loss.append(np.nan)
        elif i == n:
            avg_gain.append(df['gain'].rolling(n).mean()[n])
            avg_loss.append(df['loss'].rolling(n).mean()[n])
        elif i > n:
            avg_gain.append(((n-1)*avg_gain[i-1] + gain[i])/n)
            avg_loss.append(((n-1)*avg_loss[i-1] + loss[i])/n)
    df['avg_gain']=np.array(avg_gain)
    df['avg_loss']=np.array(avg_loss)
    df['RS'] = df['avg_gain']/df['avg_loss']
    df['RSI'] = 100 - (100/(1+df['RS']))
    df.drop(columns=["delta", "gain", "loss", "avg_gain", "avg_loss", 'RS'], inplace=True)
    return df


def adx(df2, n=20):
    "function to calculate ADX"
    df2['H-L'] = abs(df2['high'] - df2['low'])
    df2['H-PC'] = abs(df2['high'] - df2['close'].shift(1))
    df2['L-PC'] = abs(df2['low'] - df2['close'].shift(1))
    df2['TR'] = df2[['H-L', 'H-PC', 'L-PC']].max(axis=1, skipna=False)
    df2['+DM'] = np.where((df2['high'] - df2['high'].shift(1)) > (df2['low'].shift(1) - df2['low']),
                          df2['high'] - df2['high'].shift(1), 0)
    df2['+DM'] = np.where(df2['+DM'] < 0, 0, df2['+DM'])
    df2['-DM'] = np.where((df2['low'].shift(1) - df2['low']) > (df2['high'] - df2['high'].shift(1)),
                          df2['low'].shift(1) - df2['low'], 0)
    df2['-DM'] = np.where(df2['-DM'] < 0, 0, df2['-DM'])

    df2["+DMMA"] = df2['+DM'].ewm(span=n, min_periods=n).mean()
    df2["-DMMA"] = df2['-DM'].ewm(span=n, min_periods=n).mean()
    df2["TRMA"] = df2['TR'].ewm(span=n, min_periods=n).mean()

    df2["+DI"] = 100 * (df2["+DMMA"] / df2["TRMA"])
    df2["-DI"] = 100 * (df2["-DMMA"] / df2["TRMA"])
    df2["DX"] = 100 * (abs(df2["+DI"] - df2["-DI"]) / (df2["+DI"] + df2["-DI"]))

    df2["ADX"] = df2["DX"].ewm(span=n, min_periods=n).mean()

    df2.drop(columns=['H-L', 'H-PC', 'L-PC', 'TR', '+DM', '-DM', '+DMMA', '-DMMA', 'TRMA', '+DI', '-DI', 'DX'], inplace=True)

    return df2


def stochOscltr(df,a=20,b=3):
    """function to calculate Stochastics
       a = lookback period
       b = moving average window for %D"""
    df['C-L'] = df['close'] - df['low'].rolling(a).min()
    df['H-L'] = df['high'].rolling(a).max() - df['low'].rolling(a).min()
    df['StochasticOsceltr'] = df['C-L']/df['H-L']*100
    df['ewmStochasticOsceltr'] = df['StochasticOsceltr'].ewm(span=b,min_periods=b).mean()
    df.drop(columns=['C-L', 'H-L'], inplace=True)
    return df


def stochOscltr(df,a=20,b=3):
    """function to calculate Stochastics
       a = lookback period
       b = moving average window for %D"""
    df = DF.copy()
    df['C-L'] = df['Close'] - df['Low'].rolling(a).min()
    df['H-L'] = df['High'].rolling(a).max() - df['Low'].rolling(a).min()
    df['%K'] = df['C-L']/df['H-L']*100
    df['%D'] = df['%K'].ewm(span=b,min_periods=b).mean()
    return df[['%K','%D']]

if __name__ == '__main__':
    dfQuotes = pd.read_csv('../data/projection.csv')
    dfQuotes_columns = dfQuotes.columns.tolist()

    MACD(dfQuotes)
    bollBnd(dfQuotes)
    atr(dfQuotes)
    rsi(dfQuotes)
    adx(dfQuotes)

    # drop columns: open,high,low,close,avg_bid,avg_ask,max_ask,min_bid,
    dfQuotes.drop(columns=['open', 'high', 'low', 'close', 'avg_bid', 'avg_ask', 'max_ask', 'min_bid'], inplace=True)
    dfQuotes.to_csv('../data/moving_avgs.csv', float_format='%.3f', index=False)
    pd.set_option('display.max_colwidth', 20000)
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', None)

    print(dfQuotes.head(n=100))
    print(dfQuotes.describe())