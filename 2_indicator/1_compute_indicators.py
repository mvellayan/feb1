"""
compute_indicators.py

Computes all technical indicator intermediate columns for intraday AAPL backtesting.
Reads sq_AAPL.csv, adds ~140 computed columns with consistent prefixes,
writes sq_AAPL_extended.csv.

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
  ses_   Session time flags
  --- NEW (13 additional indicators) ---
  sar_   Parabolic SAR
  don_   Donchian Channels
  arn_   Aroon Up / Down / Oscillator
  vtx_   Vortex Indicator (VI+ / VI-)
  cmo_   Chande Momentum Oscillator
  tsi_   True Strength Index
  roc_   Rate of Change
  frc_   Force Index
  srsi_  Stochastic RSI
  rmi_   Relative Momentum Index
  klg_   Klinger Volume Oscillator
  vrc_   Volume Rate of Change
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


def _compute_parabolic_sar(
    high: pd.Series, low: pd.Series,
    af_start: float = 0.02, af_step: float = 0.02, af_max: float = 0.20
) -> tuple:
    """
    Full iterative Parabolic SAR computation.
    Returns (sar_series, bull_series) where bull=1 means price > SAR (uptrend).
    Cannot be vectorized due to state dependency between bars.
    """
    n = len(high)
    sar  = np.zeros(n, dtype=np.float64)
    ep   = np.zeros(n, dtype=np.float64)   # extreme point
    af   = np.zeros(n, dtype=np.float64)   # acceleration factor
    bull = np.zeros(n, dtype=np.int8)      # 1 = uptrend

    hi = high.values
    lo = low.values

    # Seed: assume uptrend on first bar
    bull[0] = 1
    sar[0]  = lo[0]
    ep[0]   = hi[0]
    af[0]   = af_start

    for i in range(1, n):
        pb, ps, pe, pa = bull[i-1], sar[i-1], ep[i-1], af[i-1]
        new_sar = ps + pa * (pe - ps)

        if pb == 1:                             # uptrend
            new_sar = min(new_sar, lo[i-1])
            if i >= 2:
                new_sar = min(new_sar, lo[i-2])
            if lo[i] < new_sar:                 # reversal to downtrend
                bull[i] = 0
                sar[i]  = pe
                ep[i]   = lo[i]
                af[i]   = af_start
            else:
                bull[i] = 1
                sar[i]  = new_sar
                if hi[i] > pe:
                    ep[i] = hi[i]
                    af[i] = min(pa + af_step, af_max)
                else:
                    ep[i] = pe
                    af[i] = pa
        else:                                   # downtrend
            new_sar = max(new_sar, hi[i-1])
            if i >= 2:
                new_sar = max(new_sar, hi[i-2])
            if hi[i] > new_sar:                 # reversal to uptrend
                bull[i] = 1
                sar[i]  = pe
                ep[i]   = hi[i]
                af[i]   = af_start
            else:
                bull[i] = 0
                sar[i]  = new_sar
                if lo[i] < pe:
                    ep[i] = lo[i]
                    af[i] = min(pa + af_step, af_max)
                else:
                    ep[i] = pe
                    af[i] = pa

    return (
        pd.Series(sar,  index=high.index, dtype=np.float32),
        pd.Series(bull, index=high.index, dtype=np.int8),
    )


def _compute_klinger(
    high: pd.Series, low: pd.Series,
    close: pd.Series, volume: pd.Series,
    fast: int = 34, slow: int = 55, signal: int = 13
) -> tuple:
    """
    Klinger Volume Oscillator.
    Returns (vf_series, kvo_line, kvo_signal) — all pd.Series.
    The CM (cumulative measurement) state requires an explicit loop.
    """
    n   = len(close)
    tp  = (high + low + close).values
    dm  = (high - low).values
    vol = volume.values

    trend = np.zeros(n, dtype=np.float64)
    cm    = np.zeros(n, dtype=np.float64)
    vf    = np.zeros(n, dtype=np.float64)

    for i in range(1, n):
        trend[i] = 1.0 if tp[i] > tp[i-1] else -1.0

    cm[0] = dm[0]
    for i in range(1, n):
        cm[i] = (cm[i-1] + dm[i]) if trend[i] == trend[i-1] else (dm[i-1] + dm[i])

    for i in range(n):
        if cm[i] != 0:
            vf[i] = vol[i] * abs(2.0 * (dm[i] / cm[i]) - 1.0) * trend[i] * 100.0

    vf_s    = pd.Series(vf, index=close.index)
    kvo     = vf_s.ewm(span=fast, adjust=False).mean() - \
              vf_s.ewm(span=slow, adjust=False).mean()
    kvo_sig = kvo.ewm(span=signal, adjust=False).mean()

    return vf_s.astype(np.float32), kvo.astype(np.float32), kvo_sig.astype(np.float32)


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
    # Defragment before adding the 13 new indicator groups
    # (avoids pandas PerformanceWarning from many sequential column inserts)
    # -----------------------------------------------------------------------
    df = df.copy()

    # -----------------------------------------------------------------------
    # PARABOLIC SAR  (prefix: sar_)
    # -----------------------------------------------------------------------

    df['sar_value'], df['sar_bull'] = _compute_parabolic_sar(df['high'], df['low'])

    sar_bull_prev          = df['sar_bull'].shift(1).fillna(0).astype(np.int8)
    df['sar_flip_bull']    = (
        (df['sar_bull'] == 1) & (sar_bull_prev == 0)
    ).astype(np.int8)                                               # SAR just flipped bullish

    # -----------------------------------------------------------------------
    # DONCHIAN CHANNELS  (prefix: don_)
    # -----------------------------------------------------------------------

    # Use period=20; upper = highest high, lower = lowest low over 20 bars
    # The breakout signal uses the PRIOR bar's channel to avoid look-ahead
    df['don_upper_20']     = df['high'].rolling(20).max()
    df['don_lower_20']     = df['low'].rolling(20).min()
    df['don_mid_20']       = (df['don_upper_20'] + df['don_lower_20']) / 2.0
    df['don_width']        = df['don_upper_20'] - df['don_lower_20']

    # Breakout: close exceeds the PRIOR bar's upper channel (avoids look-ahead)
    df['don_breakout_up']  = (
        df['close'] > df['don_upper_20'].shift(1)
    ).astype(np.int8)
    df['don_bull']         = (df['close'] > df['don_mid_20']).astype(np.int8)

    # -----------------------------------------------------------------------
    # AROON UP / DOWN / OSCILLATOR  (prefix: arn_)
    # Period = 25 (standard)
    # -----------------------------------------------------------------------

    arn_period = 25

    # bars_since_high: position of rolling max within the window (0 = most recent)
    def _bars_since_high(s, w):
        return s.rolling(w + 1).apply(
            lambda x: w - np.argmax(x), raw=True
        )

    def _bars_since_low(s, w):
        return s.rolling(w + 1).apply(
            lambda x: w - np.argmin(x), raw=True
        )

    bars_high              = _bars_since_high(df['high'], arn_period)
    bars_low               = _bars_since_low(df['low'],  arn_period)

    df['arn_up']           = (100.0 * (arn_period - bars_high) / arn_period).astype(np.float32)
    df['arn_dn']           = (100.0 * (arn_period - bars_low)  / arn_period).astype(np.float32)
    df['arn_osc']          = (df['arn_up'] - df['arn_dn']).astype(np.float32)

    arn_up_prev            = df['arn_up'].shift(1)
    arn_dn_prev            = df['arn_dn'].shift(1)
    df['arn_bull']         = (
        (df['arn_up'] > 70) & (df['arn_dn'] < 30)
    ).astype(np.int8)
    df['arn_cross_up']     = (
        (df['arn_up'] > df['arn_dn']) & (arn_up_prev <= arn_dn_prev)
    ).astype(np.int8)                                               # Aroon Up crosses above Down

    # -----------------------------------------------------------------------
    # VORTEX INDICATOR  (prefix: vtx_)
    # Period = 14
    # -----------------------------------------------------------------------

    vtx_period = 14
    vm_plus    = (df['high'] - df['low'].shift(1)).abs()
    vm_minus   = (df['low']  - df['high'].shift(1)).abs()

    vm_plus_sum  = vm_plus.rolling(vtx_period).sum()
    vm_minus_sum = vm_minus.rolling(vtx_period).sum()
    tr_sum_vtx   = df['fnd_true_range'].rolling(vtx_period).sum()

    df['vtx_plus']         = (vm_plus_sum  / tr_sum_vtx.replace(0, np.nan)).astype(np.float32)
    df['vtx_minus']        = (vm_minus_sum / tr_sum_vtx.replace(0, np.nan)).astype(np.float32)

    vtx_plus_prev          = df['vtx_plus'].shift(1)
    vtx_minus_prev         = df['vtx_minus'].shift(1)
    df['vtx_bull']         = (df['vtx_plus'] > df['vtx_minus']).astype(np.int8)
    df['vtx_cross_up']     = (
        (df['vtx_plus'] > df['vtx_minus']) &
        (vtx_plus_prev <= vtx_minus_prev)
    ).astype(np.int8)                                               # VI+ crosses above VI-

    # -----------------------------------------------------------------------
    # CHANDE MOMENTUM OSCILLATOR  (prefix: cmo_)
    # Period = 14
    # -----------------------------------------------------------------------

    cmo_period  = 14
    cmo_up      = df['fnd_close_chg'].clip(lower=0)
    cmo_dn      = (-df['fnd_close_chg']).clip(lower=0)

    cmo_up_sum  = cmo_up.rolling(cmo_period).sum()
    cmo_dn_sum  = cmo_dn.rolling(cmo_period).sum()
    cmo_denom   = (cmo_up_sum + cmo_dn_sum).replace(0, np.nan)

    df['cmo_14']           = (100.0 * (cmo_up_sum - cmo_dn_sum) / cmo_denom).astype(np.float32)

    cmo_prev               = df['cmo_14'].shift(1)
    df['cmo_bull']         = (df['cmo_14'] > 0).astype(np.int8)
    df['cmo_cross_0']      = (
        (df['cmo_14'] > 0) & (cmo_prev <= 0)
    ).astype(np.int8)

    # -----------------------------------------------------------------------
    # TRUE STRENGTH INDEX  (prefix: tsi_)
    # Double-smoothed momentum: EMA(25) of EMA(13) of price change
    # -----------------------------------------------------------------------

    mom                    = df['fnd_close_chg']
    mom_abs                = df['fnd_abs_chg']

    # Double EMA smoothing (25 then 13)
    tsi_smooth1            = mom.ewm(span=25, adjust=False).mean()
    tsi_smooth2            = tsi_smooth1.ewm(span=13, adjust=False).mean()
    tsi_abs1               = mom_abs.ewm(span=25, adjust=False).mean()
    tsi_abs2               = tsi_abs1.ewm(span=13, adjust=False).mean()

    df['tsi_val']          = (100.0 * tsi_smooth2 / tsi_abs2.replace(0, np.nan)).astype(np.float32)
    df['tsi_signal']       = df['tsi_val'].ewm(span=13, adjust=False).mean().astype(np.float32)

    tsi_prev               = df['tsi_val'].shift(1)
    tsi_sig_prev           = df['tsi_signal'].shift(1)
    df['tsi_bull']         = (df['tsi_val'] > 0).astype(np.int8)
    df['tsi_cross_0']      = (
        (df['tsi_val'] > 0) & (tsi_prev <= 0)
    ).astype(np.int8)
    df['tsi_cross_sig']    = (
        (df['tsi_val'] > df['tsi_signal']) & (tsi_prev <= tsi_sig_prev)
    ).astype(np.int8)                                               # TSI crosses above signal

    # -----------------------------------------------------------------------
    # RATE OF CHANGE  (prefix: roc_)
    # Period = 12;  ROC = ((close - close[12]) / close[12]) * 100
    # -----------------------------------------------------------------------

    roc_period             = 12
    close_n                = df['close'].shift(roc_period)
    df['roc_12']           = (
        100.0 * (df['close'] - close_n) / close_n.replace(0, np.nan)
    ).astype(np.float32)

    roc_prev               = df['roc_12'].shift(1)
    df['roc_bull']         = (df['roc_12'] > 0).astype(np.int8)
    df['roc_cross_0']      = (
        (df['roc_12'] > 0) & (roc_prev <= 0)
    ).astype(np.int8)

    # -----------------------------------------------------------------------
    # FORCE INDEX  (prefix: frc_)
    # Raw = close_change × volume; smoothed with 2-period and 13-period EMA
    # -----------------------------------------------------------------------

    df['frc_raw']          = (df['fnd_close_chg'] * df['volume']).astype(np.float32)
    df['frc_ema_2']        = df['frc_raw'].ewm(span=2,  adjust=False).mean().astype(np.float32)
    df['frc_ema_13']       = df['frc_raw'].ewm(span=13, adjust=False).mean().astype(np.float32)

    frc_prev               = df['frc_ema_13'].shift(1)
    df['frc_bull']         = (df['frc_ema_13'] > 0).astype(np.int8)
    df['frc_cross_0']      = (
        (df['frc_ema_13'] > 0) & (frc_prev <= 0)
    ).astype(np.int8)

    # -----------------------------------------------------------------------
    # STOCHASTIC RSI  (prefix: srsi_)
    # Applies Stochastic formula to RSI values; period = 14 for both
    # -----------------------------------------------------------------------

    rsi_min_14             = df['rsi_14'].rolling(14).min()
    rsi_max_14             = df['rsi_14'].rolling(14).max()
    rsi_range_14           = (rsi_max_14 - rsi_min_14).replace(0, np.nan)

    df['srsi_k']           = (
        100.0 * (df['rsi_14'] - rsi_min_14) / rsi_range_14
    ).astype(np.float32)
    df['srsi_d']           = df['srsi_k'].rolling(3).mean().astype(np.float32)

    srsi_k_prev            = df['srsi_k'].shift(1)
    srsi_d_prev            = df['srsi_d'].shift(1)
    df['srsi_cross_up']    = (
        (df['srsi_k'] > df['srsi_d']) & (srsi_k_prev <= srsi_d_prev)
    ).astype(np.int8)
    df['srsi_oversold']    = (df['srsi_k'] < 20).astype(np.int8)
    df['srsi_overbought']  = (df['srsi_k'] > 80).astype(np.int8)

    # -----------------------------------------------------------------------
    # RELATIVE MOMENTUM INDEX  (prefix: rmi_)
    # Like RSI but uses n-bar momentum instead of 1-bar changes.
    # Period = 14, momentum lookback = 3
    # -----------------------------------------------------------------------

    rmi_mom_period = 3
    rmi_period     = 14
    rmi_chg        = df['close'] - df['close'].shift(rmi_mom_period)
    rmi_gain       = rmi_chg.clip(lower=0)
    rmi_loss       = (-rmi_chg).clip(lower=0)

    rmi_avg_gain   = _wilder_smooth(rmi_gain, rmi_period)
    rmi_avg_loss   = _wilder_smooth(rmi_loss, rmi_period)
    rmi_rs         = rmi_avg_gain / rmi_avg_loss.replace(0, np.nan)
    df['rmi_14']   = (100.0 - 100.0 / (1.0 + rmi_rs)).astype(np.float32)

    rmi_prev               = df['rmi_14'].shift(1)
    df['rmi_cross_50']     = (
        (df['rmi_14'] > 50) & (rmi_prev <= 50)
    ).astype(np.int8)
    df['rmi_oversold']     = (df['rmi_14'] < 30).astype(np.int8)
    df['rmi_overbought']   = (df['rmi_14'] > 70).astype(np.int8)

    # -----------------------------------------------------------------------
    # KLINGER VOLUME OSCILLATOR  (prefix: klg_)
    # Requires stateful CM computation — uses helper function.
    # -----------------------------------------------------------------------

    df['klg_vf'], df['klg_line'], df['klg_signal'] = _compute_klinger(
        df['high'], df['low'], df['close'], df['volume']
    )

    klg_line_prev          = df['klg_line'].shift(1)
    klg_sig_prev           = df['klg_signal'].shift(1)
    df['klg_bull']         = (df['klg_line'] > df['klg_signal']).astype(np.int8)
    df['klg_cross_sig']    = (
        (df['klg_line'] > df['klg_signal']) & (klg_line_prev <= klg_sig_prev)
    ).astype(np.int8)                                               # KVO crosses above signal

    # -----------------------------------------------------------------------
    # VOLUME RATE OF CHANGE  (prefix: vrc_)
    # Period = 14
    # -----------------------------------------------------------------------

    vrc_period             = 14
    vol_n                  = df['volume'].shift(vrc_period)
    df['vrc_14']           = (
        100.0 * (df['volume'] - vol_n) / vol_n.replace(0, np.nan)
    ).astype(np.float32)

    df['vrc_pos']          = (df['vrc_14'] > 0).astype(np.int8)    # volume expanding
    df['vrc_spike']        = (df['vrc_14'] > 50).astype(np.int8)   # volume up > 50%

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

    # Columns that are already int8 flags — do not cast to float32
    int8_flag_cols = {
        'ses_after_10', 'ses_before_345', 'ses_minute',
        'ema_crossover', 'ema_cross_event',
        'mcd_hist_growing', 'mcd_cross_event', 'mcd_sig_event',
        'adx_rising', 'adx_trend_gate',
        'rsi_cross_50', 'rsi_cross_30', 'rsi_oversold', 'rsi_overbought',
        'sto_cross_up', 'sto_oversold', 'sto_overbought',
        'cci_cross_0', 'cci_cross_m100', 'cci_oversold', 'cci_overbought',
        'atr_spike',
        'bbd_squeeze', 'bbd_expanding', 'bbd_above_sma',
        'chp_trending', 'chp_ranging',
        'vwp_above', 'vwp_cross_up',
        'obv_above_ema', 'obv_rising', 'obv_div_bear',
        'mfi_cross_50', 'mfi_oversold', 'mfi_bounce',
        # New indicator flags
        'sar_bull', 'sar_flip_bull',
        'don_breakout_up', 'don_bull',
        'arn_bull', 'arn_cross_up',
        'vtx_bull', 'vtx_cross_up',
        'cmo_bull', 'cmo_cross_0',
        'tsi_bull', 'tsi_cross_0', 'tsi_cross_sig',
        'roc_bull', 'roc_cross_0',
        'frc_bull', 'frc_cross_0',
        'srsi_cross_up', 'srsi_oversold', 'srsi_overbought',
        'rmi_cross_50', 'rmi_oversold', 'rmi_overbought',
        'klg_bull', 'klg_cross_sig',
        'vrc_pos', 'vrc_spike',
    }

    float_cols = [
        c for c in df.columns
        if c not in raw_cols
        and c not in int8_flag_cols
        and df[c].dtype in [np.float64, np.int64]
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
--- RAW COLUMNS (from sq_AAPL.csv) ---
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
23,fnd_true_range,fnd_,max(H-L |H-prevC| |L-prevC|) -- shared by ATR/ADX/Choppiness
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
86,chp_regime,chp_,String label: trend / range / neutral

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

--- PARABOLIC SAR columns (sar_) ---
108,sar_value,sar_,SAR price level for the current bar
109,sar_bull,sar_,1 if price > SAR (uptrend) else 0 (downtrend)
110,sar_flip_bull,sar_,1 on bar where SAR flips from bearish to bullish (entry signal) else 0

--- DONCHIAN CHANNELS columns (don_) ---
111,don_upper_20,don_,20-period rolling highest high
112,don_lower_20,don_,20-period rolling lowest low
113,don_mid_20,don_,(don_upper_20 + don_lower_20) / 2 -- channel midpoint
114,don_width,don_,don_upper_20 - don_lower_20 -- channel width
115,don_breakout_up,don_,1 if close > prior bar don_upper_20 (breakout signal) else 0
116,don_bull,don_,1 if close > don_mid_20 else 0

--- AROON columns (arn_) ---
117,arn_up,arn_,Aroon Up (0-100) -- 100 x (25 - bars since 25-period high) / 25
118,arn_dn,arn_,Aroon Down (0-100) -- 100 x (25 - bars since 25-period low) / 25
119,arn_osc,arn_,Aroon Oscillator = arn_up - arn_dn (-100 to +100)
120,arn_bull,arn_,1 if arn_up > 70 AND arn_dn < 30 (strong uptrend) else 0
121,arn_cross_up,arn_,1 on bar where Aroon Up crosses above Aroon Down else 0

--- VORTEX columns (vtx_) ---
122,vtx_plus,vtx_,VI+ = sum(|high - prev_low|) / sum(true_range) over 14 bars
123,vtx_minus,vtx_,VI- = sum(|low - prev_high|) / sum(true_range) over 14 bars
124,vtx_bull,vtx_,1 if vtx_plus > vtx_minus else 0
125,vtx_cross_up,vtx_,1 on bar where VI+ crosses above VI- else 0

--- CHANDE MOMENTUM OSCILLATOR columns (cmo_) ---
126,cmo_14,cmo_,CMO value (-100 to +100) -- 14-period
127,cmo_bull,cmo_,1 if cmo_14 > 0 else 0
128,cmo_cross_0,cmo_,1 on bar where CMO crosses above 0 from below else 0

--- TRUE STRENGTH INDEX columns (tsi_) ---
129,tsi_val,tsi_,TSI value -- double-smoothed momentum (EMA25 of EMA13)
130,tsi_signal,tsi_,TSI signal line = 13-period EMA of tsi_val
131,tsi_bull,tsi_,1 if tsi_val > 0 else 0
132,tsi_cross_0,tsi_,1 on bar where TSI crosses above 0 from below else 0
133,tsi_cross_sig,tsi_,1 on bar where TSI crosses above signal line else 0

--- RATE OF CHANGE columns (roc_) ---
134,roc_12,roc_,ROC = ((close - close[12]) / close[12]) * 100 -- 12-period
135,roc_bull,roc_,1 if roc_12 > 0 else 0
136,roc_cross_0,roc_,1 on bar where ROC crosses above 0 from below else 0

--- FORCE INDEX columns (frc_) ---
137,frc_raw,frc_,Raw Force Index = close_change x volume
138,frc_ema_2,frc_,2-period EMA of frc_raw (fast)
139,frc_ema_13,frc_,13-period EMA of frc_raw (main signal)
140,frc_bull,frc_,1 if frc_ema_13 > 0 else 0
141,frc_cross_0,frc_,1 on bar where frc_ema_13 crosses above 0 else 0

--- STOCHASTIC RSI columns (srsi_) ---
142,srsi_k,srsi_,Stochastic %K applied to RSI values (0-100)
143,srsi_d,srsi_,3-period SMA of srsi_k (signal line)
144,srsi_cross_up,srsi_,1 on bar where srsi_k crosses above srsi_d else 0
145,srsi_oversold,srsi_,1 if srsi_k < 20 else 0
146,srsi_overbought,srsi_,1 if srsi_k > 80 else 0

--- RELATIVE MOMENTUM INDEX columns (rmi_) ---
147,rmi_14,rmi_,RMI value (0-100) -- RSI using 3-bar momentum over 14-period
148,rmi_cross_50,rmi_,1 on bar where RMI crosses above 50 from below else 0
149,rmi_oversold,rmi_,1 if rmi_14 < 30 else 0
150,rmi_overbought,rmi_,1 if rmi_14 > 70 else 0

--- KLINGER VOLUME OSCILLATOR columns (klg_) ---
151,klg_vf,klg_,Volume Force -- raw KVO input
152,klg_line,klg_,KVO line = EMA(34) - EMA(55) of volume force
153,klg_signal,klg_,KVO signal line = 13-period EMA of klg_line
154,klg_bull,klg_,1 if klg_line > klg_signal else 0
155,klg_cross_sig,klg_,1 on bar where KVO crosses above signal line else 0

--- VOLUME RATE OF CHANGE columns (vrc_) ---
156,vrc_14,vrc_,Volume ROC = ((volume - volume[14]) / volume[14]) * 100
157,vrc_pos,vrc_,1 if vrc_14 > 0 (volume expanding vs 14 bars ago) else 0
158,vrc_spike,vrc_,1 if vrc_14 > 50 (volume up more than 50%) else 0
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
    schema_path.parent.mkdir(parents=True, exist_ok=True)
    schema_path.write_text(SCHEMA.strip())
    print("  Done.")

    # Sanity check — non-null counts for all 25 indicator families
    check_cols = [
        # Original 13
        'ema_9', 'mcd_histogram', 'adx_14', 'rsi_14',
        'sto_k', 'cci_20', 'atr_14', 'bbd_width',
        'chp_14', 'vwp_vwap', 'obv_raw', 'mfi_14',
        # New 13
        'sar_value', 'don_upper_20', 'arn_up',
        'vtx_plus', 'cmo_14', 'tsi_val',
        'roc_12', 'frc_ema_13', 'srsi_k',
        'rmi_14', 'klg_line', 'vrc_14',
    ]
    print("\nSanity check — non-null counts per indicator:")
    for col in check_cols:
        if col in dfExtended.columns:
            n = dfExtended[col].notna().sum()
            print(f"  {col:<20} {n:>8,} / {len(dfExtended):,}")