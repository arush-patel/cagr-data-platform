-- depends: 0011_market_cap
-- Raw files are packed into one archive per feed and month once they are loaded and their
-- hashes check (cagr_data.raw.archive). The receipt then points into the archive, so the
-- writer may change storage_uri, and only that column.
GRANT UPDATE (storage_uri) ON raw.vendor_file TO cagr_writer;
