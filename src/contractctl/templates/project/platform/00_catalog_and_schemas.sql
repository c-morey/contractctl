-- ─────────────────────────────────────────────────────────────────────────────
-- Platform setup 1/3: catalog, medallion schemas, landing volume, browse rights.
-- Run once, in order, in the SQL editor as a user who can create catalogs.
-- Schema and volume names must match contractctl.yml.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE CATALOG IF NOT EXISTS {{catalog}}
  COMMENT 'Governed medallion data products, compiled from data contracts';
-- No default storage on your metastore? Add:
--   MANAGED LOCATION 'abfss://<container>@<account>.dfs.core.windows.net/{{catalog}}'

USE CATALOG {{catalog}};

CREATE SCHEMA IF NOT EXISTS bronze     COMMENT 'Raw data exactly as delivered by source systems';
CREATE SCHEMA IF NOT EXISTS silver     COMMENT 'Typed, validated data products and their quarantine tables';
CREATE SCHEMA IF NOT EXISTS gold       COMMENT 'Business-level data products';
CREATE SCHEMA IF NOT EXISTS governance COMMENT 'Contract registry, SLA results, pipeline event logs, policy functions';

-- Landing zone the source systems deliver files to, one folder per source:
--   /Volumes/{{catalog}}/bronze/landing/<source path>/
-- Use an external volume to point at existing cloud storage instead.
CREATE VOLUME IF NOT EXISTS bronze.landing
  COMMENT 'Files delivered by source systems, one folder per source';

-- Everyone may see what exists and read the contract registry.
-- Reading data always requires a SELECT grant, which comes from the contract roles.
GRANT USE CATALOG ON CATALOG {{catalog}} TO `account users`;
GRANT USE SCHEMA ON SCHEMA bronze     TO `account users`;
GRANT USE SCHEMA ON SCHEMA silver     TO `account users`;
GRANT USE SCHEMA ON SCHEMA gold       TO `account users`;
GRANT USE SCHEMA ON SCHEMA governance TO `account users`;
