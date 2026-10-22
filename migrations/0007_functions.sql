-- 0007 reference and corporate-action functions
-- Source: Cagr Schema Blueprint v3 (platform additions of 16 Oct 2026), adapted for a from-scratch database.

-- ---------------------------------------------------------------------------------------------
-- Reference data: apply one queued change. Everything is keyed by listing_id, so no price,
-- indicator or feature row moves when a ticker, ISIN or vendor symbol changes.
-- ---------------------------------------------------------------------------------------------
CREATE FUNCTION ref.apply_listing_change(p_change_id bigint) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    c        ref.listing_change;
    p        jsonb;
    v_inst   integer;
    v_lst    integer;
    v_venue  smallint;
    v_scheme smallint;
    v_ind    smallint;
    v_old    smallint;
    v_from   date;
    v_mem    text[];
    k        text;
    val      text;
BEGIN
    SELECT * INTO c FROM ref.listing_change WHERE change_id = p_change_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'unknown change %', p_change_id; END IF;
    IF c.status = 'applied' THEN RAISE EXCEPTION 'change % already applied', p_change_id; END IF;
    IF c.status <> 'approved' AND NOT c.auto_apply THEN
        RAISE EXCEPTION 'change % is % and not auto-apply', p_change_id, c.status;
    END IF;
    p := c.payload;

    IF c.kind = 'new_listing' THEN
        -- payload: {venue, asset_class, name, ticker, quote_currency?, isin?, listed_on?,
        --           industry?, scheme?, vendor_symbols: {vendor_code: symbol}}
        SELECT venue_id INTO STRICT v_venue FROM ref.venue WHERE code = p ->> 'venue';
        -- reuse the instrument when the ISIN is already known (second listing of the same company)
        SELECT instrument_id INTO v_inst FROM ref.instrument_identifier
         WHERE id_type = 'isin' AND value = p ->> 'isin' AND valid_to IS NULL;
        IF v_inst IS NULL THEN
            INSERT INTO ref.instrument (asset_class, name, base_asset)
            VALUES (p ->> 'asset_class', p ->> 'name', p ->> 'base_asset')
            RETURNING instrument_id INTO v_inst;
            IF p ? 'isin' THEN
                INSERT INTO ref.instrument_identifier VALUES (v_inst, 'isin', p ->> 'isin', c.effective_date, NULL);
            END IF;
        END IF;
        INSERT INTO ref.listing (instrument_id, venue_id, quote_currency, listed_on)
        VALUES (v_inst, v_venue, coalesce(p ->> 'quote_currency', (SELECT currency FROM ref.venue WHERE venue_id = v_venue)),
                coalesce((p ->> 'listed_on')::date, c.effective_date))
        RETURNING listing_id INTO v_lst;
        -- The ticker is known from the day the change was observed; listed_on keeps the real listing
        -- date. Starting the ticker at listed_on would claim today's ticker for years it may not have held.
        INSERT INTO ref.listing_symbol (listing_id, venue_id, valid_from, ticker, display_name)
        VALUES (v_lst, v_venue, c.effective_date, p ->> 'ticker', p ->> 'name');
        FOR k, val IN SELECT * FROM jsonb_each_text(coalesce(p -> 'vendor_symbols', '{}'::jsonb)) LOOP
            INSERT INTO ref.vendor_symbol (listing_id, vendor_id, valid_from, symbol)
            SELECT v_lst, vendor_id, c.effective_date, val FROM ref.vendor WHERE code = k;
            IF NOT FOUND THEN RAISE EXCEPTION 'unknown vendor %', k; END IF;
        END LOOP;
        IF p ? 'industry' THEN
            SELECT s.scheme_id, i.industry_id INTO STRICT v_scheme, v_ind
            FROM ref.industry i JOIN ref.classification_scheme s USING (scheme_id)
            WHERE s.code = coalesce(p ->> 'scheme', 'cagr_industry_v1') AND i.name = p ->> 'industry';
            INSERT INTO ref.instrument_classification (instrument_id, scheme_id, valid_from, industry_id, change_id)
            VALUES (v_inst, v_scheme, coalesce((p ->> 'listed_on')::date, c.effective_date), v_ind, p_change_id)
            ON CONFLICT DO NOTHING;
        END IF;
        -- history gets backfilled by the ingest flow; every family recomputes from listing date
        INSERT INTO clean.dirty_range (listing_id, venue_id, from_date, reason, source_ref)
        VALUES (v_lst, v_venue, coalesce((p ->> 'listed_on')::date, c.effective_date), 'new_listing',
                'listing_change:' || p_change_id);
        UPDATE ref.listing_change SET listing_id = v_lst WHERE change_id = p_change_id;

    ELSIF c.kind = 'ticker_change' THEN
        -- payload: {ticker, vendor_symbols?: {vendor_code: symbol}}
        SELECT venue_id INTO STRICT v_venue FROM ref.listing WHERE listing_id = c.listing_id;
        UPDATE ref.listing_symbol SET valid_to = c.effective_date
         WHERE listing_id = c.listing_id AND valid_to IS NULL;
        INSERT INTO ref.listing_symbol (listing_id, venue_id, valid_from, ticker, display_name)
        VALUES (c.listing_id, v_venue, c.effective_date, p ->> 'ticker', p ->> 'display_name');
        FOR k, val IN SELECT * FROM jsonb_each_text(coalesce(p -> 'vendor_symbols', '{}'::jsonb)) LOOP
            UPDATE ref.vendor_symbol vs SET valid_to = c.effective_date
              FROM ref.vendor v WHERE v.vendor_id = vs.vendor_id AND v.code = k
               AND vs.listing_id = c.listing_id AND vs.valid_to IS NULL;
            INSERT INTO ref.vendor_symbol (listing_id, vendor_id, valid_from, symbol)
            SELECT c.listing_id, vendor_id, c.effective_date, val FROM ref.vendor WHERE code = k;
        END LOOP;

    ELSIF c.kind = 'vendor_symbol_change' THEN
        -- payload: {vendor, symbol | null, status?}
        UPDATE ref.vendor_symbol vs SET valid_to = c.effective_date
          FROM ref.vendor v WHERE v.vendor_id = vs.vendor_id AND v.code = p ->> 'vendor'
           AND vs.listing_id = c.listing_id AND vs.valid_to IS NULL;
        INSERT INTO ref.vendor_symbol (listing_id, vendor_id, valid_from, symbol, status)
        SELECT c.listing_id, vendor_id, c.effective_date, p ->> 'symbol',
               coalesce(p ->> 'status', CASE WHEN p ->> 'symbol' IS NULL THEN 'unmapped' ELSE 'mapped' END)
        FROM ref.vendor WHERE code = p ->> 'vendor';
        -- a corrected mapping means the old prices were another company's: recompute from the fix
        IF (p ->> 'history_wrong_from') IS NOT NULL THEN
            INSERT INTO clean.dirty_range (listing_id, venue_id, from_date, anchor_date, reason, source_ref)
            SELECT c.listing_id, venue_id, (p ->> 'history_wrong_from')::date, (p ->> 'history_wrong_from')::date,
                   'mapping_fix', 'listing_change:' || p_change_id
            FROM ref.listing WHERE listing_id = c.listing_id;
        END IF;

    ELSIF c.kind = 'identifier_change' THEN
        -- payload: {id_type, value}   (e.g. new ISIN after a face-value split)
        SELECT instrument_id INTO STRICT v_inst FROM ref.listing WHERE listing_id = c.listing_id;
        UPDATE ref.instrument_identifier SET valid_to = c.effective_date
         WHERE instrument_id = v_inst AND id_type = p ->> 'id_type' AND valid_to IS NULL;
        INSERT INTO ref.instrument_identifier VALUES (v_inst, p ->> 'id_type', p ->> 'value', c.effective_date, NULL);

    ELSIF c.kind = 'classification_change' THEN
        -- payload: {industry, scheme?, mode?}. mode 'restate' (the default, D15) makes the new industry
        -- hold for the stock's whole history; 'from_date' is the exception for a real change of business.
        -- Bars are unchanged either way: only industry cross-sections are recomputed.
        SELECT l.instrument_id, l.venue_id INTO STRICT v_inst, v_venue FROM ref.listing l WHERE l.listing_id = c.listing_id;
        SELECT s.scheme_id, i.industry_id INTO STRICT v_scheme, v_ind
        FROM ref.industry i JOIN ref.classification_scheme s USING (scheme_id)
        WHERE s.code = coalesce(p ->> 'scheme', 'cagr_industry_v1') AND i.name = p ->> 'industry';
        SELECT index_membership INTO v_mem FROM ref.instrument_classification
         WHERE instrument_id = v_inst AND scheme_id = v_scheme AND superseded_at IS NULL
         ORDER BY valid_from DESC LIMIT 1;

        IF coalesce(p ->> 'mode', 'restate') = 'restate' THEN
            SELECT least(min(ic.valid_from), (SELECT listed_on FROM ref.listing WHERE listing_id = c.listing_id))
              INTO v_from
              FROM ref.instrument_classification ic
             WHERE ic.instrument_id = v_inst AND ic.scheme_id = v_scheme AND ic.superseded_at IS NULL;
            v_from := coalesce(v_from, c.effective_date);
            -- every industry it was ever in, and the new one, recompute over the whole history
            INSERT INTO clean.dirty_range (listing_id, venue_id, from_date, anchor_date, industry_id, reason, source_ref)
            SELECT c.listing_id, v_venue, v_from, v_from, x.ind, 'classification_change', 'listing_change:' || p_change_id
            FROM (SELECT industry_id AS ind FROM ref.instrument_classification
                   WHERE instrument_id = v_inst AND scheme_id = v_scheme AND superseded_at IS NULL
                  UNION SELECT v_ind) x;
            UPDATE ref.instrument_classification SET superseded_at = now()
             WHERE instrument_id = v_inst AND scheme_id = v_scheme AND superseded_at IS NULL;
            INSERT INTO ref.instrument_classification (instrument_id, scheme_id, valid_from, industry_id, index_membership, change_id)
            VALUES (v_inst, v_scheme, v_from, v_ind, coalesce(v_mem, '{}'), p_change_id);

        ELSIF p ->> 'mode' = 'from_date' THEN
            SELECT industry_id INTO v_old FROM ref.instrument_classification
             WHERE instrument_id = v_inst AND scheme_id = v_scheme AND superseded_at IS NULL
               AND valid_from < c.effective_date AND (valid_to IS NULL OR valid_to > c.effective_date);
            UPDATE ref.instrument_classification SET valid_to = c.effective_date
             WHERE instrument_id = v_inst AND scheme_id = v_scheme AND superseded_at IS NULL
               AND valid_from < c.effective_date AND (valid_to IS NULL OR valid_to > c.effective_date);
            INSERT INTO ref.instrument_classification (instrument_id, scheme_id, valid_from, industry_id, index_membership, change_id)
            VALUES (v_inst, v_scheme, c.effective_date, v_ind, coalesce(v_mem, '{}'), p_change_id);
            -- the old and the new industry's cross-sections change from the effective date
            INSERT INTO clean.dirty_range (listing_id, venue_id, from_date, anchor_date, industry_id, reason, source_ref)
            SELECT c.listing_id, v_venue, c.effective_date, c.effective_date, x.ind, 'classification_change',
                   'listing_change:' || p_change_id
            FROM (VALUES (v_old), (v_ind)) x(ind) WHERE x.ind IS NOT NULL;
        ELSE
            RAISE EXCEPTION 'classification_change % has unknown mode %', p_change_id, p ->> 'mode';
        END IF;

    ELSIF c.kind = 'delisting' THEN
        -- payload: {reason}
        UPDATE ref.listing SET status = 'delisted', delisted_on = c.effective_date, delisting_reason = p ->> 'reason'
         WHERE listing_id = c.listing_id;
        UPDATE ref.listing_symbol SET valid_to = c.effective_date WHERE listing_id = c.listing_id AND valid_to IS NULL;
        UPDATE ref.vendor_symbol SET valid_to = c.effective_date WHERE listing_id = c.listing_id AND valid_to IS NULL;
    END IF;

    UPDATE ref.listing_change SET status = 'applied', applied_at = now() WHERE change_id = p_change_id;
    RETURN coalesce(v_lst, c.listing_id);
END $$;

-- ---------------------------------------------------------------------------------------------
-- Corporate actions: one confirmed action → rescale history once, record what must recompute.
-- ---------------------------------------------------------------------------------------------
CREATE FUNCTION clean.apply_corporate_action(p_action_id bigint) RETURNS bigint
LANGUAGE plpgsql AS $$
DECLARE
    a        clean.corporate_action;
    v_venue  smallint;
    v_first  date;
    v_shares double precision;
    n        bigint := 0;
BEGIN
    SELECT * INTO a FROM clean.corporate_action WHERE action_id = p_action_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'unknown action %', p_action_id; END IF;
    IF a.status <> 'confirmed' THEN RAISE EXCEPTION 'action % is %, expected confirmed', p_action_id, a.status; END IF;
    SELECT venue_id INTO STRICT v_venue FROM ref.listing WHERE listing_id = a.listing_id;

    IF a.price_factor <> 1 THEN
        UPDATE clean.bar_1d
           SET open = open * a.price_factor, high = high * a.price_factor,
               low = low * a.price_factor, close = close * a.price_factor,
               adj_factor = adj_factor * a.price_factor,
               volume_adj = volume / (adj_factor * a.price_factor),
               qa_flags = qa_flags, updated_at = now()
         WHERE listing_id = a.listing_id AND bar_date < a.ex_date;
        GET DIAGNOSTICS n = ROW_COUNT;
        UPDATE clean.bar_1d SET qa_flags = qa_flags | (1 << 4)        -- corporate_action_day
         WHERE listing_id = a.listing_id AND bar_date = a.ex_date;
        SELECT min(bar_date) INTO v_first FROM clean.bar_1d WHERE listing_id = a.listing_id;
        -- the listing's own levels changed for its whole history; ranks of % changes only
        -- change around the ex-date (anchor_date), which fb.dirty_plan uses for invariant families
        INSERT INTO clean.dirty_range (listing_id, venue_id, from_date, anchor_date, reason, source_ref)
        VALUES (a.listing_id, v_venue, coalesce(v_first, a.ex_date), a.ex_date, 'corporate_action',
                'corporate_action:' || p_action_id);
    END IF;

    -- splits, bonuses and consolidations change the share count on the ex-date
    IF a.kind IN ('split','bonus','consolidation') THEN
        SELECT shares INTO v_shares FROM ref.shares_outstanding
         WHERE listing_id = a.listing_id AND valid_from < a.ex_date AND (valid_to IS NULL OR valid_to > a.ex_date);
        IF v_shares IS NOT NULL THEN
            UPDATE ref.shares_outstanding SET valid_to = a.ex_date
             WHERE listing_id = a.listing_id AND valid_from < a.ex_date AND (valid_to IS NULL OR valid_to > a.ex_date);
            INSERT INTO ref.shares_outstanding (listing_id, valid_from, shares, source, source_ref)
            VALUES (a.listing_id, a.ex_date, v_shares / a.price_factor, 'corporate_action', 'corporate_action:' || p_action_id);
        END IF;
    END IF;

    IF a.kind = 'merger' THEN
        -- the target stops trading; its history stays (no survivorship bias)
        UPDATE ref.listing SET status = 'delisted', delisted_on = a.ex_date, delisting_reason = 'merger'
         WHERE listing_id = a.listing_id AND status <> 'delisted';
        UPDATE ref.listing_symbol SET valid_to = a.ex_date WHERE listing_id = a.listing_id AND valid_to IS NULL;
        UPDATE ref.vendor_symbol SET valid_to = a.ex_date WHERE listing_id = a.listing_id AND valid_to IS NULL;
    END IF;

    UPDATE clean.corporate_action SET status = 'applied', applied_at = now() WHERE action_id = p_action_id;
    RETURN n;
END $$;
