"""
all_models.py — model/6_paper2

Historical replay of paper2's live covered-call strategy.  Same params.json
schema as paper2, same per-strategy signal_mode / cooldown / cash / TV / expiry
/ strike semantics, same BAG combo entry/exit mechanics — but walks historical
1-min bars + historical option quotes instead of connecting to IB.

Design references (see model/6_paper2/CLAUDE.md for full spec):
  1. Data: data/stock/sq_AAPL_signals.csv + data/option_index.csv +
     data/options/**.  Pre-computed `bsig_*` columns drive signal_mode gates.
  2. Params: ./params.json (same schema as paper2/params.json).
  3. Code reuse: imports constants + pure helpers from paper2/arbo702.py.
  4. Fill semantics: "option (ii)" — entry BAG fills only if the live combo
     market price crosses the LMT within ENTRY_BAG_TIMEOUT_BARS (2); exit BAG
     fills within EXIT_BAG_TIMEOUT_BARS (5).  Fill price = the bar's observed
     combo market price at the fill bar (realistic IB LMT semantics).
  5. Run shape: one continuous replay over --data-first → --data-last.
  6. Output: both paper2-style transaction.csv and model-family
     trades.csv / summary.csv / batch_run_analysis.md.
  7. Quote freshness: MAX_QUOTE_AGE_MINUTES = 3 (tight).  Stale / missing
     quotes log a warning and count toward a `no_quote` counter.
  8. Session filter: ENTRY_EARLIEST_MINUTE (09:35) lower bound; no upper
     cutoff.  Friday expiry at 15:45 (paper2's EOD_MINUTE).
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import logging
import os
import shutil
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

# ── path setup ────────────────────────────────────────────────────────────────
# Two files named `single_model.py` exist in this tree (model/1a… and
# model/3_covered_calls/), and arbo702's own import ladder puts 1a on
# sys.path first.  Rather than play sys.path-ordering games, we load the
# specific files we need via importlib.util — insensitive to caching order.
import importlib.util as _il                                  # noqa: E402

_HERE    = Path(__file__).parent
_PAPER2  = _HERE.parent.parent / 'paper2'
_MODEL1A = _HERE.parent / '1a_tech_indicators_sock_trade'
_MODEL3  = _HERE.parent / '3_covered_calls'
_BASE    = _HERE.parent.parent
sys.path.insert(0, str(_PAPER2))


def _load_by_path(mod_name: str, file_path: Path):
    spec = _il.spec_from_file_location(mod_name, file_path)
    mod  = _il.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# paper2 is the source of truth for strategy constants + gates.  Importing
# arbo702 triggers its module-level params load and logging setup; both are
# benign side effects we can ignore (we use our own logger + params below).
from arbo702 import (                                         # noqa: E402
    SIGNAL_MODES, _signal_mode_fires,
    BSIG_TREND_COLS, BSIG_MOMENTUM_COLS,
    SYMBOL, COMMISSION,
    BAG_LMT_ENTRY_BUFFER, BAG_LMT_EXIT_BUFFER,
    _STRATEGY_KEYS, _DEFAULT_STRATEGY,
    ENTRY_EARLIEST_MINUTE, EOD_MINUTE,
)

# Model 3's CC helpers — loaded by explicit path to avoid a name clash with
# model/1a's own `single_model.py` (arbo702 put 1a on sys.path first).
_cc_mod = _load_by_path('cc_single_model', _MODEL3 / 'single_model.py')
find_cc_variant     = _cc_mod.find_cc_variant
get_option_price_at = _cc_mod.get_option_price_at

# md_table lives in model/1a's utils.py
_utils_mod = _load_by_path('model1a_utils', _MODEL1A / 'utils.py')
md_table   = _utils_mod.md_table

# ── constants (model-6-specific overrides) ────────────────────────────────────
MAX_QUOTE_AGE_MINUTES  = 3       # tight freshness guard (paper2-parity target)
ENTRY_BAG_TIMEOUT_BARS = 2       # paper2.cancel_stale_orders — entry BAGs
EXIT_BAG_TIMEOUT_BARS  = 5       # paper2.cancel_stale_orders — exit  BAGs
_TRADING_MINS_PER_DAY  = 390

REPORTS_DIR = _HERE / 'reports'
SIGNALS_CSV = _BASE / 'data/stock/sq_AAPL_signals.csv'
DEFAULT_PARAMS_FILE = _HERE / 'params.json'


# ══════════════════════════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════════════════════════

def _setup_logger(run_dir: Path) -> logging.Logger:
    lg = logging.getLogger('model6')
    if lg.handlers:                 # idempotent under reimports
        return lg
    lg.setLevel(logging.INFO)
    fmt = logging.Formatter('%(asctime)s  %(levelname)-7s  %(message)s',
                            datefmt='%H:%M:%S')
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    lg.addHandler(ch)
    if run_dir is not None:
        run_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(run_dir / 'run.log', mode='w', encoding='utf-8')
        fh.setLevel(logging.INFO)
        fh.setFormatter(fmt)
        lg.addHandler(fh)
    return lg


log: logging.Logger | None = None


# ══════════════════════════════════════════════════════════════════════════════
# PARAMS
# ══════════════════════════════════════════════════════════════════════════════

_DEFAULT_TOP = {
    'symbol':         'AAPL',
    'starting_cash':  500_000,
    'strategies':     [dict(_DEFAULT_STRATEGY)],
}


def load_params(path: Path) -> dict:
    """
    Read + validate a params.json file using paper2's strategy schema.
    Same validation as paper2 (shares multiple of 100, tv_min ≤ tv_max,
    signal_mode ∈ SIGNAL_MODES).  If the file is missing, auto-create
    with paper2 defaults so the first run has a template to edit.
    """
    if not path.exists():
        print(f"[params] {path} missing — writing defaults")
        path.write_text(json.dumps(_DEFAULT_TOP, indent=2))

    p = json.loads(path.read_text())
    for k, v in _DEFAULT_TOP.items():
        if k != 'strategies':
            p.setdefault(k, v)

    strategies = p.get('strategies') or [dict(_DEFAULT_STRATEGY)]
    cleaned = []
    for i, s in enumerate(strategies):
        if not isinstance(s, dict):
            raise ValueError(f"strategies[{i}] is not an object: {s!r}")
        merged = {**_DEFAULT_STRATEGY, **s}
        shares = int(merged['shares_per_position'])
        if shares <= 0 or shares % 100 != 0:
            raise ValueError(
                f"strategies[{i}].shares_per_position must be positive "
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


def load_signals_window(first: str, last: str) -> pd.DataFrame:
    cols = list(dict.fromkeys(_REQUIRED_BASE_COLS + BSIG_TREND_COLS + BSIG_MOMENTUM_COLS))
    log.info(f"[data] reading {SIGNALS_CSV} columns={len(cols)} ...")
    df = pd.read_csv(SIGNALS_CSV, parse_dates=['date'], usecols=cols, low_memory=False)
    first_ts = pd.Timestamp(first)
    last_ts  = pd.Timestamp(last) + pd.Timedelta(days=1)    # inclusive last
    mask = (df['date'] >= first_ts) & (df['date'] < last_ts)
    df = df[mask].reset_index(drop=True)
    if df.empty:
        raise ValueError(f"No bars in [{first}, {last}] in {SIGNALS_CSV}")
    log.info(f"[data] loaded {len(df):,} bars from {df['date'].min()} to {df['date'].max()}")
    return df


# ══════════════════════════════════════════════════════════════════════════════
# QUOTE HELPERS (wrap single_model's as-of lookup + warn on stale / missing)
# ══════════════════════════════════════════════════════════════════════════════

_missing_quote_counts: dict[tuple, int] = defaultdict(int)


def _quote_at(opt_data, ts, col: str, tag: str) -> float | None:
    """
    Freshness-guarded option quote lookup.  Returns None + logs WARNING
    (rate-limited to once per (tag, day)) when the quote is missing or
    older than MAX_QUOTE_AGE_MINUTES.
    """
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


# ══════════════════════════════════════════════════════════════════════════════
# BAG FILL MECHANICS — option (ii) semantics
# ══════════════════════════════════════════════════════════════════════════════

def _try_entry_bag(
    df:        pd.DataFrame,
    idx:       int,
    variant:   dict,
    shares:    int,
    tag:       str,
) -> dict | None:
    """
    BAG entry: submit a BUY combo LMT at per-share net debit + buffer at bar
    `idx`.  Walk bars idx … idx+ENTRY_BAG_TIMEOUT_BARS (inclusive) looking for
    a bar whose market combo price (stock_ask − opt_bid) crosses the LMT.
    On fill, return {'fill_idx', 'stock_fill_px', 'opt_fill_bid', 'lmt'}.
    On timeout / end-of-data, return None.
    """
    bar0       = df.iloc[idx]
    stk_ask_0  = float(bar0['avg_ask'])
    opt_bid_0  = _quote_at(variant['opt_data'], bar0['date'], 'avg_bid', tag)
    if opt_bid_0 is None:
        return None
    lmt_per_share = (stk_ask_0 - float(opt_bid_0)) + BAG_LMT_ENTRY_BUFFER

    for k in range(ENTRY_BAG_TIMEOUT_BARS + 1):
        j = idx + k
        if j >= len(df):
            return None
        bar_j     = df.iloc[j]
        stk_ask_j = float(bar_j['avg_ask'])
        opt_bid_j = _quote_at(variant['opt_data'], bar_j['date'], 'avg_bid', tag)
        if opt_bid_j is None:
            continue
        combo_j = stk_ask_j - float(opt_bid_j)
        if combo_j <= lmt_per_share:
            return {
                'fill_idx':     j,
                'stock_fill_px': stk_ask_j,
                'opt_fill_bid': float(opt_bid_j),
                'lmt':          lmt_per_share,
            }
    return None


def _try_exit_bag(
    df:         pd.DataFrame,
    idx:        int,
    position:   dict,
    tag:        str,
) -> dict | None:
    """
    BAG exit: submit a SELL combo LMT at per-share net credit − buffer at
    bar `idx`.  Walk idx … idx+EXIT_BAG_TIMEOUT_BARS looking for a bar whose
    market combo credit (stock_bid − opt_ask) clears the LMT.  Fill at the
    observed market prices of that bar.
    """
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
        stk_bid_j = float(bar_j['avg_bid'])
        opt_ask_j = _quote_at(opt_data, bar_j['date'], 'avg_ask', tag)
        if opt_ask_j is None:
            continue
        combo_j = stk_bid_j - float(opt_ask_j)
        if combo_j >= lmt_per_share:
            return {
                'fill_idx':     j,
                'stock_fill_px': stk_bid_j,
                'opt_fill_ask': float(opt_ask_j),
                'lmt':          lmt_per_share,
            }
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


def _book_close(
    pos:        dict,
    exit_time:  pd.Timestamp,
    exit_reason: str,
    stock_exit_px: float,
    cc_close_price: float | None,
) -> dict:
    """Compute P&L for a closed position and return the trade record."""
    shares        = pos['shares']
    stock_pnl     = (stock_exit_px - pos['entry_stock_px']) * shares - COMMISSION
    if cc_close_price is not None:
        option_pnl   = (pos['cc_open_price'] - cc_close_price) * shares - COMMISSION
        combined     = stock_pnl + option_pnl
        is_winner    = combined > 0
    else:
        option_pnl   = None
        combined     = None
        is_winner    = None
    pos_days = (pd.Timestamp(exit_time) - pd.Timestamp(pos['entry_time'])).total_seconds() / 86400

    return {
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
        **{k: pos[k] for k in ('atr_at_entry', 'rsi_at_entry',
                                'adx_at_entry', 'vwap_at_entry')},
    }


# ══════════════════════════════════════════════════════════════════════════════
# REPLAY LOOP
# ══════════════════════════════════════════════════════════════════════════════

def _ses_minute(ts: pd.Timestamp) -> int:
    return int(ts.hour) * 60 + int(ts.minute)


def _is_friday_expiry_moment(bar_dt: pd.Timestamp, expiry_date) -> bool:
    return (
        bar_dt.date() == pd.Timestamp(expiry_date).date() and
        _ses_minute(bar_dt) >= EOD_MINUTE
    )


def replay(df: pd.DataFrame, strategies: list, starting_cash: float) -> dict:
    """
    Sequential replay of bars.  Returns a dict with trades, transactions,
    and per-strategy funnel counters.
    """
    trend_any_arr = df[BSIG_TREND_COLS].to_numpy().any(axis=1)
    mom_any_arr   = df[BSIG_MOMENTUM_COLS].to_numpy().any(axis=1)

    pos_counter    = [0]
    open_positions: dict[str, dict] = {}
    trades:        list[dict] = []
    transactions:  list[dict] = []

    funnel = [
        {'signal_skip': 0, 'no_quote': 0, 'tv_fail_low': 0, 'tv_fail_high': 0,
         'cooldown_skip': 0, 'cash_skip': 0, 'bag_timeout': 0,
         'no_strike': 0, 'accepted': 0,
         'last_entry_ts': None}
        for _ in strategies
    ]

    def _cash() -> float:
        used = sum(p['cash_used'] for p in open_positions.values())
        return float(starting_cash) - used

    for idx in range(len(df)):
        bar    = df.iloc[idx]
        bar_dt = pd.Timestamp(bar['date'])

        # ── Exits on currently open positions ────────────────────────────────
        to_drop = []
        for pos_id, pos in list(open_positions.items()):
            if pos['fill_idx'] >= idx:
                continue  # entry hasn't filled yet or filled THIS bar

            # Friday expiry (assignment / expired_otm)
            if _is_friday_expiry_moment(bar_dt, pos['expiry_date']):
                stock_bid = float(bar['avg_bid'])
                if stock_bid > pos['strike']:
                    exit_px  = float(pos['strike'])
                    reason   = 'assigned'
                else:
                    exit_px  = stock_bid
                    reason   = 'expired_otm'
                trade = _book_close(pos, bar_dt, reason, exit_px, 0.0)
                trades.append(trade)
                transactions.extend(_txn_rows_close(pos, bar_dt, reason,
                                                    exit_px, 0.0))
                to_drop.append(pos_id)
                log.info(f"[expiry] {pos_id} {reason} at {bar_dt} "
                         f"strike={pos['strike']} stk_bid={stock_bid} "
                         f"combined=${trade['combined_pnl']}")
                continue

            # Buyback TV gate — try an exit BAG when (ask − intrinsic) < buyback_tv
            stock_bid = float(bar['avg_bid'])
            opt_ask   = _quote_at(pos['opt_data'], bar_dt, 'avg_ask',
                                    pos['tag'])
            if opt_ask is None:
                continue
            intrinsic = max(0.0, stock_bid - pos['strike'])
            tv_ask    = float(opt_ask) - intrinsic
            if tv_ask >= pos['buyback_tv']:
                continue

            fill = _try_exit_bag(df, idx, pos, pos['tag'])
            if fill is None:
                continue
            trade = _book_close(
                pos, df.iloc[fill['fill_idx']]['date'], 'buyback',
                fill['stock_fill_px'], fill['opt_fill_ask'],
            )
            trades.append(trade)
            transactions.extend(_txn_rows_close(
                pos, df.iloc[fill['fill_idx']]['date'], 'buyback',
                fill['stock_fill_px'], fill['opt_fill_ask'],
            ))
            to_drop.append(pos_id)
            log.info(
                f"[exit] {pos_id} buyback filled at idx={fill['fill_idx']} "
                f"tv={tv_ask:.4f} stk_bid={fill['stock_fill_px']} "
                f"opt_ask={fill['opt_fill_ask']} combined=${trade['combined_pnl']}"
            )
        for pos_id in to_drop:
            del open_positions[pos_id]

        # ── Entries: session floor + per-strategy gates ──────────────────────
        if _ses_minute(bar_dt) < ENTRY_EARLIEST_MINUTE:
            continue

        trend_any = bool(trend_any_arr[idx])
        mom_any   = bool(mom_any_arr[idx])
        entry_px  = float(bar['avg_ask'])
        snap      = _entry_snapshot(bar)

        for strat_id, strat in enumerate(strategies):
            st = funnel[strat_id]
            # 1) signal gate
            if not _signal_mode_fires(strat['signal_mode'], trend_any, mom_any):
                st['signal_skip'] += 1
                continue
            # 2) cooldown
            cd = _dt.timedelta(minutes=int(strat['cooldown_minutes']))
            if st['last_entry_ts'] is not None and (bar_dt - st['last_entry_ts']) < cd:
                st['cooldown_skip'] += 1
                continue
            # 3) cash
            shares = int(strat['shares_per_position'])
            cost   = entry_px * shares
            if _cash() < cost:
                st['cash_skip'] += 1
                continue
            # 4) resolve strike + contract
            variant = find_cc_variant(
                bar_dt.date(), bar_dt, entry_px,
                strat['expiry_label'], strat['strike_label'],
            )
            if variant is None:
                st['no_strike'] += 1
                continue
            tag = f"{strat['expiry_label']}/{strat['strike_label']}"
            # 5) fresh quote + TV gate (evaluated at firing bar)
            opt_bid_firing = _quote_at(variant['opt_data'], bar_dt, 'avg_bid', tag)
            if opt_bid_firing is None:
                st['no_quote'] += 1
                continue
            cc_tv = float(opt_bid_firing) - max(0.0, entry_px - variant['strike'])
            tv_min, tv_max = float(strat['cc_tv_min']), float(strat['cc_tv_max'])
            if cc_tv < tv_min:
                st['tv_fail_low']  += 1
                continue
            if cc_tv > tv_max:
                st['tv_fail_high'] += 1
                continue
            # 6) BAG fill (option ii: 2-bar timeout, fill at market combo price)
            fill = _try_entry_bag(df, idx, variant, shares, tag)
            if fill is None:
                st['bag_timeout'] += 1
                log.info(f"[entry] strat={strat_id} {tag} BAG timeout at "
                         f"{bar_dt} lmt would've been "
                         f"{(entry_px - opt_bid_firing) + BAG_LMT_ENTRY_BUFFER:.2f}")
                continue

            # Accepted
            st['accepted']     += 1
            st['last_entry_ts'] = bar_dt
            pos_id = _new_position_id(pos_counter)
            pos = {
                'position_id':    pos_id,
                'strategy_id':    strat_id,
                'signal_mode':    strat['signal_mode'],
                'expiry_label':   strat['expiry_label'],
                'strike_label':   strat['strike_label'],
                'variant_key':    variant['variant_key'],
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
                **snap,
            }
            open_positions[pos_id] = pos
            transactions.extend(_txn_rows_open(pos, fill))
            log.info(
                f"[entry] strat={strat_id} {tag} OPEN {pos_id} at idx={fill['fill_idx']} "
                f"stk={fill['stock_fill_px']} opt_bid={fill['opt_fill_bid']} "
                f"cc_tv={cc_tv:.4f} shares={shares}"
            )

    # ── End of replay: open_at_end for anything still held ────────────────────
    for pos_id, pos in open_positions.items():
        last_bar = df.iloc[-1]
        trade = _book_close(pos, pd.Timestamp(last_bar['date']),
                            'open_at_end',
                            float(last_bar['avg_bid']), None)
        trade['status'] = 'open_at_end'
        trades.append(trade)
        log.info(f"[eod] {pos_id} open_at_end — no P&L booked")

    return {
        'trades':       trades,
        'transactions': transactions,
        'funnel':       funnel,
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
         'leg': 'stock', 'action': 'BUY',  'symbol': SYMBOL,
         'local_symbol': SYMBOL, 'sec_type': 'STK',
         'quantity': shares, 'price': round(fill['stock_fill_px'], 4),
         'order_id': '', 'reason': reason},
        {'timestamp': ts, 'position_id': pos['position_id'],
         'strategy_id': pos['strategy_id'], 'signal_mode': pos['signal_mode'],
         'leg': 'option', 'action': 'SELL', 'symbol': SYMBOL,
         'local_symbol': _local_symbol(pos['expiry_date'], pos['strike']),
         'sec_type': 'OPT', 'quantity': shares // 100,
         'price': round(fill['opt_fill_bid'], 4),
         'order_id': '', 'reason': reason},
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
         'price': round(float(opt_close_px), 4),
         'order_id': '', 'reason': reason},
    ]


# ══════════════════════════════════════════════════════════════════════════════
# OUTPUT WRITERS
# ══════════════════════════════════════════════════════════════════════════════

def write_trades_csv(run_dir: Path, trades: list[dict]) -> Path:
    path = run_dir / 'trades.csv'
    col_order = [
        'position_id', 'strategy_id', 'signal_mode',
        'expiry_label', 'strike_label', 'strike', 'expiry_date',
        'entry_time', 'exit_time',
        'entry_stock_price', 'stock_exit_price',
        'cc_open_price', 'cc_tv_at_entry', 'cc_close_price',
        'exit_reason', 'days_held', 'shares', 'status',
        'stock_pnl', 'option_pnl', 'combined_pnl', 'is_winner',
        'atr_at_entry', 'rsi_at_entry', 'adx_at_entry', 'vwap_at_entry',
    ]
    if not trades:
        pd.DataFrame(columns=col_order).to_csv(path, index=False)
        return path
    df = pd.DataFrame(trades)
    cols = [c for c in col_order if c in df.columns] + \
           [c for c in df.columns if c not in col_order]
    df[cols].to_csv(path, index=False)
    return path


def write_summary_csv(run_dir: Path, trades: list[dict],
                      strategies: list[dict], funnel: list[dict]) -> Path:
    path = run_dir / 'summary.csv'
    rows = []
    closed_trades = [t for t in trades if t['status'] == 'closed']
    by_strat: dict[int, list] = defaultdict(list)
    for t in closed_trades:
        by_strat[t['strategy_id']].append(t)

    for sid, strat in enumerate(strategies):
        ts_list = by_strat.get(sid, [])
        if ts_list:
            pnls     = [t['combined_pnl'] for t in ts_list]
            wins     = [p for p in pnls if p > 0]
            win_rate = round(len(wins) / len(pnls) * 100, 2)
            total    = round(sum(pnls), 2)
            avg      = round(total / len(pnls), 2) if pnls else 0.0
        else:
            win_rate = 0.0
            total    = 0.0
            avg      = 0.0
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
            **{k: funnel[sid][k] for k in
               ('signal_skip', 'cooldown_skip', 'cash_skip', 'no_strike',
                'no_quote', 'tv_fail_low', 'tv_fail_high', 'bag_timeout',
                'accepted')},
        })
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def write_transaction_csv(run_dir: Path, transactions: list[dict]) -> Path:
    path = run_dir / 'transaction.csv'
    with open(path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=_TX_COLS, extrasaction='ignore')
        w.writeheader()
        for row in transactions:
            w.writerow(row)
    return path


def write_markdown_report(run_dir: Path, params: dict, run_ts: str,
                          trades: list[dict], strategies: list[dict],
                          funnel: list[dict], missing: dict,
                          data_first: str, data_last: str,
                          argv: list[str]) -> Path:
    path   = run_dir / 'batch_run_analysis.md'
    closed = [t for t in trades if t['status'] == 'closed']
    pnls   = [t['combined_pnl'] for t in closed]
    total_pnl = round(sum(pnls), 2) if pnls else 0.0
    win_rate  = round(sum(1 for p in pnls if p > 0) / len(pnls) * 100, 2) if pnls else 0.0

    lines = [f"# Model 6 — paper2 historical replay", ""]
    lines.append(f"_Generated: {_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_")
    lines.append("")
    lines.append("## Run Parameters")
    lines.append("| Parameter | Value |")
    lines.append("|---|---|")
    lines.append(f"| Run directory | `{run_dir.name}` |")
    lines.append(f"| Symbol | {params.get('symbol', SYMBOL)} |")
    lines.append(f"| Date range | {data_first} → {data_last} |")
    lines.append(f"| Starting cash | ${params.get('starting_cash',0):,.0f} |")
    lines.append(f"| Strategies | {len(strategies)} |")
    lines.append(f"| MAX_QUOTE_AGE_MINUTES | {MAX_QUOTE_AGE_MINUTES} |")
    lines.append(f"| ENTRY_EARLIEST_MINUTE | {ENTRY_EARLIEST_MINUTE//60:02d}:{ENTRY_EARLIEST_MINUTE%60:02d} |")
    lines.append(f"| EOD_MINUTE (Friday expiry) | {EOD_MINUTE//60:02d}:{EOD_MINUTE%60:02d} |")
    lines.append(f"| BAG entry timeout | {ENTRY_BAG_TIMEOUT_BARS} bars |")
    lines.append(f"| BAG exit  timeout | {EXIT_BAG_TIMEOUT_BARS} bars |")
    lines.append(f"| BAG entry buffer  | ${BAG_LMT_ENTRY_BUFFER:.2f} |")
    lines.append(f"| BAG exit  buffer  | ${BAG_LMT_EXIT_BUFFER:.2f} |")
    lines.append(f"| Commission | ${COMMISSION:.2f} per leg |")
    lines.append(f"| Command | `{' '.join(argv)}` |")
    lines.append("")

    lines.append("## Strategies")
    strat_df = pd.DataFrame(strategies)
    strat_df.insert(0, 'strategy_id', range(len(strategies)))
    lines.append(md_table(strat_df, n=len(strat_df)))
    lines.append("")

    lines.append("## Summary")
    lines.append(f"- Total closed trades: **{len(closed)}**")
    lines.append(f"- Open-at-end: **{sum(1 for t in trades if t['status'] == 'open_at_end')}**")
    lines.append(f"- Win rate: **{win_rate:.2f}%**")
    lines.append(f"- Total P&L: **${total_pnl:,.2f}**")
    lines.append("")

    lines.append("## Per-strategy Funnel")
    lines.append("| strat | signal_skip | cooldown | cash | no_strike | no_quote | tv_low | tv_high | bag_timeout | accepted |")
    lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for sid, f in enumerate(funnel):
        lines.append(
            f"| {sid} | {f['signal_skip']:,} | {f['cooldown_skip']:,} | {f['cash_skip']:,} | "
            f"{f['no_strike']:,} | {f['no_quote']:,} | {f['tv_fail_low']:,} | "
            f"{f['tv_fail_high']:,} | {f['bag_timeout']:,} | {f['accepted']:,} |"
        )
    lines.append("")

    if missing:
        lines.append("## Missing / stale quotes (first occurrence per day counted)")
        rows = sorted(missing.items(), key=lambda kv: (str(kv[0][1]), kv[0][0]))
        lines.append("| tag | day | count |")
        lines.append("|---|---|---:|")
        for (tag, day), n in rows[:50]:
            lines.append(f"| {tag} | {day} | {n:,} |")
        if len(rows) > 50:
            lines.append(f"_… {len(rows)-50} additional rows omitted._")
        lines.append("")

    path.write_text('\n'.join(lines), encoding='utf-8')
    return path


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(
        description='Historical replay of paper2 covered-call strategy '
                    '(same params, signal_mode, TV gates, BAG fill semantics).'
    )
    ap.add_argument('--data-first', required=True,
                    help='First date of replay range (YYYY-MM-DD)')
    ap.add_argument('--data-last',  required=True,
                    help='Last date of replay range, inclusive (YYYY-MM-DD)')
    ap.add_argument('--params', type=str, default=str(DEFAULT_PARAMS_FILE),
                    help='Path to params.json (default: ./params.json)')
    args = ap.parse_args()

    run_ts  = _dt.datetime.now().strftime('%m%d%H%M')
    run_dir = REPORTS_DIR / run_ts
    run_dir.mkdir(parents=True, exist_ok=True)

    global log
    log = _setup_logger(run_dir)

    # Save a copy of the params file for this run's reports
    params_path = Path(args.params)
    shutil.copy(params_path, run_dir / 'params.json')

    params = load_params(params_path)
    log.info(f"=" * 72)
    log.info(f"model/6_paper2 — historical replay  (run_ts={run_ts})")
    log.info(f"  symbol={params['symbol']}  starting_cash=${params['starting_cash']:,}  "
             f"date range: {args.data_first} → {args.data_last}")
    log.info(f"  strategies: {len(params['strategies'])}")
    for i, s in enumerate(params['strategies']):
        log.info(f"    [{i}] shares={s['shares_per_position']}  "
                 f"cooldown={s['cooldown_minutes']}m  "
                 f"cc_tv=[{s['cc_tv_min']},{s['cc_tv_max']}]  "
                 f"buyback_tv={s['buyback_tv']}  "
                 f"{s['expiry_label']}/{s['strike_label']}  "
                 f"mode={s['signal_mode']}")
    log.info(f"  MAX_QUOTE_AGE_MINUTES={MAX_QUOTE_AGE_MINUTES}  "
             f"entry_timeout={ENTRY_BAG_TIMEOUT_BARS}b  "
             f"exit_timeout={EXIT_BAG_TIMEOUT_BARS}b")
    log.info(f"=" * 72)

    df = load_signals_window(args.data_first, args.data_last)

    result = replay(df, params['strategies'], float(params['starting_cash']))

    trades_path = write_trades_csv(run_dir, result['trades'])
    log.info(f"[report] trades      -> {trades_path}")
    summary_path = write_summary_csv(run_dir, result['trades'],
                                      params['strategies'], result['funnel'])
    log.info(f"[report] summary     -> {summary_path}")
    txn_path = write_transaction_csv(run_dir, result['transactions'])
    log.info(f"[report] transaction -> {txn_path}")
    md_path  = write_markdown_report(
        run_dir, params, run_ts, result['trades'], params['strategies'],
        result['funnel'], result['missing_quotes'],
        args.data_first, args.data_last, sys.argv,
    )
    log.info(f"[report] markdown    -> {md_path}")

    closed = [t for t in result['trades'] if t['status'] == 'closed']
    open_end = [t for t in result['trades'] if t['status'] == 'open_at_end']
    total_pnl = sum(t['combined_pnl'] for t in closed) if closed else 0.0
    log.info(f"=" * 72)
    log.info(f"  closed trades: {len(closed):,}   open_at_end: {len(open_end):,}")
    log.info(f"  total P&L: ${total_pnl:,.2f}")
    log.info(f"  per-strategy accepted: "
             + ", ".join(f"s{i}={f['accepted']}" for i, f in enumerate(result['funnel'])))
    log.info(f"  output: {run_dir}")
    log.info(f"=" * 72)


if __name__ == '__main__':
    main()
