-- 0008 seeds: XNSE, vendors, feeds, schemes, QA flags, DQ checks
-- Source: Cagr Schema Blueprint v3 (platform additions of 16 Oct 2026), adapted for a from-scratch database.

-- ---------------------------------------------------------------------------------------------
-- seeds: what exists today (NSE only). Other venues are added with rows, not DDL.
-- ---------------------------------------------------------------------------------------------
INSERT INTO ref.currency (code, name, is_fiat, minor_units) VALUES
 ('INR', 'Indian rupee', true, 2),
 ('USD', 'US dollar', true, 2);

INSERT INTO ref.calendar (code, timezone, kind) VALUES ('XNSE', 'Asia/Kolkata', 'exchange');

INSERT INTO ref.venue (code, name, kind, calendar_id, currency, country)
SELECT 'XNSE', 'National Stock Exchange of India', 'stock_exchange', calendar_id, 'INR', 'IN'
FROM ref.calendar WHERE code = 'XNSE';

INSERT INTO ref.vendor (code, kind, adapter, credential_block, rate_limit_per_min, notes) VALUES
 ('nse_archives', 'exchange_direct', 'cagr_data.vendors.nse_archives.ArchivesAdapter', NULL,              30,  'Static files on nsearchives.nseindia.com: bhavcopy (UDiFF, with ISIN), EQUITY_L, SME_EQUITY_L, symbolchange, ind_close_all, shareholding XBRL. Tested 2026-10-08: 15 requests in 10 s, all served.'),
 ('nse_api',      'exchange_direct', 'cagr_data.vendors.nse_api.ApiAdapter',      NULL,              10,  'JSON on www.nseindia.com/api with a cookie session behind Akamai: corporate actions, holidays, shareholding filings list. Some endpoints return 403 to scripts.'),
 ('yahoo',        'market_data',     'cagr_data.vendors.yahoo.YahooAdapter',  NULL,              60,  'yfinance. Secondary: adjusted-price cross-check and index history. Unofficial; 429 blocks reported.'),
 ('screener',     'fundamentals',    'cagr_data.vendors.screener.ScreenerAdapter', 'screener-cookie', 20, 'Reference closes for adjustment cross-check.'),
 ('zerodha',      'broker',          'cagr_data.vendors.zerodha.KiteAdapter', 'kite-api',        180, 'Instrument tokens and order routing for rec-engine. Not a price source.');

-- Feed schedules are the source of truth for the Prefect deployments (times in Asia/Kolkata).
INSERT INTO ref.feed (code, vendor_id, venue_id, dataset, asset_class, priority, schedule_cron, schedule_tz,
                      lookback_sessions, full_refresh_cron, max_staleness, active)
SELECT x.code, v.vendor_id, ve.venue_id, x.dataset, x.asset_class, x.priority, x.cron, 'Asia/Kolkata',
       x.lookback, x.full_cron, x.stale::interval, x.active
FROM (VALUES
  ('xnse_holidays',          'nse_api',      'holiday_calendar', 'equity', 1, '0 7 * * 1',     1,  NULL,        '8 days',        true),
  ('xnse_listing_master',    'nse_archives', 'listing_master',   'equity', 1, '30 7 * * 1',    1,  NULL,        '8 days',        true),
  ('xnse_symbol_change',     'nse_archives', 'symbol_change',    'equity', 1, '35 7 * * 1-5',  1,  NULL,        '4 days',        true),
  ('xnse_corporate_actions', 'nse_api',      'corporate_action', 'equity', 1, '45 7 * * 1-5',  1,  NULL,        '2 days',        true),
  ('xnse_eod_bhavcopy',      'nse_archives', 'eod_bar',          'equity', 1, '0 19 * * 1-5',  1,  NULL,        '1 day 6 hours', true),
  ('xnse_index_close',       'nse_archives', 'index_close',      'index',  1, '0 19 * * 1-5',  1,  NULL,        '1 day 6 hours', true),
  ('xnse_eod_yahoo',         'yahoo',        'eod_bar',          'equity', 2, '30 19 * * 1-5', 5,  '0 6 * * 6', '3 days',        true),
  ('xnse_index_yahoo',       'yahoo',        'eod_bar',          'index',  2, '30 19 * * 1-5', 5,  '0 6 * * 6', '3 days',        true),
  ('xnse_screener_close',    'screener',     'reference_close',  'equity', 1, '0 8 * * 6',     20, NULL,        '8 days',        true),
  ('xnse_shareholding',      'nse_api',      'shareholding',     'equity', 1, '0 6 * * 0',     1,  NULL,        '8 days',        true)
) AS x(code, vendor, dataset, asset_class, priority, cron, lookback, full_cron, stale, active)
JOIN ref.vendor v ON v.code = x.vendor
CROSS JOIN ref.venue ve WHERE ve.code = 'XNSE';

INSERT INTO ref.classification_scheme (code, description) VALUES
 ('cagr_industry_v1', 'In-house industry list (today''s script_master.group_3)');

INSERT INTO ref.universe (code, venue_id, scheme_id, rule, description)
SELECT 'xnse_mcap_gt_1500cr', v.venue_id, s.scheme_id,
       '{"asset_class": "equity", "market_cap_gt": 15000000000, "currency": "INR"}',
       'XNSE equities whose market cap on that session exceeds Rs 1,500 Cr (replaces today''s script_master filter)'
FROM ref.venue v, ref.classification_scheme s WHERE v.code = 'XNSE' AND s.code = 'cagr_industry_v1';

INSERT INTO clean.qa_flag (bit, code, severity, description) VALUES
 (0, 'not_traded',           'info',     'No trades this session (zero volume or no vendor bar)'),
 (1, 'ohlc_inconsistent',    'warn',     'low > min(open, close) or high < max(open, close)'),
 (2, 'stale_price',          'warn',     'Close unchanged for >= 5 traded sessions'),
 (3, 'return_outlier',       'warn',     '|log return| > max(25%, 6 x ATR%) with no corporate action on file'),
 (4, 'corporate_action_day', 'info',     'Ex-date of an applied corporate action'),
 (5, 'vendor_mismatch',      'critical', 'Primary and secondary vendor closes disagree beyond tolerance with no corporate action'),
 (6, 'vendor_revised',       'info',     'Vendor sent a different value for this past session'),
 (7, 'carried_forward',      'info',     'Value carried from the previous session'),
 (8, 'special_session',      'info',     'Non-weekday exchange session');

INSERT INTO ops.dq_check (check_code, layer, severity, threshold, description) VALUES
 ('feed_freshness',          'raw',   'critical', NULL, 'Feed watermark older than ref.feed.max_staleness'),
 ('coverage_latest_session', 'clean', 'critical', 0.98, 'Share of universe with a bar on the latest calendar session'),
 ('calendar_last_session',   'clean', 'critical', NULL, 'Latest bar date equals the latest calendar session'),
 ('bar_on_calendar',         'clean', 'critical', 0,    'Bars whose date is not a session of the listing''s calendar'),
 ('index_present',           'clean', 'critical', NULL, 'Venue benchmark index has a bar for the latest session'),
 ('index_vs_universe',       'clean', 'critical', 0.10, '|benchmark return - median universe return| on the latest session'),
 ('vendor_mismatch',         'clean', 'critical', 0,    'Listings flagged vendor_mismatch on the latest session'),
 ('history_drift',           'clean', 'warn',     NULL, 'Listings whose past adjusted closes changed in this run'),
 ('pending_listing_changes', 'ref',   'warn',     0,    'Listing changes waiting for approval'),
 ('pending_corporate_actions','ref',  'warn',     0,    'Corporate actions proposed but not confirmed before ex-date'),
 ('ca_needs_review',         'ref',   'critical', 3,    'Corporate actions whose text could not be parsed, with an ex-date within 3 sessions'),
 ('unknown_isin_in_bhavcopy','ref',   'warn',     0,    'Bhavcopy rows whose ISIN is not in ref.instrument_identifier (queues a listing_change)'),
 ('holiday_mismatch',        'raw',   'critical', 0,    'Primary EOD file missing on a calendar session, or present on a holiday'),
 ('shares_stale',            'ref',   'warn',     120,  'Listings whose shares_outstanding row is older than 120 days'),
 ('lookahead_exposed',       'serve', 'critical', 0,    'A uses_future feature published to an as-of serving table'),
 ('dirty_backlog',           'fb',    'warn',     0,    'Dirty ranges older than one day not consumed by every family'),
 ('feature_null_rate',       'fb',    'warn',     0.02, 'Null share per active feature on the latest bar, traded rows only'),
 ('feature_orphans',         'fb',    'critical', 0,    'Feature rows without a clean bar'),
 ('incremental_parity',      'fb',    'critical', 0,    'Weekly shadow full rebuild of a 25-listing sample differs from incremental rows'),
 ('serve_row_count',         'serve', 'critical', 0.98, 'Published row count vs previous build');
