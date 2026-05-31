"""
model.py — model/1_cc_with_stoploss

Covered-call historical replay with a **trailing combo-net stop-loss**.

This engine is a near-clone of model/6_paper2's replay (same signal-gated entry,
TV buyback, weekly Friday expiry, shared cash pool, same signals + option data)
with ONE behavioural change: an open stock+call position can **stop out** instead
of always riding to expiry.  Isolating that single variable makes its P&L directly
comparable to model 6's no-stop baseline.

See ./DESIGN.md for the full specification.  Key decisions:
  * Stop basis  : combo-net P&L (mark-to-market of long stock + short call).
  * Stop type   : trailing — stop = HWM(net_per_share) − ATR×k; never moves down.
  * On fire     : close the entire BAG at the firing bar's market (stk_bid, opt_ask).
  * Stale quote : combo-net needs a fresh option quote; on a stale bar the stop
                  check is SKIPPED until a fresh quote returns (strict combo-net).
  * Late fire   : if the quote returns and net is already ≤ stop, fire at the
                  returning bar's price and flag `late_stop`.
  * Exit order  : stop_loss → Friday expiry → buyback_tv.  (Expiry retains priority
                  over buyback exactly as in the model-6 baseline, so the ONLY
                  behavioural delta vs baseline is the stop.)

Standalone: unlike model 6 this does NOT import paper2/arbo702 (and therefore needs
no `ibapi`).  The handful of strategy constants/gates that model 6 pulled from
arbo702 are inlined below; the pure option helpers are reused from model 3.

Run:
    cd model/1_cc_with_stoploss
    python model.py --data-first 2022-09-01 --data-last 2026-03-25 \
        [--params PATH] [--stop-atr-mult 1.5]
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import importlib.util as _il
import json
import logging
import shutil
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

# ── path setup ────────────────────────────────────────────────────────────────
_HERE    = Path(__file__).resolve().parent
_BASE    = _HERE.parent.parent
_MODEL1A = _HERE.parent / '1a_tech_indicators_sock_trade'
_MODEL3  = _HERE.parent / '3_covered_calls'


def _load_by_path(mod_name: str, file_path: Path):
    spec = _il.spec_from_file_location(mod_name, file_path)
    mod  = _il.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Pure option helpers from model 3 (clean — no ibapi / arbo702 dependency).
_cc_mod = _load_by_path('cc_single_model', _MODEL3 / 'single_model.py')
find_cc_variant     = _cc_mod.find_cc_variant
get_option_price_at = _cc_mod.get_option_price_at

# md_table from model 1a utils.
_utils_mod = _load_by_path('model1a_utils', _MODEL1A / 'utils.py')
md_table   = _utils_mod.md_table


# ══════════════════════════════════════════════════════════════════════════════
# INLINED STRATEGY CONSTANTS / GATES  (verbatim from paper2/arbo702.py so this
# program is standalone and needs no ibapi)
# ══════════════════════════════════════════════════════════════════════════════

SYMBOL     = 'AAPL'
COMMISSION = 2.00                 # $ per leg
EOD_MINUTE = 15 * 60 + 45         # 15:45 — Friday CC expiry handling cutoff
ENTRY_EARLIEST_MINUTE = 9 * 60 + 35   # 09:35 — no entries before this
BAG_LMT_ENTRY_BUFFER  = 0.10
BAG_LMT_EXIT_BUFFER   = 0.10
ENTRY_BAG_TIMEOUT_BARS = 2        # paper2.cancel_stale_orders — entry BAGs
EXIT_BAG_TIMEOUT_BARS  = 5        # paper2.cancel_stale_orders — exit  BAGs

SIGNAL_MODES = ('none', 'trend_only', 'momentum_only', 'both')

_STRATEGY_KEYS = (
    'shares_per_position', 'cooldown_minutes',
    'cc_tv_min', 'cc_tv_max', 'buyback_tv',
    'expiry_label', 'strike_label', 'signal_mode',
)

_DEFAULT_STRATEGY = {
    'shares_per_position': 100,
    'cooldown_minutes':    15,
    'cc_tv_min':           1.5,
    'cc_tv_max':           3.5,
    'buyback_tv':          0.25,
    'expiry_label':        'w0',
    'strike_label':        's+0',
    'signal_mode':         'none',
}

TREND    = ['ema', 'macd', 'adx', 'sar', 'don', 'arn', 'vtx']
MOMENTUM = ['rsi', 'sto', 'cci', 'cmo', 'tsi', 'roc', 'frc', 'srsi', 'rmi', 'macd']
BSIG_TREND_COLS    = [f'bsig_{k}' for k in TREND]
BSIG_MOMENTUM_COLS = [f'bsig_{k}' for k in MOMENTUM]


def _signal_mode_fires(mode: str, trend_any: bool, mom_any: bool) -> bool:
    if mode == 'none':          return True
    if mode == 'trend_only':    return trend_any
    if mode == 'momentum_only': return mom_any
    if mode == 'both':          return trend_any and mom_any
    raise ValueError(f"unknown signal_mode: {mode!r}")


# ── stop-loss / data-hygiene defaults (this model's additions) ────────────────
DEFAULT_STOP_ATR_MULT         = 1.5
DEFAULT_MAX_QUOTE_AGE_MINUTES = 3
DEFAULT_STALE_STOP_FALLBACK   = 'skip'        # 'skip' | 'stock_leg'
STALE_STOP_FALLBACKS          = ('skip', 'stock_leg')
# When firing the stock-leg fallback we still need an option price to close the
# call; use the most-recent quote within this window (best-effort, real data),
# falling back to intrinsic value if none exists all day.
FALLBACK_QUOTE_AGE_MINUTES    = 1440          # 1 trading day

# Set from params in main() before replay().
MAX_QUOTE_AGE_MINUTES = DEFAULT_MAX_QUOTE_AGE_MINUTES

REPORTS_DIR = _HERE / 'reports'
SIGNALS_CSV = _BASE / 'data/stock/sq_AAPL_signals.csv'
DEFAULT_PARAMS_FILE = _HERE / 'params.json'


# ══════════════════════════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════════════════════════

def _setup_logger(run_dir: Path) -> logging.Logger:
    lg = logging.getLogger('cc_stoploss')
    if lg.handlers:
        return lg
    lg.setLevel(logging.INFO)
    fmt = logging.Formatter('%(asctime)s  %(levelname)-7s  %(message)s',
                            datefmt='%H:%M:%S')
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    lg.addHandler(ch)
    if run_dir is not None:
        run_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(run_dir / 'run.log', mode='w', encoding='utf-8')
        fh.setFormatter(fmt)
        lg.addHandler(fh)
    return lg


log: logging.Logger | None = None


# ══════════════════════════════════════════════════════════════════════════════
# PARAMS
# ══════════════════════════════════════════════════════════════════════════════

_DEFAULT_TOP = {
    'symbol':                 'AAPL',
    'starting_cash':          1_000_000,
    'strategies':             [dict(_DEFAULT_STRATEGY)],
    'stop_atr_mult':          DEFAULT_STOP_ATR_MULT,
    'stop_type':              'trailing',     # only 'trailing' implemented
    'stop_basis':             'combo_net',    # only 'combo_net' implemented
    'max_quote_age_minutes':  DEFAULT_MAX_QUOTE_AGE_MINUTES,
    'stale_stop_fallback':    DEFAULT_STALE_STOP_FALLBACK,
}


def load_params(path: Path) -> dict:
    """Read + validate params.json (paper2 strategy schema + stop keys)."""
    if not path.exists():
        print(f"[params] {path} missing — writing defaults")
        path.write_text(json.dumps(_DEFAULT_TOP, indent=2))

    p = json.loads(path.read_text())
    for k, v in _DEFAULT_TOP.items():
        if k != 'strategies':
            p.setdefault(k, v)

    # Validate stop config (only the implemented variants are accepted).
    if str(p['stop_type']) != 'trailing':
        raise ValueError(f"stop_type only supports 'trailing' (got {p['stop_type']!r})")
    if str(p['stop_basis']) != 'combo_net':
        raise ValueError(f"stop_basis only supports 'combo_net' (got {p['stop_basis']!r})")
    p['stop_atr_mult']         = float(p['stop_atr_mult'])
    p['max_quote_age_minutes'] = int(p['max_quote_age_minutes'])
    if p['stop_atr_mult'] <= 0:
        raise ValueError(f"stop_atr_mult must be > 0 (got {p['stop_atr_mult']})")
    p['stale_stop_fallback'] = str(p['stale_stop_fallback'])
    if p['stale_stop_fallback'] not in STALE_STOP_FALLBACKS:
        raise ValueError(
            f"stale_stop_fallback must be one of {list(STALE_STOP_FALLBACKS)}: "
            f"got {p['stale_stop_fallback']!r}")

    strategies = p.get('strategies') or [dict(_DEFAULT_STRATEGY)]
    cleaned = []
    for i, s in enumerate(strategies):
        if not isinstance(s, dict):
            raise ValueError(f"strategies[{i}] is not an object: {s!r}")
        merged = {**_DEFAULT_STRATEGY, **s}
        shares = int(merged['shares_per_position'])
        if shares <= 0 or shares % 100 != 0:
            raise ValueError(
                f"strategies[{i}].shares_per_position must be a positive "
                f"multiple of 100: {shares}"
            )
        merged['shares_per_position'] = shares
        merged['cooldown_minutes']    = int(merged['cooldown_minutes'])
        merged['cc_tv_min']           = float(merged['cc_tv_min'])
        merged['cc_tv_max']           = float(merged['cc_tv_max'])
        merged['buyback_tv']          = float(merged['buyback_tv'])
        merged['signal_mode']         = str(merged['signal_mode'])
        if merged['cc_tv_min'] > merged['cc_tv_max']:
            raise ValueError(f"strategies[{i}] cc_tv_min > cc_tv_max")
        if merged['signal_mode'] not in SIGNAL_MODES:
            raise ValueError(
                f"strategies[{i}].signal_mode must be one of "
                f"{list(SIGNAL_MODES)}: got {merged['signal_mode']!r}"
            )
        cleaned.append(merged)
    p['strategies'] = cleaned
    return p


# ══════════════════════════════════════════════════════════════════════════════
# DATA
# ══════════════════════════════════════════════════════════════════════════════

_REQUIRED_BASE_COLS = [
    'date', 'fnd_trade_date',
    'avg_ask', 'avg_bid', 'average',
    'atr_14', 'rsi_14', 'adx_14', 'vwp_vwap',
]

# Columns that must be finite for a bar to be tradeable (bad-data guard).
_BAR_REQUIRED_FINITE = ['avg_ask', 'avg_bid', 'average', 'atr_14']


def load_signals_window(first: str, last: str) -> pd.DataFrame:
    cols = list(dict.fromkeys(_REQUIRED_BASE_COLS + BSIG_TREND_COLS + BSIG_MOMENTUM_COLS))
    log.info(f"[data] reading {SIGNALS_CSV} columns={len(cols)} ...")
    df = pd.read_csv(SIGNALS_CSV, parse_dates=['date'], usecols=cols, low_memory=False)
    first_ts = pd.Timestamp(first)
    last_ts  = pd.Timestamp(last) + pd.Timedelta(days=1)   # inclusive last
    mask = (df['date'] >= first_ts) & (df['date'] < last_ts)
    df = df[mask].reset_index(drop=True)
    if df.empty:
        raise ValueError(f"No bars in [{first}, {last}] in {SIGNALS_CSV}")
    log.info(f"[data] loaded {len(df):,} bars from {df['date'].min()} to {df['date'].max()}")
    return df


def _bar_ok(bar: pd.Series) -> bool:
    """A bar is tradeable only if every required price/ATR field is finite."""
    for c in _BAR_REQUIRED_FINITE:
        v = bar.get(c)
        if v is None or pd.isna(v):
            return False
    return True


# ══════════════════════════════════════════════════════════════════════════════
# QUOTE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

_missing_quote_counts: dict[tuple, int] = defaultdict(int)


def _quote_at(opt_data, ts, col: str, tag: str) -> float | None:
    """Freshness-guarded option quote lookup (None when missing/stale)."""
    price = get_option_price_at(opt_data, ts, col, MAX_QUOTE_AGE_MINUTES)
    if price is None:
        day_key = (tag, pd.Timestamp(ts).date())
        _missing_quote_counts[day_key] += 1
        if _missing_quote_counts[day_key] == 1:
            log.warning(
                f"[quote] stale/missing {col} for {tag} at {ts} "
                f"(>{MAX_QUOTE_AGE_MINUTES} min old)"
            )
    return price


def _best_effort_opt_ask(opt_data, ts, tag) -> float | None:
    """
    Most-recent option ask within FALLBACK_QUOTE_AGE_MINUTES — used only to price
    the call when closing on the stock-leg fallback (no fresh quote available).
    Best-effort real data; does not count toward the freshness funnel.
    """
    return get_option_price_at(opt_data, ts, 'avg_ask', FALLBACK_QUOTE_AGE_MINUTES)


# ══════════════════════════════════════════════════════════════════════════════
# BAG FILL MECHANICS — option (ii) semantics (identical to model 6)
# ══════════════════════════════════════════════════════════════════════════════

def _try_entry_bag(df: pd.DataFrame, idx: int, variant: dict,
                   shares: int, tag: str) -> dict | None:
    bar0      = df.iloc[idx]
    stk_ask_0 = float(bar0['avg_ask'])
    opt_bid_0 = _quote_at(variant['opt_data'], bar0['date'], 'avg_bid', tag)
    if opt_bid_0 is None:
        return None
    lmt_per_share = (stk_ask_0 - float(opt_bid_0)) + BAG_LMT_ENTRY_BUFFER

    for k in range(ENTRY_BAG_TIMEOUT_BARS + 1):
        j = idx + k
        if j >= len(df):
            return None
        bar_j     = df.iloc[j]
        if not _bar_ok(bar_j):
            continue
        stk_ask_j = float(bar_j['avg_ask'])
        opt_bid_j = _quote_at(variant['opt_data'], bar_j['date'], 'avg_bid', tag)
        if opt_bid_j is None:
            continue
        if (stk_ask_j - float(opt_bid_j)) <= lmt_per_share:
            return {'fill_idx': j, 'stock_fill_px': stk_ask_j,
                    'opt_fill_bid': float(opt_bid_j), 'lmt': lmt_per_share}
    return None


def _try_exit_bag(df: pd.DataFrame, idx: int, position: dict, tag: str) -> dict | None:
    opt_data = position['opt_data']
    bar0     = df.iloc[idx]
    stk_bid0 = float(bar0['avg_bid'])
    opt_ask0 = _quote_at(opt_data, bar0['date'], 'avg_ask', tag)
    if opt_ask0 is None:
        return None
    lmt_per_share = (stk_bid0 - float(opt_ask0)) - BAG_LMT_EXIT_BUFFER

    for k in range(EXIT_BAG_TIMEOUT_BARS + 1):
        j = idx + k
        if j >= len(df):
            return None
        bar_j    = df.iloc[j]
        if not _bar_ok(bar_j):
            continue
        stk_bid_j = float(bar_j['avg_bid'])
        opt_ask_j = _quote_at(opt_data, bar_j['date'], 'avg_ask', tag)
        if opt_ask_j is None:
            continue
        if (stk_bid_j - float(opt_ask_j)) >= lmt_per_share:
            return {'fill_idx': j, 'stock_fill_px': stk_bid_j,
                    'opt_fill_ask': float(opt_ask_j), 'lmt': lmt_per_share}
    return None


# ══════════════════════════════════════════════════════════════════════════════
# POSITION / TRADE RECORDS
# ══════════════════════════════════════════════════════════════════════════════

def _entry_snapshot(row: pd.Series) -> dict:
    def _rnd(v):
        return round(float(v), 4) if pd.notna(v) else None
    return {
        'atr_at_entry':  _rnd(row.get('atr_14')),
        'rsi_at_entry':  _rnd(row.get('rsi_14')),
        'adx_at_entry':  _rnd(row.get('adx_14')),
        'vwap_at_entry': _rnd(row.get('vwp_vwap')),
    }


def _new_position_id(counter: list) -> str:
    counter[0] += 1
    return f"pos_{counter[0]:06d}"


def _book_close(pos: dict, exit_time, exit_reason: str,
                stock_exit_px: float, cc_close_price: float | None,
                stop_fields: dict | None = None) -> dict:
    """Compute P&L for a closed position and return the trade record."""
    shares    = pos['shares']
    stock_pnl = (stock_exit_px - pos['entry_stock_px']) * shares - COMMISSION
    if cc_close_price is not None:
        option_pnl = (pos['cc_open_price'] - cc_close_price) * shares - COMMISSION
        combined   = stock_pnl + option_pnl
        is_winner  = combined > 0
    else:
        option_pnl = combined = is_winner = None
    pos_days = (pd.Timestamp(exit_time) - pd.Timestamp(pos['entry_time'])).total_seconds() / 86400

    rec = {
        'position_id':       pos['position_id'],
        'strategy_id':       pos['strategy_id'],
        'signal_mode':       pos['signal_mode'],
        'expiry_label':      pos['expiry_label'],
        'strike_label':      pos['strike_label'],
        'strike':            pos['strike'],
        'expiry_date':       pos['expiry_date'],
        'entry_time':        str(pos['entry_time']),
        'exit_time':         str(exit_time),
        'entry_stock_price': round(pos['entry_stock_px'], 4),
        'stock_exit_price':  round(stock_exit_px, 4),
        'cc_open_price':     round(pos['cc_open_price'], 4),
        'cc_tv_at_entry':    round(pos['cc_tv_at_entry'], 4),
        'cc_close_price':    round(cc_close_price, 4) if cc_close_price is not None else None,
        'exit_reason':       exit_reason,
        'days_held':         round(pos_days, 3),
        'shares':            shares,
        'stock_pnl':         round(stock_pnl, 2),
        'option_pnl':        round(option_pnl, 2) if option_pnl is not None else None,
        'combined_pnl':      round(combined, 2) if combined is not None else None,
        'is_winner':         is_winner,
        'status':            'closed' if combined is not None else 'open_at_end',
        # stop-specific columns (None for non-stop exits)
        'stop_level_at_exit': None,
        'hwm_net':            round(pos['hwm_net'], 4) if pos['hwm_net'] != float('-inf') else None,
        'bars_to_stop':       None,
        'late_stop':          None,
        'stop_basis':         None,
        **{k: pos[k] for k in ('atr_at_entry', 'rsi_at_entry',
                                'adx_at_entry', 'vwap_at_entry')},
    }
    if stop_fields:
        rec.update(stop_fields)
    return rec


# ══════════════════════════════════════════════════════════════════════════════
# REPLAY LOOP
# ══════════════════════════════════════════════════════════════════════════════

def _ses_minute(ts: pd.Timestamp) -> int:
    return int(ts.hour) * 60 + int(ts.minute)


def _is_friday_expiry_moment(bar_dt: pd.Timestamp, expiry_date) -> bool:
    return (bar_dt.date() == pd.Timestamp(expiry_date).date() and
            _ses_minute(bar_dt) >= EOD_MINUTE)


def replay(df: pd.DataFrame, strategies: list, starting_cash: float,
           stop_atr_mult: float, stale_fallback: str) -> dict:
    trend_any_arr = df[BSIG_TREND_COLS].to_numpy().any(axis=1)
    mom_any_arr   = df[BSIG_MOMENTUM_COLS].to_numpy().any(axis=1)

    pos_counter    = [0]
    open_positions: dict[str, dict] = {}
    trades:        list[dict] = []
    transactions:  list[dict] = []
    hygiene = {'bad_bar': 0}

    funnel = [
        {'signal_skip': 0, 'no_quote': 0, 'tv_fail_low': 0, 'tv_fail_high': 0,
         'cooldown_skip': 0, 'cash_skip': 0, 'bag_timeout': 0, 'no_strike': 0,
         'accepted': 0, 'stop_loss': 0, 'late_stop': 0, 'stale_stop_skipped': 0,
         'stop_via_fallback': 0, 'stale_stop_checked': 0,
         'last_entry_ts': None}
        for _ in strategies
    ]

    def _cash() -> float:
        used = sum(p['cash_used'] for p in open_positions.values())
        return float(starting_cash) - used

    for idx in range(len(df)):
        bar    = df.iloc[idx]
        bar_dt = pd.Timestamp(bar['date'])

        # Bad-data guard: skip the whole bar (no entries; open positions wait,
        # never force-exit on missing data).
        if not _bar_ok(bar):
            hygiene['bad_bar'] += 1
            continue

        stock_bid = float(bar['avg_bid'])

        # ── Exits on open positions ──────────────────────────────────────────
        to_drop = []
        for pos_id, pos in list(open_positions.items()):
            if pos['fill_idx'] >= idx:
                continue  # entry hasn't filled before this bar
            st  = funnel[pos['strategy_id']]
            tag = pos['tag']
            opt_ask = _quote_at(pos['opt_data'], bar_dt, 'avg_ask', tag)

            # 1) STOP-LOSS (highest priority).  Primary = combo-net trailing
            #    (needs a fresh option ask to mark the call).  On a stale bar:
            #      'skip'      → no stop check until a fresh quote returns;
            #      'stock_leg' → trailing stop on the stock leg alone (the stock
            #                    price is always present), closing the call at the
            #                    best-effort most-recent quote, else intrinsic.
            #    The stock-leg HWM is maintained every bar so the fallback always
            #    has a current trailing reference.
            stock_net = stock_bid - pos['entry_stock_px']
            if stock_net > pos['hwm_stock']:
                pos['hwm_stock'] = stock_net

            fired = False
            if opt_ask is not None:
                net = stock_net - (float(opt_ask) - pos['cc_open_price'])
                gap = (idx - pos['last_net_idx']) > 1
                pos['last_net_idx'] = idx
                if net > pos['hwm_net']:
                    pos['hwm_net'] = net
                stop_level = pos['hwm_net'] - pos['atr_entry'] * stop_atr_mult
                if net <= stop_level:
                    sf = {'stop_level_at_exit': round(stop_level, 4),
                          'hwm_net': round(pos['hwm_net'], 4),
                          'bars_to_stop': idx - pos['fill_idx'],
                          'late_stop': bool(gap), 'stop_basis': 'combo_net'}
                    trade = _book_close(pos, bar_dt, 'stop_loss',
                                        stock_bid, float(opt_ask), sf)
                    trades.append(trade)
                    transactions.extend(_txn_rows_close(
                        pos, bar_dt, 'stop_loss', stock_bid, float(opt_ask)))
                    st['stop_loss'] += 1
                    if gap:
                        st['late_stop'] += 1
                    log.info(f"[stop] {pos_id} combo_net at {bar_dt} "
                             f"net={net:.4f} <= {stop_level:.4f} "
                             f"combined=${trade['combined_pnl']}")
                    fired = True
            elif stale_fallback == 'stock_leg':
                st['stale_stop_checked'] += 1
                stock_stop_level = pos['hwm_stock'] - pos['atr_entry'] * stop_atr_mult
                if stock_net <= stock_stop_level:
                    opt_close = _best_effort_opt_ask(pos['opt_data'], bar_dt, pos['tag'])
                    if opt_close is None:
                        opt_close = max(0.0, stock_bid - pos['strike'])  # intrinsic
                    sf = {'stop_level_at_exit': round(stock_stop_level, 4),
                          'hwm_net': (round(pos['hwm_net'], 4)
                                      if pos['hwm_net'] != float('-inf') else None),
                          'bars_to_stop': idx - pos['fill_idx'],
                          'late_stop': False, 'stop_basis': 'stock_fallback'}
                    trade = _book_close(pos, bar_dt, 'stop_loss',
                                        stock_bid, float(opt_close), sf)
                    trades.append(trade)
                    transactions.extend(_txn_rows_close(
                        pos, bar_dt, 'stop_loss', stock_bid, float(opt_close)))
                    st['stop_loss'] += 1
                    st['stop_via_fallback'] += 1
                    log.info(f"[stop] {pos_id} stock_fallback at {bar_dt} "
                             f"stk_net={stock_net:.4f} <= {stock_stop_level:.4f} "
                             f"opt_close={opt_close:.4f} "
                             f"combined=${trade['combined_pnl']}")
                    fired = True
            else:
                st['stale_stop_skipped'] += 1

            if fired:
                to_drop.append(pos_id)
                continue

            # 2) Friday expiry (deadline — retains priority over buyback, as in
            #    the model-6 baseline).
            if _is_friday_expiry_moment(bar_dt, pos['expiry_date']):
                if stock_bid > pos['strike']:
                    exit_px, reason = float(pos['strike']), 'assigned'
                else:
                    exit_px, reason = stock_bid, 'expired_otm'
                trade = _book_close(pos, bar_dt, reason, exit_px, 0.0)
                trades.append(trade)
                transactions.extend(_txn_rows_close(pos, bar_dt, reason, exit_px, 0.0))
                to_drop.append(pos_id)
                log.info(f"[expiry] {pos_id} {reason} at {bar_dt} "
                         f"strike={pos['strike']} stk_bid={stock_bid} "
                         f"combined=${trade['combined_pnl']}")
                continue

            # 3) Buyback TV gate (needs the same fresh opt_ask).
            if opt_ask is None:
                continue
            intrinsic = max(0.0, stock_bid - pos['strike'])
            if (float(opt_ask) - intrinsic) >= pos['buyback_tv']:
                continue
            fill = _try_exit_bag(df, idx, pos, tag)
            if fill is None:
                continue
            trade = _book_close(pos, df.iloc[fill['fill_idx']]['date'], 'buyback',
                                fill['stock_fill_px'], fill['opt_fill_ask'])
            trades.append(trade)
            transactions.extend(_txn_rows_close(
                pos, df.iloc[fill['fill_idx']]['date'], 'buyback',
                fill['stock_fill_px'], fill['opt_fill_ask']))
            to_drop.append(pos_id)
            log.info(f"[exit] {pos_id} buyback filled at idx={fill['fill_idx']} "
                     f"stk_bid={fill['stock_fill_px']} opt_ask={fill['opt_fill_ask']} "
                     f"combined=${trade['combined_pnl']}")
        for pos_id in to_drop:
            del open_positions[pos_id]

        # ── Entries ──────────────────────────────────────────────────────────
        if _ses_minute(bar_dt) < ENTRY_EARLIEST_MINUTE:
            continue

        trend_any = bool(trend_any_arr[idx])
        mom_any   = bool(mom_any_arr[idx])
        entry_px  = float(bar['avg_ask'])
        atr_entry = float(bar['atr_14'])   # guaranteed finite by _bar_ok
        snap      = _entry_snapshot(bar)

        for strat_id, strat in enumerate(strategies):
            st = funnel[strat_id]
            if not _signal_mode_fires(strat['signal_mode'], trend_any, mom_any):
                st['signal_skip'] += 1
                continue
            cd = _dt.timedelta(minutes=int(strat['cooldown_minutes']))
            if st['last_entry_ts'] is not None and (bar_dt - st['last_entry_ts']) < cd:
                st['cooldown_skip'] += 1
                continue
            shares = int(strat['shares_per_position'])
            if _cash() < entry_px * shares:
                st['cash_skip'] += 1
                continue
            variant = find_cc_variant(bar_dt.date(), bar_dt, entry_px,
                                      strat['expiry_label'], strat['strike_label'])
            if variant is None:
                st['no_strike'] += 1
                continue
            tag = f"{strat['expiry_label']}/{strat['strike_label']}"
            opt_bid_firing = _quote_at(variant['opt_data'], bar_dt, 'avg_bid', tag)
            if opt_bid_firing is None:
                st['no_quote'] += 1
                continue
            cc_tv = float(opt_bid_firing) - max(0.0, entry_px - variant['strike'])
            if cc_tv < float(strat['cc_tv_min']):
                st['tv_fail_low'] += 1
                continue
            if cc_tv > float(strat['cc_tv_max']):
                st['tv_fail_high'] += 1
                continue
            fill = _try_entry_bag(df, idx, variant, shares, tag)
            if fill is None:
                st['bag_timeout'] += 1
                continue

            st['accepted']     += 1
            st['last_entry_ts'] = bar_dt
            pos_id = _new_position_id(pos_counter)
            pos = {
                'position_id':    pos_id,
                'strategy_id':    strat_id,
                'signal_mode':    strat['signal_mode'],
                'expiry_label':   strat['expiry_label'],
                'strike_label':   strat['strike_label'],
                'strike':         float(variant['strike']),
                'expiry_date':    variant['expiry_date'],
                'opt_data':       variant['opt_data'],
                'tag':            tag,
                'entry_time':     str(df.iloc[fill['fill_idx']]['date']),
                'fill_idx':       fill['fill_idx'],
                'entry_stock_px': float(fill['stock_fill_px']),
                'cc_open_price':  float(fill['opt_fill_bid']),
                'cc_tv_at_entry': cc_tv,
                'shares':         shares,
                'cash_used':      fill['stock_fill_px'] * shares,
                'buyback_tv':     float(strat['buyback_tv']),
                # stop-loss state
                'atr_entry':      atr_entry,
                'hwm_net':        float('-inf'),
                'hwm_stock':      float('-inf'),
                'last_net_idx':   fill['fill_idx'],
                **snap,
            }
            open_positions[pos_id] = pos
            transactions.extend(_txn_rows_open(pos, fill))
            log.info(f"[entry] strat={strat_id} {tag} OPEN {pos_id} "
                     f"idx={fill['fill_idx']} stk={fill['stock_fill_px']} "
                     f"opt_bid={fill['opt_fill_bid']} cc_tv={cc_tv:.4f} "
                     f"shares={shares} atr={atr_entry:.4f}")

    # ── End of replay: open_at_end ───────────────────────────────────────────
    last_bar = df.iloc[-1]
    for pos_id, pos in open_positions.items():
        trade = _book_close(pos, pd.Timestamp(last_bar['date']),
                            'open_at_end', float(last_bar['avg_bid']), None)
        trade['status'] = 'open_at_end'
        trades.append(trade)
        log.info(f"[eod] {pos_id} open_at_end — no P&L booked")

    return {
        'trades':         trades,
        'transactions':   transactions,
        'funnel':         funnel,
        'hygiene':        hygiene,
        'missing_quotes': dict(_missing_quote_counts),
    }


# ══════════════════════════════════════════════════════════════════════════════
# PAPER2-STYLE TRANSACTION LOG
# ══════════════════════════════════════════════════════════════════════════════

_TX_COLS = [
    'timestamp', 'position_id', 'strategy_id', 'signal_mode',
    'leg', 'action', 'symbol', 'local_symbol', 'sec_type', 'quantity',
    'price', 'order_id', 'reason',
]


def _local_symbol(expiry_date, strike) -> str:
    d = pd.Timestamp(expiry_date)
    return f"{SYMBOL} {d.strftime('%y%m%d')}C{int(float(strike)*1000):08d}"


def _txn_rows_open(pos: dict, fill: dict) -> list[dict]:
    shares = pos['shares']
    ts     = pos['entry_time']
    reason = f"signal_mode={pos['signal_mode']}_strat{pos['strategy_id']}_tv{pos['cc_tv_at_entry']:.4f}"
    return [
        {'timestamp': ts, 'position_id': pos['position_id'],
         'strategy_id': pos['strategy_id'], 'signal_mode': pos['signal_mode'],
         'leg': 'stock', 'action': 'BUY', 'symbol': SYMBOL,
         'local_symbol': SYMBOL, 'sec_type': 'STK',
         'quantity': shares, 'price': round(fill['stock_fill_px'], 4),
         'order_id': '', 'reason': reason},
        {'timestamp': ts, 'position_id': pos['position_id'],
         'strategy_id': pos['strategy_id'], 'signal_mode': pos['signal_mode'],
         'leg': 'option', 'action': 'SELL', 'symbol': SYMBOL,
         'local_symbol': _local_symbol(pos['expiry_date'], pos['strike']),
         'sec_type': 'OPT', 'quantity': shares // 100,
         'price': round(fill['opt_fill_bid'], 4), 'order_id': '', 'reason': reason},
    ]


def _txn_rows_close(pos: dict, ts, reason: str,
                    stock_exit_px: float, opt_close_px: float) -> list[dict]:
    shares = pos['shares']
    return [
        {'timestamp': str(ts), 'position_id': pos['position_id'],
         'strategy_id': pos['strategy_id'], 'signal_mode': pos['signal_mode'],
         'leg': 'stock', 'action': 'SELL', 'symbol': SYMBOL,
         'local_symbol': SYMBOL, 'sec_type': 'STK',
         'quantity': shares, 'price': round(stock_exit_px, 4),
         'order_id': '', 'reason': reason},
        {'timestamp': str(ts), 'position_id': pos['position_id'],
         'strategy_id': pos['strategy_id'], 'signal_mode': pos['signal_mode'],
         'leg': 'option', 'action': 'BUY', 'symbol': SYMBOL,
         'local_symbol': _local_symbol(pos['expiry_date'], pos['strike']),
         'sec_type': 'OPT', 'quantity': shares // 100,
         'price': round(float(opt_close_px), 4), 'order_id': '', 'reason': reason},
    ]


# ══════════════════════════════════════════════════════════════════════════════
# OUTPUT WRITERS
# ══════════════════════════════════════════════════════════════════════════════

_TRADE_COLS = [
    'position_id', 'strategy_id', 'signal_mode',
    'expiry_label', 'strike_label', 'strike', 'expiry_date',
    'entry_time', 'exit_time',
    'entry_stock_price', 'stock_exit_price',
    'cc_open_price', 'cc_tv_at_entry', 'cc_close_price',
    'exit_reason', 'days_held', 'shares', 'status',
    'stock_pnl', 'option_pnl', 'combined_pnl', 'is_winner',
    'stop_basis', 'stop_level_at_exit', 'hwm_net', 'bars_to_stop', 'late_stop',
    'atr_at_entry', 'rsi_at_entry', 'adx_at_entry', 'vwap_at_entry',
]


def write_trades_csv(run_dir: Path, trades: list[dict]) -> Path:
    path = run_dir / 'trades.csv'
    if not trades:
        pd.DataFrame(columns=_TRADE_COLS).to_csv(path, index=False)
        return path
    df = pd.DataFrame(trades)
    cols = [c for c in _TRADE_COLS if c in df.columns] + \
           [c for c in df.columns if c not in _TRADE_COLS]
    df[cols].to_csv(path, index=False)
    return path


def write_summary_csv(run_dir: Path, trades: list[dict],
                      strategies: list[dict], funnel: list[dict]) -> Path:
    path = run_dir / 'summary.csv'
    closed = [t for t in trades if t['status'] == 'closed']
    by_strat: dict[int, list] = defaultdict(list)
    for t in closed:
        by_strat[t['strategy_id']].append(t)

    rows = []
    for sid, strat in enumerate(strategies):
        ts_list = by_strat.get(sid, [])
        pnls = [t['combined_pnl'] for t in ts_list]
        wins = [p for p in pnls if p > 0]
        win_rate = round(len(wins) / len(pnls) * 100, 2) if pnls else 0.0
        total    = round(sum(pnls), 2) if pnls else 0.0
        avg      = round(total / len(pnls), 2) if pnls else 0.0
        reasons  = defaultdict(int)
        for t in ts_list:
            reasons[t['exit_reason']] += 1
        rows.append({
            'strategy_id':         sid,
            'signal_mode':         strat['signal_mode'],
            'expiry_label':        strat['expiry_label'],
            'strike_label':        strat['strike_label'],
            'cc_tv_min':           strat['cc_tv_min'],
            'cc_tv_max':           strat['cc_tv_max'],
            'buyback_tv':          strat['buyback_tv'],
            'cooldown_minutes':    strat['cooldown_minutes'],
            'shares_per_position': strat['shares_per_position'],
            'trades':              len(ts_list),
            'win_rate_pct':        win_rate,
            'total_pnl':           total,
            'avg_pnl_per_trade':   avg,
            'n_stop_loss':         reasons.get('stop_loss', 0),
            'n_buyback':           reasons.get('buyback', 0),
            'n_assigned':          reasons.get('assigned', 0),
            'n_expired_otm':       reasons.get('expired_otm', 0),
            **{k: funnel[sid][k] for k in
               ('signal_skip', 'cooldown_skip', 'cash_skip', 'no_strike',
                'no_quote', 'tv_fail_low', 'tv_fail_high', 'bag_timeout',
                'accepted', 'late_stop', 'stale_stop_skipped',
                'stop_via_fallback', 'stale_stop_checked')},
        })
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def write_transaction_csv(run_dir: Path, transactions: list[dict]) -> Path:
    path = run_dir / 'transaction.csv'
    with open(path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=_TX_COLS, extrasaction='ignore')
        w.writeheader()
        w.writerows(transactions)
    return path


def write_markdown_report(run_dir: Path, params: dict, trades: list[dict],
                          strategies: list[dict], funnel: list[dict],
                          hygiene: dict, missing: dict,
                          data_first: str, data_last: str,
                          stop_atr_mult: float, argv: list[str]) -> Path:
    path   = run_dir / 'analysis.md'
    closed = [t for t in trades if t['status'] == 'closed']
    pnls   = [t['combined_pnl'] for t in closed]
    total_pnl = round(sum(pnls), 2) if pnls else 0.0
    win_rate  = round(sum(1 for p in pnls if p > 0) / len(pnls) * 100, 2) if pnls else 0.0
    reasons   = defaultdict(int)
    reason_pnl = defaultdict(float)
    for t in closed:
        reasons[t['exit_reason']] += 1
        reason_pnl[t['exit_reason']] += t['combined_pnl']

    L = [f"# model/1_cc_with_stoploss — covered call + combo-net trailing stop", ""]
    L.append(f"_Generated: {_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_")
    L.append("")
    L.append("## Run Parameters")
    L.append("| Parameter | Value |")
    L.append("|---|---|")
    L.append(f"| Run directory | `{run_dir.name}` |")
    L.append(f"| Date range | {data_first} → {data_last} |")
    L.append(f"| Starting cash | ${params.get('starting_cash',0):,.0f} |")
    L.append(f"| Strategies | {len(strategies)} |")
    L.append(f"| stop_basis / stop_type | combo_net / trailing |")
    L.append(f"| stop_atr_mult | {stop_atr_mult} |")
    L.append(f"| max_quote_age_minutes | {MAX_QUOTE_AGE_MINUTES} |")
    L.append(f"| Commission | ${COMMISSION:.2f} per leg |")
    L.append(f"| Command | `{' '.join(argv)}` |")
    L.append("")

    L.append("## Strategies")
    sdf = pd.DataFrame(strategies)
    sdf.insert(0, 'strategy_id', range(len(strategies)))
    L.append(md_table(sdf, n=len(sdf)))
    L.append("")

    L.append("## Summary")
    L.append(f"- Total closed trades: **{len(closed)}**")
    L.append(f"- Open-at-end: **{sum(1 for t in trades if t['status'] == 'open_at_end')}**")
    L.append(f"- Win rate: **{win_rate:.2f}%**")
    L.append(f"- Total P&L: **${total_pnl:,.2f}**")
    L.append("")
    L.append("### Exit-reason breakdown")
    L.append("| reason | trades | total P&L |")
    L.append("|---|---:|---:|")
    for r in ('stop_loss', 'buyback', 'assigned', 'expired_otm'):
        if reasons.get(r):
            L.append(f"| {r} | {reasons[r]:,} | ${reason_pnl[r]:,.2f} |")
    L.append("")
    L.append("> Compare Total P&L against the no-stop model-6 baseline over the same "
             "range to read the stop's effect.")
    L.append("")

    L.append("## Per-strategy Funnel")
    L.append("| strat | signal_skip | cooldown | cash | no_strike | no_quote | "
             "tv_low | tv_high | bag_timeout | accepted | stops | late_stop | stale_skip |")
    L.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for sid, f in enumerate(funnel):
        L.append(
            f"| {sid} | {f['signal_skip']:,} | {f['cooldown_skip']:,} | {f['cash_skip']:,} | "
            f"{f['no_strike']:,} | {f['no_quote']:,} | {f['tv_fail_low']:,} | "
            f"{f['tv_fail_high']:,} | {f['bag_timeout']:,} | {f['accepted']:,} | "
            f"{f['stop_loss']:,} | {f['late_stop']:,} | {f['stale_stop_skipped']:,} |")
    L.append("")

    L.append("## Data hygiene")
    L.append(f"- Bad/incomplete bars skipped: **{hygiene['bad_bar']:,}**")
    L.append(f"- Stale-quote stop checks skipped (strict combo_net): "
             f"**{sum(f['stale_stop_skipped'] for f in funnel):,}**")
    L.append(f"- Stale-quote stop checks via stock-leg fallback: "
             f"**{sum(f['stale_stop_checked'] for f in funnel):,}**")
    L.append(f"- Stops fired via stock-leg fallback: "
             f"**{sum(f['stop_via_fallback'] for f in funnel):,}**")
    L.append(f"- Late stops (fired after a data gap): "
             f"**{sum(f['late_stop'] for f in funnel):,}**")
    L.append("")

    if missing:
        L.append("## Missing / stale quotes (first occurrence per day counted)")
        rows = sorted(missing.items(), key=lambda kv: (str(kv[0][1]), kv[0][0]))
        L.append("| tag | day | count |")
        L.append("|---|---|---:|")
        for (tag, day), n in rows[:50]:
            L.append(f"| {tag} | {day} | {n:,} |")
        if len(rows) > 50:
            L.append(f"_… {len(rows)-50} additional rows omitted._")
        L.append("")

    path.write_text('\n'.join(L), encoding='utf-8')
    return path


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(
        description='Covered-call replay with a trailing combo-net stop-loss.')
    ap.add_argument('--data-first', required=True, help='First date (YYYY-MM-DD)')
    ap.add_argument('--data-last',  required=True, help='Last date, inclusive (YYYY-MM-DD)')
    ap.add_argument('--params', type=str, default=str(DEFAULT_PARAMS_FILE),
                    help='Path to params.json (default: ./params.json)')
    ap.add_argument('--stop-atr-mult', type=float, default=None,
                    help='Override stop_atr_mult from params.json')
    ap.add_argument('--stale-fallback', choices=STALE_STOP_FALLBACKS, default=None,
                    help="Override stale_stop_fallback ('skip' | 'stock_leg')")
    args = ap.parse_args()

    run_ts  = _dt.datetime.now().strftime('%m%d%H%M')
    run_dir = REPORTS_DIR / run_ts
    run_dir.mkdir(parents=True, exist_ok=True)

    global log, MAX_QUOTE_AGE_MINUTES
    log = _setup_logger(run_dir)

    params_path = Path(args.params)
    params = load_params(params_path)
    if args.stop_atr_mult is not None:
        params['stop_atr_mult'] = float(args.stop_atr_mult)
    if args.stale_fallback is not None:
        params['stale_stop_fallback'] = args.stale_fallback
    MAX_QUOTE_AGE_MINUTES = int(params['max_quote_age_minutes'])
    stop_atr_mult  = float(params['stop_atr_mult'])
    stale_fallback = str(params['stale_stop_fallback'])

    # Persist the exact effective config used for this run.
    (run_dir / 'params.json').write_text(json.dumps(params, indent=2))

    log.info("=" * 72)
    log.info(f"model/1_cc_with_stoploss  (run_ts={run_ts})")
    log.info(f"  symbol={params['symbol']}  starting_cash=${params['starting_cash']:,}  "
             f"range: {args.data_first} → {args.data_last}")
    log.info(f"  stop: combo_net trailing  atr_mult={stop_atr_mult}  "
             f"max_quote_age={MAX_QUOTE_AGE_MINUTES}m  "
             f"stale_fallback={stale_fallback}")
    for i, s in enumerate(params['strategies']):
        log.info(f"    [{i}] shares={s['shares_per_position']} cd={s['cooldown_minutes']}m "
                 f"cc_tv=[{s['cc_tv_min']},{s['cc_tv_max']}] buyback_tv={s['buyback_tv']} "
                 f"{s['expiry_label']}/{s['strike_label']} mode={s['signal_mode']}")
    log.info("=" * 72)

    df = load_signals_window(args.data_first, args.data_last)
    result = replay(df, params['strategies'], float(params['starting_cash']),
                    stop_atr_mult, stale_fallback)

    log.info(f"[report] trades      -> {write_trades_csv(run_dir, result['trades'])}")
    log.info(f"[report] summary     -> {write_summary_csv(run_dir, result['trades'], params['strategies'], result['funnel'])}")
    log.info(f"[report] transaction -> {write_transaction_csv(run_dir, result['transactions'])}")
    log.info(f"[report] analysis    -> {write_markdown_report(run_dir, params, result['trades'], params['strategies'], result['funnel'], result['hygiene'], result['missing_quotes'], args.data_first, args.data_last, stop_atr_mult, sys.argv)}")

    closed   = [t for t in result['trades'] if t['status'] == 'closed']
    open_end = [t for t in result['trades'] if t['status'] == 'open_at_end']
    total    = sum(t['combined_pnl'] for t in closed) if closed else 0.0
    n_stop   = sum(1 for t in closed if t['exit_reason'] == 'stop_loss')
    log.info("=" * 72)
    log.info(f"  closed trades: {len(closed):,}   open_at_end: {len(open_end):,}   "
             f"stop_loss exits: {n_stop:,}")
    log.info(f"  total P&L: ${total:,.2f}")
    log.info(f"  bad bars skipped: {result['hygiene']['bad_bar']:,}")
    log.info(f"  per-strategy accepted: "
             + ", ".join(f"s{i}={f['accepted']}" for i, f in enumerate(result['funnel'])))
    log.info(f"  output: {run_dir}")
    log.info("=" * 72)


if __name__ == '__main__':
    main()
