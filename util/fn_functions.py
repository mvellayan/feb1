from datetime import date, datetime, time, timedelta
import math

TRADING_DAYS_PER_YEAR  = 252
TRADING_MINUTES_PER_DAY = 390          # 9:30am – 4:00pm
TRADING_MINUTES_PER_YEAR = TRADING_DAYS_PER_YEAR * TRADING_MINUTES_PER_DAY  # 98,280

MARKET_OPEN  = time(9, 30)
MARKET_CLOSE = time(16, 0)


def _to_datetime(d):
    """Normalize date or datetime to datetime."""
    if isinstance(d, datetime):
        return d
    if isinstance(d, date):
        return datetime(d.year, d.month, d.day)
    raise TypeError(f"Expected date or datetime, got {type(d)}")


def _trading_minutes_between(purchase_dt, sale_dt):
    """
    Count trading minutes between two datetimes.

    Each calendar day's contribution is clamped to [MARKET_OPEN, MARKET_CLOSE].
    Weekends are skipped. Holidays are not accounted for.
    """
    total = 0.0
    current = purchase_dt.date()
    end     = sale_dt.date()

    while current <= end:
        if current.weekday() < 5:          # Monday–Friday only
            day_open  = datetime.combine(current, MARKET_OPEN)
            day_close = datetime.combine(current, MARKET_CLOSE)
            window_start = max(purchase_dt, day_open)
            window_end   = min(sale_dt,     day_close)
            if window_end > window_start:
                total += (window_end - window_start).total_seconds() / 60
        current += timedelta(days=1)

    return total


def calculate_cagr(purchase_date, purchase_price, sale_date, sale_price):
    """
    Calculate CAGR from purchase/sale dates and prices.

    Holding period is measured in trading minutes only — non-trading hours
    and weekends are excluded.  Annualization basis:
        252 trading days × 390 trading minutes/day = 98,280 trading min/year.

    Args:
        purchase_date (date | datetime): Date/time of purchase
        purchase_price (float): Purchase price (> 0)
        sale_date (date | datetime): Date/time of sale
        sale_price (float): Sale price (> 0)

    Returns:
        float: CAGR as a decimal (e.g., 0.1234 for 12.34%)

    Raises:
        TypeError:  If dates are not date or datetime objects
        ValueError: If prices are invalid or holding period <= 0 trading minutes
    """
    if purchase_price <= 0 or sale_price <= 0:
        raise ValueError("Prices must be greater than 0.")

    purchase_dt = _to_datetime(purchase_date)
    sale_dt     = _to_datetime(sale_date)

    trading_minutes = _trading_minutes_between(purchase_dt, sale_dt)
    if trading_minutes <= 0:
        raise ValueError("Holding period must be > 0 trading minutes.")

    years = trading_minutes / TRADING_MINUTES_PER_YEAR
    ##return min(((sale_price / purchase_price) ** (1 / years) - 1), 99.99)
    max_exp = min(1/years, 1000)
    x = ((sale_price / purchase_price) ** max_exp - 1)
    return min(x, 9999.99)


def calculate_sgr(purchase_date, purchase_price, sale_date, sale_price):
    """
    Calculate Simple Growth Rate (SGR) — a simple (non-compounding) annualized return.

    SGR = (sale_price / purchase_price - 1) × (TRADING_MINUTES_PER_YEAR / trading_minutes)

    Unlike CAGR, this scales linearly, making it more meaningful for short holding
    periods where compounding assumptions produce unrealistically extreme values.

    Args:
        purchase_date (date | datetime): Date/time of purchase
        purchase_price (float): Purchase price (> 0)
        sale_date (date | datetime): Date/time of sale
        sale_price (float): Sale price (> 0)

    Returns:
        float: SGR as a decimal (e.g., 0.1234 for 12.34%)

    Raises:
        TypeError:  If dates are not date or datetime objects
        ValueError: If prices are invalid or holding period <= 0 trading minutes
    """
    if purchase_price <= 0 or sale_price <= 0:
        raise ValueError("Prices must be greater than 0.")

    purchase_dt = _to_datetime(purchase_date)
    sale_dt     = _to_datetime(sale_date)

    trading_minutes = _trading_minutes_between(purchase_dt, sale_dt)
    if trading_minutes <= 0:
        raise ValueError("Holding period must be > 0 trading minutes.")

    period_return = sale_price / purchase_price - 1
    return period_return * (TRADING_MINUTES_PER_YEAR / trading_minutes)


## Unit Testing
if __name__ == '__main__':
    cases = [
        ("1 hour",   datetime(2026, 3, 16, 10, 0), datetime(2026, 3, 16, 11, 0)),
        ("2 hours",  datetime(2026, 3, 16, 10, 0), datetime(2026, 3, 16, 12, 0)),
        ("1 day",    datetime(2026, 3, 16, 10, 0), datetime(2026, 3, 17, 10, 0)),
        ("2 days",   datetime(2026, 3, 16, 10, 0), datetime(2026, 3, 18, 10, 0)),
        ("1 week",   datetime(2026, 3,  9, 10, 0), datetime(2026, 3, 16, 10, 0)),
        ("2 weeks",  datetime(2026, 3,  2, 10, 0), datetime(2026, 3, 16, 10, 0)),
        ("1 month",  datetime(2026, 2, 16, 10, 0), datetime(2026, 3, 16, 10, 0)),
        ("2 months", datetime(2026, 1, 16, 10, 0), datetime(2026, 3, 16, 10, 0)),
        ("1 year",   datetime(2025, 3, 16, 10, 0), datetime(2026, 3, 16, 10, 0)),
        ("2 years",  datetime(2024, 3, 16, 10, 0), datetime(2026, 3, 16, 10, 0)),
    ]

    print(f"{'Period':<10}  {'CAGR':>14}  {'SGR':>14}")
    print("-" * 42)
    for label, buy, sell in cases:
        cagr = calculate_cagr(buy, 100.0, sell, 200)
        sgr  = calculate_sgr( buy, 100.0, sell, 200)
        print(f"{label:<10}  {cagr:>14.2%}  {sgr:>14.2%}")

