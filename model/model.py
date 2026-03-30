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

6.  Output       : ../reports/model_summary_DDMMHHMMSS.md
                   ../reports/model_detailed_DDMMHHMMSS.md
                   ../reports/trades_DDMMHHMMSS.csv
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

# ──────────────────────────────────────────────────────────────────────────────
# Indicator categories
# ──────────────────────────────────────────────────────────────────────────────
TREND      = ['ema', 'macd', 'adx', 'sar', 'don', 'arn', 'vtx']
MOMENTUM   = ['rsi', 'sto', 'cci', 'cmo', 'tsi', 'roc', 'frc', 'srsi', 'rmi', 'macd']
VOLATILITY = ['atr', 'bbd', 'chp']
VOLUME     = ['vwap', 'obv', 'mfi', 'klg', 'frc', 'vrc']


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 -- SIGNALS CSV  (load or build)
# ══════════════════════════════════════════════════════════════════════════════

def load_or_build_signals() -> pd.DataFrame:
    """
    Returns the signals DataFrame.
    If sq_AAPL_signals.csv exists, read it directly.
    Otherwise, build from sq_AAPL_extended.csv and save.
    """
    if SIGNALS_CSV.exists():
        print(f"[signals] Loading cached {SIGNALS_CSV} ...")
        df = pd.read_csv(SIGNALS_CSV, parse_dates=['date'], low_memory=False)
        print(f"  Loaded  : {df.shape[0]:,} rows x {df.shape[1]} columns")
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
    bars_held   = 0

    for i in range(entry_iloc + 1, len(df_day)):
        bar = df_day.iloc[i]
        bars_held += 1

        # 1. Time box
        if int(bar['ses_minute']) >= EXIT_MINUTE:
            exit_price  = float(bar['close'])
            exit_reason = 'time_box'
            break

        # 2. Stop-loss
        if float(bar['low']) <= stop:
            exit_price  = stop
            exit_reason = 'stop_loss'
            break

        # 3. Profit target
        if float(bar['high']) >= target:
            exit_price  = target
            exit_reason = 'profit_target'
            break

        # 4. Sell signals from this model's 4 indicators
        for sc in sell_cols:
            if int(bar.get(sc, 0)):
                exit_price  = float(bar['close'])
                exit_reason = f'sell_{sc[5:]}'   # strip 'ssig_' prefix
                break
        if exit_price is not None:
            break

    # Safety net -- no exit found before end of day data
    if exit_price is None:
        last = df_day.iloc[-1]
        exit_price  = float(last['close'])
        exit_reason = 'eod_forced'

    cost       = shares * entry_price
    proceeds   = shares * exit_price
    pnl_dollar = proceeds - cost - COMMISSION
    pnl_pct    = (pnl_dollar / cost * 100) if cost > 0 else 0.0

    return {
        'exit_price':    round(exit_price, 4),
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

    print(f"\n[backtest] Running {n_combos:,} model combinations ...")
    print(f"           Sample size : {len(sample_idx):,} bars")

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

            rsi_val  = row.get('rsi_14', np.nan)
            adx_val  = row.get('adx_14', np.nan)
            vwap_val = row.get('vwp_vwap', np.nan)

            trade_record = {
                'model_id':      model_id,
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
                f"  [{model_id:>5}/{n_combos}]  "
                f"trades so far: {len(all_trades):,}"
            )

    print(f"\n[backtest] Complete.  Total trades: {len(all_trades):,}")
    return pd.DataFrame(summary_rows), pd.DataFrame(all_trades)


# ══════════════════════════════════════════════════════════════════════════════
# STEP 6 -- REPORTS
# ══════════════════════════════════════════════════════════════════════════════

def _md_table(df: pd.DataFrame, float_fmt: str = '.2f') -> str:
    """Convert a DataFrame to a GitHub-flavoured Markdown table string."""
    lines  = []
    header = '| ' + ' | '.join(str(c) for c in df.columns) + ' |'
    sep    = '| ' + ' | '.join(['---'] * len(df.columns)) + ' |'
    lines.append(header)
    lines.append(sep)
    for _, row in df.iterrows():
        cells = []
        for v in row.values:
            if isinstance(v, float):
                cells.append(f'{v:{float_fmt}}')
            else:
                cells.append(str(v))
        lines.append('| ' + ' | '.join(cells) + ' |')
    return '\n'.join(lines)


def write_summary_report(
    summary_df:     pd.DataFrame,
    timestamp:      str,
    n_valid_models: int,
    n_total_trades: int,
) -> Path:
    """
    Writes model_summary_TIMESTAMP.md.
    Sections: config, aggregate stats, top-50 by Sharpe, top-50 by P&L,
              bottom-20, full ranked table.
    """
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f'model_summary_{timestamp}.md'

    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    sdf = summary_df.copy()
    sdf_active = sdf[sdf['trades'] > 0].copy()

    lines = []
    lines.append('# AAPL Intraday Backtest -- Model Summary')
    lines.append(f'\n_Generated: {now}_\n')

    # Config block
    lines.append('## Configuration\n')
    lines.append('| Parameter | Value |')
    lines.append('|---|---|')
    for k, v in [
        ('Date Range',         f'{DATE_START} to {DATE_END}'),
        ('Instrument',         'AAPL -- 1-minute bars'),
        ('Entry Window',       'After 10:00 AM'),
        ('Exit Time-Box',      '3:45 PM'),
        ('Sample Size',        f'{N_SAMPLE:,} fixed bars (seed={RANDOM_SEED})'),
        ('Trade Capital',      f'${TRADE_CAPITAL:,.0f}'),
        ('Commission',         f'${COMMISSION:.2f} round-trip'),
        ('ATR Stop Mult',      f'{ATR_STOP_MULT}x'),
        ('Reward:Risk',        f'{ATR_TARGET_RR}:1'),
        ('Total Combinations', '1,260'),
        ('Valid Models',       f'{n_valid_models:,}'),
        ('Models with Trades', f'{len(sdf_active):,}'),
        ('Total Trades',       f'{n_total_trades:,}'),
    ]:
        lines.append(f'| {k} | {v} |')
    lines.append('')

    # Aggregate stats
    if not sdf_active.empty:
        lines.append('## Aggregate Performance (all active models)\n')
        lines.append('| Metric | Value |')
        lines.append('|---|---|')
        total_pnl = sdf_active['total_pnl'].sum()
        avg_wr    = sdf_active['win_rate'].mean()
        finite_pf = sdf_active.loc[sdf_active['profit_factor'] < 1e9, 'profit_factor']
        avg_pf    = finite_pf.mean() if len(finite_pf) > 0 else 0.0
        avg_sh    = sdf_active['sharpe'].mean()
        lines.append(f'| Combined P&L across all models | ${total_pnl:,.2f} |')
        lines.append(f'| Average Win Rate               | {avg_wr:.1f}% |')
        lines.append(f'| Average Profit Factor          | {avg_pf:.3f} |')
        lines.append(f'| Average Sharpe                 | {avg_sh:.3f} |')
        lines.append('')

    display_cols = [
        'model_id', 'trend', 'momentum', 'volatility', 'volume',
        'trades', 'win_rate', 'avg_entry', 'avg_exit',
        'avg_duration_bars', 'total_pnl', 'profit_factor', 'sharpe'
    ]

    def _rank_section(df_sub, title, n=50):
        lines.append(f'## {title}\n')
        sub = df_sub.head(n)[display_cols].copy()
        sub.insert(0, 'rank', range(1, len(sub) + 1))
        lines.append(_md_table(sub))
        lines.append('')

    if not sdf_active.empty:
        _rank_section(
            sdf_active.sort_values('sharpe', ascending=False),
            'Top 50 Models -- Ranked by Sharpe', 50
        )
        _rank_section(
            sdf_active.sort_values('total_pnl', ascending=False),
            'Top 50 Models -- Ranked by Total P&L ($)', 50
        )
        _rank_section(
            sdf_active.sort_values('total_pnl', ascending=True),
            'Bottom 20 Models -- Worst Total P&L', 20
        )

    # Full table
    lines.append('## Full Model Table (all 1,221 combinations)\n')
    all_display = [
        'model_id', 'trend', 'momentum', 'volatility', 'volume',
        'trades', 'win_rate', 'total_pnl', 'profit_factor', 'sharpe', 'max_drawdown'
    ]
    lines.append(_md_table(
        sdf.sort_values('total_pnl', ascending=False)[all_display]
    ))
    lines.append('')

    path.write_text('\n'.join(lines), encoding='utf-8')
    print(f"[report]  Summary  -> {path}")
    return path


def write_detailed_report(
    trades_df:  pd.DataFrame,
    summary_df: pd.DataFrame,
    timestamp:  str,
) -> Path:
    """
    Writes model_detailed_TIMESTAMP.md.
    Trade-by-trade breakdown for the top TOP_N_DETAIL models by total P&L.
    """
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f'model_detailed_{timestamp}.md'

    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    lines = []
    lines.append('# AAPL Intraday Backtest -- Detailed Trade Report')
    lines.append(
        f'\n_Generated: {now} | '
        f'Showing top {TOP_N_DETAIL} models by Total P&L_\n'
    )

    if trades_df.empty or summary_df.empty:
        lines.append('_No trades were executed._')
        path.write_text('\n'.join(lines), encoding='utf-8')
        return path

    sdf_active = summary_df[summary_df['trades'] > 0]
    top_ids = (
        sdf_active
        .sort_values('total_pnl', ascending=False)
        .head(TOP_N_DETAIL)['model_id']
        .tolist()
    )

    for rank, mid in enumerate(top_ids, 1):
        model_row    = summary_df[summary_df['model_id'] == mid].iloc[0]
        model_trades = trades_df[trades_df['model_id'] == mid].copy()

        t   = model_row['trend']
        m   = model_row['momentum']
        v   = model_row['volatility']
        vol = model_row['volume']

        lines.append('---\n')
        lines.append(
            f'## Rank {rank} -- Model {mid}: '
            f'{t.upper()} + {m.upper()} + {v.upper()} + {vol.upper()}\n'
        )
        lines.append('| Metric | Value |')
        lines.append('|---|---|')

        exit_counts = model_trades['exit_reason'].value_counts()
        ec_str = '  '.join(f'{r}: {c}' for r, c in exit_counts.items())

        for k, val in [
            ('Trades',           int(model_row['trades'])),
            ('Win Rate',         f'{model_row["win_rate"]:.1f}%'),
            ('Avg Entry Price',  f'${model_row["avg_entry"]:.2f}'),
            ('Avg Exit Price',   f'${model_row["avg_exit"]:.2f}'),
            ('Avg Duration',     f'{model_row["avg_duration_bars"]:.1f} bars'),
            ('Total P&L',        f'${model_row["total_pnl"]:,.2f}'),
            ('Avg P&L / Trade',  f'${model_row["avg_pnl"]:,.2f}'),
            ('Profit Factor',    f'{model_row["profit_factor"]:.3f}'),
            ('Sharpe (annual)',  f'{model_row["sharpe"]:.3f}'),
            ('Max Drawdown',     f'${model_row["max_drawdown"]:,.2f}'),
            ('Exit Breakdown',   ec_str),
        ]:
            lines.append(f'| {k} | {val} |')
        lines.append('')

        lines.append('### Trades\n')
        td = model_trades[[
            'trade_date', 'entry_time', 'entry_price', 'shares',
            'stop_loss', 'profit_target',
            'exit_price', 'exit_reason', 'bars_held',
            'pnl_dollar', 'pnl_pct'
        ]].copy()
        td['entry_time'] = pd.to_datetime(td['entry_time']).dt.strftime('%H:%M')
        td['cum_pnl']    = td['pnl_dollar'].cumsum().round(2)
        lines.append(_md_table(td))
        lines.append('')

    path.write_text('\n'.join(lines), encoding='utf-8')
    print(f"[report]  Detailed -> {path}")
    return path


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def run_model_set():
    ts = datetime.datetime.now().strftime('%d%m%H%M%S')
    print(f"\n{'='*60}")
    print(f"  AAPL Intraday Backtest  |  run {ts}")
    print(f"{'='*60}\n")

    # 1. Load / build signals CSV
    df = load_or_build_signals()

    # 2. Filter date range
    mask = (
        (df['date'] >= pd.Timestamp(DATE_START)) &
        (df['date'] <= pd.Timestamp(DATE_END))
    )
    df = df[mask].reset_index(drop=True)
    print(f"[filter]  {DATE_START} -> {DATE_END}: {len(df):,} rows")
    if len(df) == 0:
        sys.exit("ERROR: No data in the specified date range.")

    # 3. Fixed sample
    sample_idx = draw_sample(df)

    # 4. Build day-level structures once (reused across all 1,221 models)
    print("[index]   Building day index ...")
    df = df.copy()
    df['_day_pos'] = df.groupby('fnd_trade_date').cumcount()

    day_dict    = {}
    day_pos_map = {}

    for date, group in df.groupby('fnd_trade_date'):
        grp_reset = group.reset_index(drop=True)
        day_dict[date] = grp_reset
        for pos, global_idx in enumerate(group.index):
            day_pos_map[global_idx] = pos

    print(f"          Trading days indexed: {len(day_dict):,}")

    # 5. Run all models
    summary_df, trades_df = run_all_models(
        df, sample_idx, day_dict, day_pos_map
    )

    # 6. Save trades CSV
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    trades_path = REPORTS_DIR / f'trades_{ts}.csv'
    trades_df.to_csv(trades_path, index=False)
    print(f"[output]  Trades CSV -> {trades_path}  ({len(trades_df):,} records)")

    # 7. Write reports
    n_valid  = len(generate_combos())
    n_trades = len(trades_df)
    write_summary_report(summary_df, ts, n_valid, n_trades)
    write_detailed_report(trades_df, summary_df, ts)

    # 8. Console summary
    sdf_active = summary_df[summary_df['trades'] > 0]
    print(f"\n{'='*60}")
    print(f"  RUN COMPLETE")
    print(f"{'='*60}")
    print(f"  Models run       : {len(summary_df):,}")
    print(f"  Models w/ trades : {len(sdf_active):,}")
    print(f"  Total trades     : {n_trades:,}")

    if not sdf_active.empty:
        best  = sdf_active.loc[sdf_active['total_pnl'].idxmax()]
        worst = sdf_active.loc[sdf_active['total_pnl'].idxmin()]
        print(
            f"  Best  model : #{int(best['model_id'])} "
            f"{best['trend'].upper()}+{best['momentum'].upper()}+"
            f"{best['volatility'].upper()}+{best['volume'].upper()} "
            f"  P&L=${best['total_pnl']:,.2f}  Sharpe={best['sharpe']:.2f}"
        )
        print(
            f"  Worst model : #{int(worst['model_id'])} "
            f"{worst['trend'].upper()}+{worst['momentum'].upper()}+"
            f"{worst['volatility'].upper()}+{worst['volume'].upper()} "
            f"  P&L=${worst['total_pnl']:,.2f}  Sharpe={worst['sharpe']:.2f}"
        )

    print(f"{'='*60}\n")
    return summary_df

if __name__ == '__main__':
    # create an array of 100 random seeds
    random_seeds = np.random.choice(10000, size=100, replace=False)
    for each in random_seeds:
        RANDOM_SEED = each
        # execute the backtest 100 times with different seeds
        df = run_model_set()

