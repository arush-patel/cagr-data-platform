-- 0004 clean: trusted bars, corporate actions, dirty ranges
-- Source: Cagr Schema Blueprint v3 (platform additions of 16 Oct 2026), adapted for a from-scratch database.

-- ---------------------------------------------------------------------------------------------
-- clean: one trusted row per listing per session of its venue's calendar
-- ---------------------------------------------------------------------------------------------

CREATE TABLE clean.qa_flag (
    bit             smallint PRIMARY KEY CHECK (bit BETWEEN 0 AND 30),
    code            text NOT NULL UNIQUE,
    severity        text NOT NULL CHECK (severity IN ('info','warn','critical')),
    description     text NOT NULL
);

-- price_factor multiplies every price BEFORE ex_date (volume divides by it):
--   split / bonus / consolidation  ratio_old / ratio_new   (1:5 split → 1/5; 1:4 bonus → 4/5)
--   rights                         TERP / cum-rights close
--   demerger                       exchange-published cost-of-acquisition ratio of the parent
--   merger / dividend / redenomination(no price effect)  1
CREATE TABLE clean.corporate_action (
    action_id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    listing_id              integer NOT NULL REFERENCES ref.listing (listing_id),
    ex_date                 date NOT NULL,
    kind                    text NOT NULL CHECK (kind IN ('split','bonus','consolidation','rights','demerger',
                                                          'merger','dividend','redenomination','other')),
    ratio_new               numeric(18,8) CHECK (ratio_new > 0),
    ratio_old               numeric(18,8) CHECK (ratio_old > 0),
    price_factor            double precision NOT NULL DEFAULT 1 CHECK (price_factor > 0),
    counterparty_listing_id integer REFERENCES ref.listing (listing_id),  -- merger: acquirer; demerger: new child
    counterparty_ratio      numeric(18,8) CHECK (counterparty_ratio > 0), -- counterparty shares per share held
    cash_amount             double precision CHECK (cash_amount >= 0),    -- dividend per share, cash in lieu
    cash_currency           text REFERENCES ref.currency (code),
    announced_on            date,
    record_date             date,
    source                  text NOT NULL CHECK (source IN ('exchange','screener','detected','manual')),
    subject_raw             text,                  -- exchange free text, e.g. 'Bonus 2:5', 'Face Value Split … Rs 10/- To Rs 2/-'
    parse_status            text NOT NULL DEFAULT 'parsed' CHECK (parse_status IN ('parsed','needs_review','manual')),
    status                  text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed','confirmed','applied','rejected')),
    created_at              timestamptz NOT NULL DEFAULT now(),
    applied_at              timestamptz,
    UNIQUE (listing_id, ex_date, kind),
    CHECK (kind NOT IN ('split','bonus','consolidation')
           OR (ratio_new IS NOT NULL AND ratio_old IS NOT NULL
               AND abs(price_factor - (ratio_old / ratio_new)::double precision) < 1e-9)),
    CHECK (kind NOT IN ('merger','demerger') OR (counterparty_listing_id IS NOT NULL AND counterparty_ratio IS NOT NULL)),
    CHECK (kind NOT IN ('merger','dividend','buyback','bonus_preference','redenomination') OR price_factor = 1),
    CHECK (parse_status <> 'needs_review' OR status = 'proposed'),
    CHECK ((cash_amount IS NULL) = (cash_currency IS NULL)),
    CHECK ((status = 'applied') = (applied_at IS NOT NULL))
);
CREATE INDEX corporate_action_pending ON clean.corporate_action (status, ex_date) WHERE status IN ('proposed','confirmed');

-- Bars for every timeframe share one shape and one key name, so code addresses them as
-- clean.bar_<timeframe> and ind/feat/sig tables as <tier>.<family>_<timeframe>.
--   1d  : bar_date = session_date of the listing's calendar
--   1w  : bar_date = last session of the week (calendar_session.week_end)
--   1mo : bar_date = last session of the month (calendar_session.month_end)
CREATE TABLE clean.bar_1d (
    bar_date        date NOT NULL,
    listing_id      integer NOT NULL REFERENCES ref.listing (listing_id),
    open            double precision CHECK (open > 0),   -- adjusted = raw × adj_factor
    high            double precision CHECK (high > 0),
    low             double precision CHECK (low > 0),
    close           double precision CHECK (close > 0),
    volume          double precision CHECK (volume >= 0),     -- as reported (today's behaviour)
    volume_adj      double precision CHECK (volume_adj >= 0), -- volume / adj_factor (split-consistent)
    quote_volume    double precision CHECK (quote_volume >= 0),
    raw_close       double precision,                    -- primary vendor close before adjustment
    raw_adj_close   double precision,                    -- vendor dividend-adjusted close
    adj_factor      double precision NOT NULL DEFAULT 1 CHECK (adj_factor > 0),  -- product of applied price_factors after this bar
    is_traded       boolean NOT NULL,
    qa_flags        integer NOT NULL DEFAULT 0,          -- bitmask over clean.qa_flag
    source_vendor_id smallint REFERENCES ref.vendor (vendor_id),  -- NULL when no vendor had a bar
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (bar_date, listing_id),
    CHECK (is_traded = (coalesce(volume, 0) > 0 AND close IS NOT NULL)),
    CHECK ((close IS NULL) = (source_vendor_id IS NULL))
) PARTITION BY RANGE (bar_date);
CREATE INDEX bar_1d_listing_idx ON clean.bar_1d (listing_id, bar_date);
SELECT ops.create_year_partitions('clean.bar_1d', 2010, 2030);

CREATE TABLE clean.bar_1w (
    bar_date        date NOT NULL,
    listing_id      integer NOT NULL REFERENCES ref.listing (listing_id),
    period_start    date NOT NULL,
    open            double precision,
    high            double precision,
    low             double precision,
    close           double precision,
    volume          double precision,
    sessions_traded smallint NOT NULL CHECK (sessions_traded BETWEEN 0 AND 7),
    is_complete     boolean NOT NULL,
    PRIMARY KEY (bar_date, listing_id),
    CHECK (period_start <= bar_date)
);
CREATE INDEX bar_1w_listing_idx ON clean.bar_1w (listing_id, bar_date);

CREATE TABLE clean.bar_1mo (
    bar_date        date NOT NULL,
    listing_id      integer NOT NULL REFERENCES ref.listing (listing_id),
    period_start    date NOT NULL,
    open            double precision,
    high            double precision,
    low             double precision,
    close           double precision,
    volume          double precision,
    sessions_traded smallint NOT NULL CHECK (sessions_traded BETWEEN 0 AND 31),
    is_complete     boolean NOT NULL,
    PRIMARY KEY (bar_date, listing_id),
    CHECK (period_start <= bar_date)
);
CREATE INDEX bar_1mo_listing_idx ON clean.bar_1mo (listing_id, bar_date);

-- A fact: "this listing's history changed from from_date". anchor_date is the event date
-- (ex-date, mapping fix date) when one exists. fb.dirty_plan turns facts into per-family work.
CREATE TABLE clean.dirty_range (
    dirty_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    listing_id      integer REFERENCES ref.listing (listing_id),   -- NULL = every listing on venue_id
    venue_id        smallint NOT NULL REFERENCES ref.venue (venue_id),
    from_date       date NOT NULL,
    to_date         date,                          -- NULL = through the latest session
    anchor_date     date,
    industry_id     smallint REFERENCES ref.industry (industry_id),   -- set: only this industry's cross-sections changed
    reason          text NOT NULL CHECK (reason IN ('corporate_action','mapping_fix','vendor_revision','new_listing',
                                                    'classification_change','calendar_fix','universe_rule_change','manual')),
    source_ref      text,                          -- e.g. 'corporate_action:42', 'listing_change:7'
    detected_at     timestamptz NOT NULL DEFAULT now(),
    CHECK (to_date IS NULL OR to_date >= from_date)
);
CREATE INDEX dirty_range_from_idx ON clean.dirty_range (venue_id, from_date);
