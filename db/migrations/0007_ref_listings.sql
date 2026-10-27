-- depends: 0006_ohlc_t0
-- Listings, their dated symbols and ISINs, the change log, parsed corporate actions and price
-- adjustments (spec 0002, sections 6, 8 and 9). ref.listing is the stock master.
-- Dated rows: valid_from inclusive, valid_to exclusive and null while current.

CREATE TABLE ref.listing (
    listing_id     integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    venue_id       text NOT NULL REFERENCES ref.venue,
    asset_class    text NOT NULL CHECK (asset_class IN ('equity', 'etf', 'reit', 'invit', 'index')),
    board          text CHECK (board IN ('main', 'sme')),
    name           text,
    listed_on      date,
    first_session  date NOT NULL,
    last_session   date NOT NULL,
    status         text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive', 'merged')),
    merged_into    integer REFERENCES ref.listing,
    CHECK ((asset_class = 'index') = (board IS NULL)),
    CHECK (last_session >= first_session),
    CHECK ((status = 'merged') = (merged_into IS NOT NULL)),
    CHECK (merged_into <> listing_id)
);
COMMENT ON TABLE ref.listing IS
    'The stock master: one row per security on a venue, kept for its whole life (spec 0002, 4)';

CREATE TABLE ref.listing_change (
    change_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind            text NOT NULL
                    CHECK (kind IN ('new_listing', 'symbol_change', 'isin_change', 'merge',
                                    'index_rename')),
    listing_id      integer NOT NULL REFERENCES ref.listing,
    effective_date  date NOT NULL,
    payload         jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(payload) = 'object'),
    evidence        jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(evidence) = 'object'),
    status          text NOT NULL CHECK (status IN ('applied', 'pending', 'rejected')),
    auto            boolean NOT NULL,
    proposed_by     text NOT NULL,
    decided_by      text,
    decided_at      timestamptz,
    note            text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CHECK (decided_by <> proposed_by),
    CHECK ((decided_by IS NULL) = (decided_at IS NULL)),
    CHECK (status <> 'pending' OR decided_by IS NULL),
    CHECK (auto OR status = 'pending' OR decided_by IS NOT NULL)
);
CREATE INDEX listing_change_listing ON ref.listing_change (listing_id, effective_date);
CREATE INDEX listing_change_pending ON ref.listing_change (created_at) WHERE status = 'pending';
COMMENT ON COLUMN ref.listing_change.proposed_by IS
    '''rules'' for rows the builder proposes, otherwise the person''s name';

-- One symbol and one ISIN per listing at a time. A text column cannot go in a gist exclusion
-- without btree_gist, so "no symbol on two listings on one day" is the ref-layer checks' job.
CREATE TABLE ref.listing_symbol (
    listing_id  integer NOT NULL REFERENCES ref.listing,
    symbol      text NOT NULL CHECK (symbol <> ''),
    valid_from  date NOT NULL,
    valid_to    date CHECK (valid_to > valid_from),
    change_id   bigint NOT NULL REFERENCES ref.listing_change,
    PRIMARY KEY (listing_id, valid_from),
    EXCLUDE USING gist (int4range(listing_id, listing_id, '[]') WITH &&,
                        daterange(valid_from, valid_to) WITH &&)
);
CREATE INDEX listing_symbol_symbol ON ref.listing_symbol (symbol, valid_from);

CREATE TABLE ref.listing_isin (
    listing_id  integer NOT NULL REFERENCES ref.listing,
    isin        text NOT NULL CHECK (isin ~ '^IN[A-Z0-9]{10}$'),
    valid_from  date NOT NULL,
    valid_to    date CHECK (valid_to > valid_from),
    change_id   bigint NOT NULL REFERENCES ref.listing_change,
    PRIMARY KEY (listing_id, valid_from),
    EXCLUDE USING gist (int4range(listing_id, listing_id, '[]') WITH &&,
                        daterange(valid_from, valid_to) WITH &&)
);
CREATE INDEX listing_isin_isin ON ref.listing_isin (isin, valid_from);

-- NSE has renamed its indices; ind_close_all carries the name of the day (spec 0002, 5.5).
CREATE TABLE ref.index_alias (
    name       text PRIMARY KEY,
    canonical  text NOT NULL
);
INSERT INTO ref.index_alias (name, canonical) VALUES
    ('S&P CNX Nifty', 'Nifty 50'),
    ('CNX Nifty',     'Nifty 50');

CREATE TABLE ref.corporate_action (
    action_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    listing_id      integer NOT NULL REFERENCES ref.listing,
    ex_date         date NOT NULL,
    kind            text NOT NULL
                    CHECK (kind IN ('split', 'bonus', 'consolidation', 'rights', 'demerger',
                                    'dividend', 'buyback', 'other')),
    ratio_new       numeric CHECK (ratio_new > 0),
    ratio_old       numeric CHECK (ratio_old > 0),
    cash_per_share  numeric CHECK (cash_per_share >= 0),
    issue_price     numeric CHECK (issue_price > 0),
    subject         text NOT NULL,
    parse_status    text NOT NULL CHECK (parse_status IN ('parsed', 'unparsed')),
    source_file_id  bigint NOT NULL REFERENCES raw.vendor_file
);
CREATE INDEX corporate_action_listing ON ref.corporate_action (listing_id, ex_date);

-- A row's factor never changes once applied; a correction rejects it and applies a new row.
CREATE TABLE ref.price_adjustment (
    adjustment_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    listing_id       integer NOT NULL REFERENCES ref.listing,
    ex_date          date NOT NULL,
    factor           double precision CHECK (factor > 0),
    observed_factor  double precision CHECK (observed_factor > 0),
    expected_factor  double precision CHECK (expected_factor > 0),
    close_before     numeric,
    close_on         numeric,
    action_id        bigint REFERENCES ref.corporate_action,
    status           text NOT NULL CHECK (status IN ('applied', 'pending', 'rejected')),
    auto             boolean NOT NULL,
    proposed_by      text NOT NULL,
    decided_by       text,
    decided_at       timestamptz,
    applied_at       timestamptz,
    note             text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CHECK (decided_by <> proposed_by),
    CHECK ((decided_by IS NULL) = (decided_at IS NULL)),
    CHECK (status <> 'pending' OR decided_by IS NULL),
    CHECK (auto OR status = 'pending' OR decided_by IS NOT NULL),
    CHECK ((status = 'applied') = (applied_at IS NOT NULL)),
    CHECK (status <> 'applied' OR factor IS NOT NULL)
);
CREATE UNIQUE INDEX price_adjustment_one_live ON ref.price_adjustment (listing_id, ex_date)
    WHERE status <> 'rejected';
CREATE INDEX price_adjustment_pending ON ref.price_adjustment (created_at)
    WHERE status = 'pending';

-- Read interface (spec 0002, section 9).
CREATE VIEW ref.listing_current AS
SELECT l.listing_id, s.symbol, i.isin, l.name, l.asset_class, l.board, l.status,
       l.first_session, l.last_session
FROM ref.listing l
LEFT JOIN LATERAL (
    SELECT symbol FROM ref.listing_symbol
    WHERE listing_id = l.listing_id ORDER BY valid_from DESC LIMIT 1
) s ON true
LEFT JOIN LATERAL (
    SELECT isin FROM ref.listing_isin
    WHERE listing_id = l.listing_id ORDER BY valid_from DESC LIMIT 1
) i ON true;

CREATE FUNCTION ref.listings_as_of(d date)
RETURNS TABLE (listing_id integer, symbol text, isin text, name text, asset_class text,
               board text)
LANGUAGE sql STABLE AS $$
    SELECT l.listing_id, s.symbol, i.isin, l.name, l.asset_class, l.board
    FROM ref.listing l
    LEFT JOIN ref.listing_symbol s
        ON s.listing_id = l.listing_id AND s.valid_from <= d
       AND (s.valid_to IS NULL OR s.valid_to > d)
    LEFT JOIN ref.listing_isin i
        ON i.listing_id = l.listing_id AND i.valid_from <= d
       AND (i.valid_to IS NULL OR i.valid_to > d)
    WHERE l.first_session <= d AND l.status <> 'merged'
$$;

CREATE FUNCTION ref.listing_by_symbol(sym text, d date)
RETURNS integer
LANGUAGE sql STABLE AS $$
    SELECT s.listing_id
    FROM ref.listing_symbol s JOIN ref.listing l USING (listing_id)
    WHERE s.symbol = sym AND s.valid_from <= d AND (s.valid_to IS NULL OR s.valid_to > d)
      AND l.status <> 'merged'
$$;

INSERT INTO ops.dq_check (check_id, layer, feed_id, severity, description, params, owner_action) VALUES
    ('listing_unique_symbol', 'ref', NULL, 'critical',
     'No symbol belongs to two listings on the same day', '{}',
     'Block; find the two listings and merge or fix one'),
    ('listing_unique_isin', 'ref', NULL, 'critical',
     'No ISIN belongs to two listings on the same day', '{}',
     'Block; find the two listings and merge or fix one'),
    ('listing_rows_mapped', 'ref', NULL, 'critical',
     'Every bhavcopy row in a listing series maps to exactly one listing',
     '{"series": ["EQ", "BE", "BZ", "SM", "ST", "SZ", "RR", "IV"], "etf_isin_prefix": "INF"}',
     'Block; the listing builder missed a row'),
    ('listing_isin_present', 'ref', NULL, 'warn',
     'Every active equity listing has an ISIN from 22 Jun 2011', '{"from": "2011-06-22"}',
     'List the listings; check their bhavcopy rows'),
    ('symbol_change_confirmed', 'ref', NULL, 'warn',
     'Each symbol change applied by rule 1 has a matching symbolchange.csv row',
     '{"window_sessions": 5}',
     'Look at the change; reject it if it is wrong'),
    ('master_coverage', 'ref', NULL, 'warn',
     'Every symbol in the latest masters has a listing, and every listing that traded is in one',
     '{"datasets": ["nse_equity_list", "nse_sme_equity_list", "nse_etf_list"]}',
     'List the gaps; usually a new listing or a delisting the masters already know'),
    ('corporate_action_parsed', 'ref', NULL, 'warn',
     'Every action with an ex-date in the next 3 sessions is parsed', '{"ahead_sessions": 3}',
     'Add a parser pattern or enter the action by hand'),
    ('adjustment_explained', 'ref', NULL, 'warn',
     'Every adjustment event on the session is applied or pending, never ignored',
     '{"jump_below": 0.75, "jump_above": 1.33}',
     'Look at the jump and decide it'),
    ('review_waiting', 'ref', NULL, 'warn',
     'No pending listing change or adjustment is older than 3 sessions', '{"max_sessions": 3}',
     'Decide the pending items on the review page');

-- New tables get full DML for cagr_writer by default; narrow it to what the builder needs.
REVOKE ALL ON ref.listing, ref.listing_change, ref.listing_symbol, ref.listing_isin,
              ref.index_alias, ref.corporate_action, ref.price_adjustment,
              ref.listing_current FROM cagr_writer;
GRANT SELECT, INSERT, UPDATE ON ref.listing, ref.listing_change, ref.price_adjustment
    TO cagr_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON ref.listing_symbol, ref.listing_isin,
    ref.corporate_action TO cagr_writer;
GRANT SELECT ON ref.index_alias, ref.listing_current TO cagr_writer;
GRANT SELECT ON ref.listing, ref.listing_change, ref.listing_symbol, ref.listing_isin,
    ref.index_alias, ref.corporate_action, ref.price_adjustment, ref.listing_current
    TO cagr_read;
