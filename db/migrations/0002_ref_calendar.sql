-- depends: 0001_ops
-- Venue and trading calendar (spec 0001, section 6.2).

CREATE SCHEMA ref;

CREATE TABLE ref.venue (
    venue_id       text PRIMARY KEY CHECK (venue_id ~ '^[A-Z]{4}$'),  -- ISO 10383 MIC
    name           text NOT NULL,
    time_zone      text NOT NULL,
    weekend_days   smallint[] NOT NULL CHECK (weekend_days <@ ARRAY[1, 2, 3, 4, 5, 6, 7]::smallint[]),
    regular_open   time NOT NULL,
    regular_close  time NOT NULL CHECK (regular_close > regular_open)
);
COMMENT ON COLUMN ref.venue.weekend_days IS 'ISO weekday numbers, 1 = Monday ... 7 = Sunday';

-- The facts the calendar is built from. A date can hold both kinds: Muhurat trading is a
-- special session on a listed holiday (Diwali).
CREATE TABLE ref.calendar_exception (
    venue_id        text NOT NULL REFERENCES ref.venue,
    date            date NOT NULL,
    kind            text NOT NULL CHECK (kind IN ('holiday', 'special_session')),
    description     text,
    source          text NOT NULL CHECK (source IN ('holiday_api', 'archive_proof')),
    source_file_id  bigint,  -- references raw.vendor_file; the foreign key is added in 0003
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (venue_id, date, kind)
);

-- Derived from ref.venue and ref.calendar_exception; rebuilt by code, never edited by hand.
CREATE TABLE ref.calendar_session (
    venue_id       text NOT NULL REFERENCES ref.venue,
    date           date NOT NULL,
    is_session     boolean NOT NULL,
    session_index  integer CHECK (session_index >= 1),
    reason         text NOT NULL
                   CHECK (reason IN ('regular', 'weekend', 'holiday', 'special_session')),
    is_week_end    boolean NOT NULL DEFAULT false,
    is_month_end   boolean NOT NULL DEFAULT false,
    PRIMARY KEY (venue_id, date),
    UNIQUE (venue_id, session_index),
    CHECK (is_session = (session_index IS NOT NULL)),
    CHECK (is_session = (reason IN ('regular', 'special_session'))),
    CHECK (is_session OR NOT (is_week_end OR is_month_end))
);
COMMENT ON COLUMN ref.calendar_session.session_index IS
    'Dense count of sessions for the venue, so a window of N sessions is an index range';

GRANT USAGE ON SCHEMA ref TO cagr_writer, cagr_read;
GRANT SELECT ON ALL TABLES IN SCHEMA ref TO cagr_read;
REVOKE ALL ON ALL TABLES IN SCHEMA ref FROM cagr_writer;
GRANT SELECT ON ref.venue TO cagr_writer;
GRANT SELECT, INSERT ON ref.calendar_exception TO cagr_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON ref.calendar_session TO cagr_writer;
