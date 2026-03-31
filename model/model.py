"""
model.py

Intraday AAPL backtesting engine — 1,260 indicator combinations.

Pipeline
────────
1.  Signals CSV  : loads sq_AAPL_signals.csv if present; otherwise reads the
                   extended CSV and adds 48 bsig/ssig columns then saves.

2.  Fixed sample : draws 10,000 valid entry bars once (same seed for all models).

3.  Combinations : 7 trend x 10 momentum x 3 volatility x 6 volume = 1,260.
                   Skips 39 where the same indicator appears in two slots
                   (macd in trend+momentum, frc in momentum+volume).
                   Valid models: 1,221.

4.  Per model    : vectorised composite buy signal on the 10,000 sample rows.
                   For each firing bar -> forward-simulate through end of day.
                   Exit: stop-loss | profit-target | any model sell signal | 3:45 PM.

5.  Position     : $10,000 per trade, floor(10000/entry_price) shares, $2 commission.

6.  Output       : ../reports/{batch_no}_summary.csv
                   ../reports/{batch_no}_trades.csv
"""

from __future__ import annotations

import datetime
import random
import sys
import warnings
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

# Project-local imports
sys.path.insert(0, str(Path(__file__).parent))
from indicator_signals import add_buy_signals, add_sell_signals

warnings.filterwarnings('ignore')

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

EXTENDED_CSV  = Path('../data/stock/sq_AAPL_extended.csv')
SIGNALS_CSV   = Path('../data/stock/sq_AAPL_signals.csv')
REPORTS_DIR   = Path('../reports')

DATE_START    = '2023-01-01'
DATE_END      = '2024-12-31'

N_SAMPLE      = 10_000
RANDOM_SEED   = 41

TRADE_CAPITAL = 10_000.0          # dollars per trade
COMMISSION    = 2.00              # round-trip commission per trade ($)
ATR_STOP_MULT = 1.5               # stop  = entry - ATR * 1.5
ATR_TARGET_RR = 2.0               # target = entry + ATR * 1.5 * 2.0
EXIT_MINUTE   = 15 * 60 + 45      # 3:45 PM in minutes-since-midnight

TOP_N_DETAIL  = 20                # models shown in the detailed report

BATCH_FILE    = Path('batch.txt')  # tracks batch number across runs

# ──────────────────────────────────────────────────────────────────────────────
# Indicator categories
# ──────────────────────────────────────────────────────────────────────────────
TREND      = ['ema', 'macd', 'adx', 'sar', 'don', 'arn', 'vtx']
MOMENTUM   = ['rsi', 'sto', 'cci', 'cmo', 'tsi', 'roc', 'frc', 'srsi', 'rmi', 'macd']
VOLATILITY = ['atr', 'bbd', 'chp']
VOLUME     = ['vwap', 'obv', 'mfi', 'klg', 'frc', 'vrc']


# ══════════════════════════════════════════════════════════════════════════════
# BATCH COUNTER
# ══════════════════════════════════════════════════════════════════════════════

def read_increment_batch() -> int:
    """
    Reads batch.txt (creates it at 0 if missing), increments by 1,
    writes the new value back, and returns it.
    """
    val = int(BATCH_FILE.read_text().strip()) if BATCH_FILE.exists() else 0
    batch_no = val + 1
    BATCH_FILE.write_text(str(batch_no))
    return batch_no


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 -- SIGNALS CSV  (load or build)
# ══════════════════════════════════════════════════════════════════════════════

_signals_cache: pd.DataFrame | None = None


def load_or_build_signals() -> pd.DataFrame:
    """
    Returns the signals DataFrame.
    Cached in memory after the first load — subsequent calls return instantly.
    """
    global _signals_cache
    if _signals_cache is not None:
        print("[signals] Using in-memory cache.")
        return _signals_cache

    if SIGNALS_CSV.exists():
        print(f"[signals] Loading cached {SIGNALS_CSV} ...")
        df = pd.read_csv(SIGNALS_CSV, parse_dates=['date'], low_memory=False)
        print(f"  Loaded  : {df.shape[0]:,} rows x {df.shape[1]} columns")
        _signals_cache = df
        return df

    print(f"[signals] {SIGNALS_CSV} not found -- building from {EXTENDED_CSV} ...")
    if not EXTENDED_CSV.exists():
        raise FileNotFoundError(
            f"Extended CSV not found: {EXTENDED_CSV}\n"
            "Run 1_compute_indicators.py first."
        )

    df = pd.read_csv(EXTENDED_CSV, parse_dates=['date'], low_memory=False)
    print(f"  Extended loaded : {df.shape[0]:,} rows x {df.shape[1]} columns")

    print("  Adding buy signals ...")
    df = add_buy_signals(df)
    print("  Adding sell signals ...")
    df = add_sell_signals(df)

    SIGNALS_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(SIGNALS_CSV, index=False)
    print(f"  Saved   : {SIGNALS_CSV}  ({df.shape[1]} columns)")
    _signals_cache = df
    return df


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 -- FIXED SAMPLE
# ══════════════════════════════════════════════════════════════════════════════

def draw_sample(df: pd.DataFrame) -> list:
    """
    Returns a fixed list of N_SAMPLE valid entry bar indices.
    Valid = after 10 AM, before 3:45 PM, no ATR spike, key indicators available.
    Same indices reused for all 1,221 models.
    """
    valid_mask = (
        (df['ses_after_10']   == 1) &
        (df['ses_before_345'] == 1) &
        (df['atr_spike']      == 0) &
        df['atr_14'].notna() &
        df['rsi_14'].notna() &
        df['chp_14'].notna()
    )
    valid_idx = df.index[valid_mask].tolist()
    n = min(N_SAMPLE, len(valid_idx))
    random.seed(RANDOM_SEED)
    sample = random.sample(valid_idx, n)
    sample.sort()
    print(f"[sample]  Valid bars: {len(valid_idx):,}  ->  sampled: {n:,}")
    return sample


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 -- COMBINATION GENERATOR
# ══════════════════════════════════════════════════════════════════════════════

def generate_combos() -> list:
    """
    Returns all valid (trend, momentum, volatility, volume) 4-tuples.
    Skips any combination where the same indicator key appears in multiple slots.
    Total = 1,260 - 39 duplicates = 1,221 valid combinations.
    """
    combos = []
    for t, m, v, vol in product(TREND, MOMENTUM, VOLATILITY, VOLUME):
        if len({t, m, v, vol}) == 4:   # all four slots are distinct
            combos.append((t, m, v, vol))
    return combos


# ══════════════════════════════════════════════════════════════════════════════
# STEP 4 -- TRADE SIMULATION UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

def compute_bracket(entry_price: float, atr: float):
    """
    Returns (stop_loss, profit_target) based on ATR.
    Returns None if ATR is invalid.
    """
    if np.isnan(atr) or atr <= 0:
        return None
    stop   = entry_price - atr * ATR_STOP_MULT
    target = entry_price + atr * ATR_STOP_MULT * ATR_TARGET_RR
    return round(stop, 4), round(target, 4)


def compute_shares(entry_price: float) -> int:
    """Floor integer shares purchasable with TRADE_CAPITAL."""
    if entry_price <= 0:
        return 0
    return int(TRADE_CAPITAL / entry_price)


def simulate_trade(
    df_day:      pd.DataFrame,
    entry_iloc:  int,
    stop:        float,
    target:      float,
    sell_cols:   list,
    entry_price: float,
    shares:      int,
) -> dict:
    """
    Walks forward from entry_iloc+1 through end of day (or 3:45 PM).
    Returns a dict of trade result fields.

    Exit priority:
      1. Time box    -- bar's ses_minute >= EXIT_MINUTE -> exit at close
      2. Stop-loss   -- bar's low  <= stop   -> exit at stop price
      3. Profit tgt  -- bar's high >= target -> exit at target price
      4. Sell signal -- any sell col is 1    -> exit at close
    """
    exit_price  = None
    exit_reason = None
    exit_bar    = None
    bars_held   = 0

    for i in range(entry_iloc + 1, len(df_day)):
        bar = df_day.iloc[i]
        bars_held += 1

        # 1. Time box
        if int(bar['ses_minute']) >= EXIT_MINUTE:
            exit_price  = float(bar['close'])
            exit_reason = 'time_box'
            exit_bar    = bar
            break

        # 2. Stop-loss
        if float(bar['low']) <= stop:
            exit_price  = stop
            exit_reason = 'stop_loss'
            exit_bar    = bar
            break

        # 3. Profit target
        if float(bar['high']) >= target:
            exit_price  = target
            exit_reason = 'profit_target'
            exit_bar    = bar
            break

        # 4. Sell signals from this model's 4 indicators
        for sc in sell_cols:
            if int(bar.get(sc, 0)):
                exit_price  = float(bar['close'])
                exit_reason = f'sell_{sc[5:]}'   # strip 'ssig_' prefix
                exit_bar    = bar
                break
        if exit_price is not None:
            break

    # Safety net -- no exit found before end of day data
    if exit_price is None:
        last        = df_day.iloc[-1]
        exit_price  = float(last['close'])
        exit_reason = 'eod_forced'
        exit_bar    = last

    exit_time = str(exit_bar['date']) if exit_bar is not None else ''

    cost       = shares * entry_price
    proceeds   = shares * exit_price
    pnl_dollar = proceeds - cost - COMMISSION
    pnl_pct    = (pnl_dollar / cost * 100) if cost > 0 else 0.0

    return {
        'exit_price':    round(exit_price, 4),
        'exit_time':     exit_time,
        'exit_reason':   exit_reason,
        'bars_held':     bars_held,
        'shares':        shares,
        'cost':          round(cost, 2),
        'proceeds':      round(proceeds, 2),
        'pnl_dollar':    round(pnl_dollar, 2),
        'pnl_pct':       round(pnl_pct, 4),
        'is_winner':     pnl_dollar > 0,
    }


# ══════════════════════════════════════════════════════════════════════════════
# STEP 5 -- MAIN BACKTEST LOOP
# ══════════════════════════════════════════════════════════════════════════════

def run_all_models(
    df:          pd.DataFrame,
    sample_idx:  list,
    day_dict:    dict,
    day_pos_map: dict,
    batch_no:    int,
):
    """
    Iterates over all 1,221 valid combinations.
    Returns (summary_df, trades_df).
    """
    combos    = generate_combos()
    n_combos  = len(combos)
    sample_df = df.loc[sample_idx].copy()

    all_trades   = []
    summary_rows = []

    print(f"\n[B{batch_no}][backtest] Running {n_combos:,} model combinations ...")
    print(f"[B{batch_no}]           Sample size : {len(sample_idx):,} bars")

    for model_id, (t, m, v, vol) in enumerate(combos, 1):

        # -- Vectorised composite buy signal ----------------------------------
        buy_cols  = [f'bsig_{t}', f'bsig_{m}', f'bsig_{v}', f'bsig_{vol}']
        sell_cols = [f'ssig_{t}', f'ssig_{m}', f'ssig_{v}', f'ssig_{vol}']

        composite = (
            sample_df[buy_cols[0]].astype(bool) &
            sample_df[buy_cols[1]].astype(bool) &
            sample_df[buy_cols[2]].astype(bool) &
            sample_df[buy_cols[3]].astype(bool)
        )
        fired_idx = sample_df.index[composite].tolist()

        model_trades = []
        trade_no     = 0

        for idx in fired_idx:
            row         = df.loc[idx]
            entry_price = float(row['close'])
            atr_val     = row['atr_14']
            atr         = float(atr_val) if pd.notna(atr_val) else 0.0
            bracket     = compute_bracket(entry_price, atr)
            if bracket is None:
                continue
            stop, target = bracket
            shares = compute_shares(entry_price)
            if shares == 0:
                continue

            trade_date = row['fnd_trade_date']
            day_pos    = day_pos_map.get(idx)
            df_day     = day_dict.get(trade_date)
            if df_day is None or day_pos is None:
                continue

            result = simulate_trade(
                df_day, day_pos, stop, target,
                sell_cols, entry_price, shares
            )

            trade_no += 1
            rsi_val  = row.get('rsi_14', np.nan)
            adx_val  = row.get('adx_14', np.nan)
            vwap_val = row.get('vwp_vwap', np.nan)

            trade_record = {
                'batch_no':      batch_no,
                'model_no':      model_id,
                'trade_no':      trade_no,
                'trend':         t,
                'momentum':      m,
                'volatility':    v,
                'volume':        vol,
                'trade_date':    str(trade_date),
                'entry_time':    str(row['date']),
                'entry_price':   round(entry_price, 4),
                'stop_loss':     stop,
                'profit_target': target,
                'atr_at_entry':  round(atr, 4),
                'rsi_at_entry':  round(float(rsi_val)  if pd.notna(rsi_val)  else np.nan, 4),
                'adx_at_entry':  round(float(adx_val)  if pd.notna(adx_val)  else np.nan, 4),
                'vwap_at_entry': round(float(vwap_val) if pd.notna(vwap_val) else np.nan, 4),
                **result,
            }
            model_trades.append(trade_record)
            all_trades.append(trade_record)

        # -- Model-level statistics -------------------------------------------
        n_trades = len(model_trades)

        if n_trades == 0:
            summary_rows.append({
                'batch_no': batch_no,
                'model_id': model_id,
                'trend': t, 'momentum': m,
                'volatility': v, 'volume': vol,
                'trades': 0, 'win_rate': 0.0,
                'avg_entry': 0.0, 'avg_exit': 0.0,
                'avg_duration_bars': 0.0,
                'total_pnl': 0.0, 'avg_pnl': 0.0,
                'profit_factor': 0.0, 'sharpe': 0.0,
                'max_drawdown': 0.0,
            })
            continue

        tdf   = pd.DataFrame(model_trades)
        wins  = tdf.loc[tdf['is_winner']]
        loses = tdf.loc[~tdf['is_winner']]

        gross_w  = wins['pnl_dollar'].sum()  if len(wins)  > 0 else 0.0
        gross_l  = loses['pnl_dollar'].sum() if len(loses) > 0 else 0.0
        pf       = abs(gross_w / gross_l) if gross_l < 0 else float('inf')

        cum      = tdf['pnl_dollar'].cumsum()
        drawdown = (cum - cum.cummax()).min()

        tdf2 = tdf.copy()
        tdf2['_dt'] = pd.to_datetime(tdf2['entry_time']).dt.date
        daily_pnl = tdf2.groupby('_dt')['pnl_dollar'].sum()
        sharpe = (
            (daily_pnl.mean() / daily_pnl.std() * np.sqrt(252))
            if daily_pnl.std() > 0 else 0.0
        )

        summary_rows.append({
            'batch_no':          batch_no,
            'model_id':          model_id,
            'trend':             t,
            'momentum':          m,
            'volatility':        v,
            'volume':            vol,
            'trades':            n_trades,
            'win_rate':          round(tdf['is_winner'].mean() * 100, 1),
            'avg_entry':         round(tdf['entry_price'].mean(), 2),
            'avg_exit':          round(tdf['exit_price'].mean(), 2),
            'avg_duration_bars': round(tdf['bars_held'].mean(), 1),
            'total_pnl':         round(tdf['pnl_dollar'].sum(), 2),
            'avg_pnl':           round(tdf['pnl_dollar'].mean(), 2),
            'profit_factor':     round(pf, 3),
            'sharpe':            round(sharpe, 3),
            'max_drawdown':      round(drawdown, 2),
        })

        # -- Progress every 100 models ----------------------------------------
        if model_id % 100 == 0 or model_id == n_combos:
            print(
                f"  [B{batch_no}|M{model_id:>5}/{n_combos}]  "
                f"trades so far: {len(all_trades):,}"
            )

    print(f"\n[B{batch_no}][backtest] Complete.  Total trades: {len(all_trades):,}")
    return pd.DataFrame(summary_rows), pd.DataFrame(all_trades)


# ══════════════════════════════════════════════════════════════════════════════
# STEP 6 -- REPORTS (CSV)
# ══════════════════════════════════════════════════════════════════════════════

def write_summary_csv(
    summary_df: pd.DataFrame,
    batch_no:   int,
) -> Path:
    """
    Writes {batch_no}_summary.csv — one row per model with aggregated stats.
    Columns: batch_no, model_id, trend, momentum, volatility, volume,
             number_of_trades, win_rate, avg_entry_price, avg_exit_price,
             avg_stop_loss, avg_profit_target, avg_atr_at_entry,
             avg_rsi_at_entry, avg_adx_at_entry, avg_vwap_at_entry,
             avg_bars_held, avg_shares, total_cost, total_proceeds,
             total_pnl, avg_pnl, avg_pnl_pct, top_exit_reason,
             profit_factor, sharpe, max_drawdown
    """
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f'{batch_no}_summary.csv'

    out = summary_df[summary_df['trades'] > 0].rename(columns={
        'trades':            'number_of_trades',
        'avg_entry':         'avg_entry_price',
        'avg_exit':          'avg_exit_price',
        'avg_duration_bars': 'avg_bars_held',
        'total_pnl':         'total_pnl',
        'avg_pnl':           'avg_pnl',
    })

    out.to_csv(path, index=False)
    print(f"[B{batch_no}][report]  Summary  -> {path}  ({len(out):,} rows)")
    return path


def write_trades_csv(
    trades_df: pd.DataFrame,
    batch_no:  int,
) -> Path:
    """
    Writes {batch_no}_trades.csv — one row per executed trade.
    Key columns: batch_no, model_no, trade_no, plus all entry/exit/performance fields.
    """
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f'{batch_no}_trades.csv'

    col_order = [
        'batch_no', 'model_no', 'trade_no',
        'trend', 'momentum', 'volatility', 'volume',
        'trade_date', 'entry_time', 'exit_time', 'entry_price',
        'stop_loss', 'profit_target',
        'atr_at_entry', 'rsi_at_entry', 'adx_at_entry', 'vwap_at_entry',
        'exit_price', 'exit_reason', 'bars_held',
        'shares', 'cost', 'proceeds',
        'pnl_dollar', 'pnl_pct', 'is_winner',
    ]
    # keep only columns that exist (guards against empty trades_df)
    cols = [c for c in col_order if c in trades_df.columns]
    trades_df[cols].to_csv(path, index=False)
    print(f"[B{batch_no}][report]  Trades   -> {path}  ({len(trades_df):,} records)")
    return path


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def run_model_set(batch_no: int):
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"\n{'='*60}")
    print(f"[B{batch_no}]  AAPL Intraday Backtest  |  start at {ts}")
    print(f"[B{batch_no}]  Seed: {RANDOM_SEED}")
    print(f"{'='*60}\n")

    # 1. Load / build signals CSV
    df = load_or_build_signals()

    # 2. Filter date range
    mask = (
        (df['date'] >= pd.Timestamp(DATE_START)) &
        (df['date'] <= pd.Timestamp(DATE_END))
    )
    df = df[mask].reset_index(drop=True)
    print(f"[B{batch_no}][filter]  {DATE_START} -> {DATE_END}: {len(df):,} rows")
    if len(df) == 0:
        sys.exit(f"[B{batch_no}] ERROR: No data in the specified date range.")

    # 3. Fixed sample
    sample_idx = draw_sample(df)

    # 4. Build day-level structures once (reused across all 1,221 models)
    print(f"[B{batch_no}][index]   Building day index ...")
    df = df.copy()
    df['_day_pos'] = df.groupby('fnd_trade_date').cumcount()

    day_dict    = {}
    day_pos_map = {}

    for date, group in df.groupby('fnd_trade_date'):
        grp_reset = group.reset_index(drop=True)
        day_dict[date] = grp_reset
        for pos, global_idx in enumerate(group.index):
            day_pos_map[global_idx] = pos

    print(f"[B{batch_no}]          Trading days indexed: {len(day_dict):,}")

    # 5. Run all models
    summary_df, trades_df = run_all_models(
        df, sample_idx, day_dict, day_pos_map, batch_no
    )

    # 6. Write CSV reports
    write_summary_csv(summary_df, batch_no)
    write_trades_csv(trades_df, batch_no)

    # 7. Console summary
    n_trades   = len(trades_df)
    sdf_active = summary_df[summary_df['trades'] > 0]
    print(f"\n{'='*60}")
    print(f"[B{batch_no}]  RUN COMPLETE")
    print(f"{'='*60}")
    print(f"[B{batch_no}]  Models run       : {len(summary_df):,}")
    print(f"[B{batch_no}]  Models w/ trades : {len(sdf_active):,}")
    print(f"[B{batch_no}]  Total trades     : {n_trades:,}")

    if not sdf_active.empty:
        best  = sdf_active.loc[sdf_active['total_pnl'].idxmax()]
        worst = sdf_active.loc[sdf_active['total_pnl'].idxmin()]
        print(
            f"[B{batch_no}]  Best  model : [B{batch_no}|M{int(best['model_id'])}] "
            f"{best['trend'].upper()}+{best['momentum'].upper()}+"
            f"{best['volatility'].upper()}+{best['volume'].upper()} "
            f"  P&L=${best['total_pnl']:,.2f}  Sharpe={best['sharpe']:.2f}"
        )
        print(
            f"[B{batch_no}]  Worst model : [B{batch_no}|M{int(worst['model_id'])}] "
            f"{worst['trend'].upper()}+{worst['momentum'].upper()}+"
            f"{worst['volatility'].upper()}+{worst['volume'].upper()} "
            f"  P&L=${worst['total_pnl']:,.2f}  Sharpe={worst['sharpe']:.2f}"
        )

    print(f"{'='*60}\n")


if __name__ == '__main__':
    random_seeds = np.random.choice(10000, size=100, replace=False)

    for seed in random_seeds:
        RANDOM_SEED = int(seed)
        batch_no = read_increment_batch()
        print(f"[B{batch_no}]  batch.txt updated  (seed={RANDOM_SEED})")
        run_model_set(batch_no)
