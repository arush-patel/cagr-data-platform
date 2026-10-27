-- depends: 0005_writer_maintain
-- NSE's T0 (same-day settlement) series reports the normal segment's closing price as its close,
-- which can sit outside T0's own high and low (6 rows in Jun to Aug 2024). Hold only open
-- between low and high for those series.

UPDATE ops.dq_check
SET description = 'low <= open, close <= high (open only for series whose close is borrowed)',
    params = '{"close_exempt_series": ["T0"]}'
WHERE check_id = 'ohlc_order';
