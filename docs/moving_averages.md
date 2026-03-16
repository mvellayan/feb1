# Moving Average Technical Indicators


## MACD - Moving Average Convergence Divergence

* It is a trend following momentum indicator which is calculated by taking the difference of two moving averages of an asset price (typically 12 period MA and 26 period MA). 
* A signal line is also calculated which is again a moving average (typically 9
period) of the MACD line calculated as per the above step. 
* The MACD line cutting the signal line from below signals bullish period and the former cutting the latter from above signals bearish period. This is called crossover strategy.
Many false positives - especially during sideways market

WARNINGS:
* Too many false positive signals
* Lagging indicator - Trails behind the actual price action
* Suggested that this indicator be used in conjunction with other indicators



## Bollinger Bands & ATR (Average True Range)
* Both Bollinger bands and ATR are <b>volatility based indicators</b> or 2x StDev 
* Bollinger band comprises two lines plotted n (n is typically 2) standard deviations from a m period simple moving average line (m is typically 20); The bands widen during periods of increased volatility and shrink during period of reduced volatility. 
* ATR focuses on total price movement and conveys how wildly the market is swinging as it moves. It takes into account the price movement in each period by considering the following ranges
Difference between High and Low of each period 
  * Difference between High and previous period's close 
  * Difference between Low and previous period's close
  
* Traders typically use them in conjunction as they approach volatility differently and are complimentary.

## RSI - Relative Strength Index

    * if value > 70, the asset is over bought 
    * if value < 30 it's oversold 
    * problem: it doesn't imply timeframe. 

* RSI is a momentum oscillator which measures the speed and change of price movements. 
* RSI value oscillates between 0 and 100 with values above 70 indicating that the asset has now reached overbought territory. Values below 30 signify oversold territory.
* Assets can remain in overbought and oversold territories for long durations
Calculation follows a two step method wherein the second step acts as a smoothening technique (similar to calculating exponential MA).

WARNINGS: 
* does not imply timing of correction

## ADX (Average Directional Index)
• ADX is a way of measuring the strength of a trend
• Values range from 0 to 100 and quantifies the strength of a trend as per below:
- 0-25 : Absent or weak trend
- 25-50 : Strong trend
- 50-75 : Very strong trend
- 75-100 : Extremely strong trend
• ADX is non directional meaning the ADX value makes no inference about the direction of the trend but only about the strength of the trend
• The calculation involves finding both positive and negative directional movement (by comparing successive highs and successive lows) and then calculating the smoothed average of the difference of these.


## Stochastic Oscillator
• Momentum based indicator which measures the speed or momentum of price change.
• Based on the premise that momentum must reduce before price reversal. Works well during trending markets
• Simple calculation: ((Close - Lowest Low)/(Highest High - Lowest Low)) * 100
• Value varies from 0 to 100 with higher number signifying that the present price is closer to the highest price over the look back period and lower number signifying proximity to the lowest price over the look back period
• Above 80 indicate overbought and below 20 indicate oversold (however, users should be mindful that these numbers are indicative of momentum)
• Suggested that this indicator be used in conjunction with other indicators

WARNING: 
* works only in trending market -- horrible in sideways market 
