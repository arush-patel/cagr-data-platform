-- 0010 buybacks are a corporate-action kind.
--
-- The blueprint's price-factor check already names 'buyback' (no price effect), but the kind list
-- left it out, so every "Buy Back" subject went to needs_review as 'other' and blocked the run
-- (27 of the 47 review items in the first real backfill, Jun–Aug 2024). A tender or open-market
-- buyback changes the share count, not the price basis: it is recorded and confirmed at factor 1.

ALTER TABLE clean.corporate_action DROP CONSTRAINT corporate_action_kind_check;
ALTER TABLE clean.corporate_action ADD CONSTRAINT corporate_action_kind_check
    CHECK (kind IN ('split','bonus','consolidation','rights','demerger','merger','dividend','buyback',
                    'redenomination','other'));
