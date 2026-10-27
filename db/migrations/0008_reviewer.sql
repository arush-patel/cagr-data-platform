-- depends: 0007_ref_listings
-- The reviewer's login (spec 0005, section 5): what cagr_data.reference.review needs to approve or
-- reject a pending item and carry out an approved merge, and nothing more. The Observatory's
-- review page connects as cagr_reviewer. The role itself is made outside migrations, like the
-- other three (db-setup/create_cagr_reviewer.sql); this grants it its privileges.
-- Column-level UPDATE also covers SELECT ... FOR UPDATE, which the review functions use.

GRANT USAGE ON SCHEMA ref TO cagr_reviewer;

GRANT SELECT ON ref.listing_change, ref.price_adjustment, ref.listing, ref.listing_symbol,
    ref.listing_isin TO cagr_reviewer;

GRANT UPDATE (status, decided_by, decided_at, note) ON ref.listing_change TO cagr_reviewer;
GRANT UPDATE (status, decided_by, decided_at, note, factor, applied_at)
    ON ref.price_adjustment TO cagr_reviewer;

-- An approved merge moves the merged listing's periods to the survivor and marks it merged.
GRANT UPDATE (listing_id, valid_to) ON ref.listing_symbol, ref.listing_isin TO cagr_reviewer;
GRANT UPDATE (status, merged_into, first_session, last_session) ON ref.listing TO cagr_reviewer;
