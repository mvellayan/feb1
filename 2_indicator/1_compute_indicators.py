"""
compute_indicators.py

Computes all technical indicator intermediate columns for intraday AAPL backtesting.
Reads sq-AAPL.csv, adds ~70 computed columns with consistent prefixes, writes sq-AAPL-extended.csv.

Column prefix conventions:
  fnd_   Foundation / price primitives
  ema_   Exponential Moving Average (crossover)
  mcd_   MACD
  adx_   ADX / DMI
  rsi_   RSI
  sto_   Stochastic Oscillator
  cci_   Commodity Channel Index
  atr_   Average True Range
  bbd_   Bollinger Bands
  chp_   Choppiness Index
  vwp_   VWAP (intraday, session-reset)
  obv_   On Balance Volume
  mfi_   Money Flow Index
"""

import numpy as np
import pandas as pd
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wilder_smooth(series: pd.Series, period: int) -> pd.Series:
    """
    Wilder's smoothed moving average (RMA).
    Equivalent to EMA with alpha = 1 / period.
    Used natively by RSI, ATR, and ADX.
    """
    return series.ewm(alpha=1.0 / period, adjust=False).mean()


def _rolling_apply_mean_dev(series: pd.Series, window: int) -> pd.Series:
    """Mean absolute deviation over a rolling window — required for CCI."""
    return series.rolling(window).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)


# ---------------------------------------------------------------------------
# Main computation function
# ---------------------------------------------------------------------------

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Accepts the raw sq-AAPL dataframe and returns an extended copy with all
    indicator columns appended.  Input dataframe is not mutated.

    Parameters
    ----------
    df : pd.DataFrame
        Must contain columns: date, open, high, low, close, volume
        Optional but used if present: vix, average (WAP), barCount

    Returns
    -------
    pd.DataFrame
        Original columns preserved; ~70 indicator columns appended.
    """
    df = df.copy()

    # Ensure date is datetime and dataframe is sorted
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)

    # -----------------------------------------------------------------------
    # FOUNDATION columns  (prefix: fnd_)
    # Shared primitives consumed by multiple indicators — compute once.
    # -----------------------------------------------------------------------

    df['fnd_typical_price']  = (df['high'] + df['low'] + df['close']) / 3.0
    df['fnd_hl2']            = (df['high'] + df['low']) / 2.0          # bar midpoint
    df['fnd_price_range']    = df['high'] - df['low']
    df['fnd_prev_close']     = df['close'].shift(1)
    df['fnd_close_chg']      = df['close'] - df['fnd_prev_close']
    df['fnd_abs_chg']        = df['fnd_close_chg'].abs()

    # True Range — shared by ATR, ADX, Choppiness Index
    tr1 = df['fnd_price_range']
    tr2 = (df['high'] - df['fnd_prev_close']).abs()
    tr3 = (df['low']  - df['fnd_prev_close']).abs()
    df['fnd_true_range'] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # Rolling 14-bar high/low — shared by Stochastic and Choppiness Index
    df['fnd_high_14'] = df['high'].rolling(14).max()
    df['fnd_low_14']  = df['low'].rolling(14).min()

    # Trade date (date-only) — used to reset VWAP each session
    df['fnd_trade_date'] = df['date'].dt.date

    # -----------------------------------------------------------------------
    # EMA CROSSOVER  (prefix: ema_)
    # -----------------------------------------------------------------------

    df['ema_9']           = df['close'].ewm(span=9,  adjust=False).mean()
    df['ema_21']          = df['close'].ewm(span=21, adjust=False).mean()
    # Also compute 12 and 26 here — reused by MACD
    df['ema_12']          = df['close'].ewm(span=12, adjust=False).mean()
    df['ema_26']          = df['close'].ewm(span=26, adjust=False).mean()

    ema_bull              = (df['ema_9'] > df['ema_21']).astype(np.int8)
    df['ema_crossover']   = ema_bull                                    # 1 = bullish state
    df['ema_cross_event'] = ema_bull.diff().fillna(0).astype(np.int8)   # +1 bull, -1 bear

    # -----------------------------------------------------------------------
    # MACD  (prefix: mcd_)
    # -----------------------------------------------------------------------

    df['mcd_line']         = df['ema_12'] - df['ema_26']
    df['mcd_signal']       = df['mcd_line'].ewm(span=9, adjust=False).mean()
    df['mcd_histogram']    = df['mcd_line'] - df['mcd_signal']
    df['mcd_hist_prev']    = df['mcd_histogram'].shift(1)

    # Growing histogram (momentum accelerating)
    df['mcd_hist_growing'] = (
        df['mcd_histogram'] > df['mcd_hist_prev']
    ).astype(np.int8)

    # Zero-line crossover events
    mcd_pos                = (df['mcd_histogram'] > 0).astype(np.int8)
    df['mcd_cross_event']  = mcd_pos.diff().fillna(0).astype(np.int8)  # +1 bull, -1 bear

    # Signal-line crossover events
    mcd_above_sig          = (df['mcd_line'] > df['mcd_signal']).astype(np.int8)
    df['mcd_sig_event']    = mcd_above_sig.diff().fillna(0).astype(np.int8)

    # -----------------------------------------------------------------------
    # ADX / DMI  (prefix: adx_)
    # -----------------------------------------------------------------------

    up_move   = df['high'] - df['high'].shift(1)
    down_move = df['low'].shift(1) - df['low']

    plus_dm  = np.where((up_move > down_move)   & (up_move > 0),   up_move,   0.0)
    minus_dm = np.where((down_move > up_move)   & (down_move > 0), down_move, 0.0)

    plus_dm_s  = _wilder_smooth(pd.Series(plus_dm,  index=df.index), 14)
    minus_dm_s = _wilder_smooth(pd.Series(minus_dm, index=df.index), 14)

    # atr_14 computed here, referenced again in ATR section
    df['atr_14']      = _wilder_smooth(df['fnd_true_range'], 14)

    df['adx_plus_di']  = 100.0 * (plus_dm_s  / df['atr_14'])
    df['adx_minus_di'] = 100.0 * (minus_dm_s / df['atr_14'])

    dx_num             = (df['adx_plus_di'] - df['adx_minus_di']).abs()
    dx_den             = df['adx_plus_di']  + df['adx_minus_di']
    dx                 = 100.0 * dx_num / dx_den.replace(0, np.nan)

    df['adx_14']       = _wilder_smooth(dx.fillna(0), 14)
    df['adx_rising']   = (df['adx_14'] > df['adx_14'].shift(1)).astype(np.int8)

    # Composite gate: strong trend + bullish direction
    df['adx_trend_gate'] = (
        (df['adx_14'] > 25) & (df['adx_plus_di'] > df['adx_minus_di'])
    ).astype(np.int8)

    # -----------------------------------------------------------------------
    # RSI  (prefix: rsi_)
    # -----------------------------------------------------------------------

    gain = df['fnd_close_chg'].clip(lower=0)
    loss = (-df['fnd_close_chg']).clip(lower=0)

    df['rsi_avg_gain'] = _wilder_smooth(gain, 14)
    df['rsi_avg_loss'] = _wilder_smooth(loss, 14)
    df['rsi_rs']       = df['rsi_avg_gain'] / df['rsi_avg_loss'].replace(0, np.nan)
    df['rsi_14']       = 100.0 - (100.0 / (1.0 + df['rsi_rs']))

    rsi_prev           = df['rsi_14'].shift(1)
    # Bullish 50-line cross (momentum turns positive)
    df['rsi_cross_50'] = (
        (df['rsi_14'] > 50) & (rsi_prev <= 50)
    ).astype(np.int8)
    # Oversold bounce (exit from below 30)
    df['rsi_cross_30'] = (
        (df['rsi_14'] > 30) & (rsi_prev <= 30)
    ).astype(np.int8)
    df['rsi_oversold']   = (df['rsi_14'] < 30).astype(np.int8)
    df['rsi_overbought'] = (df['rsi_14'] > 70).astype(np.int8)

    # -----------------------------------------------------------------------
    # STOCHASTIC OSCILLATOR  (prefix: sto_)
    # -----------------------------------------------------------------------

    hl_range_14         = (df['fnd_high_14'] - df['fnd_low_14']).replace(0, np.nan)
    df['sto_k']         = 100.0 * (df['close'] - df['fnd_low_14']) / hl_range_14
    df['sto_d']         = df['sto_k'].rolling(3).mean()             # signal line

    sto_k_prev          = df['sto_k'].shift(1)
    sto_d_prev          = df['sto_d'].shift(1)

    # %K crosses above %D
    df['sto_cross_up']   = (
        (df['sto_k'] > df['sto_d']) & (sto_k_prev <= sto_d_prev)
    ).astype(np.int8)
    df['sto_oversold']   = (df['sto_k'] < 20).astype(np.int8)
    df['sto_overbought'] = (df['sto_k'] > 80).astype(np.int8)

    # -----------------------------------------------------------------------
    # CCI  (prefix: cci_)
    # -----------------------------------------------------------------------

    df['cci_sma_tp']    = df['fnd_typical_price'].rolling(20).mean()
    df['cci_mean_dev']  = _rolling_apply_mean_dev(df['fnd_typical_price'], 20)
    df['cci_20']        = (
        (df['fnd_typical_price'] - df['cci_sma_tp']) /
        (0.015 * df['cci_mean_dev'].replace(0, np.nan))
    )

    cci_prev             = df['cci_20'].shift(1)
    df['cci_cross_0']    = (
        (df['cci_20'] > 0) & (cci_prev <= 0)
    ).astype(np.int8)                                               # bullish zero cross
    df['cci_cross_m100'] = (
        (df['cci_20'] > -100) & (cci_prev <= -100)
    ).astype(np.int8)                                               # oversold exit
    df['cci_oversold']   = (df['cci_20'] < -100).astype(np.int8)
    df['cci_overbought'] = (df['cci_20'] >  100).astype(np.int8)

    # -----------------------------------------------------------------------
    # ATR  (prefix: atr_)
    # atr_14 already computed above under ADX — reused here, no duplication.
    # -----------------------------------------------------------------------

    df['atr_20_avg']      = df['atr_14'].rolling(20).mean()
    df['atr_spike']       = (
        df['atr_14'] > 2.0 * df['atr_20_avg']
    ).astype(np.int8)                                               # suppress entries on spikes
    df['atr_bar_ratio']   = df['fnd_price_range'] / df['atr_14']   # bar strength vs ATR

    # Pre-computed stop and target distances (1.5× ATR baseline)
    df['atr_stop_1x']     = df['atr_14'] * 1.0
    df['atr_stop_15x']    = df['atr_14'] * 1.5
    df['atr_stop_2x']     = df['atr_14'] * 2.0
    df['atr_tgt_15rr']    = df['atr_14'] * 1.5 * 1.5               # 1.5× ATR stop × 1.5 RR
    df['atr_tgt_2rr']     = df['atr_14'] * 1.5 * 2.0               # 1.5× ATR stop × 2.0 RR

    # -----------------------------------------------------------------------
    # BOLLINGER BANDS / BB WIDTH  (prefix: bbd_)
    # -----------------------------------------------------------------------

    df['bbd_sma_20']      = df['close'].rolling(20).mean()
    df['bbd_std_20']      = df['close'].rolling(20).std()
    df['bbd_upper']       = df['bbd_sma_20'] + 2.0 * df['bbd_std_20']
    df['bbd_lower']       = df['bbd_sma_20'] - 2.0 * df['bbd_std_20']
    df['bbd_width']       = (df['bbd_upper'] - df['bbd_lower']) / df['bbd_sma_20']

    bbd_width_min_20      = df['bbd_width'].rolling(20).min()
    df['bbd_squeeze']     = (
        df['bbd_width'] <= bbd_width_min_20 * 1.1
    ).astype(np.int8)                                               # inside squeeze
    df['bbd_expanding']   = (
        df['bbd_width'] > df['bbd_width'].shift(1)
    ).astype(np.int8)                                               # width growing
    df['bbd_pct_b']       = (
        (df['close'] - df['bbd_lower']) /
        (df['bbd_upper'] - df['bbd_lower']).replace(0, np.nan)
    )
    df['bbd_above_sma']   = (df['close'] > df['bbd_sma_20']).astype(np.int8)

    # -----------------------------------------------------------------------
    # CHOPPINESS INDEX  (prefix: chp_)
    # Reuses: fnd_true_range, fnd_high_14, fnd_low_14
    # -----------------------------------------------------------------------

    atr_sum_14            = df['fnd_true_range'].rolling(14).sum()
    hl_range_14_chp       = df['fnd_high_14'] - df['fnd_low_14']

    df['chp_14']          = (
        100.0 * np.log10(atr_sum_14 / hl_range_14_chp.replace(0, np.nan)) /
        np.log10(14)
    )
    df['chp_trending']    = (df['chp_14'] < 38.2).astype(np.int8)  # clearly trending
    df['chp_ranging']     = (df['chp_14'] > 61.8).astype(np.int8)  # clearly choppy
    df['chp_regime']      = np.where(
        df['chp_14'] < 38.2, 'trend',
        np.where(df['chp_14'] > 61.8, 'range', 'neutral')
    )

    # -----------------------------------------------------------------------
    # VWAP — Intraday, session-reset  (prefix: vwp_)
    # CRITICAL: cumsum must restart at the beginning of each trading day.
    # -----------------------------------------------------------------------

    df['vwp_pv']          = df['fnd_typical_price'] * df['volume']

    cum_vol = df.groupby('fnd_trade_date')['volume'].cumsum()
    cum_pv  = df.groupby('fnd_trade_date')['vwp_pv'].cumsum()

    df['vwp_vwap']        = cum_pv / cum_vol.replace(0, np.nan)

    # Standard deviation bands around VWAP (1σ)
    df['vwp_vwap_upper']  = df['vwp_vwap'] + df['bbd_std_20']      # approximate band
    df['vwp_vwap_lower']  = df['vwp_vwap'] - df['bbd_std_20']

    df['vwp_above']       = (df['close'] > df['vwp_vwap']).astype(np.int8)
    vwp_above_prev        = df['vwp_above'].shift(1)
    df['vwp_cross_up']    = (
        (df['vwp_above'] == 1) & (vwp_above_prev == 0)
    ).astype(np.int8)                                               # price crosses above VWAP
    df['vwp_distance']    = (
        (df['close'] - df['vwp_vwap']) / df['vwp_vwap'] * 100.0
    )                                                               # % distance from VWAP

    # -----------------------------------------------------------------------
    # OBV — On Balance Volume  (prefix: obv_)
    # -----------------------------------------------------------------------

    obv_dir               = np.sign(df['fnd_close_chg'])
    df['obv_raw']         = (obv_dir * df['volume']).cumsum()
    df['obv_ema_20']      = df['obv_raw'].ewm(span=20, adjust=False).mean()
    df['obv_above_ema']   = (df['obv_raw'] > df['obv_ema_20']).astype(np.int8)
    df['obv_rising']      = (df['obv_raw'] > df['obv_raw'].shift(3)).astype(np.int8)

    # Bearish divergence: price higher but OBV lower over last 3 bars
    df['obv_div_bear']    = (
        (df['close'] > df['close'].shift(3)) &
        (df['obv_raw'] < df['obv_raw'].shift(3))
    ).astype(np.int8)

    # -----------------------------------------------------------------------
    # MFI — Money Flow Index  (prefix: mfi_)
    # -----------------------------------------------------------------------

    raw_mf                = df['fnd_typical_price'] * df['volume']
    tp_up                 = df['fnd_typical_price'] > df['fnd_typical_price'].shift(1)

    pos_flow              = raw_mf.where(tp_up,  0.0)
    neg_flow              = raw_mf.where(~tp_up, 0.0)

    df['mfi_pos_flow']    = pos_flow.rolling(14).sum()
    df['mfi_neg_flow']    = neg_flow.rolling(14).sum()
    mfi_ratio             = df['mfi_pos_flow'] / df['mfi_neg_flow'].replace(0, np.nan)
    df['mfi_14']          = 100.0 - (100.0 / (1.0 + mfi_ratio))

    mfi_prev              = df['mfi_14'].shift(1)
    df['mfi_cross_50']    = (
        (df['mfi_14'] > 50) & (mfi_prev <= 50)
    ).astype(np.int8)
    df['mfi_oversold']    = (df['mfi_14'] < 20).astype(np.int8)
    df['mfi_bounce']      = (
        (df['mfi_14'] > 30) & (mfi_prev <= 30)
    ).astype(np.int8)                                               # oversold bounce

    # -----------------------------------------------------------------------
    # SESSION TIME FLAGS  (prefix: ses_)
    # Useful for enforcing the 10 AM entry and 4 PM exit rules in backtesting.
    # -----------------------------------------------------------------------

    t = df['date'].dt.time
    import datetime
    df['ses_after_10']    = (df['date'].dt.hour >= 10).astype(np.int8)
    df['ses_before_345']  = (
        df['date'] < df['date'].dt.normalize() +
        pd.to_timedelta('15:45:00')
    ).astype(np.int8)
    df['ses_minute']      = df['date'].dt.hour * 60 + df['date'].dt.minute

    # -----------------------------------------------------------------------
    # Downcast indicator columns to float32 to reduce memory footprint
    # -----------------------------------------------------------------------

    raw_cols = {
        'date', 'vix', 'open', 'high', 'low', 'close',
        'avg_bid', 'avg_ask', 'max_ask', 'min_bid',
        'average', 'barCount', 'volume', 'symbol',
        'localSymbol', 'conId', 'fnd_trade_date', 'chp_regime'
    }

    float_cols = [
        c for c in df.columns
        if c not in raw_cols and df[c].dtype in [np.float64, np.int64, np.int8]
        and c not in ['ses_after_10', 'ses_before_345', 'ses_minute',
                      'ema_crossover', 'ema_cross_event',
                      'mcd_hist_growing', 'mcd_cross_event', 'mcd_sig_event',
                      'adx_rising', 'adx_trend_gate',
                      'rsi_cross_50', 'rsi_cross_30', 'rsi_oversold', 'rsi_overbought',
                      'sto_cross_up', 'sto_oversold', 'sto_overbought',
                      'cci_cross_0', 'cci_cross_m100', 'cci_oversold', 'cci_overbought',
                      'atr_spike', 'bbd_squeeze', 'bbd_expanding', 'bbd_above_sma',
                      'chp_trending', 'chp_ranging',
                      'vwp_above', 'vwp_cross_up',
                      'obv_above_ema', 'obv_rising', 'obv_div_bear',
                      'mfi_cross_50', 'mfi_oversold', 'mfi_bounce']
    ]
    for col in float_cols:
        try:
            df[col] = df[col].astype(np.float32)
        except (ValueError, TypeError):
            pass

    return df


# ---------------------------------------------------------------------------
# Schema documentation
# ---------------------------------------------------------------------------

SCHEMA = """
Index,Column Name,Prefix,Description
--- RAW COLUMNS (from sq-AAPL.csv) ---
1,date,,Quote datetime -- unique, sorted ascending
2,vix,,CBOE Implied Volatility Index at bar time
3,open,,First traded price of the bar
4,high,,Highest traded price of the bar
5,low,,Lowest traded price of the bar
6,close,,Last traded price of the bar
7,avg_bid,,Time-averaged bid price over the bar
8,avg_ask,,Time-averaged ask price over the bar
9,max_ask,,Maximum ask price observed in the bar
10,min_bid,,Minimum bid price observed in the bar
11,average,,Volume-weighted average price (WAP) from IB
12,barCount,,Number of individual trades in the bar
13,volume,,Total share volume in the bar
14,symbol,,Stock symbol (AAPL)
15,localSymbol,,Option symbol if applicable
16,conId,,Interactive Brokers contract ID

--- FOUNDATION columns (fnd_) ---
17,fnd_typical_price,fnd_,(high + low + close) / 3 -- shared by VWAP / CCI / MFI
18,fnd_hl2,fnd_,(high + low) / 2 -- bar midpoint
19,fnd_price_range,fnd_,high - low -- raw bar range
20,fnd_prev_close,fnd_,close.shift(1) -- prior bar close
21,fnd_close_chg,fnd_,close - prev_close -- bar price change
22,fnd_abs_chg,fnd_,abs(close_change) -- absolute price change
23,fnd_true_range,fnd_,max(H-L, |H-prevC|, |L-prevC|) -- shared by ATR/ADX/Choppiness
24,fnd_high_14,fnd_,Rolling 14-bar highest high -- shared by Stochastic/Choppiness
25,fnd_low_14,fnd_,Rolling 14-bar lowest low -- shared by Stochastic/Choppiness
26,fnd_trade_date,fnd_,Date-only portion of datetime -- used to reset VWAP each session

--- EMA CROSSOVER columns (ema_) ---
27,ema_9,ema_,9-period EMA of close
28,ema_12,ema_,12-period EMA of close (shared with MACD)
29,ema_21,ema_,21-period EMA of close
30,ema_26,ema_,26-period EMA of close (shared with MACD)
31,ema_crossover,ema_,1 if ema_9 > ema_21 (bullish state) else 0
32,ema_cross_event,ema_,+1 on bullish cross -1 on bearish cross 0 otherwise

--- MACD columns (mcd_) ---
33,mcd_line,mcd_,MACD line = ema_12 - ema_26
34,mcd_signal,mcd_,Signal line = 9-period EMA of mcd_line
35,mcd_histogram,mcd_,mcd_line - mcd_signal
36,mcd_hist_prev,mcd_,Prior bar histogram value
37,mcd_hist_growing,mcd_,1 if histogram growing bar-over-bar else 0
38,mcd_cross_event,mcd_,+1 histogram crosses above zero -1 crosses below 0 otherwise
39,mcd_sig_event,mcd_,+1 MACD line crosses above signal line -1 crosses below

--- ADX / DMI columns (adx_) ---
40,adx_plus_di,adx_,+DI directional indicator (14-period Wilder smooth)
41,adx_minus_di,adx_,-DI directional indicator (14-period Wilder smooth)
42,adx_14,adx_,Average Directional Index (14-period)
43,adx_rising,adx_,1 if ADX rising versus prior bar else 0
44,adx_trend_gate,adx_,1 if ADX > 25 AND +DI > -DI (strong bullish trend) else 0

--- ATR columns (atr_) ---
45,atr_14,atr_,14-period Wilder smoothed Average True Range (also used by ADX)
46,atr_20_avg,atr_,20-bar rolling average of atr_14
47,atr_spike,atr_,1 if atr_14 > 2x atr_20_avg (volatility spike suppressor) else 0
48,atr_bar_ratio,atr_,price_range / atr_14 -- bar strength relative to ATR
49,atr_stop_1x,atr_,1.0 x atr_14 -- tight stop distance
50,atr_stop_15x,atr_,1.5 x atr_14 -- baseline stop distance
51,atr_stop_2x,atr_,2.0 x atr_14 -- wide stop distance
52,atr_tgt_15rr,atr_,1.5 RR profit target based on 1.5x ATR stop
53,atr_tgt_2rr,atr_,2.0 RR profit target based on 1.5x ATR stop

--- RSI columns (rsi_) ---
54,rsi_avg_gain,rsi_,14-period Wilder smoothed average gain
55,rsi_avg_loss,rsi_,14-period Wilder smoothed average loss
56,rsi_rs,rsi_,avg_gain / avg_loss ratio
57,rsi_14,rsi_,RSI value (0-100)
58,rsi_cross_50,rsi_,1 on bar where RSI crosses above 50 from below else 0
59,rsi_cross_30,rsi_,1 on bar where RSI crosses above 30 from below (oversold bounce) else 0
60,rsi_oversold,rsi_,1 if rsi_14 < 30 else 0
61,rsi_overbought,rsi_,1 if rsi_14 > 70 else 0

--- STOCHASTIC columns (sto_) ---
62,sto_k,sto_,%K stochastic value (0-100)
63,sto_d,sto_,%D signal line = 3-period SMA of sto_k
64,sto_cross_up,sto_,1 on bar where %K crosses above %D else 0
65,sto_oversold,sto_,1 if sto_k < 20 else 0
66,sto_overbought,sto_,1 if sto_k > 80 else 0

--- CCI columns (cci_) ---
67,cci_sma_tp,cci_,20-period SMA of typical price
68,cci_mean_dev,cci_,20-period mean absolute deviation of typical price
69,cci_20,cci_,CCI value (unbounded oscillator)
70,cci_cross_0,cci_,1 on bar where CCI crosses above 0 from below else 0
71,cci_cross_m100,cci_,1 on bar where CCI crosses above -100 from below (oversold exit) else 0
72,cci_oversold,cci_,1 if cci_20 < -100 else 0
73,cci_overbought,cci_,1 if cci_20 > 100 else 0

--- BOLLINGER BANDS / WIDTH columns (bbd_) ---
74,bbd_sma_20,bbd_,20-period SMA of close (Bollinger center line)
75,bbd_std_20,bbd_,20-period rolling standard deviation of close
76,bbd_upper,bbd_,Upper Bollinger Band = sma_20 + 2 x std_20
77,bbd_lower,bbd_,Lower Bollinger Band = sma_20 - 2 x std_20
78,bbd_width,bbd_,(upper - lower) / sma_20 -- normalized band width
79,bbd_squeeze,bbd_,1 if bbd_width <= 1.1 x 20-bar min width (squeeze condition) else 0
80,bbd_expanding,bbd_,1 if bbd_width wider than prior bar else 0
81,bbd_pct_b,bbd_,%B = (close - lower) / (upper - lower) -- price position within bands
82,bbd_above_sma,bbd_,1 if close > bbd_sma_20 else 0

--- CHOPPINESS INDEX columns (chp_) ---
83,chp_14,chp_,Choppiness Index (14-period) -- scale 1-100
84,chp_trending,chp_,1 if chp_14 < 38.2 (clearly trending) else 0
85,chp_ranging,chp_,1 if chp_14 > 61.8 (clearly choppy) else 0
86,chp_regime,chp_,String label: 'trend' / 'range' / 'neutral'

--- VWAP columns (vwp_) ---
87,vwp_pv,vwp_,Price x Volume -- cumulative numerator for VWAP
88,vwp_vwap,vwp_,Intraday VWAP -- resets at start of each trading session
89,vwp_vwap_upper,vwp_,Approximate upper VWAP band (vwap + bbd_std_20)
90,vwp_vwap_lower,vwp_,Approximate lower VWAP band (vwap - bbd_std_20)
91,vwp_above,vwp_,1 if close > vwap else 0
92,vwp_cross_up,vwp_,1 on bar where price crosses above VWAP else 0
93,vwp_distance,vwp_,% distance of close from VWAP ((close-vwap)/vwap*100)

--- OBV columns (obv_) ---
94,obv_raw,obv_,Cumulative On Balance Volume
95,obv_ema_20,obv_,20-period EMA of obv_raw (OBV trend line)
96,obv_above_ema,obv_,1 if obv_raw > obv_ema_20 (OBV in uptrend) else 0
97,obv_rising,obv_,1 if obv_raw > obv_raw 3 bars ago else 0
98,obv_div_bear,obv_,1 if price higher but OBV lower over last 3 bars (bearish divergence) else 0

--- MFI columns (mfi_) ---
99,mfi_pos_flow,mfi_,14-period sum of positive money flow
100,mfi_neg_flow,mfi_,14-period sum of negative money flow
101,mfi_14,mfi_,Money Flow Index value (0-100)
102,mfi_cross_50,mfi_,1 on bar where MFI crosses above 50 from below else 0
103,mfi_oversold,mfi_,1 if mfi_14 < 20 else 0
104,mfi_bounce,mfi_,1 on bar where MFI crosses above 30 from below (oversold bounce) else 0

--- SESSION TIME FLAGS (ses_) ---
105,ses_after_10,ses_,1 if bar is at or after 10:00 AM (valid entry window) else 0
106,ses_before_345,ses_,1 if bar is before 3:45 PM (safe new-entry window) else 0
107,ses_minute,ses_,Minutes since midnight (hour*60 + minute) -- for time-based rules
"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    input_path  = Path('../data/stock/sq_AAPL.csv')
    output_path = Path('../data/stock/sq_AAPL_extended.csv')
    schema_path = Path('../docs/schema/stock_extended.csv')

    print(f"Reading {input_path} ...")
    dfQuotes = pd.read_csv(input_path)
    print(f"  Raw shape: {dfQuotes.shape}")

    print("Computing indicators ...")
    dfExtended = compute_indicators(dfQuotes)
    print(f"  Extended shape: {dfExtended.shape}")

    print(f"Writing {output_path} ...")
    dfExtended.to_csv(output_path, index=False)
    print(f"  Done. {len(dfExtended.columns)} columns, {len(dfExtended):,} rows.")

    print(f"Writing {schema_path} ...")
    schema_path.write_text(SCHEMA.strip())
    print("  Done.")

    # Quick sanity check — print non-null counts for key indicator columns
    check_cols = [
        'ema_9', 'mcd_histogram', 'adx_14', 'rsi_14',
        'sto_k', 'cci_20', 'atr_14', 'bbd_width',
        'chp_14', 'vwp_vwap', 'obv_raw', 'mfi_14'
    ]
    print("\nSanity check — non-null counts per indicator:")
    for col in check_cols:
        if col in dfExtended.columns:
            n = dfExtended[col].notna().sum()
            print(f"  {col:<20} {n:>8,} / {len(dfExtended):,}")