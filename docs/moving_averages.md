# Moving Average Technical Indicators

## MACD - Moving Average Convergence Divergence

* Trend-following momentum indicator calculated by taking the difference of two moving averages of an asset price (typically 12-period MA and 26-period MA).
* A signal line is also calculated — a moving average (typically 9-period) of the MACD line.
* The MACD line cutting the signal line from below signals a bullish period; cutting from above signals a bearish period. This is called the crossover strategy.

**Warnings:**
* Too many false positive signals, especially during sideways markets
* Lagging indicator — trails behind actual price action
* Should be used in conjunction with other indicators

---

## Bollinger Bands & ATR (Average True Range)

* Both are **volatility-based indicators**.
* Bollinger Bands comprise two lines plotted n standard deviations (typically 2) from an m-period simple moving average (typically 20). The bands widen during periods of increased volatility and shrink during reduced volatility.
* ATR focuses on total price movement and conveys how wildly the market is swinging. It considers the following ranges for each period:
  * Difference between High and Low
  * Difference between High and previous period's Close
  * Difference between Low and previous period's Close
* Traders typically use them together as they approach volatility differently and are complementary.

---

## RSI - Relative Strength Index

* Momentum oscillator that measures the speed and change of price movements.
* RSI value oscillates between 0 and 100:
  * Values above 70 indicate the asset is in overbought territory
  * Values below 30 indicate oversold territory
* Assets can remain in overbought or oversold territories for extended durations.
* Calculation follows a two-step method where the second step acts as a smoothing technique (similar to calculating an exponential MA).

**Warnings:**
* Does not imply timing of a correction

---

## ADX - Average Directional Index

* Measures the strength of a trend.
* Values range from 0 to 100:
  * 0–25: Absent or weak trend
  * 25–50: Strong trend
  * 50–75: Very strong trend
  * 75–100: Extremely strong trend
* Non-directional — ADX conveys only the strength of a trend, not its direction.
* Calculation involves finding positive and negative directional movement (by comparing successive highs and lows) and then computing the smoothed average of their difference.

---

## Stochastic Oscillator

* Momentum-based indicator that measures the speed of price change.
* Based on the premise that momentum must reduce before a price reversal. Works well in trending markets.
* Calculation: `((Close - Lowest Low) / (Highest High - Lowest Low)) * 100`
* Values range from 0 to 100:
  * Above 80 indicates overbought
  * Below 20 indicates oversold
* Higher values signify the current price is near the highest price over the lookback period; lower values signify proximity to the lowest.

**Warnings:**
* Works only in trending markets — unreliable in sideways markets
* Should be used in conjunction with other indicators
