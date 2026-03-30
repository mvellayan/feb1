# AAPL Intraday Backtest — Performance Report

_Generated: 2026-03-30 02:06:53_

## Configuration

| Parameter | Value |
|---|---|
| Date Range | 2022-01-01 → 2023-12-31 |
| Instrument | AAPL (1-minute bars) |
| Entry Window | After 10:00 AM |
| Exit Time-Box | 3:45 PM |
| Candidate Bars Sampled | 10,000 |
| Signal Combination | ADX AND EMA AND ATR AND VWAP |
| Bracket Method | ATR |
| ATR Stop Multiplier | 1.5× |
| Reward:Risk Ratio | 2.0:1 |

## Summary Statistics

| Metric | Value |
|---|---|
| Total Trades | 2 |
| Winning Trades | 1 (50.0%) |
| Losing Trades | 1 (50.0%) |
| Win Rate | **50.0%** |
| Total P&L (pts) | **+1.0000** |
| Average P&L per Trade | +0.5000 |
| Average Win | +1.0000 |
| Average Loss | +0.0000 |
| Payoff Ratio (Avg Win / Avg Loss) | inf |
| Profit Factor (Gross W / Gross L) | inf |
| Max Single Win | +1.0000 |
| Max Single Loss | +0.0000 |
| Max Drawdown (cumulative pts) | +0.0000 |
| Annualised Sharpe (daily PnL) | 11.22 |
| Average Bars Held | 307.0 mins |

## Signal Fire Rate

Percentage of executed trades where each indicator was True at entry:

| Indicator | Fire Rate |
|---|---|
| ADX | 100.0% |
| ATR | 100.0% |
| EMA | 100.0% |
| VWAP | 100.0% |

## Exit Reason Breakdown

| Exit Reason | Count | % of Trades |
|---|---|---|
| time_box | 2 | 100.0% |

## Exit Reason vs Outcome

| Exit Reason | Count | Avg P&L | Win Rate |
|---|---|---|---|
| time_box | 2 | +0.5000 | 50.0% |

## Monthly Performance

| Month | Trades | Total P&L | Win Rate |
|---|---|---|---|
| 2022-12 | 1 | +1.0000 | 100.0% |
| 2023-02 | 1 | +0.0000 | 0.0% |

## Best and Worst Trades

**Best Trade:**

| Field | Value |
|---|---|
| trade_date | 2022-12-12 |
| entry_time | 2022-12-12 11:05:00 |
| entry_price | 142 |
| exit_time | 2022-12-12 15:45:00 |
| exit_price | 143 |
| exit_reason | time_box |
| pnl_pts | 1 |
| pnl_pct | 0.7042 |
| bars_held | 280 |
| atr_at_entry | 0.7382 |
| rsi_at_entry | 92.8779 |

**Worst Trade:**

| Field | Value |
|---|---|
| trade_date | 2023-02-10 |
| entry_time | 2023-02-10 10:11:00 |
| entry_price | 150 |
| exit_time | 2023-02-10 15:45:00 |
| exit_price | 150 |
| exit_reason | time_box |
| pnl_pts | 0 |
| pnl_pct | 0.0 |
| bars_held | 334 |
| atr_at_entry | 0.7751 |
| rsi_at_entry | 55.8366 |

## Interpretation Notes

- **Profit Factor > 1.5** is generally considered viable; > 2.0 is strong.
- **Win Rate alone is insufficient** — a 40% win rate with 2:1 payoff ratio is profitable.
- **Sharpe > 1.0** annualised suggests the strategy generates returns proportional to its risk.
- **Time-box exits** that are profitable suggest the entry signal is valid but the bracket is too tight.
- **High stop-loss rate with negative avg PnL** suggests the stop multiplier should be widened.
- These results are **in-sample** and require walk-forward validation before drawing conclusions.
