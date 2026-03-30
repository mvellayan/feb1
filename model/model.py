"""
model.py

Intraday backtesting engine for AAPL 1-minute data.

Architecture:
  - Reads sq-AAPL-extended.csv (output of compute_indicators.py)
  - Subsets to a configurable date range
  - Defines buy_signal_xxx() functions for each indicator
  - Defines exit_bracket_xxx() functions returning (stop_loss, profit_target)
  - Randomly samples 10,000 candidate bars after 10:00 AM
  - For each bar with a buy signal, simulates the trade through end of day
  - Logs all trade details to a DataFrame and writes a Markdown performance report

Exit priority (first triggered wins):
  1. Profit target hit
  2. Stop loss hit
  3. Hard time exit at 3:45 PM
"""

import numpy as np
import pandas as pd
import random
import datetime
from pathlib import Path


# ============================================================
# CONFIGURATION
# ============================================================

DATA_PATH       = Path('../data/sq-AAPL-extended.csv')
OUTPUT_TRADES   = Path('../data/trades.csv')
OUTPUT_REPORT   = Path('../data/performance_report.md')

DATE_START      = '2022-01-01'
DATE_END        = '2023-12-31'

N_SAMPLE        = 10_000          # candidate bars to test for buy signals
RANDOM_SEED     = 42

EXIT_TIME_STR   = '15:45'         # hard time-box exit (HH:MM)
ATR_STOP_MULT   = 1.5             # stop-loss = entry - ATR * multiplier
ATR_TARGET_RR   = 2.0             # reward:risk ratio for profit target


# ============================================================
# BUY SIGNAL FUNCTIONS
# Each function receives a single row (pd.Series) from the
# extended dataframe and returns True (signal active) or False.
# ============================================================

def buy_signal_ema(row) -> bool:
    """
    EMA Crossover — bullish when fast EMA (9) is above slow EMA (21)
    AND a crossover event just occurred on this bar.
    """
    try:
        return (
            row['ema_crossover'] == 1 and
            row['ema_cross_event'] == 1         # fired on this exact bar
        )
    except (KeyError, TypeError):
        return False


def buy_signal_macd(row) -> bool:
    """
    MACD — bullish when MACD line crosses above signal line (mcd_sig_event == +1)
    AND histogram is positive and growing.
    """
    try:
        return (
            row['mcd_sig_event'] == 1 and
            row['mcd_histogram'] > 0 and
            row['mcd_hist_growing'] == 1
        )
    except (KeyError, TypeError):
        return False


def buy_signal_adx(row) -> bool:
    """
    ADX/DMI — bullish regime gate.
    ADX > 25 (strong trend) AND +DI > -DI (bullish direction) AND ADX rising.
    Used as a filter rather than a precise entry trigger.
    """
    try:
        return (
            row['adx_14'] > 25 and
            row['adx_plus_di'] > row['adx_minus_di'] and
            row['adx_rising'] == 1
        )
    except (KeyError, TypeError):
        return False


def buy_signal_dmi(row) -> bool:
    """
    DMI only — directional confirmation without requiring ADX threshold.
    +DI > -DI AND the gap is meaningful (> 5 points).
    """
    try:
        return (
            row['adx_plus_di'] > row['adx_minus_di'] and
            (row['adx_plus_di'] - row['adx_minus_di']) > 5.0
        )
    except (KeyError, TypeError):
        return False


def buy_signal_rsi(row) -> bool:
    """
    RSI — bullish when RSI crosses above 50 from below (momentum shift)
    OR bounces from oversold (crosses above 30).
    """
    try:
        return (
            row['rsi_cross_50'] == 1 or
            row['rsi_cross_30'] == 1
        )
    except (KeyError, TypeError):
        return False


def buy_signal_stochastic(row) -> bool:
    """
    Stochastic — bullish when %K crosses above %D
    AND the crossover occurs below 80 (not already overbought).
    """
    try:
        return (
            row['sto_cross_up'] == 1 and
            row['sto_k'] < 80
        )
    except (KeyError, TypeError):
        return False


def buy_signal_cci(row) -> bool:
    """
    CCI — bullish when CCI crosses above -100 (exits oversold)
    OR crosses above 0 (positive momentum confirmation).
    """
    try:
        return (
            row['cci_cross_m100'] == 1 or
            row['cci_cross_0'] == 1
        )
    except (KeyError, TypeError):
        return False


def buy_signal_atr(row) -> bool:
    """
    ATR — quality filter, not a directional signal.
    Returns True when the bar has meaningful size (> 0.5x ATR)
    AND there is no volatility spike (atr_spike == 0).
    Used to confirm other signals have real price action behind them.
    """
    try:
        return (
            row['atr_bar_ratio'] >= 0.5 and
            row['atr_spike'] == 0 and
            row['atr_14'] > 0
        )
    except (KeyError, TypeError):
        return False


def buy_signal_bbwidth(row) -> bool:
    """
    Bollinger Band Width — bullish when squeeze is ending (was in squeeze,
    now expanding) AND price is above the SMA center line.
    """
    try:
        return (
            row['bbd_expanding'] == 1 and
            row['bbd_above_sma'] == 1 and
            row['bbd_pct_b'] > 0.5          # price in upper half of bands
        )
    except (KeyError, TypeError):
        return False


def buy_signal_choppiness(row) -> bool:
    """
    Choppiness Index — regime filter.
    True when CI < 50 (trending or transitioning to trend).
    Suppresses entries in choppy conditions.
    """
    try:
        return (
            pd.notna(row['chp_14']) and
            row['chp_14'] < 50.0
        )
    except (KeyError, TypeError):
        return False


def buy_signal_vwap(row) -> bool:
    """
    VWAP — bullish when price is above VWAP
    OR just crossed above VWAP on this bar.
    """
    try:
        return (
            row['vwp_above'] == 1 and
            row['vwp_distance'] > 0.0
        )
    except (KeyError, TypeError):
        return False


def buy_signal_obv(row) -> bool:
    """
    OBV — bullish when OBV is rising (higher than 3 bars ago)
    AND above its 20-period EMA (uptrend in volume flow)
    AND no bearish divergence.
    """
    try:
        return (
            row['obv_rising'] == 1 and
            row['obv_above_ema'] == 1 and
            row['obv_div_bear'] == 0
        )
    except (KeyError, TypeError):
        return False


def buy_signal_mfi(row) -> bool:
    """
    MFI — bullish when MFI crosses above 50 (money flowing in)
    OR bounces from oversold (crosses above 30).
    """
    try:
        return (
            row['mfi_cross_50'] == 1 or
            row['mfi_bounce'] == 1
        )
    except (KeyError, TypeError):
        return False


# Registry — used to iterate over all signals programmatically
BUY_SIGNAL_FUNCTIONS = {
    'ema':         buy_signal_ema,
    'macd':        buy_signal_macd,
    'adx':         buy_signal_adx,
    'dmi':         buy_signal_dmi,
    'rsi':         buy_signal_rsi,
    'stochastic':  buy_signal_stochastic,
    'cci':         buy_signal_cci,
    'atr':         buy_signal_atr,
    'bbwidth':     buy_signal_bbwidth,
    'choppiness':  buy_signal_choppiness,
    'vwap':        buy_signal_vwap,
    'obv':         buy_signal_obv,
    'mfi':         buy_signal_mfi,
}


# ============================================================
# EXIT BRACKET FUNCTIONS
# Each returns a dict: {'stop_loss': float, 'profit_target': float}
# Computed at entry time based on the entry bar's ATR.
# ============================================================

def exit_bracket_atr(row, stop_mult: float = ATR_STOP_MULT,
                     rr: float = ATR_TARGET_RR) -> dict:
    """
    ATR-based bracket — the primary exit method.
    stop_loss     = entry_price - (atr_14 * stop_mult)
    profit_target = entry_price + (atr_14 * stop_mult * rr)
    """
    entry = row['close']
    atr   = row['atr_14']
    stop  = entry - (atr * stop_mult)
    tgt   = entry + (atr * stop_mult * rr)
    return {'stop_loss': round(stop, 4), 'profit_target': round(tgt, 4)}


def exit_bracket_bb(row) -> dict:
    """
    Bollinger Band bracket.
    stop_loss     = lower Bollinger Band
    profit_target = upper Bollinger Band
    """
    entry = row['close']
    stop  = row['bbd_lower']
    tgt   = row['bbd_upper']
    # Fallback to ATR if bands are NaN
    if pd.isna(stop) or pd.isna(tgt):
        return exit_bracket_atr(row)
    return {'stop_loss': round(stop, 4), 'profit_target': round(tgt, 4)}


def exit_bracket_vwap(row) -> dict:
    """
    VWAP bracket.
    stop_loss     = VWAP level (lose conviction if price falls back below)
    profit_target = VWAP + 2x the distance from VWAP to entry
    """
    entry = row['close']
    vwap  = row['vwp_vwap']
    if pd.isna(vwap) or vwap >= entry:
        return exit_bracket_atr(row)
    dist  = entry - vwap
    stop  = vwap
    tgt   = entry + (dist * ATR_TARGET_RR)
    return {'stop_loss': round(stop, 4), 'profit_target': round(tgt, 4)}


# Registry
EXIT_BRACKET_FUNCTIONS = {
    'atr':  exit_bracket_atr,
    'bb':   exit_bracket_bb,
    'vwap': exit_bracket_vwap,
}


# ============================================================
# COMPOSITE BUY SIGNAL
# AND-combines a user-specified set of indicator signals.
# ============================================================

def composite_buy_signal(row, signal_keys: list) -> bool:
    """
    Returns True only if ALL specified signals are active on this row.

    Parameters
    ----------
    row         : pd.Series — one bar from the extended dataframe
    signal_keys : list of str — keys from BUY_SIGNAL_FUNCTIONS

    Returns
    -------
    bool
    """
    for key in signal_keys:
        fn = BUY_SIGNAL_FUNCTIONS.get(key)
        if fn is None:
            raise ValueError(f"Unknown signal key: '{key}'")
        if not fn(row):
            return False
    return True


def get_signal_detail(row, signal_keys: list) -> dict:
    """Returns per-indicator True/False for logging purposes."""
    return {
        f'sig_{k}': bool(BUY_SIGNAL_FUNCTIONS[k](row))
        for k in signal_keys
    }


# ============================================================
# TRADE SIMULATION
# ============================================================

def simulate_trade(df_day: pd.DataFrame,
                   entry_idx: int,
                   bracket: dict,
                   exit_time_str: str = EXIT_TIME_STR) -> dict:
    """
    Simulates a trade from entry_idx to end of day (or bracket/time exit).

    Parameters
    ----------
    df_day        : DataFrame — all bars for this trading day, sorted by date
    entry_idx     : int — iloc position of the entry bar within df_day
    bracket       : dict with 'stop_loss' and 'profit_target'
    exit_time_str : str — HH:MM for hard time exit

    Returns
    -------
    dict with trade result fields
    """
    entry_bar   = df_day.iloc[entry_idx]
    entry_price = entry_bar['close']
    entry_time  = pd.to_datetime(entry_bar['date'])
    stop_loss   = bracket['stop_loss']
    profit_tgt  = bracket['profit_target']

    exit_time_limit = entry_time.replace(
        hour=int(exit_time_str.split(':')[0]),
        minute=int(exit_time_str.split(':')[1]),
        second=0
    )

    exit_price  = None
    exit_time   = None
    exit_reason = None
    bars_held   = 0

    # Walk forward bar by bar from entry
    for i in range(entry_idx + 1, len(df_day)):
        bar  = df_day.iloc[i]
        btime = pd.to_datetime(bar['date'])
        bars_held += 1

        # Check time-box first
        if btime >= exit_time_limit:
            exit_price  = bar['close']
            exit_time   = btime
            exit_reason = 'time_box'
            break

        # Check stop loss (low of bar touches or breaks stop)
        if bar['low'] <= stop_loss:
            exit_price  = stop_loss          # fill assumed at stop price
            exit_time   = btime
            exit_reason = 'stop_loss'
            break

        # Check profit target (high of bar touches or breaks target)
        if bar['high'] >= profit_tgt:
            exit_price  = profit_tgt         # fill assumed at target price
            exit_time   = btime
            exit_reason = 'profit_target'
            break

    # No exit found before end of data (shouldn't happen with time-box but safety net)
    if exit_price is None:
        last_bar    = df_day.iloc[-1]
        exit_price  = last_bar['close']
        exit_time   = pd.to_datetime(last_bar['date'])
        exit_reason = 'eod_forced'

    pnl_pts   = exit_price - entry_price
    pnl_pct   = (pnl_pts / entry_price) * 100.0
    is_winner = pnl_pts > 0

    return {
        'entry_time':     entry_time,
        'entry_price':    round(entry_price, 4),
        'exit_time':      exit_time,
        'exit_price':     round(exit_price, 4),
        'exit_reason':    exit_reason,
        'stop_loss':      round(stop_loss, 4),
        'profit_target':  round(profit_tgt, 4),
        'bars_held':      bars_held,
        'pnl_pts':        round(pnl_pts, 4),
        'pnl_pct':        round(pnl_pct, 4),
        'is_winner':      is_winner,
        'atr_at_entry':   round(float(entry_bar.get('atr_14', np.nan)), 4),
        'adx_at_entry':   round(float(entry_bar.get('adx_14', np.nan)), 4),
        'rsi_at_entry':   round(float(entry_bar.get('rsi_14', np.nan)), 4),
        'vwap_at_entry':  round(float(entry_bar.get('vwp_vwap', np.nan)), 4),
        'chp_at_entry':   round(float(entry_bar.get('chp_14', np.nan)), 4),
    }


# ============================================================
# MAIN BACKTEST RUNNER
# ============================================================

def run_backtest(df: pd.DataFrame,
                 signal_keys: list,
                 bracket_fn_key: str = 'atr',
                 n_sample: int = N_SAMPLE,
                 seed: int = RANDOM_SEED,
                 exit_time_str: str = EXIT_TIME_STR) -> pd.DataFrame:
    """
    Core backtest loop.

    1. Filters to valid entry bars (after 10 AM, before 3:45 PM, no ATR spike)
    2. Randomly samples n_sample bars
    3. Evaluates composite buy signal on each sampled bar
    4. If signal fires, simulates trade through end of day
    5. Returns DataFrame of all executed trades

    Parameters
    ----------
    df            : extended quotes DataFrame
    signal_keys   : list of indicator keys to AND-combine
    bracket_fn_key: 'atr', 'bb', or 'vwap'
    n_sample      : number of candidate bars to test
    seed          : random seed for reproducibility
    exit_time_str : HH:MM hard exit time

    Returns
    -------
    pd.DataFrame of trade records
    """
    random.seed(seed)
    np.random.seed(seed)

    bracket_fn = EXIT_BRACKET_FUNCTIONS[bracket_fn_key]

    # Build index of valid candidate bars
    valid_mask = (
        (df['ses_after_10'] == 1) &
        (df['ses_before_345'] == 1) &
        (df['atr_spike'] == 0) &
        (df['atr_14'].notna()) &
        (df['rsi_14'].notna()) &
        (df['chp_14'].notna())
    )
    valid_indices = df.index[valid_mask].tolist()

    if len(valid_indices) == 0:
        raise ValueError("No valid candidate bars found. Check date range and data.")

    n_sample = min(n_sample, len(valid_indices))
    sampled  = random.sample(valid_indices, n_sample)
    sampled.sort()

    print(f"  Valid candidate bars : {len(valid_indices):,}")
    print(f"  Sampled for testing  : {n_sample:,}")

    # Group df by trading date for efficient day-slice lookup
    df = df.copy()
    df['_date_key'] = df['date'].dt.date
    day_groups = {date: group.reset_index(drop=True)
                  for date, group in df.groupby('_date_key')}

    trades = []
    signals_tested  = 0
    signals_fired   = 0

    for global_idx in sampled:
        row = df.loc[global_idx]
        signals_tested += 1

        # Evaluate composite buy signal
        if not composite_buy_signal(row, signal_keys):
            continue

        signals_fired += 1

        # Get the day slice and find this bar's position within it
        trade_date = row['_date_key']
        df_day     = day_groups.get(trade_date)
        if df_day is None:
            continue

        # Find entry position within the day slice
        entry_dt   = row['date']
        match      = df_day[df_day['date'] == entry_dt]
        if match.empty:
            continue
        entry_iloc = match.index[0]

        # Compute bracket
        bracket = bracket_fn(row)

        # Simulate trade
        result = simulate_trade(df_day, entry_iloc, bracket, exit_time_str)

        # Attach metadata
        signal_detail = get_signal_detail(row, signal_keys)
        trade_record  = {
            'trade_date':      trade_date,
            'signal_keys':     '+'.join(signal_keys),
            'bracket_method':  bracket_fn_key,
            **result,
            **signal_detail,
        }
        trades.append(trade_record)

    print(f"  Signals tested       : {signals_tested:,}")
    print(f"  Signals fired        : {signals_fired:,}")
    print(f"  Trades executed      : {len(trades):,}")

    df.drop(columns=['_date_key'], inplace=True)

    if not trades:
        return pd.DataFrame()

    return pd.DataFrame(trades)


# ============================================================
# PERFORMANCE REPORT
# ============================================================

def build_report(trades_df: pd.DataFrame,
                 signal_keys: list,
                 bracket_fn_key: str,
                 date_start: str,
                 date_end: str,
                 n_sample: int) -> str:
    """
    Generates a detailed Markdown performance report from the trades DataFrame.
    """
    if trades_df.empty:
        return "# Performance Report\n\nNo trades executed.\n"

    total_trades   = len(trades_df)
    winners        = trades_df[trades_df['is_winner']]
    losers         = trades_df[~trades_df['is_winner']]
    win_count      = len(winners)
    loss_count     = len(losers)
    win_rate       = win_count / total_trades * 100 if total_trades > 0 else 0.0

    total_pnl      = trades_df['pnl_pts'].sum()
    avg_pnl        = trades_df['pnl_pts'].mean()
    avg_win        = winners['pnl_pts'].mean() if win_count > 0 else 0.0
    avg_loss       = losers['pnl_pts'].mean()  if loss_count > 0 else 0.0
    gross_profit   = winners['pnl_pts'].sum()  if win_count > 0 else 0.0
    gross_loss     = losers['pnl_pts'].sum()   if loss_count > 0 else 0.0
    profit_factor  = abs(gross_profit / gross_loss) if gross_loss != 0 else float('inf')

    payoff_ratio   = abs(avg_win / avg_loss) if avg_loss != 0 else float('inf')

    avg_bars       = trades_df['bars_held'].mean()
    max_win        = trades_df['pnl_pts'].max()
    max_loss       = trades_df['pnl_pts'].min()

    # Sharpe approximation (daily PnL)
    trades_df2     = trades_df.copy()
    trades_df2['trade_date'] = pd.to_datetime(trades_df2['trade_date'])
    daily_pnl      = trades_df2.groupby('trade_date')['pnl_pts'].sum()
    sharpe         = (daily_pnl.mean() / daily_pnl.std() * np.sqrt(252)
                      if daily_pnl.std() > 0 else 0.0)

    # Drawdown
    cumulative     = trades_df['pnl_pts'].cumsum()
    rolling_max    = cumulative.cummax()
    drawdown       = cumulative - rolling_max
    max_drawdown   = drawdown.min()

    # Exit reason breakdown
    exit_counts    = trades_df['exit_reason'].value_counts()

    # Per-signal fire rate
    sig_cols       = [c for c in trades_df.columns if c.startswith('sig_')]
    sig_rates      = {c: trades_df[c].mean() * 100 for c in sig_cols}

    # Best and worst trades
    best_trade     = trades_df.loc[trades_df['pnl_pts'].idxmax()]
    worst_trade    = trades_df.loc[trades_df['pnl_pts'].idxmin()]

    # Monthly breakdown
    trades_df2['month'] = trades_df2['trade_date'].dt.to_period('M')
    monthly = trades_df2.groupby('month').agg(
        trades=('pnl_pts', 'count'),
        pnl=('pnl_pts', 'sum'),
        win_rate=('is_winner', 'mean')
    ).reset_index()

    # ---- Build Markdown ----
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    lines = []
    lines.append(f"# AAPL Intraday Backtest — Performance Report")
    lines.append(f"\n_Generated: {now}_\n")

    lines.append("## Configuration\n")
    lines.append(f"| Parameter | Value |")
    lines.append(f"|---|---|")
    lines.append(f"| Date Range | {date_start} → {date_end} |")
    lines.append(f"| Instrument | AAPL (1-minute bars) |")
    lines.append(f"| Entry Window | After 10:00 AM |")
    lines.append(f"| Exit Time-Box | 3:45 PM |")
    lines.append(f"| Candidate Bars Sampled | {n_sample:,} |")
    lines.append(f"| Signal Combination | {' AND '.join(signal_keys).upper()} |")
    lines.append(f"| Bracket Method | {bracket_fn_key.upper()} |")
    lines.append(f"| ATR Stop Multiplier | {ATR_STOP_MULT}× |")
    lines.append(f"| Reward:Risk Ratio | {ATR_TARGET_RR}:1 |")
    lines.append("")

    lines.append("## Summary Statistics\n")
    lines.append(f"| Metric | Value |")
    lines.append(f"|---|---|")
    lines.append(f"| Total Trades | {total_trades:,} |")
    lines.append(f"| Winning Trades | {win_count:,} ({win_rate:.1f}%) |")
    lines.append(f"| Losing Trades | {loss_count:,} ({100-win_rate:.1f}%) |")
    lines.append(f"| Win Rate | **{win_rate:.1f}%** |")
    lines.append(f"| Total P&L (pts) | **{total_pnl:+.4f}** |")
    lines.append(f"| Average P&L per Trade | {avg_pnl:+.4f} |")
    lines.append(f"| Average Win | {avg_win:+.4f} |")
    lines.append(f"| Average Loss | {avg_loss:+.4f} |")
    lines.append(f"| Payoff Ratio (Avg Win / Avg Loss) | {payoff_ratio:.2f} |")
    lines.append(f"| Profit Factor (Gross W / Gross L) | {profit_factor:.2f} |")
    lines.append(f"| Max Single Win | {max_win:+.4f} |")
    lines.append(f"| Max Single Loss | {max_loss:+.4f} |")
    lines.append(f"| Max Drawdown (cumulative pts) | {max_drawdown:+.4f} |")
    lines.append(f"| Annualised Sharpe (daily PnL) | {sharpe:.2f} |")
    lines.append(f"| Average Bars Held | {avg_bars:.1f} mins |")
    lines.append("")

    lines.append("## Signal Fire Rate\n")
    lines.append("Percentage of executed trades where each indicator was True at entry:\n")
    lines.append(f"| Indicator | Fire Rate |")
    lines.append(f"|---|---|")
    for col, rate in sorted(sig_rates.items()):
        ind = col.replace('sig_', '').upper()
        lines.append(f"| {ind} | {rate:.1f}% |")
    lines.append("")

    lines.append("## Exit Reason Breakdown\n")
    lines.append(f"| Exit Reason | Count | % of Trades |")
    lines.append(f"|---|---|---|")
    for reason, count in exit_counts.items():
        pct = count / total_trades * 100
        lines.append(f"| {reason} | {count:,} | {pct:.1f}% |")
    lines.append("")

    lines.append("## Exit Reason vs Outcome\n")
    exit_outcome = trades_df.groupby('exit_reason').agg(
        count=('pnl_pts', 'count'),
        avg_pnl=('pnl_pts', 'mean'),
        win_rate=('is_winner', 'mean')
    ).reset_index()
    lines.append(f"| Exit Reason | Count | Avg P&L | Win Rate |")
    lines.append(f"|---|---|---|---|")
    for _, r in exit_outcome.iterrows():
        lines.append(
            f"| {r['exit_reason']} | {int(r['count']):,} | "
            f"{r['avg_pnl']:+.4f} | {r['win_rate']*100:.1f}% |"
        )
    lines.append("")

    lines.append("## Monthly Performance\n")
    lines.append(f"| Month | Trades | Total P&L | Win Rate |")
    lines.append(f"|---|---|---|---|")
    for _, r in monthly.iterrows():
        lines.append(
            f"| {r['month']} | {int(r['trades']):,} | "
            f"{r['pnl']:+.4f} | {r['win_rate']*100:.1f}% |"
        )
    lines.append("")

    lines.append("## Best and Worst Trades\n")
    lines.append("**Best Trade:**\n")
    lines.append(f"| Field | Value |")
    lines.append(f"|---|---|")
    for field in ['trade_date', 'entry_time', 'entry_price', 'exit_time',
                  'exit_price', 'exit_reason', 'pnl_pts', 'pnl_pct',
                  'bars_held', 'atr_at_entry', 'rsi_at_entry']:
        lines.append(f"| {field} | {best_trade[field]} |")
    lines.append("")

    lines.append("**Worst Trade:**\n")
    lines.append(f"| Field | Value |")
    lines.append(f"|---|---|")
    for field in ['trade_date', 'entry_time', 'entry_price', 'exit_time',
                  'exit_price', 'exit_reason', 'pnl_pts', 'pnl_pct',
                  'bars_held', 'atr_at_entry', 'rsi_at_entry']:
        lines.append(f"| {field} | {worst_trade[field]} |")
    lines.append("")

    lines.append("## Interpretation Notes\n")
    lines.append(
        "- **Profit Factor > 1.5** is generally considered viable; > 2.0 is strong.\n"
        "- **Win Rate alone is insufficient** — a 40% win rate with 2:1 payoff "
        "ratio is profitable.\n"
        "- **Sharpe > 1.0** annualised suggests the strategy generates returns "
        "proportional to its risk.\n"
        "- **Time-box exits** that are profitable suggest the entry signal is valid "
        "but the bracket is too tight.\n"
        "- **High stop-loss rate with negative avg PnL** suggests the stop multiplier "
        "should be widened.\n"
        "- These results are **in-sample** and require walk-forward validation before "
        "drawing conclusions.\n"
    )

    return '\n'.join(lines)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == '__main__':

    # ----------------------------------------------------------
    # 1. Load extended data
    # ----------------------------------------------------------
    print(f"\n{'='*60}")
    print(f"AAPL Intraday Backtest")
    print(f"{'='*60}")
    print(f"\nLoading {DATA_PATH} ...")

    df = pd.read_csv(DATA_PATH, parse_dates=['date'], low_memory=False)
    df = df.sort_values('date').reset_index(drop=True)
    print(f"  Loaded: {df.shape[0]:,} rows × {df.shape[1]} columns")

    # ----------------------------------------------------------
    # 2. Subset to date range
    # ----------------------------------------------------------
    mask = (
        (df['date'] >= pd.Timestamp(DATE_START)) &
        (df['date'] <= pd.Timestamp(DATE_END))
    )
    df = df[mask].reset_index(drop=True)
    print(f"  After date filter ({DATE_START} → {DATE_END}): {len(df):,} rows")

    if len(df) == 0:
        raise SystemExit("ERROR: No data in specified date range.")

    # ----------------------------------------------------------
    # 3. Define model configurations to run
    #    Each tuple: (signal_key_list, bracket_fn_key, description)
    # ----------------------------------------------------------
    models = [
        # Model 1 — Trend-following: ADX + EMA + ATR + VWAP
        (['adx', 'ema', 'atr', 'vwap'],       'atr',  'ADX+EMA+ATR+VWAP (ATR bracket)'),

        # Model 2 — MACD momentum: MACD + BB Width + OBV
        (['macd', 'bbwidth', 'obv'],           'bb',   'MACD+BBWidth+OBV (BB bracket)'),

        # Model 3 — Mean-reversion: Choppiness + RSI + BB Width + MFI
        (['choppiness', 'rsi', 'bbwidth', 'mfi'], 'atr', 'Chop+RSI+BBW+MFI (ATR bracket)'),

        # Model 4 — Mean-reversion: Choppiness + Stochastic + ATR + MFI
        (['choppiness', 'stochastic', 'atr', 'mfi'], 'atr', 'Chop+Stoch+ATR+MFI (ATR bracket)'),

        # Model 5 — Momentum sweep: RSI + MACD + VWAP
        (['rsi', 'macd', 'vwap'],              'vwap', 'RSI+MACD+VWAP (VWAP bracket)'),
    ]

    all_trades = []

    # ----------------------------------------------------------
    # 4. Run each model
    # ----------------------------------------------------------
    for signal_keys, bracket_key, description in models:
        print(f"\n{'─'*60}")
        print(f"Model: {description}")
        print(f"{'─'*60}")

        trades_df = run_backtest(
            df             = df,
            signal_keys    = signal_keys,
            bracket_fn_key = bracket_key,
            n_sample       = N_SAMPLE,
            seed           = RANDOM_SEED,
            exit_time_str  = EXIT_TIME_STR,
        )

        if trades_df.empty:
            print("  No trades executed for this model.")
            continue

        trades_df['model_description'] = description
        all_trades.append(trades_df)

        # Quick inline summary
        wr  = trades_df['is_winner'].mean() * 100
        pnl = trades_df['pnl_pts'].sum()
        pf_num = trades_df.loc[trades_df['is_winner'],  'pnl_pts'].sum()
        pf_den = trades_df.loc[~trades_df['is_winner'], 'pnl_pts'].sum()
        pf  = abs(pf_num / pf_den) if pf_den != 0 else float('inf')
        print(f"  Win Rate: {wr:.1f}%  |  Total PnL: {pnl:+.4f}  |  Profit Factor: {pf:.2f}")

    # ----------------------------------------------------------
    # 5. Combine and save all trades
    # ----------------------------------------------------------
    if all_trades:
        combined = pd.concat(all_trades, ignore_index=True)
        combined.to_csv(OUTPUT_TRADES, index=False)
        print(f"\nTrades saved → {OUTPUT_TRADES}  ({len(combined):,} records)")
    else:
        combined = pd.DataFrame()
        print("\nNo trades to save.")

    # ----------------------------------------------------------
    # 6. Generate performance report for the first (primary) model
    # ----------------------------------------------------------
    primary_trades   = all_trades[0] if all_trades else pd.DataFrame()
    primary_signals  = models[0][0]
    primary_bracket  = models[0][1]

    report_md = build_report(
        trades_df     = primary_trades,
        signal_keys   = primary_signals,
        bracket_fn_key= primary_bracket,
        date_start    = DATE_START,
        date_end      = DATE_END,
        n_sample      = N_SAMPLE,
    )

    OUTPUT_REPORT.write_text(report_md, encoding='utf-8')
    print(f"Report saved  → {OUTPUT_REPORT}")

    # ----------------------------------------------------------
    # 7. Cross-model comparison summary
    # ----------------------------------------------------------
    if len(all_trades) > 1:
        print(f"\n{'='*60}")
        print("CROSS-MODEL COMPARISON")
        print(f"{'='*60}")
        print(f"{'Model':<45} {'Trades':>7} {'WinRate':>8} {'TotalPnL':>10} {'PF':>6}")
        print(f"{'─'*45} {'─'*7} {'─'*8} {'─'*10} {'─'*6}")
        for _, desc, tdf in zip(models, [m[2] for m in models], all_trades):
            wr  = tdf['is_winner'].mean() * 100
            pnl = tdf['pnl_pts'].sum()
            w   = tdf.loc[tdf['is_winner'],  'pnl_pts'].sum()
            l   = tdf.loc[~tdf['is_winner'], 'pnl_pts'].sum()
            pf  = abs(w / l) if l != 0 else float('inf')
            print(f"{desc:<45} {len(tdf):>7,} {wr:>7.1f}% {pnl:>+10.4f} {pf:>6.2f}")

    print(f"\n{'='*60}")
    print("Done.")
    print(f"{'='*60}\n")