-- depends: 0008_reviewer
-- NSE's corporate-actions API and ETF list as raw feeds, and a natural key for parsed actions
-- (spec 0002, sections 4, 6.2 and 7.1). Migration 0008 is the review page's login.

-- One file per calendar year of ex-dates, partitioned on 1 Jan of the year, as the holiday
-- list is. The nightly run fetches this year and next again as a revision check, so an
-- announcement or a correction becomes a new revision.
INSERT INTO raw.feed (feed_id, vendor_id, dataset, priority, format, url_template,
                      available_from, available_to, cadence, schedule_cron) VALUES
    ('nse_corporate_actions', 'nse_api', 'nse_corporate_actions', 1, 'corporate_actions_v1',
     'https://www.nseindia.com/api/corporates-corporateActions?index=equities&from_date=01-01-{YYYY}&to_date=31-12-{YYYY}',
     '2010-01-01', NULL, 'weekly', '30 7 * * *'),
    ('nse_sme_corporate_actions', 'nse_api', 'nse_sme_corporate_actions', 1,
     'sme_corporate_actions_v1',
     'https://www.nseindia.com/api/corporates-corporateActions?index=sme&from_date=01-01-{YYYY}&to_date=31-12-{YYYY}',
     '2010-01-01', NULL, 'weekly', '30 7 * * *'),
    ('nse_etf_list', 'nse_archives', 'nse_etf_list', 1, 'etf_list_v1',
     'https://nsearchives.nseindia.com/content/equities/eq_etfseclist.csv',
     NULL, NULL, 'daily', '30 7 * * *');

CREATE TABLE raw.nse_corporate_action (
    file_id        bigint NOT NULL REFERENCES raw.vendor_file,
    row_no         integer NOT NULL CHECK (row_no >= 1),  -- position in the response
    symbol         text,
    series         text,
    isin           text,
    company        text,
    subject        text,
    ex_date        date,
    record_date    date,
    face_value     numeric(18, 4),
    bc_start_date  date,
    bc_end_date    date,
    nd_start_date  date,
    nd_end_date    date,
    board          text NOT NULL CHECK (board IN ('main', 'sme')),
    PRIMARY KEY (file_id, row_no)
);
CREATE INDEX nse_corporate_action_ex_date ON raw.nse_corporate_action (ex_date);

CREATE TABLE raw.nse_etf_list (
    file_id         bigint NOT NULL REFERENCES raw.vendor_file,
    snapshot_date   date NOT NULL,
    symbol          text NOT NULL,
    underlying      text,
    security_name   text,
    listing_date    date,
    market_lot      integer,
    isin            text,
    face_value      numeric(18, 4),
    etf_underlying  text,
    underlying_key  text,
    PRIMARY KEY (file_id, symbol)
);

CREATE VIEW raw.nse_corporate_action_current AS
SELECT a.* FROM raw.nse_corporate_action a JOIN raw.vendor_file_current f USING (file_id);

-- A parsed action is one action of one row: a subject can hold two ("Bon 1:1/Fv Spl").
ALTER TABLE ref.corporate_action
    ADD COLUMN seq smallint NOT NULL DEFAULT 1 CHECK (seq >= 1),
    ADD COLUMN face_value numeric(18, 4);
CREATE UNIQUE INDEX corporate_action_natural ON ref.corporate_action
    (listing_id, ex_date, subject, seq);

REVOKE ALL ON raw.nse_corporate_action, raw.nse_etf_list, raw.nse_corporate_action_current
    FROM cagr_writer;
GRANT SELECT, INSERT ON raw.nse_corporate_action, raw.nse_etf_list TO cagr_writer;
GRANT SELECT ON raw.nse_corporate_action_current TO cagr_writer;
GRANT SELECT ON raw.nse_corporate_action, raw.nse_etf_list, raw.nse_corporate_action_current
    TO cagr_read;
