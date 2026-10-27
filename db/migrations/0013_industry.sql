-- depends: 0012_raw_archive
-- Arush's industry classification, cagr_industry (the old stockdb's group_3), and NSE index tiers
-- (spec 0006, section 4). One industry per listing, applied from its first session: a change
-- restates history, and ref.industry_change keeps every assignment so a past one can be found.

CREATE TABLE ref.industry (
    industry_id   smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name          text NOT NULL UNIQUE CHECK (name <> ''),
    sector_index  text,
    active        boolean NOT NULL DEFAULT true
);
COMMENT ON COLUMN ref.industry.sector_index IS
    'The Nifty sector index the industry belongs to (the old group_2)';

-- The 52 industries and their sector indices, from the old repo's industry_index_map.json.
INSERT INTO ref.industry (name, sector_index) VALUES
    ('Oil and Gas',                             'Nifty Oil & Gas'),
    ('Oil and Gas Consumables/Services',        'Nifty Oil & Gas'),
    ('Private Bank',                            'Nifty Private Bank'),
    ('PSU Bank',                                'Nifty PSU Bank'),
    ('NBFC',                                    'Nifty Financial Services'),
    ('Insurance & Fintech',                     'Nifty Financial Services'),
    ('Capital Market',                          'Nifty Financial Services'),
    ('Investment Holdings',                     'Nifty Financial Services'),
    ('Housing Loan',                            'Nifty Financial Services'),
    ('Telecom Services/EPC/Assets',             'Nifty IT'),
    ('Software',                                'Nifty IT'),
    ('IT Software II',                          'Nifty IT'),
    ('IT Services/Platforms',                   'Nifty IT'),
    ('EPC & Other Infra',                       'Nifty Infra'),
    ('Building Materials I',                    'Nifty Infra'),
    ('Building Materials II',                   'Nifty Infra'),
    ('Ports, Shipping, Railways',               'Nifty Infra'),
    ('Industrial Products (Capital Goods)',     'Nifty Infra'),
    ('Logistics',                               'Nifty Infra'),
    ('FMCG',                                    'Nifty FMCG'),
    ('Food & Beverages',                        'Nifty FMCG'),
    ('Agri Products',                           'Nifty FMCG'),
    ('Packaging & Consumer Ancillary',          'Nifty FMCG'),
    ('Auto OEMs',                               'Nifty Auto'),
    ('Auto Ancillaries I',                      'Nifty Auto'),
    ('Auto Ancillaries II',                     'Nifty Auto'),
    ('Auto Ancillaries III',                    'Nifty Auto'),
    ('Pharma Conglomerates',                    'Nifty Pharma'),
    ('CDMO/CRO/APIs',                           'Nifty Pharma'),
    ('Pharma IPMs/Formulations',                'Nifty Pharma'),
    ('Healthcare Infra',                        'Nifty Healthcare Index'),
    ('Jewellery',                               'Nifty Consumption'),
    ('Retail',                                  'Nifty Consumption'),
    ('Agriculture',                             'Nifty Consumption'),
    ('Pesticides & Agrochemicals',              'Nifty Consumption'),
    ('Industrial Products (Consumables) I',     'Nifty Consumption'),
    ('Industrial Products (Consumables) II',    'Nifty Consumption'),
    ('Textile',                                 'Nifty Consumption'),
    ('Power Asset Owners',                      'Nifty Energy'),
    ('Power Equipment & EPC I',                 'Nifty Energy'),
    ('Power Equipment & EPC II',                'Nifty Energy'),
    ('Steel',                                   'Nifty Metal'),
    ('Non Ferrous',                             'Nifty Metal'),
    ('Media & New Age Consumption',             'Nifty Media'),
    ('Real Estate Developer',                   'Nifty Realty'),
    ('Real Estate Linked + Hotels + Travel I',  'Nifty Realty'),
    ('Real Estate Linked + Hotels + Travel II', 'Nifty Realty'),
    ('Chemicals I',                             'Nifty Chemicals'),
    ('Chemicals II',                            'Nifty Chemicals'),
    ('Home Appliances & Products I',            'Nifty Consumer Durables'),
    ('Home Appliances & Products II',           'Nifty Consumer Durables'),
    ('Defence',                                 'Nifty Defence');

-- Every proposed or decided assignment. A pending row has no industry until a person picks one.
-- The seed's rows (proposed_by 'seed') were curated by hand in the old master, so they are
-- applied without a second decision.
CREATE TABLE ref.industry_change (
    change_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    listing_id      integer NOT NULL REFERENCES ref.listing,
    industry_id     smallint REFERENCES ref.industry,
    effective_date  date NOT NULL,
    status          text NOT NULL CHECK (status IN ('applied', 'pending', 'rejected')),
    proposed_by     text NOT NULL,
    decided_by      text,
    decided_at      timestamptz,
    note            text,
    evidence        jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(evidence) = 'object'),
    created_at      timestamptz NOT NULL DEFAULT now(),
    CHECK (decided_by <> proposed_by),
    CHECK ((decided_by IS NULL) = (decided_at IS NULL)),
    CHECK (status <> 'pending' OR decided_by IS NULL),
    CHECK (status <> 'applied' OR industry_id IS NOT NULL),
    CHECK (status = 'pending' OR decided_by IS NOT NULL OR proposed_by = 'seed')
);
CREATE INDEX industry_change_listing ON ref.industry_change (listing_id, created_at);
CREATE UNIQUE INDEX industry_change_one_pending ON ref.industry_change (listing_id)
    WHERE status = 'pending';
COMMENT ON COLUMN ref.industry_change.proposed_by IS
    '''seed'' for the starting point, ''rules'' for items the builder raises, otherwise a name';

-- The current industry of each listing, for all of its history (decision 6).
CREATE TABLE ref.listing_industry (
    listing_id   integer PRIMARY KEY REFERENCES ref.listing,
    industry_id  smallint NOT NULL REFERENCES ref.industry,
    change_id    bigint NOT NULL REFERENCES ref.industry_change
);
CREATE INDEX listing_industry_industry ON ref.listing_industry (industry_id);

-- NSE index tier periods; no row means no tier. Filled by the tier feed (spec 0006, task 3).
CREATE TABLE ref.listing_tier (
    listing_id  integer NOT NULL REFERENCES ref.listing,
    tier        text NOT NULL
                CHECK (tier IN ('nifty50', 'next50', 'midcap150', 'smallcap250', 'microcap250')),
    valid_from  date NOT NULL,
    valid_to    date CHECK (valid_to > valid_from),
    PRIMARY KEY (listing_id, valid_from),
    EXCLUDE USING gist (int4range(listing_id, listing_id, '[]') WITH &&,
                        daterange(valid_from, valid_to) WITH &&)
);

CREATE FUNCTION ref.listing_classification(d date)
RETURNS TABLE (listing_id integer, cagr_industry text, sector_index text, tier text)
LANGUAGE sql STABLE AS $$
    SELECT l.listing_id, i.name, i.sector_index, t.tier
    FROM ref.listing l
    LEFT JOIN ref.listing_industry li ON li.listing_id = l.listing_id
    LEFT JOIN ref.industry i ON i.industry_id = li.industry_id
    LEFT JOIN ref.listing_tier t
        ON t.listing_id = l.listing_id AND t.valid_from <= d
       AND (t.valid_to IS NULL OR t.valid_to > d)
    WHERE l.first_session <= d AND l.status <> 'merged'
$$;

INSERT INTO ops.dq_check (check_id, layer, feed_id, severity, description, params, owner_action) VALUES
    ('industry_known', 'ref', NULL, 'critical',
     'Every listing''s industry is an active ref.industry row', '{}',
     'Block; reactivate the industry or move its listings'),
    ('universe_classified', 'ref', NULL, 'warn',
     'Every listing ever in the universe has an industry, or a pending item younger than 3 sessions',
     '{"floor_inr": 15000000000, "max_sessions": 3}',
     'Pick the industries on the review page');

-- New tables get full DML for cagr_writer by default; narrow it to what the loaders need.
REVOKE ALL ON ref.industry, ref.industry_change, ref.listing_industry, ref.listing_tier
    FROM cagr_writer;
GRANT SELECT ON ref.industry TO cagr_writer;
GRANT SELECT, INSERT ON ref.industry_change, ref.listing_industry TO cagr_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON ref.listing_tier TO cagr_writer;
GRANT SELECT ON ref.industry, ref.industry_change, ref.listing_industry, ref.listing_tier
    TO cagr_read;

-- The review page picks an industry for a pending item and applies it.
GRANT SELECT ON ref.industry, ref.industry_change, ref.listing_industry TO cagr_reviewer;
GRANT UPDATE (industry_id, status, decided_by, decided_at, note) ON ref.industry_change
    TO cagr_reviewer;
GRANT INSERT, UPDATE (industry_id, change_id) ON ref.listing_industry TO cagr_reviewer;
