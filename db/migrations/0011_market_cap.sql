-- depends: 0010_clean
-- Shares outstanding and daily market cap (spec 0007): NSE's mcap file from 1 Feb 2024 and the
-- index files' issued capital for 2010 to 2013 as raw feeds, share periods per listing, and the
-- daily market cap view.

-- Both come from the same daily PR zip; each feed keeps only its own members. The zip still
-- exists from 2014 to Jan 2024, but holds neither file, so neither feed covers those years.
INSERT INTO raw.feed (feed_id, vendor_id, dataset, priority, format, url_template,
                      available_from, available_to, cadence, schedule_cron) VALUES
    ('nse_mcap', 'nse_archives', 'nse_mcap', 1, 'pr_mcap_v1',
     'https://nsearchives.nseindia.com/archives/equities/bhavcopy/pr/PR{DD}{MM}{YY}.zip',
     '2024-02-01', NULL, 'session', '0 19 * * *'),
    ('nse_index_issue_cap', 'nse_archives', 'nse_index_issue_cap', 1, 'pr_issue_cap_v1',
     'https://nsearchives.nseindia.com/archives/equities/bhavcopy/pr/PR{DD}{MM}{YY}.zip',
     '2010-01-01', '2013-12-31', 'session', NULL);

CREATE TABLE raw.nse_mcap (
    file_id          bigint NOT NULL REFERENCES raw.vendor_file,
    trade_date       date NOT NULL,
    symbol           text NOT NULL,
    series           text NOT NULL,
    security_name    text,
    category         text,  -- Listed, or Permitted (listed elsewhere, traded on NSE)
    last_trade_date  date,  -- earlier than trade_date when the close is stale
    face_value       numeric(18, 4),
    issue_size       bigint CHECK (issue_size >= 0),  -- shares outstanding
    close            numeric(18, 4),
    market_cap_inr   numeric(24, 2),
    PRIMARY KEY (file_id, symbol, series)
);
CREATE INDEX nse_mcap_date ON raw.nse_mcap (trade_date);

CREATE TABLE raw.nse_index_issue_cap (
    file_id            bigint NOT NULL REFERENCES raw.vendor_file,
    trade_date         date NOT NULL,
    source             text NOT NULL CHECK (source IN ('ix', 'ffix')),
    index_name         text NOT NULL,  -- INDEX_FLG as sent (NIFTY, JR. NIFTY, CNX 500, ...)
    symbol             text NOT NULL,
    series             text NOT NULL,
    security_name      text,
    issue_cap          bigint CHECK (issue_cap >= 0),  -- shares outstanding
    investible_factor  numeric(10, 6),  -- ffix only
    close              numeric(18, 4),
    market_cap_inr     numeric(24, 2),  -- MKT_CAP in Ix, FF_MKT_CAP in ffix
    weightage          numeric(10, 4),
    PRIMARY KEY (file_id, source, index_name, symbol, series)
);
CREATE INDEX nse_index_issue_cap_date ON raw.nse_index_issue_cap (trade_date);

CREATE VIEW raw.nse_mcap_current AS
SELECT m.* FROM raw.nse_mcap m JOIN raw.vendor_file_current f USING (file_id);
CREATE VIEW raw.nse_index_issue_cap_current AS
SELECT i.* FROM raw.nse_index_issue_cap i JOIN raw.vendor_file_current f USING (file_id);

-- A listing's share count, in periods. The builder rebuilds a listing's periods as a whole.
CREATE TABLE ref.listing_shares (
    listing_id  integer NOT NULL REFERENCES ref.listing,
    valid_from  date NOT NULL,
    valid_to    date CHECK (valid_to > valid_from),
    shares      bigint NOT NULL CHECK (shares > 0),
    source      text NOT NULL CHECK (source IN ('nse_mcap', 'index_file', 'walk_back', 'filing')),
    evidence    jsonb NOT NULL DEFAULT '{}',
    built_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (listing_id, valid_from),
    EXCLUDE USING gist (int4range(listing_id, listing_id, '[]') WITH &&,
                        daterange(valid_from, valid_to) WITH &&)
);

-- Unadjusted close times the shares on the same day: both point in time, so no adjustment.
CREATE VIEW clean.market_cap_1d AS
SELECT b.listing_id, b.date, s.shares, b.close,
       b.close * s.shares AS market_cap_inr, s.source
  FROM clean.bar_1d b
  JOIN ref.listing_shares s
    ON s.listing_id = b.listing_id AND s.valid_from <= b.date
   AND (s.valid_to IS NULL OR s.valid_to > b.date);

INSERT INTO ops.dq_check (check_id, layer, feed_id, severity, description, params, owner_action) VALUES
    ('mcap_rows_mapped', 'ref', NULL, 'warn',
     'Every mcap row in a listing series maps to one listing', '{}',
     'Look at the unmapped symbols; usually a listing the builder has not seen yet'),
    ('shares_present', 'ref', NULL, 'warn',
     'Every equity listing that traded has a share count', '{}',
     'List them with their last traded value; find a count if any would be in the universe'),
    ('mcap_matches_nse', 'clean', NULL, 'warn',
     'Our close times shares is within 1% of NSE''s market cap', '{"tolerance": 0.01}',
     'Look at the listing''s share periods'),
    ('walk_back_vs_index', 'ref', NULL, 'warn',
     'Walked-back counts are within 5% of ISSUE_CAP for 2010 to 2013 index members',
     '{"tolerance": 0.05}',
     'Look for a missed corporate action or a fresh issue');

REVOKE ALL ON raw.nse_mcap, raw.nse_index_issue_cap, raw.nse_mcap_current,
    raw.nse_index_issue_cap_current, ref.listing_shares, clean.market_cap_1d FROM cagr_writer;
GRANT SELECT, INSERT ON raw.nse_mcap, raw.nse_index_issue_cap TO cagr_writer;
GRANT SELECT ON raw.nse_mcap_current, raw.nse_index_issue_cap_current, clean.market_cap_1d
    TO cagr_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON ref.listing_shares TO cagr_writer;
GRANT SELECT ON raw.nse_mcap, raw.nse_index_issue_cap, raw.nse_mcap_current,
    raw.nse_index_issue_cap_current, ref.listing_shares, clean.market_cap_1d TO cagr_read;
