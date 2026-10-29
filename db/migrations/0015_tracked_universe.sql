-- depends: 0014_index_conflict
-- The tracked universe (spec 0008): main-board equities that trade in the EQ series, and the
-- indices on the hand-kept list. Review items outside it are set aside as out_of_scope, never
-- deleted, and come back to pending if the scope widens.

CREATE TABLE ref.tracked_listing (
    listing_id  integer PRIMARY KEY REFERENCES ref.listing,
    reason      text NOT NULL CHECK (reason IN ('eq_main', 'index')),
    built_at    timestamptz NOT NULL DEFAULT now()
);

-- The master list of what we track, with each listing's current symbol and ISIN.
CREATE VIEW ref.tracked_current AS
SELECT c.listing_id, c.symbol, c.isin, c.name, c.asset_class, c.board, c.status,
       c.first_session, c.last_session, t.reason
  FROM ref.tracked_listing t JOIN ref.listing_current c USING (listing_id);

ALTER TABLE ref.price_adjustment DROP CONSTRAINT price_adjustment_status_check;
ALTER TABLE ref.price_adjustment ADD CONSTRAINT price_adjustment_status_check
    CHECK (status IN ('applied', 'pending', 'rejected', 'out_of_scope'));
-- Set aside by the scope rule, so it carries who did it but no factor.
ALTER TABLE ref.price_adjustment ADD CONSTRAINT price_adjustment_out_of_scope_check
    CHECK (status <> 'out_of_scope' OR (decided_by IS NOT NULL AND applied_at IS NULL));

-- New tables get full DML for cagr_writer by default; the builder replaces the table whole.
REVOKE ALL ON ref.tracked_listing, ref.tracked_current FROM cagr_writer;
GRANT SELECT, INSERT, DELETE ON ref.tracked_listing TO cagr_writer;
GRANT SELECT ON ref.tracked_current TO cagr_writer;
GRANT SELECT ON ref.tracked_listing, ref.tracked_current TO cagr_read, cagr_reviewer;
