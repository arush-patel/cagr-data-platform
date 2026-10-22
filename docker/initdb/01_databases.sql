-- Runs once, when the volume is first created.
CREATE DATABASE cagr_test OWNER cagr;
\connect cagr_dev
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
