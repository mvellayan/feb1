# Moving Average Technical Indicators

## MACD - Moving Average Convergence Divergence

* Trend-following momentum indicator calculated by taking the difference of two moving averages of an asset price, typically the 12-period EMA and 26-period EMA.
* A signal line is also calculated as a moving average, typically a 9-period EMA, of the MACD line.
* The MACD line cutting the signal line from below signals a bullish period; cutting from above signals a bearish period. This is commonly called the crossover strategy.
* MACD is also often interpreted using the zero line:
  * Above zero suggests bullish momentum
  * Below zero suggests bearish momentum

**Starts working well when:**
* A clear trend begins to emerge
* Momentum is building in one direction
* There is enough price history to calculate the slow MA, signal line, and smoothing reliably

**Stops working well when:**
* The market is moving sideways or choppy
* Price is range-bound with frequent reversals
* Volatility spikes create repeated crossovers without follow-through

**Works well in conjunction with:**
* **ADX** to confirm whether a trend is actually strong enough to trust the MACD signal
* **RSI** to help distinguish whether momentum is becoming overextended
* **Bollinger Bands** to add volatility context around breakout or reversal attempts
* **Volume analysis** to confirm whether a crossover has real participation behind it

**Warnings:**
* Too many false positive signals, especially during sideways markets
* Lagging indicator — trails behind actual price action
* Crossovers can occur after a significant portion of the move has already happened
* Should be used in conjunction with other indicators rather than by itself

---

## Bollinger Bands & ATR (Average True Range)

* Both are **volatility-based indicators**.
* Bollinger Bands comprise two lines plotted n standard deviations, typically 2, from an m-period simple moving average, typically 20.
* The bands widen during periods of increased volatility and contract during reduced volatility.
* ATR focuses on total price movement and conveys how widely the market is swinging.
* ATR considers the following ranges for each period:
  * Difference between High and Low
  * Difference between High and previous period's Close
  * Difference between Low and previous period's Close
* Traders often use Bollinger Bands and ATR together because they measure volatility differently and are complementary.

### Bollinger Bands

**Starts working well when:**
* There is enough lookback data to establish a stable moving average and standard deviation
* Volatility begins expanding or contracting in a meaningful way
* Traders need to identify potential squeeze, breakout, or mean-reversion conditions

**Stops working well when:**
* Strong trending markets repeatedly ride the upper or lower band, making overbought or oversold interpretations misleading
* Traders assume a touch of a band automatically implies reversal
* Price action becomes erratic and breaks outside the bands without follow-through

**Works well in conjunction with:**
* **ATR** to confirm whether volatility is actually expanding
* **RSI** to evaluate whether a move near the outer band is stretched
* **MACD** to judge whether a breakout has momentum support
* **Volume** to confirm squeezes and breakouts

**Warnings:**
* A band touch does not automatically mean reversal
* In strong trends, price can stay near one band for extended periods
* Bollinger Band squeezes signal compression, not direction
* Interpretation can be misleading without trend or momentum confirmation

### ATR - Average True Range

**Starts working well when:**
* A trader wants to measure volatility, stop-loss distance, or position sizing
* Market movement begins expanding or contracting
* Enough lookback periods exist to smooth the true range values

**Stops working well when:**
* Traders try to use ATR as a directional signal
* Markets gap or spike temporarily and distort recent volatility readings
* Volatility changes abruptly and historical ATR lags the new regime

**Works well in conjunction with:**
* **Bollinger Bands** for broader volatility context
* **ADX** to separate volatile trending conditions from volatile non-trending conditions
* **MACD** or **RSI** to add direction and momentum since ATR itself is non-directional
* **Risk management rules** for stop placement and position sizing

**Warnings:**
* ATR measures volatility, not direction
* High ATR does not mean bullish and low ATR does not mean bearish
* ATR can remain elevated after a major move, even if the move is already ending
* Best used as a supporting indicator, especially for risk control

---

## RSI - Relative Strength Index

* Momentum oscillator that measures the speed and change of price movements.
* RSI values oscillate between 0 and 100:
  * Values above 70 indicate the asset is in overbought territory
  * Values below 30 indicate oversold territory
* Assets can remain in overbought or oversold territory for extended durations.
* Calculation follows a two-step method where the second step acts as a smoothing technique similar to calculating an exponential moving average.

**Starts working well when:**
* Momentum is changing noticeably
* Markets are oscillating or mean-reverting
* Traders want to identify potential exhaustion or divergence

**Stops working well when:**
* Strong trends keep RSI overbought or oversold for long periods
* Traders treat overbought or oversold readings as immediate reversal signals
* Price trends persist longer than expected

**Works well in conjunction with:**
* **MACD** to compare short-term momentum and broader trend momentum
* **Bollinger Bands** to identify stretched conditions with volatility context
* **ADX** to determine whether RSI signals should be trusted more in weak trends than in strong trends
* **Support and resistance levels** for better reversal confirmation

**Warnings:**
* Does not imply timing of a correction
* Overbought does not necessarily mean price will fall immediately
* Oversold does not necessarily mean price will rise immediately
* Can give premature reversal signals during strong trends

---

## ADX - Average Directional Index

* Measures the strength of a trend.
* Values range from 0 to 100:
  * 0–25: Absent or weak trend
  * 25–50: Strong trend
  * 50–75: Very strong trend
  * 75–100: Extremely strong trend
* ADX is non-directional — it conveys only the strength of a trend, not its direction.
* Calculation involves finding positive and negative directional movement by comparing successive highs and lows, then computing the smoothed average of their difference.

**Starts working well when:**
* Price begins transitioning from a range into a directional move
* Traders want to confirm whether a market is trending strongly enough to use trend-following indicators
* Sufficient smoothing periods have passed to stabilize the reading

**Stops working well when:**
* Traders try to infer direction from ADX alone
* Trend strength is falling even while price still appears to move directionally
* ADX reacts too slowly during sudden market regime changes

**Works well in conjunction with:**
* **MACD** to confirm whether crossover signals are happening in a strong trend
* **RSI** to determine whether overbought and oversold readings are likely to persist
* **Stochastic Oscillator** to filter momentum signals by trend strength
* **+DI and -DI** for directional interpretation alongside ADX strength

**Warnings:**
* Non-directional — it does not tell whether the trend is bullish or bearish
* Rising ADX means strengthening trend, not necessarily rising price
* Falling ADX means weakening trend, not necessarily reversal
* Can lag early trend transitions

---

## Stochastic Oscillator

* Momentum-based indicator that measures the speed of price change.
* Based on the premise that momentum often weakens before price reverses.
* Calculation: `((Close - Lowest Low) / (Highest High - Lowest Low)) * 100`
* Values range from 0 to 100:
  * Above 80 indicates overbought
  * Below 20 indicates oversold
* Higher values signify the current price is near the highest price over the lookback period; lower values signify proximity to the lowest.

**Starts working well when:**
* Price swings are occurring within a defined range
* Short-term momentum shifts are visible
* Traders want earlier warnings of possible reversal or momentum slowdown

**Stops working well when:**
* Markets are extremely choppy and signals reverse too often
* Strong trends keep the indicator pinned near extremes
* Traders use it alone without confirming broader trend structure

**Works well in conjunction with:**
* **ADX** to determine whether the market is trending strongly or not
* **RSI** for additional momentum confirmation
* **Bollinger Bands** to identify whether overbought or oversold readings occur near volatility extremes
* **Support and resistance levels** for better reversal timing

**Warnings:**
* Can produce many false signals in noisy markets
* Overbought and oversold readings do not guarantee immediate reversal
* In strong trends, the oscillator can remain extreme for extended periods
* Should be used in conjunction with other indicators

---

## General Notes

* No indicator starts working immediately at the first price bar. Most require a minimum lookback window plus additional periods for smoothing before their output becomes reliable.
* Trend indicators such as **MACD** and **ADX** work better in directional markets.
* Oscillators such as **RSI** and **Stochastic** often work better in range-bound or mean-reverting conditions.
* Volatility indicators such as **Bollinger Bands** and **ATR** are best used for context, not direction by themselves.
* The best practice is usually to combine:
  * one **trend indicator**
  * one **momentum indicator**
  * one **volatility indicator**
* No indicator should be used as a standalone buy or sell signal.