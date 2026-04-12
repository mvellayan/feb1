# Indicator Combination Analysis
**Scope:** 7 model run directories · 5,679 total model rows
**Filters applied:** positive avg PnL · appearance rate ≥ 80% · win rate > 44%
**Date:** April 2, 2026

---

## 1. Methodology

Each run directory contains an `analysis_models.csv` file ranking four-indicator combinations (one each from trend, momentum, volatility, and volume categories) across 100 randomised batches. The analysis pools all seven directories, aggregates per-combination metrics across runs, and requires a combination to appear in **all 7 runs** to be considered robust. Three scoring dimensions are used:

- **Activity** — average trades per run
- **Success Rate** — win rate (%) and hit rate (% of runs with positive PnL)
- **Loss Control** — PnL standard deviation (lower = better)

---

## 2. Single-Indicator Rankings

### 2.1 Trend Indicators
*(appearance rate ≥ 80%, averaged across 7 runs)*

| Rank | Indicator | Avg Trades | Win Rate | Avg PnL | PnL Std | Hit Rate | PF | Consistency |
|------|-----------|-----------|---------|---------|---------|---------|-----|-------------|
| 1 | **SAR** | 2.6 | 43.84% | 12.96 | 89.1 | 48.7% | 3.400 | 40.75 |
| 2 | EMA | 5.5 | 41.95% | 8.88 | 147.0 | 46.3% | 2.609 | 39.69 |
| 3 | ARN | 5.5 | 41.45% | 5.44 | 147.3 | 46.5% | 2.672 | 39.57 |
| 4 | MACD | 4.6 | 40.95% | 3.27 | 124.2 | 44.8% | 2.724 | 38.38 |
| 5 | ADX | 2.8 | 42.03% | 9.84 | 106.9 | 44.7% | 3.248 | 37.17 |
| 6 | DON | 3.0 | 40.34% | 6.92 | 91.3 | 44.7% | 2.848 | 37.10 |
| 7 | VTX | 2.4 | 40.13% | 4.04 | 88.1 | 42.7% | 3.100 | 35.87 |

**Key observations:** SAR leads on PnL, profit factor, and consistency. ARN ranks 3rd standalone but significantly outperforms in combination (see Section 4). ADX has the tightest PnL std of the mid-tier indicators.

### 2.2 Momentum Indicators

| Rank | Indicator | Avg Trades | Win Rate | Avg PnL | PnL Std | Hit Rate | PF | Consistency |
|------|-----------|-----------|---------|---------|---------|---------|-----|-------------|
| 1 | **RSI** | 4.6 | 43.29% | 11.39 | 134.5 | 47.6% | 2.930 | 40.49 |
| 2 | TSI | 4.1 | 42.81% | 12.02 | 125.0 | 47.4% | 3.010 | 40.24 |
| 3 | FRC | 3.6 | 43.43% | 12.84 | 113.5 | 47.4% | 3.172 | 40.01 |
| 4 | ROC | 5.7 | 42.21% | 10.17 | 150.8 | 46.8% | 2.667 | 39.75 |
| 5 | RMI | 4.0 | 43.04% | 6.96 | 119.0 | 46.6% | 2.985 | 39.64 |
| 6 | CCI | 2.9 | 40.20% | 8.00 | 94.4 | 44.9% | 2.987 | 37.81 |
| 7 | STO | 3.4 | 40.46% | 2.46 | 106.1 | 44.5% | 2.875 | 37.47 |
| 8 | CMO | 3.4 | 39.90% | 5.53 | 108.2 | 44.3% | 2.887 | 36.98 |
| 9 | MACD | 3.2 | 39.89% | 2.97 | 105.8 | 43.3% | 2.930 | 36.22 |
| 10 | SRSI | 2.9 | 39.91% | **-3.61** | 99.6 | 41.8% | 2.956 | 35.15 |

**Key observations:** RSI, TSI, and FRC form a clear top tier. FRC has the lowest PnL std of the top three, making it the best momentum choice when loss control is prioritised. SRSI is the only momentum indicator with a negative average PnL — excluded from top combinations.

### 2.3 Volatility Indicators

| Rank | Indicator | Avg Trades | Win Rate | Avg PnL | PnL Std | Hit Rate | PF | Consistency |
|------|-----------|-----------|---------|---------|---------|---------|-----|-------------|
| 1 | **BBD** | 4.7 | 43.18% | 7.81 | 129.3 | 47.3% | 2.837 | 40.63 |
| 2 | ATR | 2.3 | 38.18% | 3.31 | 90.2 | 42.1% | 3.044 | 34.39 |

**Key observations:** BBD (Bollinger Bands) is the dominant volatility filter across the entire dataset — it appears in virtually every high-performing four-indicator combination. ATR has lower absolute metrics but tighter PnL std; it surfaces in specific niche combos (e.g., `SAR + CCI + ATR + KLG`).

### 2.4 Volume Indicators

| Rank | Indicator | Avg Trades | Win Rate | Avg PnL | PnL Std | Hit Rate | PF | Consistency |
|------|-----------|-----------|---------|---------|---------|---------|-----|-------------|
| 1 | **FRC** | 4.1 | 44.11% | 15.66 | 120.4 | 48.9% | 3.088 | 41.69 |
| 2 | OBV | 5.9 | 41.82% | 9.88 | 148.1 | 47.6% | 2.549 | 40.80 |
| 3 | VRC | 2.6 | 42.17% | 2.99 | 86.7 | 45.7% | 3.104 | 38.20 |
| 4 | VWAP | 5.0 | 40.09% | 2.30 | 140.0 | 44.0% | 2.493 | 37.69 |
| 5 | MFI | 1.9 | 42.29% | 8.72 | 81.7 | 45.3% | 3.538 | 37.10 |
| 6 | KLG | 1.8 | 38.95% | 3.56 | 76.7 | 40.9% | 3.283 | 33.74 |

**Key observations:** FRC leads on all primary metrics. VRC has the lowest PnL std of any volume indicator and acts as a strong loss-variance suppressor in four-indicator combinations — its combination with BBD is a recurring motif in the top models. OBV drives the highest trade counts when paired with EMA or ARN.

---

## 3. Structural Findings

### BBD is Non-Negotiable
Every robust four-indicator combination (present in all 7 runs, positive PnL, win rate > 44%) includes BBD as the volatility component. It functions as a regime filter that suppresses low-quality signals before they reach execution.

### VRC vs FRC: The Volume Tradeoff
These two volume indicators define the two sub-regimes within the top combinations:
- **VRC** — lower trade count, tighter PnL std, higher profit factor. Pairs with lower-activity trend indicators (ADX, DON, ARN at low counts).
- **FRC** — higher trade count, higher absolute PnL, slightly wider variance. Pairs well with ARN and EMA for medium-to-high volume strategies.
- **OBV** — maximum trade volume but largest PnL std. Best when raw throughput is the objective.

### ARN's Combination Premium
ARN ranks 3rd in the standalone trend analysis with modest metrics. However, it appears in more "all-7-run" robust four-indicator models than any other trend indicator, and those models consistently show hit rates above 55–61%. ARN appears to be a trend filter that reduces false signals when paired with BBD — its standalone weakness is offset by the combination's filtering effect.

### The 3-vs-4 Indicator Question
Deriving three-indicator aggregates from the four-indicator models shows that specifying the fourth indicator reduces PnL std significantly in specific cases:
- `DON + RSI + BBD` (3 indicators): avg pnl_std = 93
- `DON + RSI + BBD + VRC` (4 indicators): avg pnl_std = **54** (−42%)

Adding the fourth indicator is therefore recommended when the goal is loss variance minimisation.

---

## 4. Top 5 Recommended Combinations

All five combinations satisfy: present in all 7 runs · positive PnL · appearance rate ≥ 80% · win rate > 44%.

---

### #1 — ARN + FRC + BBD + VRC
**Profile: Best Overall Balance**

| Run | Trades | Win Rate | Avg PnL | PnL Std | PF | Hit Rate | Sharpe | Consistency |
|-----|--------|---------|---------|---------|-----|---------|--------|-------------|
| 04011201 | 3.6 | 52.45% | 25.76 | 110.22 | 4.002 | 55.91% | −3.008 | 49.61 |
| 04011348 | 4.0 | 51.06% | 44.63 | 101.20 | 3.537 | 63.44% | −1.185 | 54.87 |
| 04011349 | 3.9 | 50.54% | 25.56 | 113.13 | 3.662 | 61.29% | −9.768 | 52.21 |
| 04011350 | 4.1 | 50.48% | 42.48 | 123.31 | 3.502 | 60.64% | −2.540 | 52.64 |
| 04011351 | 4.4 | 50.30% | 33.38 | 115.91 | 3.106 | 61.05% | +9.577 | 53.88 |
| 04011353 | 4.1 | 50.93% | 41.95 | 118.53 | 3.864 | 62.37% | +3.669 | 54.10 |
| 04011354 | 4.0 | 51.79% | 35.72 | 137.90 | 4.009 | 61.62% | +0.316 | 52.89 |
| **AVG** | **4.0** | **51.08%** | **35.64** | **117.17** | **3.669** | **60.90%** | **−0.420** | **52.89** |

**Strengths:** Highest hit rate of all five combinations (60.9%) — six in ten model runs are profitable. Win rate consistently above 50% across every run. Profit factor of 3.67 indicates wins are substantially larger than losses.
**Weaknesses:** Sharpe ratio is negative in most runs, indicating high return variance relative to mean. PnL std is moderate (117), not the lowest in the set.
**Best used when:** Maximum hit rate consistency is the priority across varying market conditions.

---

### #2 — ADX + ROC + BBD + VRC
**Profile: Tightest Risk / Highest Win Rate**

| Run | Trades | Win Rate | Avg PnL | PnL Std | PF | Hit Rate | Sharpe | Consistency |
|-----|--------|---------|---------|---------|-----|---------|--------|-------------|
| 04011201 | 2.5 | 54.33% | 10.77 | 74.67 | 3.931 | 57.29% | +3.461 | 48.01 |
| 04011348 | 2.8 | 55.53% | 15.39 | 74.70 | 3.875 | 58.43% | +4.001 | 50.12 |
| 04011349 | 2.7 | 49.21% | 11.82 | 100.33 | 3.200 | 43.02% | +3.092 | 37.66 |
| 04011350 | 2.9 | 54.65% | 30.25 | 112.01 | 3.690 | 52.27% | +4.317 | 45.61 |
| 04011351 | 2.7 | 55.07% | 15.66 | 90.65 | 4.122 | 54.95% | +6.792 | 47.14 |
| 04011353 | 2.8 | 58.42% | 36.99 | 100.53 | 4.392 | 64.04% | +8.827 | 53.16 |
| 04011354 | 3.0 | 54.31% | 26.73 | 101.26 | 3.781 | 59.14% | +8.492 | 50.94 |
| **AVG** | **2.8** | **54.50%** | **21.09** | **93.45** | **3.856** | **55.59%** | **+5.569** | **47.52** |

**Strengths:** Highest win rate (54.5%) and lowest PnL std (93.45) of all five combinations. The **only combination with a consistently positive Sharpe ratio** (avg +5.57). Six of seven runs show positive Sharpe, confirming risk-adjusted quality.
**Weaknesses:** Lowest trade count (2.8/run) — signals are infrequent. Run 04011349 is an outlier with a drop in win rate (49.2%) and hit rate (43%), indicating some regime sensitivity.
**Best used when:** Loss control and signal quality are the primary objectives. Suitable for risk-constrained strategies where fewer, higher-conviction trades are preferred.

---

### #3 — ARN + TSI + BBD + FRC
**Profile: Medium Volume / Balanced**

| Run | Trades | Win Rate | Avg PnL | PnL Std | PF | Hit Rate | Sharpe | Consistency |
|-----|--------|---------|---------|---------|-----|---------|--------|-------------|
| 04011201 | 7.1 | 48.65% | 29.12 | 146.99 | 2.530 | 57.58% | −0.571 | 50.45 |
| 04011348 | 7.0 | 53.25% | 58.74 | 160.12 | 2.955 | 58.59% | +1.324 | 52.66 |
| 04011349 | 7.0 | 48.09% | 11.74 | 155.34 | 2.472 | 53.61% | −0.116 | 47.40 |
| 04011350 | 7.0 | 49.89% | 44.64 | 153.97 | 2.961 | 58.00% | +2.608 | 51.62 |
| 04011351 | 7.6 | 50.85% | 44.59 | 154.09 | 2.488 | 61.00% | +0.268 | 53.30 |
| 04011353 | 6.9 | 47.39% | 43.32 | 139.79 | 2.980 | 61.62% | +1.082 | 53.24 |
| 04011354 | 7.1 | 50.24% | 33.19 | 146.50 | 3.056 | 60.00% | +2.775 | 53.38 |
| **AVG** | **7.1** | **49.77%** | **37.91** | **150.97** | **2.777** | **58.63%** | **+1.053** | **51.72** |

**Strengths:** Highest average absolute PnL of the five combinations (37.91). Trade count of 7.1/run offers materially more execution opportunities than #1 and #2 with positive average Sharpe. Hit rate of 58.6% remains strong.
**Weaknesses:** PnL std climbs to 151 — roughly 60% higher than #2. Profit factor (2.78) is the lowest of the top three, meaning the win/loss size ratio is less favourable. Run 04011349 shows the widest performance drop (PnL 11.74 vs avg 37.91).
**Best used when:** More frequent execution is needed but full high-volume deployment (OBV) is not warranted. The FRC volume filter keeps variance more controlled than OBV at similar trade counts.

---

### #4 — ARN + TSI + BBD + OBV
**Profile: High Volume**

| Run | Trades | Win Rate | Avg PnL | PnL Std | PF | Hit Rate | Sharpe | Consistency |
|-----|--------|---------|---------|---------|-----|---------|--------|-------------|
| 04011201 | 13.9 | 45.14% | 13.11 | 216.50 | 1.648 | 54.00% | −1.273 | 46.60 |
| 04011348 | 13.6 | 48.03% | 57.53 | 215.01 | 1.937 | 61.00% | +0.547 | 52.16 |
| 04011349 | 13.2 | 46.31% | 25.38 | 242.12 | 1.827 | 54.08% | −1.069 | 47.15 |
| 04011350 | 13.3 | 45.61% | 38.02 | 212.11 | 1.673 | 59.00% | −0.792 | 49.98 |
| 04011351 | 14.4 | 47.31% | 32.82 | 210.33 | 1.631 | 60.00% | −0.333 | 50.91 |
| 04011353 | 13.4 | 44.69% | 40.12 | 221.19 | 1.917 | 59.60% | −0.276 | 50.55 |
| 04011354 | 13.1 | 47.74% | 32.26 | 229.40 | 2.041 | 59.00% | −0.356 | 50.96 |
| **AVG** | **13.6** | **46.40%** | **34.18** | **220.95** | **1.811** | **58.10%** | **−0.507** | **49.76** |

**Strengths:** 13.6 trades/run provides the second-highest execution frequency. PnL std is remarkably stable across all seven runs (210–242 range), suggesting highly consistent behaviour across market conditions. Hit rate of 58.1% is solid.
**Weaknesses:** Profit factor of 1.811 is the lowest of the five combinations — wins are only ~1.8× losses, meaning the strategy is sensitive to run length and drawdown periods. Win rate sub-47% in most runs. Sharpe is negative in six of seven runs.
**Best used when:** Understanding the OBV vs FRC regime tradeoff at scale. Recommended as a benchmarking comparison against #3 rather than a primary deployment candidate.

---

### #5 — EMA + RSI + BBD + FRC
**Profile: Maximum Volume**

| Run | Trades | Win Rate | Avg PnL | PnL Std | PF | Hit Rate | Sharpe | Consistency |
|-----|--------|---------|---------|---------|-----|---------|--------|-------------|
| 04011201 | 15.7 | 47.66% | 51.58 | 253.54 | 2.059 | 54.00% | +1.170 | 47.72 |
| 04011348 | 15.7 | 49.14% | 102.47 | 269.57 | 2.097 | 59.00% | +1.582 | 51.32 |
| 04011349 | 15.5 | 44.64% | 22.64 | 277.96 | 1.763 | 51.00% | −0.280 | 44.72 |
| 04011350 | 15.7 | 46.14% | 54.59 | 253.24 | 1.635 | 60.00% | −0.140 | 50.68 |
| 04011351 | 16.4 | 48.83% | 80.87 | 265.84 | 2.014 | 60.00% | +1.078 | 51.79 |
| 04011353 | 15.6 | 45.49% | 55.44 | 263.01 | 2.128 | 56.00% | +0.646 | 48.69 |
| 04011354 | 15.7 | 47.29% | 49.71 | 235.45 | 1.845 | 57.00% | +0.253 | 49.28 |
| **AVG** | **15.8** | **47.03%** | **59.61** | **259.80** | **1.934** | **56.71%** | **+0.616** | **49.17** |

**Strengths:** Highest absolute average PnL (59.61) and highest trade count (15.8/run) of all five combinations. Sharpe is positive on average (+0.616) despite the high volume. Appearance rate is 100% in every run — this combination is present in every model configuration tested.
**Weaknesses:** PnL std of 259.80 is the highest of the five — loss variance is substantial. Run 04011349 is a significant outlier with PnL dropping to 22.64 (vs avg 59.61), indicating regime sensitivity. Profit factor of 1.934 is modest.
**Best used when:** Maximum capital deployment and throughput are the goal, and the strategy has sufficient capital cushion to absorb the higher variance. The FRC volume filter (vs OBV) is what keeps this combination's variance below the #4 threshold.

---

## 5. Comparison Summary

| | #1 ARN+FRC+BBD+VRC | #2 ADX+ROC+BBD+VRC | #3 ARN+TSI+BBD+FRC | #4 ARN+TSI+BBD+OBV | #5 EMA+RSI+BBD+FRC |
|---|---|---|---|---|---|
| **Profile** | Best Balance | Tightest Risk | Mid-Volume | High Volume | Max Volume |
| **Avg Trades** | 4.0 | 2.8 | 7.1 | 13.6 | 15.8 |
| **Win Rate** | 51.08% | **54.50%** | 49.77% | 46.40% | 47.03% |
| **Hit Rate** | **60.90%** | 55.59% | 58.63% | 58.10% | 56.71% |
| **Avg PnL** | 35.64 | 21.09 | 37.91 | 34.18 | **59.61** |
| **PnL Std Dev** | 117.17 | **93.45** | 150.97 | 220.95 | 259.80 |
| **Profit Factor** | **3.669** | 3.856 | 2.777 | 1.811 | 1.934 |
| **Avg Sharpe** | −0.420 | **+5.569** | +1.053 | −0.507 | +0.616 |
| **Consistency** | **52.89** | 47.52 | 51.72 | 49.76 | 49.17 |

---

## 6. Recommended Testing Order

1. **#2 first** (`ADX + ROC + BBD + VRC`) — cleanest signal, tightest risk, only positive Sharpe. Establishes the loss-control baseline.
2. **#1 second** (`ARN + FRC + BBD + VRC`) — highest hit rate, best composite score. Validates the ARN combination premium.
3. **#3 third** (`ARN + TSI + BBD + FRC`) — probes the volume-quality tradeoff. Compare directly against #1 to assess the cost of adding trade frequency.
4. **#4 fourth** (`ARN + TSI + BBD + OBV`) — isolates the FRC vs OBV volume decision. Run in parallel with #3 to measure the OBV regime impact cleanly.
5. **#5 last** (`EMA + RSI + BBD + FRC`) — characterises the high-frequency regime. Compare against #3 and #4 to determine where the EMA vs ARN trend tradeoff matters at scale.

---

## 7. Indicator Legend

| Code | Full Name | Category |
|------|-----------|----------|
| ARN | Aroon | Trend |
| ADX | Average Directional Index | Trend |
| EMA | Exponential Moving Average | Trend |
| SAR | Parabolic SAR | Trend |
| DON | Donchian Channel | Trend |
| MACD | Moving Average Convergence Divergence | Trend / Momentum |
| RSI | Relative Strength Index | Momentum |
| TSI | True Strength Index | Momentum |
| FRC | Force Index (volume-weighted momentum) | Momentum / Volume |
| ROC | Rate of Change | Momentum |
| RMI | Relative Momentum Index | Momentum |
| BBD | Bollinger Bands | Volatility |
| ATR | Average True Range | Volatility |
| VRC | Volume Rate of Change | Volume |
| OBV | On-Balance Volume | Volume |
| VWAP | Volume Weighted Average Price | Volume |
| MFI | Money Flow Index | Volume |

---

*Analysis generated from 7 run directories: 04011201, 04011348, 04011349, 04011350, 04011351, 04011353, 04011354*
