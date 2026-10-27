-- depends: 0003_raw
-- NSE as a venue, its vendors and feeds, and the raw-layer checks (spec 0001, sections 4 and 8).
-- Schedules are cron expressions in the venue's time zone (Asia/Kolkata).

INSERT INTO ref.venue (venue_id, name, time_zone, weekend_days, regular_open, regular_close)
VALUES ('XNSE', 'National Stock Exchange of India', 'Asia/Kolkata', '{6,7}', '09:15', '15:30');

INSERT INTO raw.vendor (vendor_id, base_url, rate_limit_per_s) VALUES
    ('nse_archives', 'https://nsearchives.nseindia.com', 2),
    ('nse_api',      'https://www.nseindia.com',         1);

INSERT INTO raw.feed (feed_id, vendor_id, dataset, priority, format, url_template,
                      available_from, available_to, cadence, schedule_cron) VALUES
    ('nse_cm_bhavcopy_legacy', 'nse_archives', 'nse_cm_bhavcopy', 1, 'legacy_v1',
     'https://nsearchives.nseindia.com/content/historical/EQUITIES/{YYYY}/{MON}/cm{DD}{MON}{YYYY}bhav.csv.zip',
     '2010-01-01', '2024-07-05', 'session', NULL),
    ('nse_cm_bhavcopy_udiff', 'nse_archives', 'nse_cm_bhavcopy', 2, 'udiff_v1',
     'https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{YYYY}{MM}{DD}_F_0000.csv.zip',
     '2024-01-01', NULL, 'session', '0 19 * * *'),
    ('nse_index_close', 'nse_archives', 'nse_index_close', 1, 'ind_close_all_v1',
     'https://nsearchives.nseindia.com/content/indices/ind_close_all_{DD}{MM}{YYYY}.csv',
     '2012-02-21', NULL, 'session', '0 19 * * *'),
    ('nse_holidays', 'nse_api', 'nse_holidays', 1, 'holiday_master_v1',
     'https://www.nseindia.com/api/holiday-master?type=trading&year={YYYY}',
     '2011-01-01', NULL, 'weekly', '0 7 * * 1'),
    ('nse_equity_list', 'nse_archives', 'nse_equity_list', 1, 'equity_l_v1',
     'https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv',
     NULL, NULL, 'daily', '30 7 * * *'),
    ('nse_sme_equity_list', 'nse_archives', 'nse_sme_equity_list', 1, 'sme_equity_l_v1',
     'https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv',
     NULL, NULL, 'daily', '30 7 * * *'),
    ('nse_symbol_change', 'nse_archives', 'nse_symbol_change', 1, 'symbolchange_v1',
     'https://nsearchives.nseindia.com/content/equities/symbolchange.csv',
     NULL, NULL, 'daily', '30 7 * * *');

INSERT INTO ops.dq_check (check_id, layer, feed_id, severity, description, params, owner_action) VALUES
    ('file_arrived', 'raw', NULL, 'critical',
     'An expected session has a bhavcopy and an index-close file by the deadline',
     '{"deadline_ist": "22:30", "datasets": ["nse_cm_bhavcopy", "nse_index_close"]}',
     'Check the NSE archive by hand; rerun the partition once the file is there'),
    ('file_shape', 'raw', NULL, 'critical',
     'The file has exactly the columns its format expects', '{}',
     'Block; the parser or the feed format needs an update'),
    ('trade_date_matches', 'raw', NULL, 'critical',
     'The date inside the file equals the partition date', '{}',
     'Block; look at the URL template and the file'),
    ('no_duplicates', 'raw', NULL, 'critical',
     'One row per (symbol, series) in bhavcopy and one per index name in index closes', '{}',
     'Block; inspect the file'),
    ('row_count', 'raw', NULL, 'critical',
     'Bhavcopy rows within a band of the median of the previous sessions',
     '{"datasets": ["nse_cm_bhavcopy"], "lookback_sessions": 20, "max_pct_diff": 10, "min_abs_diff": 50}',
     'Block; compare with the NSE archive before rerunning'),
    ('holiday_session', 'raw', NULL, 'warn',
     'A file arrived on a listed holiday (expected only for Muhurat trading)', '{}',
     'Record a special session; confirm it was Muhurat trading or tell NSE''s list is wrong'),
    ('benchmark_present', 'raw', 'nse_index_close', 'critical',
     'Nifty 50 is in the index-close file',
     '{"index_names": ["Nifty 50", "S&P CNX Nifty", "CNX Nifty"]}',
     'Block; inspect the file'),
    ('prices_positive', 'raw', NULL, 'warn',
     'open, high, low and close are positive where volume is positive', '{}',
     'Flag for the clean layer'),
    ('ohlc_order', 'raw', NULL, 'warn',
     'low <= open, close <= high', '{}',
     'Flag for the clean layer'),
    ('isin_present', 'raw', NULL, 'warn',
     'ISIN on every EQ-series bhavcopy row from 22 Jun 2011',
     '{"from": "2011-06-22", "series": ["EQ"]}',
     'List the rows; spec 0002 maps them'),
    ('vendor_revision', 'raw', NULL, 'warn',
     'A re-downloaded file differs from the stored one', '{}',
     'Revision stored; check what changed'),
    ('special_session', 'raw', NULL, 'info',
     'A file arrived on a weekend day that is not a listed holiday', '{}',
     'Recorded as a special session');
