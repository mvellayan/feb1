"""
combo_order.py — test IBKR BAG (combo) order for a covered-call buy-write.

Submits a SINGLE BAG order (one orderId, priced on net debit) combining:
    leg 1:  BUY  100 shares AAPL            (ratio 100)
    leg 2:  SELL  1  call option            (ratio 1)
            strike = s-1   (2nd strike below current ask)
            expiry = w0    (this week's Friday)

Purpose
───────
Verify that IBKR BAG orders are accepted and routed successfully in the
target IB environment (paper TWS, live TWS, or IB Gateway).  BAG support
can differ per environment — paper accounts, IB Gateway builds, and
individual account permissions all factor in.  Running this script and
inspecting the result tells you whether arbo702's buy-write flow can be
migrated from sequential MKT orders to a single-order combo.

Uses clientId=88 (does NOT conflict with arbo701=1, arbo702=2, ib_test=99).

Order type
──────────
IB often rejects MKT on BAG orders, so this test uses LMT priced at the
per-share net debit (stock_ask − option_bid) plus a small buffer.  Set
USE_LMT=False to try MKT instead.

Flattens
────────
No — positions persist after the script exits.  Inspect in TWS and close
manually.

Run
───
    cd paper2/test
    python combo_order.py
"""

from __future__ import annotations

import importlib.util
import logging
import sys
import threading
import time
from datetime import date
from pathlib import Path

from ibapi.contract import ComboLeg, Contract
from ibapi.order import Order

# ── load arbo702 module by file path ──────────────────────────────────────────
_HERE   = Path(__file__).parent
_PAPER2 = _HERE.parent
sys.path.insert(0, str(_PAPER2))

_spec = importlib.util.spec_from_file_location('arbo702', _PAPER2 / 'arbo702.py')
arbo  = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(arbo)

# ── logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-7s  %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('combo_order')

# ── parameters for the test ───────────────────────────────────────────────────
# Standalone params — does NOT depend on paper2/params.json so the test can
# be run without touching production strategy config.
TEST_PARAMS = {
    'host':                arbo.PARAMS['host'],
    'port':                arbo.PARAMS['port'],
    'client_id':           88,       # dedicated for this smoke test
    'shares_per_position': 100,
    'expiry_label':        'w0',
    'strike_label':        's-1',
}

# True  → submit as LMT priced at net-debit + buffer (safer on most IB routes)
# False → submit as MKT (often rejected on BAG — useful if you want to verify
#         that rejection and distinguish "BAG not supported" from
#         "MKT on BAG not supported")
USE_LMT    = True
LMT_BUFFER = 0.10   # dollars added above net debit per share, for MKT-ish fills


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def wait_for_tick(app: arbo.IBApp, timeout: float = 15.0) -> bool:
    """Block until current_bid and current_ask have been populated."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if app._current_bid and app._current_ask:
            return True
        time.sleep(0.2)
    return False


def wait_for_order(app: arbo.IBApp, oid: int, timeout: float = 30.0,
                   terminal: tuple = ('Filled', 'Cancelled', 'Inactive', 'ApiCancelled',
                                      'PreSubmitted', 'Submitted')) -> dict:
    """
    Poll order_status until a terminal status is reached or timeout.
    'PreSubmitted' / 'Submitted' are treated as terminal for this test because
    pre-market BAGs legitimately park there until the exchange opens.
    """
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = app.order_status.get(oid, {})
        if st.get('status') in terminal:
            return st
        time.sleep(0.25)
    return app.order_status.get(oid, {'status': 'TIMEOUT'})


def get_stock_conid(app: arbo.IBApp, symbol: str, timeout: float = 10.0) -> int | None:
    """reqContractDetails on the stock contract; return the primary conId."""
    c = Contract()
    c.symbol     = symbol
    c.secType    = 'STK'
    c.exchange   = 'SMART'
    c.primaryExch = 'NASDAQ'
    c.currency   = 'USD'
    rid = app.next_req_id()
    app._cd_rows[rid] = []
    app._cd_done[rid] = threading.Event()
    app.reqContractDetails(rid, c)
    if not app._cd_done[rid].wait(timeout=timeout):
        log.warning(f"reqContractDetails timed out for {symbol}")
    rows = app._cd_rows.pop(rid, [])
    app._cd_done.pop(rid, None)
    return int(rows[0]['conId']) if rows else None


def find_option_conid(chain_df, strike: float, expiry_ib: str) -> int | None:
    """Look up the option conId already cached in contracts_{yymmdd}.csv."""
    sub = chain_df[(chain_df['expiry'] == expiry_ib) &
                   (chain_df['strike'].astype(float) == float(strike)) &
                   (chain_df['right'] == 'C')]
    if sub.empty:
        return None
    return int(sub.iloc[0]['conId'])


def build_buywrite_bag(stock_conid: int, option_conid: int) -> Contract:
    """
    BAG combo contract: BUY 100 shares + SELL 1 call.
    ratio 100 : 1 so "1 combo unit" = 100 shares + 1 option contract.
    """
    bag = Contract()
    bag.symbol   = arbo.SYMBOL
    bag.secType  = 'BAG'
    bag.currency = 'USD'
    bag.exchange = 'SMART'

    stk_leg = ComboLeg()
    stk_leg.conId    = stock_conid
    stk_leg.ratio    = 100
    stk_leg.action   = 'BUY'
    stk_leg.exchange = 'SMART'
    stk_leg.openClose = 0   # 0 = SAME_POS (default)

    opt_leg = ComboLeg()
    opt_leg.conId    = option_conid
    opt_leg.ratio    = 1
    opt_leg.action   = 'SELL'
    opt_leg.exchange = 'SMART'
    opt_leg.openClose = 0

    bag.comboLegs = [stk_leg, opt_leg]
    return bag


def build_bag_order(use_lmt: bool, net_debit_per_share: float) -> Order:
    """
    Build a 1-unit BAG order.  totalQuantity=1 means "one covered-call combo".

    LMT price = per-share net debit + buffer.  This is the standard
    convention for stock+option BAGs: IB prices the combo on the per-share
    stock basis with the option premium already netted in.
    """
    o = Order()
    o.action        = 'BUY'    # BUY the combo (pay net debit)
    o.totalQuantity = 1
    o.tif           = 'DAY'
    o.eTradeOnly    = False
    o.firmQuoteOnly = False
    if use_lmt:
        o.orderType = 'LMT'
        o.lmtPrice  = round(net_debit_per_share + LMT_BUFFER, 2)
    else:
        o.orderType = 'MKT'
    return o


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    log.info("=" * 72)
    log.info(f"combo_order.py — BAG buy-write test  "
             f"(strike={TEST_PARAMS['strike_label']}, expiry={TEST_PARAMS['expiry_label']}, "
             f"order_type={'LMT' if USE_LMT else 'MKT'})")
    log.info("=" * 72)

    # ── connect to TWS ────────────────────────────────────────────────────────
    app = arbo.IBApp()
    app.connect(TEST_PARAMS['host'], int(TEST_PARAMS['port']), int(TEST_PARAMS['client_id']))
    ib_thread = threading.Thread(target=app.run, daemon=True, name='ib-api')
    ib_thread.start()
    if not app._connected_event.wait(timeout=arbo.CONN_TIMEOUT):
        log.error(f"Could not connect to TWS at {TEST_PARAMS['host']}:{TEST_PARAMS['port']}")
        sys.exit(1)
    time.sleep(1)

    # ── live AAPL bid/ask ─────────────────────────────────────────────────────
    app.reqMktData(arbo.REQ_STK_MKTDATA, arbo._stk_contract(), '', False, False, [])
    log.info("waiting for AAPL bid/ask ticks ...")
    if not wait_for_tick(app, timeout=15.0):
        log.error("No bid/ask received within 15s — aborting")
        app.disconnect()
        sys.exit(1)
    stk_ask = float(app._current_ask)
    stk_bid = float(app._current_bid)
    log.info(f"AAPL live  bid={stk_bid}  ask={stk_ask}")

    # ── resolve expiry + strike ───────────────────────────────────────────────
    today  = date.today()
    friday = arbo.resolve_expiry_friday(today, TEST_PARAMS['expiry_label'])
    exp_ib = friday.strftime('%Y%m%d')
    log.info(f"target expiry: {friday}  ({exp_ib})")

    chain_df = arbo.ensure_contracts(app, today)
    strike = arbo.resolve_strike(chain_df, exp_ib, stk_ask, TEST_PARAMS['strike_label'])
    if strike is None:
        log.error(f"No {TEST_PARAMS['strike_label']} strike in chain for {exp_ib}")
        app.disconnect()
        sys.exit(1)
    log.info(f"resolved {TEST_PARAMS['strike_label']} → strike={strike}")

    # ── live option quote (for LMT pricing) ───────────────────────────────────
    # NOTE: do NOT set localSymbol on the Contract.  When localSymbol is
    # populated IB ignores symbol/strike/expiry/right and matches on the
    # exact OSI string, which must be the padded 21-char canonical form.
    # Letting IB resolve from the structured fields is far more reliable.
    opt_contract = arbo._option_contract(strike, exp_ib)
    opt_display  = f"{arbo.SYMBOL} {friday.strftime('%y%m%d')}C{int(strike * 1000):08d}"
    opt_bid, opt_ask = app.get_option_quote(opt_contract, timeout=8.0)

    # If neither side returned, we can't price the combo accurately.  This
    # is normal pre-market / post-close / on illiquid strikes.  Fall back to
    # a rough intrinsic-based estimate so the test can still exercise the
    # BAG plumbing; warn loudly so the user knows the limit is not live.
    intrinsic = max(0.0, stk_ask - strike)
    if opt_bid is None and opt_ask is None:
        est_premium = max(intrinsic, 0.50)
        log.warning(f"No option quote — market likely closed or strike illiquid")
        log.warning(f"Using estimated premium ${est_premium:.2f} (intrinsic+floor)")
        log.warning(f"To get a real quote, re-run during US options market hours "
                    f"(09:30–16:00 ET).")
        opt_bid = est_premium
    elif opt_bid is None:
        # Only ask came in — estimate bid with a $0.10 haircut
        opt_bid = max(0.01, float(opt_ask) - 0.10)
        log.warning(f"No bid — estimating bid=${opt_bid:.2f} from ask=${opt_ask:.2f}")

    cc_tv = opt_bid - intrinsic
    log.info(f"option  bid={opt_bid}  ask={opt_ask}  intrinsic={intrinsic:.4f}  "
             f"cc_tv={cc_tv:.4f}")

    # ── resolve conIds ────────────────────────────────────────────────────────
    stock_conid = get_stock_conid(app, arbo.SYMBOL)
    if stock_conid is None:
        log.error(f"Could not resolve {arbo.SYMBOL} stock conId — aborting")
        app.disconnect()
        sys.exit(1)
    option_conid = find_option_conid(chain_df, strike, exp_ib)
    if option_conid is None:
        log.error(f"Could not find option conId for strike={strike} exp={exp_ib}")
        app.disconnect()
        sys.exit(1)
    log.info(f"conIds: stock={stock_conid}  option={option_conid}")

    # ── build + submit BAG ────────────────────────────────────────────────────
    bag = build_buywrite_bag(stock_conid, option_conid)
    net_debit_per_share = stk_ask - opt_bid
    order = build_bag_order(USE_LMT, net_debit_per_share)

    bag_oid = app.next_order_id()
    log.info(f"→ placing BAG order  oid={bag_oid}  "
             f"legs=[BUY 100 STK conId={stock_conid} :: SELL 1 OPT conId={option_conid}]  "
             f"type={order.orderType}  "
             f"{'lmt=' + str(order.lmtPrice) + '  ' if USE_LMT else ''}"
             f"(net debit per share ≈ ${net_debit_per_share:.2f}, "
             f"total cash outlay ≈ ${net_debit_per_share * 100:.2f})")
    app.placeOrder(bag_oid, bag, order)

    status = wait_for_order(app, bag_oid, timeout=45.0)
    log.info(f"BAG order final status: {status}")

    # ── summary ───────────────────────────────────────────────────────────────
    log.info("=" * 72)
    log.info("SUMMARY")
    log.info(f"  BAG oid={bag_oid}  status={status.get('status')}  "
             f"filled={status.get('filled')}  avg={status.get('avg_fill')}")

    s = status.get('status', '')
    if s == 'Filled':
        log.info("  ✔ BAG orders ARE supported AND filled in this environment")
        log.info(f"  position now:  100 AAPL long  +  1 short call @ {strike} exp {exp_ib}")
        log.info("  close manually in TWS when done")
    elif s in ('PreSubmitted', 'Submitted'):
        log.info("  ✔ BAG orders ARE supported — order accepted and parked at exchange")
        log.info("  (PreSubmitted/Submitted is normal pre-market or during IB combo checks)")
        log.info("  cancel in TWS if you don't want it to fill at market open")
    elif s in ('Cancelled', 'Inactive', 'ApiCancelled'):
        log.info("  ✖ BAG order was rejected or cancelled — check TWS logs")
        log.info("  common causes: no stock+option combo permission on this account,")
        log.info("  exchange route mismatch, option illiquidity, or insufficient margin")
    else:
        log.info(f"  ? BAG order status unclear ({s}) — inspect TWS")
    log.info("=" * 72)

    app.disconnect()


if __name__ == '__main__':
    main()
