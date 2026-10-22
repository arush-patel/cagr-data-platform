-- 0003 raw: vendor data exactly as received
-- Source: Cagr Schema Blueprint v3 (platform additions of 16 Oct 2026), adapted for a from-scratch database.

-- ---------------------------------------------------------------------------------------------
-- raw: vendor data exactly as received. Append-only. A new revision is written only when a
-- vendor sends a different value for a session it already sent, so revision > 1 on a past
-- session IS the history-drift signal.
-- ---------------------------------------------------------------------------------------------

CREATE TABLE raw.ingest_run (
    ingest_run_id     uuid PRIMARY KEY REFERENCES public.run_log (run_id),
    feed_id           smallint NOT NULL REFERENCES ref.feed (feed_id),
    kind              text NOT NULL CHECK (kind IN ('incremental','full_refresh','backfill')),
    requested_from    date NOT NULL,
    requested_to      date NOT NULL,
    symbols_requested integer NOT NULL,
    symbols_returned  integer NOT NULL,
    started_at        timestamptz NOT NULL,
    finished_at       timestamptz
);

CREATE TABLE raw.vendor_bar (
    session_date    date NOT NULL,
    listing_id      integer NOT NULL,
    vendor_id       smallint NOT NULL REFERENCES ref.vendor (vendor_id),
    revision        smallint NOT NULL DEFAULT 1 CHECK (revision >= 1),
    open            double precision,
    high            double precision,
    low             double precision,
    close           double precision,
    adj_close       double precision,              -- vendor's dividend-adjusted close, when offered
    volume          double precision,              -- in base units; fractional on crypto venues
    quote_volume    double precision,              -- in quote currency, when offered
    prev_close      double precision,              -- vendor's previous close (bhavcopy PrvsClsgPric); proves NSE adjusted
    trade_count     bigint,
    series          text,                          -- exchange series the row came from (EQ, BE, BZ, SM, ST …)
    ingest_run_id   uuid NOT NULL,
    ingested_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (session_date, listing_id, vendor_id, revision)
) PARTITION BY RANGE (session_date);
CREATE INDEX vendor_bar_listing_idx ON raw.vendor_bar (listing_id, session_date);
SELECT ops.create_year_partitions('raw.vendor_bar', 2010, 2030);

-- Every file a feed downloads, kept so a rerun never downloads again. The bytes live under
-- RAW_FILE_ROOT/<feed>/<path>; this row is the receipt.
CREATE TABLE raw.vendor_file (
    file_id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    feed_id         smallint NOT NULL REFERENCES ref.feed (feed_id),
    session_date    date,                          -- NULL for undated files (listing master)
    url             text NOT NULL,
    local_path      text NOT NULL,
    sha256          text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    bytes           bigint NOT NULL CHECK (bytes >= 0),
    http_status     smallint NOT NULL,
    fetched_at      timestamptz NOT NULL DEFAULT now(),
    ingest_run_id   uuid REFERENCES raw.ingest_run (ingest_run_id),
    UNIQUE (feed_id, url, sha256)
);
CREATE INDEX vendor_file_session_idx ON raw.vendor_file (feed_id, session_date);

-- Absence is a fact too: a 404 for a weekday is a hint of a holiday (never proof, D7).
CREATE TABLE raw.vendor_file_missing (
    feed_id         smallint NOT NULL REFERENCES ref.feed (feed_id),
    session_date    date NOT NULL,
    url             text NOT NULL,
    http_status     smallint NOT NULL,
    checked_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (feed_id, session_date, url)
);
