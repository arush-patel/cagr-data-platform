-- depends: 0013_industry
-- An index listed twice with different values in one index-close file (CNX Alpha Index on
-- 8 Feb 2013): neither row is loaded, the rest of the file is, and this warning names the index.

INSERT INTO ops.dq_check (check_id, layer, feed_id, severity, description, params, owner_action) VALUES
    ('index_conflict', 'raw', 'nse_index_close', 'warn',
     'No index is listed twice with different values; such an index is not loaded for the day',
     '{}',
     'Compare the rows with the days around; the index has no close for that day');
