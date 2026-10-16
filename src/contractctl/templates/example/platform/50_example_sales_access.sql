-- ─────────────────────────────────────────────────────────────────────────────
-- Example data products (sales/orders, sales/daily_revenue): groups and row filter.
-- Run after the platform setup files.
--
-- The example contracts use these ACCOUNT groups:
--   orders_producers   producing team of orders (bronze and quarantine access)
--   sales_analysts     consumers
--   pii_readers        may see unmasked personal data (platform/01_access_policies.sql)
--   sales_all_regions  sees every country in gold.daily_revenue (add the job owner)
-- ─────────────────────────────────────────────────────────────────────────────
USE CATALOG {{catalog}};

-- Row access: which group may see which countries (an entitlement table, managed as data)
CREATE TABLE IF NOT EXISTS governance.country_access (
  group_name   STRING NOT NULL COMMENT 'Account group',
  country_code STRING NOT NULL COMMENT 'ISO 3166-1 alpha-2 country the group may see'
) COMMENT 'Country entitlements used by the filter_country row filter';

MERGE INTO governance.country_access AS t
USING (
  SELECT * FROM VALUES ('sales_analysts', 'BE'), ('sales_analysts', 'NL'), ('sales_analysts', 'LU')
    AS v(group_name, country_code)
) AS s
ON t.group_name = s.group_name AND t.country_code = s.country_code
WHEN NOT MATCHED THEN INSERT *;

CREATE OR REPLACE FUNCTION governance.filter_country(row_country STRING)
  RETURNS BOOLEAN
  COMMENT 'Row filter: sales_all_regions sees everything, others only their entitled countries'
  RETURN is_account_group_member('sales_all_regions')
      OR EXISTS (
           SELECT 1 FROM {{catalog}}.governance.country_access AS a
           WHERE a.country_code = row_country AND is_account_group_member(a.group_name)
         );
