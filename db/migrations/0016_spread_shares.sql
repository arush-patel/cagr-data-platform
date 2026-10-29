-- depends: 0015_tracked_universe
-- Share counts between two reports a quarter or more apart (2013 to Feb 2024) spread the change
-- the corporate actions don't explain across the gap, as their own source (spec 0007, Q4).

ALTER TABLE ref.listing_shares DROP CONSTRAINT listing_shares_source_check;
ALTER TABLE ref.listing_shares ADD CONSTRAINT listing_shares_source_check
    CHECK (source IN ('nse_mcap', 'index_file', 'walk_back', 'spread', 'filing'));
