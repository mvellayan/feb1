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

* IBKR reference: [Bollinger Bands](https://www.interactivebrokers.com/campus/glossary-terms/bollinger-bands/)

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

## SMA - Simple Moving Average

* IBKR reference: [Simple Moving Average](https://www.interactivebrokers.com/campus/glossary-terms/simple-moving-average/)
* The most fundamental moving average, calculated as the arithmetic mean of a fixed number of past closing prices.
* Formula: `SMA = (P1 + P2 + ... + Pn) / n` where n is the lookback period.
* Each period carries equal weight regardless of recency.
* Commonly used periods are 20, 50, and 200 bars; shorter periods respond faster while longer periods emphasize the longer-term trend.

**Starts working well when:**
* Price is in a sustained trend with relatively smooth directional movement
* Sufficient history exists to populate the full lookback window
* Used as a dynamic support/resistance level or trend filter

**Stops working well when:**
* Markets chop sideways and price repeatedly crosses above and below the average
* Price is volatile enough that the SMA lags too far behind to be actionable
* Used as a standalone signal without confirmation

**Works well in conjunction with:**
* **EMA** to compare a faster-responding average against the smoother SMA for crossover signals
* **Bollinger Bands**, which are built on an SMA as their center line
* **ADX** to confirm that a trend is strong enough to trust an SMA directional reading
* **Volume** to validate breakouts above or below key SMA levels

**Warnings:**
* Equal weighting means older data has the same influence as recent data
* Highly lagging, especially over longer periods
* SMA crossovers generate whipsaws in range-bound conditions
* Does not adapt to changes in volatility or market regime

---

## EMA - Exponential Moving Average

* IBKR reference: [Exponential Moving Average](https://www.interactivebrokers.com/campus/glossary-terms/exponential-moving-average/)
* A weighted moving average that applies exponentially greater weight to more recent price data, making it more responsive than an SMA.
* Formula: `EMA = Price × k + EMA(prev) × (1 - k)` where `k = 2 / (n + 1)` and n is the period.
* The smoothing factor k determines how quickly the EMA adjusts; shorter periods produce a higher k and a faster-reacting line.
* Forms the foundation for MACD (12-period and 26-period EMAs) and many other derived indicators.

**Starts working well when:**
* Price is trending and recent price action needs heavier emphasis
* Faster response to new data is preferred over the smoothness of an SMA
* Used as the basis for crossover or divergence strategies

**Stops working well when:**
* Markets are choppy and the faster reaction generates excessive noise
* A single large price spike distorts the EMA disproportionately in the short term
* Used as a standalone buy/sell trigger without confirming signals

**Works well in conjunction with:**
* **SMA** as a slower counterpart for dual-MA crossover systems
* **MACD**, which is directly derived from two EMAs
* **ADX** to confirm directional strength before acting on an EMA signal
* **Volume** to validate EMA-based breakouts

**Warnings:**
* More sensitive to recent outliers than SMA, which can create false signals in volatile conditions
* Still a lagging indicator despite faster reaction time
* Shorter EMA periods increase noise; longer periods increase lag — finding the right balance requires calibration
* EMA crossovers remain susceptible to whipsaws in flat markets

---

## WMA - Weighted Moving Average

* IBKR reference: [Weighted Moving Average](https://www.interactivebrokers.com/campus/glossary-terms/weighted-moving-average/)
* Assigns a linearly increasing weight to each price in the lookback window, with the most recent price receiving the highest weight.
* Formula: `WMA = (P1×1 + P2×2 + ... + Pn×n) / (1 + 2 + ... + n)`
* Unlike the EMA, the weighting scheme is strictly linear and does not compound across periods.
* Sits between SMA and EMA in terms of responsiveness — faster than SMA but without the exponential memory of EMA.

**Starts working well when:**
* A trader wants a more recent-biased average with predictable, deterministic weighting
* Used in systems that require a clearly defined, reproducible calculation without recursive dependencies
* Price is trending and recent data should outweigh older data

**Stops working well when:**
* Markets are range-bound and the recency bias amplifies noise
* The lookback window is too short and the indicator overreacts to single bars
* Used as a standalone signal without trend or momentum confirmation

**Works well in conjunction with:**
* **SMA** or **EMA** as a reference point to measure how much recency weighting affects the signal
* **ADX** to ensure the market is trending before relying on WMA direction
* **MACD** or **RSI** for momentum confirmation
* **Volume** to confirm signals at key WMA levels

**Warnings:**
* Linear weighting drops oldest data abruptly at the edge of the window, introducing minor discontinuities
* Still a lagging indicator; responds to price after the move has already begun
* Less commonly available in standard platforms compared to SMA and EMA, leading to implementation inconsistencies
* Not inherently adaptive — fixed weighting regardless of market volatility

---

## DEMA - Double Exponential Moving Average

* IBKR reference: [Double Exponential Moving Average (DEMA)](https://www.interactivebrokers.com/campus/glossary-terms/double-exponential-moving-average-dema/)
* Developed by Patrick Mulloy to reduce the lag inherent in a single EMA.
* Formula: `DEMA = 2 × EMA(n) - EMA(EMA(n))` — it applies the EMA twice, then subtracts the double-smoothed value to neutralize lag introduced by the second pass.
* Responds to price changes faster than either an EMA or SMA of the same period.
* The subtraction step means DEMA can be more volatile and generate more frequent crossovers.

**Starts working well when:**
* A trader needs faster trend identification with less lag than a standard EMA
* The market is in a trending regime and early signal detection matters
* Sufficient history exists to stabilize both the first and second EMA passes

**Stops working well when:**
* Markets are choppy or range-bound, as reduced lag also means reduced noise filtering
* The heightened sensitivity generates excessive false crossovers
* Used alone without a trend-strength filter like ADX

**Works well in conjunction with:**
* **ADX** to confirm that the market is actually trending before trusting DEMA's faster signals
* **TEMA** for a comparative view of lag-reduction strategies
* **RSI** or **Stochastic** to add momentum confirmation alongside faster crossover signals
* **ATR** to assess whether volatility conditions justify the sensitivity of DEMA

**Warnings:**
* More sensitive than EMA, which increases the risk of whipsaw signals in noisy markets
* Still mathematically derived from a lagging EMA baseline — not truly predictive
* The subtraction step can cause DEMA to overshoot during sharp price moves
* Requires more lookback data than a single EMA to stabilize the double-smoothed component

---

## TEMA - Triple Exponential Moving Average

* IBKR reference: [Triple Exponential Moving Average (TEMA)](https://www.interactivebrokers.com/campus/glossary-terms/triple-exponential-moving-average-tema/)
* Also developed by Patrick Mulloy as a further extension of DEMA to reduce lag even more aggressively.
* Formula: `TEMA = 3 × EMA(n) - 3 × EMA(EMA(n)) + EMA(EMA(EMA(n)))`
* Applies the EMA three times and uses alternating addition/subtraction to cancel out compounded lag.
* Among the fastest-reacting of the classic moving average family for a given period length.

**Starts working well when:**
* The trader needs the earliest possible trend signal from a moving-average-based approach
* The market is clearly trending and early detection has high payoff
* Sufficient data history exists to initialize and stabilize three nested EMA passes

**Stops working well when:**
* Markets are sideways or noisy — TEMA's speed translates directly into more false signals
* Short lookback periods amplify volatility sensitivity to the point of unreliability
* Used in isolation without confirming indicators

**Works well in conjunction with:**
* **ADX** as a mandatory trend-strength gate before acting on TEMA crossovers
* **DEMA** for side-by-side comparison of lag-reduction aggressiveness
* **RSI** or **MACD** to ensure momentum confirms TEMA's directional reading
* **Bollinger Bands** to assess whether TEMA signals occur during volatility expansions or compressions

**Warnings:**
* Highest noise sensitivity of the EMA family — unsuitable for ranging markets
* Three nested EMA passes require a substantially longer warm-up period before the output stabilizes
* Can overshoot significantly around sharp reversals
* The complexity of the formula makes manual validation and debugging more difficult

---

## HMA - Hull Moving Average

* IBKR reference: [Hull Moving Average](https://www.interactivebrokers.com/campus/glossary-terms/hull-moving-average/)
* Developed by Alan Hull to produce a moving average that is both fast and smooth — a combination that simpler approaches trade off against each other.
* Formula: `HMA(n) = WMA(2 × WMA(n/2) - WMA(n), sqrt(n))` — it takes the difference between a short WMA and a full WMA, then applies a final WMA over the square root of the period.
* The double-WMA difference step reduces lag; the final WMA smoothing step reduces noise.
* Visually, the HMA hugs price more closely than SMA or EMA while producing fewer false reversals.

**Starts working well when:**
* A trader needs a smoother, faster-responding trend line for visual trend identification
* The market is in a directional phase with moderate to low noise
* Period is long enough to make the sqrt(n) smoothing step meaningful (typically n >= 16)

**Stops working well when:**
* Markets are highly choppy — even the HMA's smoothing step cannot eliminate noise at short periods
* Very short periods (n < 9) make the square-root rounding produce unreliable output
* Used as a standalone signal generator rather than a directional filter

**Works well in conjunction with:**
* **ADX** to confirm trend strength before treating HMA slope changes as actionable
* **RSI** or **Stochastic** for momentum confirmation at HMA crossover points
* **ATR** to calibrate stop distances relative to recent volatility
* **Volume** to validate HMA-based breakouts or pullback entries

**Warnings:**
* Still a lagging indicator despite its lag-reduction design
* Overshoot can occur at sharp trend reversals due to the double-WMA differencing step
* Less widely available natively in all platforms; manual implementation requires careful rounding of n/2 and sqrt(n)
* Period selection significantly affects behavior — no universal default exists

---

## TMA - Triangular Moving Average

* IBKR reference: [Triangular Moving Average (TMA)](https://www.interactivebrokers.com/campus/glossary-terms/triangular-moving-average-tma/)
* A double-smoothed SMA: first compute an SMA, then compute another SMA over those SMA values.
* The weighting distribution forms a triangle shape — middle periods receive the most weight, while the oldest and most recent receive the least.
* Produces a significantly smoother line than a single SMA, but with substantially more lag.
* Best suited for identifying the broader trend direction rather than precise entry and exit timing.

**Starts working well when:**
* A trader needs maximum smoothness to filter out market noise and identify the dominant trend
* Price is in a sustained macro trend where lag is acceptable in exchange for fewer false signals
* Used as a background trend filter rather than a primary signal generator

**Stops working well when:**
* The market is fast-moving and the double-smoothing lag causes signals to arrive too late
* Short lookback periods are used — the smoothing effect becomes negligible and the lag penalty remains
* Traders attempt to use TMA crossovers for precise entry timing

**Works well in conjunction with:**
* **EMA** or **DEMA** as a faster companion for detecting shorter-term deviations from the TMA baseline
* **ADX** to confirm whether the macro trend the TMA reflects is still strong
* **RSI** to evaluate momentum relative to the smoothed trend line
* **Bollinger Bands** to identify when price deviates significantly from the TMA-based center

**Warnings:**
* Double smoothing creates significantly more lag than a single SMA of the same period
* Slow to respond to trend reversals — traders may hold through large counter-moves
* Not suitable as a primary signal generator in fast or volatile markets
* The symmetrical weighting at the edges can produce edge effects at the start of the series

---

## ALMA - Arnaud Legoux Moving Average

* IBKR reference: [Arnaud Legoux Moving Average](https://www.interactivebrokers.com/campus/glossary-terms/arnaud-legoux-moving-average/)
* Developed by Arnaud Legoux and Dimitri Kouznetso to minimize lag while maintaining smoothness through a Gaussian kernel applied to the lookback window.
* Parameters: window length (n), sigma (controls the width of the Gaussian curve), and offset (shifts the peak of the Gaussian toward the most recent data, typically 0.85).
* The Gaussian weighting concentrates on the center-to-recent portion of the window, giving a smooth but forward-biased average.
* More configurable than most moving averages — sigma and offset adjustments allow practitioners to tune the lag/noise tradeoff explicitly.

**Starts working well when:**
* Smooth, low-noise trend identification is needed with less lag than an SMA
* The offset and sigma parameters are tuned to the instrument's volatility characteristics
* Sufficient lookback history exists for the Gaussian window to stabilize

**Stops working well when:**
* Default parameters are applied blindly without calibration to the instrument
* Markets are highly choppy — even a Gaussian-weighted average cannot eliminate short-period noise
* Used as a precise entry/exit signal without additional confirmation

**Works well in conjunction with:**
* **ADX** to confirm trend strength before trusting ALMA direction changes
* **RSI** or **MACD** for momentum confirmation
* **ATR** to understand whether volatility conditions are consistent with the sigma calibration
* **Volume** to validate breakout signals near ALMA crossover points

**Warnings:**
* Three parameters (window, sigma, offset) increase overfitting risk if tuned on historical data
* Less intuitive to configure than EMA or SMA — practitioners must understand Gaussian distribution behavior
* Still a lagging indicator; the offset shift reduces but does not eliminate lag
* Less universally available across platforms; custom implementation requires care

---

## AMA - Adaptive Moving Average (Kaufman)

* IBKR reference: [Adaptive Moving Average](https://www.interactivebrokers.com/campus/glossary-terms/adaptive-moving-average/)
* Developed by Perry Kaufman (also called KAMA — Kaufman Adaptive Moving Average) to automatically adjust its smoothing speed based on market efficiency.
* Core concept: the Efficiency Ratio (ER) measures how directionally the price has moved relative to total path length. High ER = trending = fast smoothing. Low ER = choppy = slow smoothing.
* Formula: `AMA = AMA(prev) + SC^2 × (Price - AMA(prev))` where SC (Smoothing Constant) is dynamically computed from the ER.
* Typical parameters: 10-period ER window, fastest EMA constant for 2-period (trending), slowest for 30-period (choppy).

**Starts working well when:**
* The market transitions between trending and ranging regimes frequently
* Automatic adaptation to volatility and directionality is preferred over fixed-parameter MAs
* Sufficient history exists to stabilize the ER calculation

**Stops working well when:**
* Markets have sustained very low efficiency (persistent chop) — AMA slows to near-flat and provides little signal
* A sudden, sharp trend emerges after a long ranging period — AMA is slow to catch up initially
* Used without understanding the ER-driven behavior, leading to misinterpretation of flat AMA as no trend

**Works well in conjunction with:**
* **ADX** to provide an external trend-strength gauge alongside AMA's internal ER
* **RSI** or **MACD** to detect early momentum shifts before AMA has fully adapted
* **ATR** to understand volatility context independent of AMA's adaptive smoothing
* **Volume** to confirm whether AMA acceleration is accompanied by market participation

**Warnings:**
* Flat AMA means the market is choppy, not necessarily directionless — interpretation requires context
* The recursive smoothing constant computation means early bars are less reliable
* Parameter choices (fast/slow EMA bounds, ER window) significantly affect behavior
* Adaptation is reactive, not predictive — AMA still trails the actual price transition point

---

## McGinley Dynamic

* IBKR reference: [McGinley Dynamic](https://www.interactivebrokers.com/campus/glossary-terms/mcginley-dynamic/)
* Developed by John McGinley as an improvement over traditional moving averages, designed to track price more faithfully by automatically adjusting speed to market conditions.
* Formula: `MD = MD(prev) + (Price - MD(prev)) / (N × (Price / MD(prev))^4)` where N is the period.
* The (Price/MD)^4 divisor acts as an automatic speed control: when price moves away from the indicator rapidly, the denominator grows and the indicator slows, preventing overreaction; when price is close, it moves faster.
* Tends to hug price more closely in fast markets and avoid whipsaws better than EMA in slow markets.

**Starts working well when:**
* The market has variable speed — alternating between quiet and fast-moving phases
* A self-adjusting trend line that reduces the need for manual period tuning is desired
* Used as a dynamic support/resistance reference in trending conditions

**Stops working well when:**
* Markets are extremely choppy with no directional bias — the formula's speed adjustment does not prevent noise-induced signals
* Initial values are poorly seeded, causing the first several output bars to be unreliable
* Used as a standalone signal without trend-strength confirmation

**Works well in conjunction with:**
* **ADX** to confirm trend strength before relying on McGinley Dynamic direction
* **RSI** or **Stochastic** for momentum-based entry refinement around the dynamic line
* **ATR** to understand whether price proximity to the indicator reflects tight or wide conditions
* **Volume** to validate breakouts and pullbacks to the McGinley Dynamic level

**Warnings:**
* The fourth-power term makes the indicator highly sensitive to the ratio of price to indicator value — initialization matters significantly
* Less widely implemented and less understood than EMA or SMA; documentation and platform support varies
* Still a lagging indicator despite the adaptive design
* Not suitable as a standalone signal generator

---

## VMA - Variable Moving Average

* IBKR reference: [Variable Moving Average](https://www.interactivebrokers.com/campus/glossary-terms/variable-moving-average/)
* Also known as VIDYA (Variable Index Dynamic Average) in some implementations, originated by Tushar Chande.
* Automatically adjusts its smoothing factor based on market volatility, typically using the Chande Momentum Oscillator (CMO) or a similar volatility index as the scaling input.
* During high-volatility trending periods, the smoothing factor increases and the VMA tracks price more closely. During low-volatility ranging periods, the smoothing slows and the VMA flattens.
* Conceptually similar to the Adaptive Moving Average (AMA) but uses a different volatility measurement mechanism.

**Starts working well when:**
* The market alternates between clearly trending and clearly ranging phases
* An adaptive approach is preferred to avoid re-tuning periods manually
* Sufficient history exists to stabilize the underlying volatility index calculation

**Stops working well when:**
* Volatility is persistently low and the VMA becomes too slow to be useful for signal generation
* A sudden regime change occurs — the lagging volatility input delays adaptation
* Used without understanding which volatility metric drives the adaptation

**Works well in conjunction with:**
* **ADX** to cross-validate whether the VMA's implied trend strength is confirmed by a directional index
* **RSI** or **MACD** for momentum context when VMA begins to slope more steeply
* **Bollinger Bands** for external volatility context compared to the VMA's internal adaptation
* **ATR** for absolute volatility measurement alongside the VMA's relative adaptation

**Warnings:**
* Behavior depends heavily on the volatility metric used — different implementations produce different outputs for the same price series
* Flat VMA in a ranging market can be misleading if the range is broken without volatility first expanding
* Still a lagging indicator; adaptation reduces but does not eliminate lag
* Platform availability and implementation consistency are uneven

---

## Wilder's Moving Average (SMMA / RMA)

* IBKR reference: [Wilder's Moving Average](https://www.interactivebrokers.com/campus/glossary-terms/wilders-moving-average/)
* Developed by J. Welles Wilder Jr. as the smoothing method underlying several of his indicators, including RSI, ATR, and ADX.
* Also called Smoothed Moving Average (SMMA) or Running Moving Average (RMA).
* Formula: `SMMA(n) = (SMMA(prev) × (n - 1) + Price) / n`
* Equivalent to an EMA with the smoothing factor `k = 1/n`, making it slower to respond than a standard EMA using `k = 2/(n+1)`.
* Historically significant because RSI uses a 14-period Wilder smooth for its average gain/loss calculations.

**Starts working well when:**
* A smoother, less reactive trend filter than EMA is needed
* Used as the native smoothing method for Wilder-based indicators (RSI, ATR, ADX) to maintain internal consistency
* Long lookback periods are used where smoothness is prioritized over responsiveness

**Stops working well when:**
* Faster market reactions are needed — Wilder's smoothing is intentionally slow and will lag significantly in fast moves
* Applied to short periods where the distinction from SMA is negligible but the initialization bias matters more
* Used as a standalone trend signal in fast-moving markets

**Works well in conjunction with:**
* **RSI**, **ATR**, and **ADX** — Wilder's MA is the native smoothing for all three; understanding it clarifies how those indicators behave
* **EMA** as a faster companion to detect when price deviates from the slower Wilder smooth
* **MACD** to add a faster momentum layer on top of a Wilder-smoothed trend baseline
* **Volume** to validate directional signals at key Wilder Moving Average levels

**Warnings:**
* Significantly more lag than a standard EMA of the same period
* Long initialization phase — the indicator requires many more bars than its period to stabilize
* Commonly misunderstood as a standard EMA when encountered in Wilder-derived indicator formulas
* Not suitable as a fast-reacting trend signal by design

---

## Bollinger Band Width Indicator

* IBKR reference: [Bollinger Bands Width Indicator](https://www.interactivebrokers.com/campus/glossary-terms/bollinger-bands-width-indicator/)
* A derived indicator that quantifies the width of the Bollinger Bands as a single value, making it easier to compare relative volatility across time.
* Formula: `Band Width = (Upper Band - Lower Band) / Middle Band` — expressed as a fraction of the center SMA.
* Periods of extremely low Band Width identify squeeze conditions, which often precede significant directional moves.
* Periods of extremely high Band Width identify volatility expansions, which often occur during or shortly after large price moves.

**Starts working well when:**
* A trader wants a numerical measure of volatility compression or expansion rather than visual band width inspection
* Identifying squeeze setups where a breakout is likely but direction is not yet determined
* Comparing current volatility to historical levels on the same instrument

**Stops working well when:**
* Used to predict the direction of a breakout — Band Width is purely a volatility measure
* Applied across different instruments directly without normalization, as absolute width values are instrument-specific
* Treated as a standalone signal; squeeze alone does not mean a breakout is imminent

**Works well in conjunction with:**
* **Bollinger Bands** as the parent indicator — Band Width makes the squeeze/expansion condition explicit and quantifiable
* **ADX** to confirm whether a post-squeeze expansion is accompanied by genuine trend strength
* **MACD** or **RSI** to identify directional momentum during a squeeze expansion
* **Volume** to confirm a breakout is real when Band Width begins to expand sharply

**Warnings:**
* Does not predict the direction of price movement — only the likelihood of a significant move
* Squeezes can persist for extended periods before resolving
* A Band Width expansion does not guarantee a sustained trend; it may be a spike that quickly reverses
* Requires calibration to historical norms for the specific instrument and timeframe

---

## Percent B (%B)

* IBKR reference: [Percent B Indicator](https://www.interactivebrokers.com/campus/glossary-terms/percent-b-indicator/)
* A derived Bollinger Band indicator that expresses where the current price sits within the band structure as a normalized value.
* Formula: `%B = (Price - Lower Band) / (Upper Band - Lower Band)`
* Values above 1.0 mean price is above the upper band; values below 0.0 mean price is below the lower band; 0.5 means price is at the center SMA.
* Converts the visual band-position observation into a quantitative value suitable for signal conditions and screening.

**Starts working well when:**
* A trader wants to programmatically identify overbought/oversold conditions relative to the Bollinger Band structure
* Used to screen for securities in squeeze conditions or at band extremes across a universe of instruments
* Combined with momentum indicators to filter for high-probability mean-reversion or breakout setups

**Stops working well when:**
* Treated as a standalone reversal signal — price can remain above 1.0 or below 0.0 during strong trends
* The Bollinger Bands themselves are not meaningful (e.g., too short a period or insufficient data)
* Used without accounting for the underlying trend direction

**Works well in conjunction with:**
* **Bollinger Band Width** to distinguish between band-extreme readings caused by high volatility versus narrow-band compression
* **RSI** to double-confirm whether a %B extreme reflects genuine momentum exhaustion
* **ADX** to assess whether the band extreme is occurring in a trending or ranging market
* **MACD** to identify whether momentum is turning at the extremes %B highlights

**Warnings:**
* Values outside 0–1 are expected and normal during volatile moves — they are not errors
* %B above 1.0 in an uptrend is often a sign of strength, not a sell signal
* Requires the same careful interpretation as Bollinger Bands themselves — context determines meaning
* No inherent overbought/oversold threshold applies universally across all instruments and timeframes

---

## Moving Standard Deviation

* IBKR reference: [Moving Standard Deviation](https://www.interactivebrokers.com/campus/glossary-terms/moving-standard-deviation/)
* Calculates the rolling standard deviation of price over a fixed lookback window, expressed as a continuous time series.
* Directly measures the dispersion of price around its rolling mean, representing raw volatility rather than a smoothed trend.
* Formula: population or sample standard deviation applied to the last n closing prices on a rolling basis.
* Serves as the building block for Bollinger Bands, where the band width equals plus or minus k standard deviations around the SMA.

**Starts working well when:**
* A trader needs a direct measure of price dispersion independent of any directional component
* Used as an input to custom volatility models or position-sizing frameworks
* Identifying volatility regime changes that precede significant price moves

**Stops working well when:**
* Used as a directional signal — standard deviation is symmetric and conveys no bias
* Very short windows make the rolling std dev highly reactive to single-bar outliers
* Compared across instruments without normalization to price level (use coefficient of variation instead)

**Works well in conjunction with:**
* **Bollinger Bands** — moving std dev is the mechanism behind band width; understanding it clarifies band behavior
* **ATR** as a complementary volatility measure using range-based rather than close-based dispersion
* **ADX** to determine whether elevated standard deviation accompanies a genuine trend or a chaotic range
* **RSI** to assess momentum alongside volatility expansion

**Warnings:**
* Not a directional indicator — rising standard deviation means increasing volatility, not necessarily a rising market
* Sensitive to the length of the lookback window; short windows react to noise, long windows lag regime changes
* Outlier bars (gaps, earnings, events) can inflate readings for the full duration of the lookback window
* Should be normalized or contextualized when comparing readings across different instruments or time periods

---

## TRIX Indicator

* IBKR reference: [TRIX Indicator](https://www.interactivebrokers.com/campus/glossary-terms/trix-indicator/)
* Stands for Triple Exponential Average — measures the percentage rate of change of a triple-smoothed EMA.
* Formula: Apply EMA three times to price (EMA of EMA of EMA), then compute `TRIX = (EMA3 - EMA3(prev)) / EMA3(prev) × 100`.
* The triple smoothing step filters out minor price fluctuations and short-term cycles, leaving only the dominant trend momentum.
* A signal line (typically a 9-period EMA of TRIX) can be applied for crossover signals, similar to MACD.
* Zero-line crossovers and signal-line crossovers are the primary signal types.

**Starts working well when:**
* Price is in a sustained trend and shorter-cycle noise needs to be filtered out
* Momentum confirmation of a macro trend is needed rather than short-term reversal detection
* The triple-smoothing warm-up period has elapsed and output has stabilized

**Stops working well when:**
* Markets are choppy or in short cycles — the triple smoothing introduces enough lag that signals arrive very late
* Used for short-term trading where the lag of a triple-smoothed series is unacceptable
* The lookback period is too short, negating the noise-filtering benefit

**Works well in conjunction with:**
* **MACD** for a comparative view of short-cycle versus long-cycle momentum
* **ADX** to confirm trend strength before acting on TRIX crossover signals
* **RSI** or **Stochastic** for shorter-term momentum context within the TRIX trend framework
* **Volume** to validate that trend momentum indicated by TRIX is supported by participation

**Warnings:**
* Significantly more lag than MACD due to triple smoothing — not suitable for fast-moving setups
* Extended warm-up period required; early output bars are unreliable
* Signal-line crossovers can be very infrequent in short-cycle markets, reducing utility
* The percentage rate-of-change output requires calibration to the instrument's typical TRIX range

---

## Know Sure Thing (KST)

* IBKR reference: [Know Sure Thing (KST)](https://www.interactivebrokers.com/campus/glossary-terms/know-sure-thing-kst/)
* Developed by Martin Pring as a smoothed, weighted rate-of-change indicator designed to capture momentum across multiple timeframes simultaneously.
* Combines four different Rate of Change (ROC) periods, each smoothed by an SMA, into a single weighted sum:
  * `KST = (RCMA1 × 1) + (RCMA2 × 2) + (RCMA3 × 3) + (RCMA4 × 4)`
  * Default ROC periods: 10, 13, 14, 15; Default SMA smoothing: 10, 13, 14, 9
* A 9-period SMA of KST serves as the signal line; crossovers generate buy and sell signals.
* The multiple ROC inputs make KST broader and smoother than a single-period momentum oscillator.

**Starts working well when:**
* A multi-timeframe momentum view is needed in a single indicator
* Price is in a clear trend and the convergence of multiple ROC periods confirms directional momentum
* The lookback and smoothing windows have fully populated for all four ROC-SMA combinations

**Stops working well when:**
* Markets are choppy and short-period ROC values generate conflicting signals across the four inputs
* A fast-moving setup requires timely signals — the multiple smoothing steps introduce substantial lag
* Applied without understanding which timeframe each ROC component represents

**Works well in conjunction with:**
* **ADX** to confirm that the multi-timeframe momentum KST reflects is backed by genuine trend strength
* **RSI** for a faster, single-timeframe momentum crosscheck
* **MACD** to compare short-cycle momentum against KST's broader, multi-cycle view
* **Volume** to confirm that KST momentum shifts are accompanied by market participation

**Warnings:**
* Complex parameterization — default settings were designed for daily charts and may need adjustment for other timeframes
* Heavier smoothing than MACD or RSI means signals lag actual price turns more significantly
* The weighting of the four ROC components (1x, 2x, 3x, 4x) means the longer-cycle components dominate the output
* Not widely available in all platforms; custom implementation requires careful formula reproduction

---

## Departure Chart

* IBKR reference: [Departure Chart](https://www.interactivebrokers.com/campus/glossary-terms/departure-chart/)
* Measures the difference (departure) between price and a selected moving average, plotted as a standalone oscillator.
* Formula: `Departure = Price - MA(n)` or alternatively as a percentage: `Departure% = ((Price - MA(n)) / MA(n)) × 100`
* Positive values indicate price is above the moving average (bullish momentum or overbought); negative values indicate price is below (bearish momentum or oversold).
* Conceptually similar to the MACD zero-line interpretation but applied to a single moving average relative to price itself.

**Starts working well when:**
* Tracking how extended price has become relative to its moving average baseline
* Mean-reversion strategies require a quantitative measure of deviation from average
* The underlying moving average is long enough to represent a stable trend reference

**Stops working well when:**
* Used to identify absolute overbought/oversold extremes without calibration to historical departure ranges
* The underlying moving average is short and departure values have a narrow normal range
* Applied in strongly trending markets where large persistent departures are normal, not mean-reverting

**Works well in conjunction with:**
* **Bollinger Bands** — the band width is essentially a ±2σ bound on departure; they measure the same concept differently
* **RSI** to confirm whether extreme departure readings are accompanied by momentum exhaustion
* **ADX** to distinguish between a departure that reflects a strong trend versus one that represents overextension
* **Volume** to assess whether an extreme departure is driven by genuine market force or a thin-market spike

**Warnings:**
* No universal overbought/oversold threshold — departure values must be calibrated per instrument and timeframe
* Persistent large departures are normal in trending conditions and do not inherently signal reversal
* The choice of underlying moving average (SMA, EMA, period) significantly affects departure behavior
* Should not be used as a standalone signal; requires confirmation from directional and momentum indicators

---

## Ease of Movement (EOM)

* IBKR reference: [Ease of Movement Indicator](https://www.interactivebrokers.com/campus/glossary-terms/ease-of-movement-indicator/)
* Developed by Richard Arms to relate price movement to volume, quantifying how easily price moves up or down per unit of volume.
* Formula involves: Distance Moved (midpoint change), Box Ratio (volume divided by price range), and their combination: `EOM = Distance Moved / Box Ratio`
* Values above zero suggest price is rising with relative ease (low volume needed to move price); below zero suggests price is falling with ease.
* Typically smoothed with a 14-period SMA for signal generation.

**Starts working well when:**
* Volume is consistent and meaningful on the instrument being analyzed
* A trader wants to identify whether a price move is efficient (high EOM) or labored (low EOM)
* Used to confirm breakouts: a price move accompanied by high EOM has strong follow-through potential

**Stops working well when:**
* Applied to instruments with thin or inconsistent volume (e.g., pre-market, illiquid securities)
* Volume data is absent or unreliable
* Used as a directional signal without considering the broader trend context

**Works well in conjunction with:**
* **Volume analysis** directly — EOM is fundamentally a volume-adjusted price indicator
* **ADX** to confirm that an efficient price move (high EOM) is occurring within a genuine trend
* **MACD** or **RSI** to add momentum confirmation alongside EOM's volume-adjusted efficiency reading
* **Bollinger Bands** to assess whether the price movement EOM highlights is breaking out of a volatility range

**Warnings:**
* EOM near zero does not mean no movement — it means the movement is not volume-efficient
* Sensitive to volume spikes from unusual events (earnings, news) that are not representative of normal market behavior
* The formula requires price range normalization; highly volatile days can distort the Box Ratio component
* Not suitable for instruments where volume is unreliable or unrepresentative

---

## MA Crossover

* IBKR reference: [Chart Indicators - Documentation](https://guides.interactivebrokers.com/tws/usersguidebook/technicalanalytics/demarkpivotpoints.htm?TocPath=Technical+Analytics%7CChart+Indicators%7C_____19)
* A strategy and signal framework rather than a single indicator — generated when a faster moving average crosses above or below a slower moving average.
* Bullish crossover (Golden Cross in long-period context): fast MA crosses above slow MA, signaling potential upward trend.
* Bearish crossover (Death Cross in long-period context): fast MA crosses below slow MA, signaling potential downward trend.
* Can be applied to any moving average type (SMA, EMA, WMA, etc.); the specific MA type affects lag and noise characteristics.

**Starts working well when:**
* Price has established a clear directional trend with enough separation between the two MAs
* The fast/slow period combination is calibrated to the instrument's typical trend duration
* The crossover is supported by expanding volume and/or confirming momentum indicators

**Stops working well when:**
* Markets are range-bound — crossovers occur repeatedly in both directions without follow-through (whipsaw)
* The two MA periods are too close together, generating excessive and noisy signals
* Used as a standalone signal without trend-strength or momentum confirmation

**Works well in conjunction with:**
* **ADX** as the primary confirmation gate — crossover signals should only be acted upon when ADX confirms a genuine trend
* **MACD**, which is structurally a normalized MA crossover system
* **RSI** or **Stochastic** to assess whether momentum at the crossover point supports the directional signal
* **Volume** to confirm that the crossover is accompanied by meaningful market participation

**Warnings:**
* Inherently lagging — crossovers occur after the price move has already begun
* Whipsaw risk is high in sideways or volatile markets
* Period selection is critical; no universal setting works across all instruments and timeframes
* A crossover is a necessary but insufficient condition for a high-probability trade signal

---

## Ultimate Oscillator

* IBKR reference: [Ultimate Oscillator](https://www.interactivebrokers.com/campus/glossary-terms/ultimate-oscillator/)
* Developed by Larry Williams to combine momentum across three different timeframes into a single bounded oscillator, reducing false divergence signals that single-period oscillators produce.
* Uses three different period lengths (typically 7, 14, 28) with weighted contributions: the shortest gets weight 4, the middle 2, the longest 1.
* Each period's Buying Pressure (BP = Close − True Low) is divided by True Range (TR) to normalize for volatility; the weighted average is scaled to 0–100.
* Values above 70 indicate overbought conditions; below 30 indicate oversold.

**Starts working well when:**
* Divergence signals are required and a multi-period oscillator reduces the false divergences that plague single-period tools
* The market is oscillating and all three timeframes show aligned overbought/oversold readings
* Sufficient lookback history exists to populate all three period calculations

**Stops working well when:**
* Strong trends keep the oscillator in overbought or oversold territory for extended periods
* Traders use raw overbought/oversold readings without waiting for the specific divergence-and-reversal setup Williams prescribed
* Markets are extremely choppy, generating conflicting signals across the three periods

**Works well in conjunction with:**
* **RSI** for a comparison of single-period versus multi-period momentum interpretation
* **ADX** to determine whether the oscillator's extreme reading is occurring in a trending or ranging context
* **MACD** for trend momentum confirmation alongside the Ultimate Oscillator's multi-timeframe overbought/oversold reading
* **Volume** to confirm reversal setups when the oscillator signals divergence

**Warnings:**
* Williams specified a precise buy/sell trigger protocol (divergence + oscillator condition + reversal confirmation) — using it as a simple threshold oscillator misapplies the indicator
* The three fixed periods were designed for daily charts; adaptation to other timeframes requires recalibration
* Still susceptible to extended overbought/oversold readings during strong trends
* More complex to compute and interpret than RSI or Stochastic; the multi-period structure requires careful period selection

---

## General Notes

* No indicator starts working immediately at the first price bar. Most require a minimum lookback window plus additional periods for smoothing before their output becomes reliable.
* Trend indicators such as **MACD**, **ADX**, **EMA**, **DEMA**, **TEMA**, and **HMA** work better in directional markets.
* Oscillators such as **RSI**, **Stochastic**, and **Ultimate Oscillator** often work better in range-bound or mean-reverting conditions.
* Volatility indicators such as **Bollinger Bands**, **ATR**, **Band Width**, and **Moving Standard Deviation** are best used for context, not direction by themselves.
* Adaptive indicators such as **AMA**, **VMA**, and **McGinley Dynamic** aim to reduce manual period selection by responding to market efficiency or volatility, but they still require initialization and confirmation.
* The best practice is usually to combine:
  * one **trend indicator**
  * one **momentum indicator**
  * one **volatility indicator**
* No indicator should be used as a standalone buy or sell signal.
