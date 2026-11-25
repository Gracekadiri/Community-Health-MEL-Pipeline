-- 01_init_db.sql
-- Creates the medallion layers for the Community Health MEL Pipeline.

CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;

-- Roles: one per job, each with only the access it needs.
CREATE ROLE etl_writer LOGIN;
CREATE ROLE dbt_transformer LOGIN;
CREATE ROLE mel_analyst LOGIN;