# model/

Contains all backtesting models. Each subdirectory is a self-contained model with its own
entry points, reports, and shared utilities.

## Subdirectories

### 1_tech_indicators_sock_trade/
**Strategy:** 4-indicator combinatorial backtest (1,221 models). Standard trailing stop-loss exit.

| File | Purpose |
|------|---------|
| `single_model.py` | Run one indicator combo across N randomised batches; writes `{nn}_run.log` and `{nn}_summary.csv` |
| `all_models.py` | Run all 1,221 combos in batch; writes summary CSV + markdown report per run |
| `signals.py` | Vectorised buy/sell signal logic for all 24 indicators (`add_buy_signals`, `add_sell_signals`) |
| `utils.py` | Shared constants, scoring, aggregation, `md_table()` — imported by both models |
| `reports/` | Output directory: `{seq_no}_summary.csv`, `{seq_no}_run.log`, markdown reports |

### 2_stop_loss_cc/
**Strategy:** Same entry logic as model 1. When trailing stop fires, pivots to a covered call
overlay instead of exiting stock. Holds through Friday expiry.

| File | Purpose |
|------|---------|
| `single_model.py` | Same structure as model 1; adds CC simulation, option data loading, `find_best_covered_call()` |
| `all_models.py` | Batch runner for all 1,221 combos with CC overlay |
| `reports/` | Output directory |

**Imports:** `signals.py` and `utils.py` from `1_tech_indicators_sock_trade/` via `sys.path.insert`.

## Shared Design
- `RANDOM_SEED`: pass `--seed N` to fix; omit for a non-repeating random seed (`secrets.randbelow(2**32)`)
- 1,221 valid combos = product(TREND×MOMENTUM×VOLATILITY×VOLUME) minus duplicate-indicator tuples
- Fixed 10,000-bar sample per run (same sample across all models for fair comparison)
- `utils.py` consistency score: 40% pnl_hit_rate + 25% sharpe_hit_rate + 20% win_rate + 15% profit_factor

## Execution
```shell
# Model 1
cd model/1_tech_indicators_sock_trade
python single_model.py --trend ema --momentum rsi --volatility atr --volume vwap
python all_models.py [--seed N]

# Model 2
cd model/2_stop_loss_cc
python single_model.py --trend ema --momentum rsi --volatility atr --volume vwap
python all_models.py [--seed N]
```
