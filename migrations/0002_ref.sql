-- 0002 ref: currencies, calendars, venues, instruments, listings, vendors, feeds, classification, universes
-- Source: Cagr Schema Blueprint v3 (platform additions of 16 Oct 2026), adapted for a from-scratch database.

-- ---------------------------------------------------------------------------------------------
-- ref: currencies, calendars, venues
-- ---------------------------------------------------------------------------------------------

CREATE TABLE ref.currency (
    code            text PRIMARY KEY CHECK (code ~ '^[A-Z0-9]{2,10}$'),   -- 'INR', 'USD', 'USDT', 'BTC'
    name            text NOT NULL,
    is_fiat         boolean NOT NULL,
    minor_units     smallint NOT NULL CHECK (minor_units BETWEEN 0 AND 18)
);

CREATE TABLE ref.fx_rate_daily (
    rate_date       date NOT NULL,
    base            text NOT NULL REFERENCES ref.currency (code),
    quote           text NOT NULL REFERENCES ref.currency (code),
    rate            double precision NOT NULL CHECK (rate > 0),          -- 1 base = rate quote
    source          text NOT NULL,
    PRIMARY KEY (rate_date, base, quote),
    CHECK (base <> quote)
);

CREATE TABLE ref.calendar (
    calendar_id     smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code            text NOT NULL UNIQUE,          -- 'XNSE', 'XNYS', 'CRYPTO_UTC'
    timezone        text NOT NULL,                 -- IANA, e.g. 'Asia/Kolkata'
    kind            text NOT NULL CHECK (kind IN ('exchange','continuous'))  -- continuous = 24/7, one session per UTC day
);

-- One row per session per calendar. Windows count sessions (session_idx), never rows, so a
-- listing that does not trade on a session still advances the clock.
CREATE TABLE ref.calendar_session (
    calendar_id     smallint NOT NULL REFERENCES ref.calendar (calendar_id),
    session_date    date NOT NULL,
    session_idx     integer NOT NULL,
    opens_at        timestamptz NOT NULL,
    closes_at       timestamptz NOT NULL,
    week_end        date NOT NULL,                 -- last session of the session's week
    month_end       date NOT NULL,                 -- last session of the session's month
    is_special      boolean NOT NULL DEFAULT false,-- e.g. XNSE 2025-02-01 (Saturday budget session)
    note            text,
    PRIMARY KEY (calendar_id, session_date),
    UNIQUE (calendar_id, session_idx),
    CHECK (closes_at > opens_at),
    CHECK (week_end >= session_date AND month_end >= session_date)
);

CREATE TABLE ref.venue (
    venue_id        smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code            text NOT NULL UNIQUE,          -- ISO 10383 MIC where one exists: 'XNSE', 'XNAS'; else 'BINANCE'
    name            text NOT NULL,
    kind            text NOT NULL CHECK (kind IN ('stock_exchange','crypto_exchange','index_provider')),
    calendar_id     smallint NOT NULL REFERENCES ref.calendar (calendar_id),
    currency        text NOT NULL REFERENCES ref.currency (code),   -- default quote currency
    country         char(2)                                          -- NULL for crypto venues
);

-- ---------------------------------------------------------------------------------------------
-- ref: instruments, listings, identifiers, tickers
-- ---------------------------------------------------------------------------------------------

CREATE TABLE ref.instrument (
    instrument_id   integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    asset_class     text NOT NULL CHECK (asset_class IN ('equity','etf','index','crypto')),
    name            text NOT NULL,
    base_asset      text REFERENCES ref.currency (code),  -- crypto only: 'BTC'
    created_at      timestamptz NOT NULL DEFAULT now(),
    CHECK ((asset_class = 'crypto') = (base_asset IS NOT NULL))
);

-- ISINs change (an NSE face-value split usually issues a new ISIN); the instrument does not.
CREATE TABLE ref.instrument_identifier (
    instrument_id   integer NOT NULL REFERENCES ref.instrument (instrument_id),
    id_type         text NOT NULL CHECK (id_type IN ('isin','cusip','figi','coingecko')),
    value           text NOT NULL,
    valid_from      date NOT NULL,
    valid_to        date,
    PRIMARY KEY (instrument_id, id_type, valid_from),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (id_type <> 'isin' OR value ~ '^[A-Z]{2}[A-Z0-9]{9}[0-9]$'),
    EXCLUDE USING gist (instrument_id WITH =, id_type WITH =, daterange(valid_from, valid_to) WITH &&),
    EXCLUDE USING gist (id_type WITH =, value WITH =, daterange(valid_from, valid_to) WITH &&)
);

CREATE TABLE ref.listing (
    listing_id       integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    instrument_id    integer NOT NULL REFERENCES ref.instrument (instrument_id),
    venue_id         smallint NOT NULL REFERENCES ref.venue (venue_id),
    quote_currency   text NOT NULL REFERENCES ref.currency (code),
    listed_on        date,
    delisted_on      date,
    status           text NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended','delisted')),
    delisting_reason text CHECK (delisting_reason IN ('merger','voluntary','regulatory','scheme','pair_removed')),
    UNIQUE (instrument_id, venue_id, quote_currency),
    UNIQUE (listing_id, venue_id),                 -- target for composite FKs below
    CHECK (delisted_on IS NULL OR listed_on IS NULL OR delisted_on >= listed_on),
    CHECK ((status = 'delisted') = (delisted_on IS NOT NULL)),
    CHECK ((status = 'delisted') = (delisting_reason IS NOT NULL))
);
CREATE INDEX listing_venue_idx ON ref.listing (venue_id, status);

-- Exchange ticker over time. Unique per venue at any instant.
CREATE TABLE ref.listing_symbol (
    listing_id      integer NOT NULL,
    venue_id        smallint NOT NULL,
    valid_from      date NOT NULL,
    valid_to        date,
    ticker          text NOT NULL,                 -- today's `script` on XNSE, e.g. 'M&M'
    display_name    text,
    PRIMARY KEY (listing_id, valid_from),
    FOREIGN KEY (listing_id, venue_id) REFERENCES ref.listing (listing_id, venue_id),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    EXCLUDE USING gist (listing_id WITH =, daterange(valid_from, valid_to) WITH &&),
    EXCLUDE USING gist (venue_id WITH =, ticker WITH =, daterange(valid_from, valid_to) WITH &&)
);
CREATE UNIQUE INDEX listing_symbol_current ON ref.listing_symbol (venue_id, ticker) WHERE valid_to IS NULL;

-- ---------------------------------------------------------------------------------------------
-- ref: vendors, brokers, feeds and their symbols
-- ---------------------------------------------------------------------------------------------

CREATE TABLE ref.vendor (
    vendor_id          smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code               text NOT NULL UNIQUE,       -- 'yahoo', 'screener', 'nse_archives', 'zerodha', 'binance'
    kind               text NOT NULL CHECK (kind IN ('market_data','fundamentals','exchange_direct','broker')),
    adapter            text NOT NULL,              -- Python adapter class, e.g. 'vendors.yahoo.YahooAdapter'
    credential_block   text,                       -- name of the Prefect Secret block; never the secret itself
    rate_limit_per_min integer CHECK (rate_limit_per_min > 0),  -- mirrored into a Prefect global concurrency limit
    notes              text
);

-- How each vendor names each listing, over time. A ticker change on the exchange usually
-- means a vendor symbol change too; both are recorded, neither moves any data.
CREATE TABLE ref.vendor_symbol (
    listing_id      integer NOT NULL REFERENCES ref.listing (listing_id),
    vendor_id       smallint NOT NULL REFERENCES ref.vendor (vendor_id),
    valid_from      date NOT NULL,
    valid_to        date,
    symbol          text,                          -- 'M&M.NS' (yahoo), '1234' (screener), 'BTCUSDT' (binance)
    status          text NOT NULL DEFAULT 'mapped' CHECK (status IN ('mapped','ambiguous','unmapped')),
    verified_at     timestamptz,
    PRIMARY KEY (listing_id, vendor_id, valid_from),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK ((status = 'mapped') = (symbol IS NOT NULL)),
    EXCLUDE USING gist (listing_id WITH =, vendor_id WITH =, daterange(valid_from, valid_to) WITH &&),
    EXCLUDE USING gist (vendor_id WITH =, symbol WITH =, daterange(valid_from, valid_to) WITH &&)
);

-- A feed is "this vendor, this dataset, this venue, on this schedule". The Prefect deployments
-- are generated from this table (one deployment per active feed), so the schedule lives here.
CREATE TABLE ref.feed (
    feed_id             smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code                text NOT NULL UNIQUE,      -- 'xnse_eod_yahoo'
    vendor_id           smallint NOT NULL REFERENCES ref.vendor (vendor_id),
    venue_id            smallint NOT NULL REFERENCES ref.venue (venue_id),
    dataset             text NOT NULL CHECK (dataset IN ('listing_master','symbol_change','holiday_calendar','corporate_action',
                                                         'eod_bar','index_close','reference_close','shareholding',
                                                         'fundamentals','intraday_bar')),
    asset_class         text NOT NULL CHECK (asset_class IN ('equity','etf','index','crypto')),
    priority            smallint NOT NULL CHECK (priority >= 1),   -- 1 = primary source for (venue, dataset, asset_class)
    schedule_cron       text NOT NULL,             -- in schedule_tz
    schedule_tz         text NOT NULL,
    lookback_sessions   smallint NOT NULL DEFAULT 5 CHECK (lookback_sessions >= 1),  -- re-pulled every run
    full_refresh_cron   text,                      -- periodic full-history pull to catch vendor revisions
    max_staleness       interval NOT NULL,         -- freshness SLA used by monitoring
    active              boolean NOT NULL DEFAULT true,
    UNIQUE (venue_id, dataset, asset_class, priority)
);

-- Everything reference-sync detects lands here first. Safe kinds (exact ISIN match, vendor symbol
-- verified) are auto-applied; anything ambiguous waits for a person. Applied by
-- ref.apply_listing_change(change_id).
CREATE TABLE ref.listing_change (
    change_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind            text NOT NULL CHECK (kind IN ('new_listing','ticker_change','identifier_change',
                                                  'vendor_symbol_change','classification_change','delisting')),
    listing_id      integer REFERENCES ref.listing (listing_id),   -- NULL until a new_listing is applied
    effective_date  date NOT NULL,
    payload         jsonb NOT NULL,
    source_feed_id  smallint REFERENCES ref.feed (feed_id),
    status          text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed','approved','applied','rejected')),
    auto_apply      boolean NOT NULL DEFAULT false,
    detected_at     timestamptz NOT NULL DEFAULT now(),
    proposed_by     text,                          -- NULL when a feed proposed it
    decided_by      text,
    decided_at      timestamptz,
    applied_at      timestamptz,
    note            text,
    CHECK ((kind = 'new_listing') OR listing_id IS NOT NULL),
    CHECK ((status = 'applied') = (applied_at IS NOT NULL)),
    CHECK (decided_by IS NULL OR proposed_by IS NULL OR decided_by <> proposed_by)   -- D16: someone else approves
);
CREATE INDEX listing_change_pending ON ref.listing_change (status, detected_at) WHERE status IN ('proposed','approved');

-- ---------------------------------------------------------------------------------------------
-- ref: classification, market cap, universes (all point in time)
-- ---------------------------------------------------------------------------------------------

CREATE TABLE ref.classification_scheme (
    scheme_id       smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code            text NOT NULL UNIQUE,          -- 'cagr_industry_v1' (today's group_3), 'gics', 'crypto_sector'
    description     text
);

CREATE TABLE ref.industry (
    industry_id      smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    scheme_id        smallint NOT NULL REFERENCES ref.classification_scheme (scheme_id),
    name             text NOT NULL,                -- script_master.group_3
    sector_index     text,                         -- script_master.group_2, e.g. 'Nifty Metal'
    index_listing_id integer REFERENCES ref.listing (listing_id),   -- the sector index as a listing
    UNIQUE (scheme_id, name),
    UNIQUE (industry_id, scheme_id)
);

-- D15: an approved change restates the whole history by default; the rows it replaces are kept
-- (superseded_at set) so every past assignment stays auditable. 'from_date' changes close the
-- current row instead.
CREATE TABLE ref.instrument_classification (
    classification_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    instrument_id    integer NOT NULL REFERENCES ref.instrument (instrument_id),
    scheme_id        smallint NOT NULL,
    valid_from       date NOT NULL,
    valid_to         date,
    industry_id      smallint NOT NULL,
    index_membership text[] NOT NULL DEFAULT '{}', -- script_master.group_1 (Nifty index)
    change_id        bigint REFERENCES ref.listing_change (change_id),   -- the approved change that wrote it
    superseded_at    timestamptz,                   -- set when a later correction replaced this row
    FOREIGN KEY (industry_id, scheme_id) REFERENCES ref.industry (industry_id, scheme_id),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    EXCLUDE USING gist (instrument_id WITH =, scheme_id WITH =, daterange(valid_from, valid_to) WITH &&)
        WHERE (superseded_at IS NULL)
);
CREATE INDEX instrument_classification_industry_idx ON ref.instrument_classification (industry_id, valid_from)
    WHERE superseded_at IS NULL;
CREATE INDEX instrument_classification_instrument_idx ON ref.instrument_classification (instrument_id, scheme_id, valid_from)
    WHERE superseded_at IS NULL;

-- Shares outstanding over time. Refreshed quarterly from the shareholding-pattern XBRL
-- (NumberOfFullyPaidUpEquityShares) and adjusted on the ex-date of every split, bonus or
-- consolidation, so market cap needs no vendor.
CREATE TABLE ref.shares_outstanding (
    listing_id      integer NOT NULL REFERENCES ref.listing (listing_id),
    valid_from      date NOT NULL,
    valid_to        date,
    shares          double precision NOT NULL CHECK (shares > 0),
    source          text NOT NULL CHECK (source IN ('shareholding_xbrl','corporate_action','exchange','manual')),
    source_ref      text,                          -- XBRL URL, 'corporate_action:42', …
    PRIMARY KEY (listing_id, valid_from),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    EXCLUDE USING gist (listing_id WITH =, daterange(valid_from, valid_to) WITH &&)
);

-- Derived nightly by the clean step: raw (unadjusted) close × shares valid on that date.
CREATE TABLE ref.market_cap_daily (
    session_date    date NOT NULL,
    listing_id      integer NOT NULL REFERENCES ref.listing (listing_id),
    market_cap      double precision NOT NULL CHECK (market_cap > 0),   -- full currency units, not crores
    currency        text NOT NULL REFERENCES ref.currency (code),
    shares_out      double precision CHECK (shares_out > 0),
    source          text NOT NULL CHECK (source IN ('derived','vendor','snapshot')),
    PRIMARY KEY (session_date, listing_id)
) PARTITION BY RANGE (session_date);
SELECT ops.create_year_partitions('ref.market_cap_daily', 2010, 2030);

CREATE TABLE ref.universe (
    universe_id     smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code            text NOT NULL UNIQUE,          -- 'xnse_mcap_gt_1500cr'
    venue_id        smallint NOT NULL REFERENCES ref.venue (venue_id),
    scheme_id       smallint REFERENCES ref.classification_scheme (scheme_id),  -- industry scheme its families use
    rule            jsonb NOT NULL,                -- {"asset_class":"equity","market_cap_gt":15000000000,"currency":"INR"}
    description     text
);

CREATE TABLE ref.universe_membership (
    universe_id     smallint NOT NULL REFERENCES ref.universe (universe_id),
    session_date    date NOT NULL,
    listing_id      integer NOT NULL,
    PRIMARY KEY (universe_id, session_date, listing_id)
) PARTITION BY RANGE (session_date);
SELECT ops.create_year_partitions('ref.universe_membership', 2010, 2030);
