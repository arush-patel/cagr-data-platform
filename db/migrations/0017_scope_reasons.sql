-- depends: 0016_spread_shares
-- Why the scope rule set an item aside (spec 0008, section 3): outside the universe, a real
-- price move, a demerger too small to price, an unparsed dividend with no jump, or an illiquid
-- stock. Possible merges can be set aside too, when both listings are illiquid.

ALTER TABLE ref.price_adjustment ADD COLUMN scope_reason text
    CHECK (scope_reason IN ('outside_universe', 'real_move', 'small_demerger',
                            'unparsed_dividend', 'illiquid'));
UPDATE ref.price_adjustment SET scope_reason = 'outside_universe' WHERE status = 'out_of_scope';
ALTER TABLE ref.price_adjustment ADD CONSTRAINT price_adjustment_scope_reason_status_check
    CHECK ((status = 'out_of_scope') = (scope_reason IS NOT NULL));

ALTER TABLE ref.listing_change DROP CONSTRAINT listing_change_status_check;
ALTER TABLE ref.listing_change ADD CONSTRAINT listing_change_status_check
    CHECK (status IN ('applied', 'pending', 'rejected', 'out_of_scope'));
ALTER TABLE ref.listing_change ADD COLUMN scope_reason text CHECK (scope_reason IN ('illiquid'));
ALTER TABLE ref.listing_change ADD CONSTRAINT listing_change_scope_reason_status_check
    CHECK ((status = 'out_of_scope') = (scope_reason IS NOT NULL));
ALTER TABLE ref.listing_change ADD CONSTRAINT listing_change_out_of_scope_check
    CHECK (status <> 'out_of_scope' OR (decided_by IS NOT NULL));
