"""
indicator_signals.py

Defines buy and sell signal logic for 24 unique technical indicators used in
the AAPL intraday backtesting system.

For each indicator:
  buy_signal_xxx(row)  -> bool   signal is active on the current bar (entry)
  sell_signal_xxx(row) -> bool   exit condition is met on the current bar (Type B)

Vectorized builders:
  add_buy_signals(df)  -> df     adds 24  bsig_xxx  boolean columns
  add_sell_signals(df) -> df     adds 24  ssig_xxx  boolean columns

The row-level functions are used for testing and documentation.
The backtest engine uses the precomputed bsig/ssig columns for speed.

Indicator categories and keys
──────────────────────────────
  TREND (7)      : ema  macd  adx  sar  don  arn  vtx
  MOMENTUM (10)  : rsi  sto   cci  cmo  tsi  roc  frc  srsi  rmi  macd
  VOLATILITY (3) : atr  bbd   chp
  VOLUME (6)     : vwap obv   mfi  klg  frc  vrc

  macd appears in both TREND and MOMENTUM  (same bsig/ssig column, one signal)
  frc  appears in both MOMENTUM and VOLUME (same bsig/ssig column, one signal)
"""

import numpy as np
import pandas as pd

# ──────────────────────────────────────────────────────────────────────────────
# Registry
# ──────────────────────────────────────────────────────────────────────────────

SIGNAL_NAMES = [
    # trend
    'ema', 'macd', 'adx', 'sar', 'don', 'arn', 'vtx',
    # momentum (macd shared with trend)
    'rsi', 'sto', 'cci', 'cmo', 'tsi', 'roc', 'frc', 'srsi', 'rmi',
    # volatility
    'atr', 'bbd', 'chp',
    # volume (frc shared with momentum)
    'vwap', 'obv', 'mfi', 'klg', 'vrc',
]  # 24 unique names


def _g(row, col, default=0):
    """Safe column accessor for both pd.Series and dict-like rows."""
    try:
        v = row[col]
        return default if (v is None or (isinstance(v, float) and np.isnan(v))) else v
    except (KeyError, TypeError, IndexError):
        return default


# ══════════════════════════════════════════════════════════════════════════════
# BUY SIGNAL FUNCTIONS
# Each returns True when the buy condition is satisfied on this bar.
# ══════════════════════════════════════════════════════════════════════════════

def buy_signal_ema(row) -> bool:
    """EMA 9/21 crossover — bullish cross occurred on this exact bar."""
    return _g(row, 'ema_cross_event') == 1


def buy_signal_macd(row) -> bool:
    """MACD — line crosses above signal AND histogram positive and growing."""
    return (
        _g(row, 'mcd_sig_event') == 1 and
        _g(row, 'mcd_histogram') > 0 and
        _g(row, 'mcd_hist_growing') == 1
    )


def buy_signal_adx(row) -> bool:
    """ADX/DMI — strong trend gate: ADX > 25, +DI > -DI, ADX rising."""
    return (
        _g(row, 'adx_trend_gate') == 1 and
        _g(row, 'adx_rising') == 1
    )


def buy_signal_sar(row) -> bool:
    """Parabolic SAR — SAR just flipped from bearish to bullish this bar."""
    return _g(row, 'sar_flip_bull') == 1


def buy_signal_don(row) -> bool:
    """Donchian — close broke above prior bar's 20-period upper channel."""
    return _g(row, 'don_breakout_up') == 1


def buy_signal_arn(row) -> bool:
    """Aroon — strong uptrend (Up>70, Dn<30) OR Aroon Up just crossed above Down."""
    return (
        _g(row, 'arn_bull') == 1 or
        _g(row, 'arn_cross_up') == 1
    )


def buy_signal_vtx(row) -> bool:
    """Vortex — VI+ crosses above VI- this bar."""
    return _g(row, 'vtx_cross_up') == 1


def buy_signal_rsi(row) -> bool:
    """RSI — crosses above 50 from below OR bounces from oversold (<30→>30)."""
    return (
        _g(row, 'rsi_cross_50') == 1 or
        _g(row, 'rsi_cross_30') == 1
    )


def buy_signal_sto(row) -> bool:
    """Stochastic — %K crosses above %D while not already overbought (K<80)."""
    return (
        _g(row, 'sto_cross_up') == 1 and
        _g(row, 'sto_overbought') == 0
    )


def buy_signal_cci(row) -> bool:
    """CCI — exits oversold territory (-100→above) OR crosses zero."""
    return (
        _g(row, 'cci_cross_m100') == 1 or
        _g(row, 'cci_cross_0') == 1
    )


def buy_signal_cmo(row) -> bool:
    """Chande Momentum — CMO crosses above zero from below."""
    return _g(row, 'cmo_cross_0') == 1


def buy_signal_tsi(row) -> bool:
    """True Strength Index — TSI crosses above zero OR crosses above signal line."""
    return (
        _g(row, 'tsi_cross_0') == 1 or
        _g(row, 'tsi_cross_sig') == 1
    )


def buy_signal_roc(row) -> bool:
    """Rate of Change — ROC crosses above zero (price accelerating upward)."""
    return _g(row, 'roc_cross_0') == 1


def buy_signal_frc(row) -> bool:
    """Force Index — 13-period EMA crosses above zero (bullish pressure with volume)."""
    return _g(row, 'frc_cross_0') == 1


def buy_signal_srsi(row) -> bool:
    """Stochastic RSI — %K crosses above %D while not overbought."""
    return (
        _g(row, 'srsi_cross_up') == 1 and
        _g(row, 'srsi_overbought') == 0
    )


def buy_signal_rmi(row) -> bool:
    """Relative Momentum Index — RMI crosses above 50 (momentum turns positive)."""
    return _g(row, 'rmi_cross_50') == 1


def buy_signal_atr(row) -> bool:
    """ATR quality filter — bar has meaningful size (>=0.5x ATR), no volatility spike."""
    atr = _g(row, 'atr_14', None)
    if atr is None or atr <= 0:
        return False
    return (
        _g(row, 'atr_bar_ratio', 0) >= 0.5 and
        _g(row, 'atr_spike') == 0
    )


def buy_signal_bbd(row) -> bool:
    """Bollinger Band Width — bands expanding AND price above SMA AND %B > 0.5."""
    return (
        _g(row, 'bbd_expanding') == 1 and
        _g(row, 'bbd_above_sma') == 1 and
        _g(row, 'bbd_pct_b', 0) > 0.5
    )


def buy_signal_chp(row) -> bool:
    """Choppiness Index — CI < 50 (market is trending or transitioning to trend)."""
    v = _g(row, 'chp_14', None)
    return v is not None and v < 50.0


def buy_signal_vwap(row) -> bool:
    """VWAP — price is above VWAP and the distance is positive."""
    return (
        _g(row, 'vwp_above') == 1 and
        _g(row, 'vwp_distance', 0) > 0.0
    )


def buy_signal_obv(row) -> bool:
    """OBV — rising (vs 3 bars ago), above 20-EMA, no bearish divergence."""
    return (
        _g(row, 'obv_rising') == 1 and
        _g(row, 'obv_above_ema') == 1 and
        _g(row, 'obv_div_bear') == 0
    )


def buy_signal_mfi(row) -> bool:
    """MFI — crosses above 50 from below OR bounces from oversold (<20→>30)."""
    return (
        _g(row, 'mfi_cross_50') == 1 or
        _g(row, 'mfi_bounce') == 1
    )


def buy_signal_klg(row) -> bool:
    """Klinger Volume Oscillator — KVO line crosses above its signal line."""
    return _g(row, 'klg_cross_sig') == 1


def buy_signal_vrc(row) -> bool:
    """Volume Rate of Change — volume is expanding (>0) but not spiking (<=50%)."""
    return (
        _g(row, 'vrc_pos') == 1 and
        _g(row, 'vrc_spike') == 0
    )


# ══════════════════════════════════════════════════════════════════════════════
# SELL SIGNAL FUNCTIONS  (Type B — state of current bar only)
# Each returns True when an independent exit condition is met.
# Functions that have no meaningful directional sell always return False.
# ══════════════════════════════════════════════════════════════════════════════

def sell_signal_ema(row) -> bool:
    """EMA crossover has turned bearish (fast EMA now below slow EMA)."""
    return _g(row, 'ema_crossover') == 0


def sell_signal_macd(row) -> bool:
    """MACD histogram has turned negative (momentum reversed)."""
    return _g(row, 'mcd_histogram', 0) < 0


def sell_signal_adx(row) -> bool:
    """ADX/DMI trend gate turned off (trend weakened or direction reversed)."""
    return _g(row, 'adx_trend_gate') == 0


def sell_signal_sar(row) -> bool:
    """Parabolic SAR has flipped above price (bearish state)."""
    return _g(row, 'sar_bull') == 0


def sell_signal_don(row) -> bool:
    """Price broke below the Donchian lower channel (breakdown signal)."""
    close = _g(row, 'close', None)
    don_lo = _g(row, 'don_lower_20', None)
    if close is None or don_lo is None:
        return False
    return close < don_lo


def sell_signal_arn(row) -> bool:
    """Aroon oscillator < -20 (downtrend bias has emerged)."""
    return _g(row, 'arn_osc', 0) < -20.0


def sell_signal_vtx(row) -> bool:
    """Vortex VI- has crossed above VI+ (bearish dominance)."""
    return (
        _g(row, 'vtx_minus', 0) > _g(row, 'vtx_plus', 0) and
        _g(row, 'vtx_bull') == 0
    )


def sell_signal_rsi(row) -> bool:
    """RSI is overbought (>70) — profit-taking signal."""
    return _g(row, 'rsi_overbought') == 1


def sell_signal_sto(row) -> bool:
    """Stochastic is overbought (K>80)."""
    return _g(row, 'sto_overbought') == 1


def sell_signal_cci(row) -> bool:
    """CCI is overbought (>100)."""
    return _g(row, 'cci_overbought') == 1


def sell_signal_cmo(row) -> bool:
    """CMO has entered overbought territory (>50)."""
    return _g(row, 'cmo_14', 0) > 50.0


def sell_signal_tsi(row) -> bool:
    """TSI has turned negative (underlying momentum reversed)."""
    return _g(row, 'tsi_val', 0) < 0.0


def sell_signal_roc(row) -> bool:
    """ROC has turned negative (price now lower than 12 bars ago)."""
    return _g(row, 'roc_12', 0) < 0.0


def sell_signal_frc(row) -> bool:
    """Force Index 13-EMA turned negative (selling pressure dominates)."""
    return _g(row, 'frc_ema_13', 0) < 0.0


def sell_signal_srsi(row) -> bool:
    """Stochastic RSI is overbought (K>80)."""
    return _g(row, 'srsi_overbought') == 1


def sell_signal_rmi(row) -> bool:
    """RMI is overbought (>70)."""
    return _g(row, 'rmi_overbought') == 1


def sell_signal_atr(row) -> bool:
    """ATR is a volatility filter — no directional sell signal."""
    return False


def sell_signal_bbd(row) -> bool:
    """Price is near the upper Bollinger Band (%B > 0.95) — approaching resistance."""
    return _g(row, 'bbd_pct_b', 0) > 0.95


def sell_signal_chp(row) -> bool:
    """Choppiness Index > 61.8 — market has turned choppy, trend signals unreliable."""
    return _g(row, 'chp_ranging') == 1


def sell_signal_vwap(row) -> bool:
    """Price has fallen below VWAP — institutional order flow has turned negative."""
    return _g(row, 'vwp_above') == 0


def sell_signal_obv(row) -> bool:
    """Bearish OBV divergence detected (price up but OBV down over last 3 bars)."""
    return _g(row, 'obv_div_bear') == 1


def sell_signal_mfi(row) -> bool:
    """MFI is overbought (>80) — money flow exhausting."""
    return _g(row, 'mfi_14', 0) > 80.0


def sell_signal_klg(row) -> bool:
    """Klinger oscillator has turned bearish (KVO below signal line)."""
    return _g(row, 'klg_bull') == 0


def sell_signal_vrc(row) -> bool:
    """Volume ROC — no meaningful directional sell signal."""
    return False


# ──────────────────────────────────────────────────────────────────────────────
# Registries (used by model.py for programmatic iteration)
# ──────────────────────────────────────────────────────────────────────────────

BUY_SIGNAL_FUNCS = {
    'ema':  buy_signal_ema,   'macd': buy_signal_macd,
    'adx':  buy_signal_adx,   'sar':  buy_signal_sar,
    'don':  buy_signal_don,   'arn':  buy_signal_arn,
    'vtx':  buy_signal_vtx,   'rsi':  buy_signal_rsi,
    'sto':  buy_signal_sto,   'cci':  buy_signal_cci,
    'cmo':  buy_signal_cmo,   'tsi':  buy_signal_tsi,
    'roc':  buy_signal_roc,   'frc':  buy_signal_frc,
    'srsi': buy_signal_srsi,  'rmi':  buy_signal_rmi,
    'atr':  buy_signal_atr,   'bbd':  buy_signal_bbd,
    'chp':  buy_signal_chp,   'vwap': buy_signal_vwap,
    'obv':  buy_signal_obv,   'mfi':  buy_signal_mfi,
    'klg':  buy_signal_klg,   'vrc':  buy_signal_vrc,
}

SELL_SIGNAL_FUNCS = {
    'ema':  sell_signal_ema,   'macd': sell_signal_macd,
    'adx':  sell_signal_adx,   'sar':  sell_signal_sar,
    'don':  sell_signal_don,   'arn':  sell_signal_arn,
    'vtx':  sell_signal_vtx,   'rsi':  sell_signal_rsi,
    'sto':  sell_signal_sto,   'cci':  sell_signal_cci,
    'cmo':  sell_signal_cmo,   'tsi':  sell_signal_tsi,
    'roc':  sell_signal_roc,   'frc':  sell_signal_frc,
    'srsi': sell_signal_srsi,  'rmi':  sell_signal_rmi,
    'atr':  sell_signal_atr,   'bbd':  sell_signal_bbd,
    'chp':  sell_signal_chp,   'vwap': sell_signal_vwap,
    'obv':  sell_signal_obv,   'mfi':  sell_signal_mfi,
    'klg':  sell_signal_klg,   'vrc':  sell_signal_vrc,
}


# ══════════════════════════════════════════════════════════════════════════════
# VECTORIZED SIGNAL BUILDERS
# Add bsig_xxx and ssig_xxx boolean columns to the extended DataFrame.
# Called once when building sq_AAPL_signals.csv.
# ══════════════════════════════════════════════════════════════════════════════

def _fi(s):
    """Fill NaN → 0 and return the series."""
    return s.fillna(0)


def add_buy_signals(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds 24 bsig_xxx columns (int8, 0/1) to df.
    Operates on all rows — session filtering is applied in model.py.
    """
    df = df.copy()

    df['bsig_ema']  = (_fi(df['ema_cross_event']) == 1).astype(np.int8)

    df['bsig_macd'] = (
        (_fi(df['mcd_sig_event']) == 1) &
        (df['mcd_histogram'].fillna(-1) > 0) &
        (_fi(df['mcd_hist_growing']) == 1)
    ).astype(np.int8)

    df['bsig_adx']  = (
        (_fi(df['adx_trend_gate']) == 1) &
        (_fi(df['adx_rising']) == 1)
    ).astype(np.int8)

    df['bsig_sar']  = (_fi(df['sar_flip_bull']) == 1).astype(np.int8)

    df['bsig_don']  = (_fi(df['don_breakout_up']) == 1).astype(np.int8)

    df['bsig_arn']  = (
        (_fi(df['arn_bull']) == 1) | (_fi(df['arn_cross_up']) == 1)
    ).astype(np.int8)

    df['bsig_vtx']  = (_fi(df['vtx_cross_up']) == 1).astype(np.int8)

    df['bsig_rsi']  = (
        (_fi(df['rsi_cross_50']) == 1) | (_fi(df['rsi_cross_30']) == 1)
    ).astype(np.int8)

    df['bsig_sto']  = (
        (_fi(df['sto_cross_up']) == 1) & (_fi(df['sto_overbought']) == 0)
    ).astype(np.int8)

    df['bsig_cci']  = (
        (_fi(df['cci_cross_m100']) == 1) | (_fi(df['cci_cross_0']) == 1)
    ).astype(np.int8)

    df['bsig_cmo']  = (_fi(df['cmo_cross_0']) == 1).astype(np.int8)

    df['bsig_tsi']  = (
        (_fi(df['tsi_cross_0']) == 1) | (_fi(df['tsi_cross_sig']) == 1)
    ).astype(np.int8)

    df['bsig_roc']  = (_fi(df['roc_cross_0']) == 1).astype(np.int8)

    df['bsig_frc']  = (_fi(df['frc_cross_0']) == 1).astype(np.int8)

    df['bsig_srsi'] = (
        (_fi(df['srsi_cross_up']) == 1) & (_fi(df['srsi_overbought']) == 0)
    ).astype(np.int8)

    df['bsig_rmi']  = (_fi(df['rmi_cross_50']) == 1).astype(np.int8)

    df['bsig_atr']  = (
        (df['atr_bar_ratio'].fillna(0) >= 0.5) &
        (_fi(df['atr_spike']) == 0) &
        (df['atr_14'].notna())
    ).astype(np.int8)

    df['bsig_bbd']  = (
        (_fi(df['bbd_expanding']) == 1) &
        (_fi(df['bbd_above_sma']) == 1) &
        (df['bbd_pct_b'].fillna(0) > 0.5)
    ).astype(np.int8)

    df['bsig_chp']  = (df['chp_14'].fillna(100) < 50.0).astype(np.int8)

    df['bsig_vwap'] = (
        (_fi(df['vwp_above']) == 1) &
        (df['vwp_distance'].fillna(0) > 0.0)
    ).astype(np.int8)

    df['bsig_obv']  = (
        (_fi(df['obv_rising']) == 1) &
        (_fi(df['obv_above_ema']) == 1) &
        (_fi(df['obv_div_bear']) == 0)
    ).astype(np.int8)

    df['bsig_mfi']  = (
        (_fi(df['mfi_cross_50']) == 1) | (_fi(df['mfi_bounce']) == 1)
    ).astype(np.int8)

    df['bsig_klg']  = (_fi(df['klg_cross_sig']) == 1).astype(np.int8)

    df['bsig_vrc']  = (
        (_fi(df['vrc_pos']) == 1) & (_fi(df['vrc_spike']) == 0)
    ).astype(np.int8)

    return df


def add_sell_signals(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds 24 ssig_xxx columns (int8, 0/1) to df.
    These are evaluated on every bar during the forward simulation.
    """
    df = df.copy()

    df['ssig_ema']  = (_fi(df['ema_crossover']) == 0).astype(np.int8)

    df['ssig_macd'] = (df['mcd_histogram'].fillna(0) < 0).astype(np.int8)

    df['ssig_adx']  = (_fi(df['adx_trend_gate']) == 0).astype(np.int8)

    df['ssig_sar']  = (_fi(df['sar_bull']) == 0).astype(np.int8)

    df['ssig_don']  = (
        df['close'] < df['don_lower_20'].fillna(0)
    ).astype(np.int8)

    df['ssig_arn']  = (df['arn_osc'].fillna(0) < -20.0).astype(np.int8)

    df['ssig_vtx']  = (
        (df['vtx_minus'].fillna(0) > df['vtx_plus'].fillna(0)) &
        (_fi(df['vtx_bull']) == 0)
    ).astype(np.int8)

    df['ssig_rsi']  = (_fi(df['rsi_overbought']) == 1).astype(np.int8)

    df['ssig_sto']  = (_fi(df['sto_overbought']) == 1).astype(np.int8)

    df['ssig_cci']  = (_fi(df['cci_overbought']) == 1).astype(np.int8)

    df['ssig_cmo']  = (df['cmo_14'].fillna(0) > 50.0).astype(np.int8)

    df['ssig_tsi']  = (df['tsi_val'].fillna(0) < 0.0).astype(np.int8)

    df['ssig_roc']  = (df['roc_12'].fillna(0) < 0.0).astype(np.int8)

    df['ssig_frc']  = (df['frc_ema_13'].fillna(0) < 0.0).astype(np.int8)

    df['ssig_srsi'] = (_fi(df['srsi_overbought']) == 1).astype(np.int8)

    df['ssig_rmi']  = (_fi(df['rmi_overbought']) == 1).astype(np.int8)

    df['ssig_atr']  = np.int8(0)   # no directional sell for ATR

    df['ssig_bbd']  = (df['bbd_pct_b'].fillna(0) > 0.95).astype(np.int8)

    df['ssig_chp']  = (_fi(df['chp_ranging']) == 1).astype(np.int8)

    df['ssig_vwap'] = (_fi(df['vwp_above']) == 0).astype(np.int8)

    df['ssig_obv']  = (_fi(df['obv_div_bear']) == 1).astype(np.int8)

    df['ssig_mfi']  = (df['mfi_14'].fillna(0) > 80.0).astype(np.int8)

    df['ssig_klg']  = (_fi(df['klg_bull']) == 0).astype(np.int8)

    df['ssig_vrc']  = np.int8(0)   # no directional sell for Volume ROC

    return df
