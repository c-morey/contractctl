-- ─────────────────────────────────────────────────────────────────────────────
-- Platform setup 3/3: the tables the generated governance and SLA tasks write to.
-- Run once; the generated governance task fails if these tables are missing.
-- ─────────────────────────────────────────────────────────────────────────────
USE CATALOG {{catalog}};

CREATE TABLE IF NOT EXISTS governance.contract_registry (
  contract_id      STRING  COMMENT 'ODCS contract id',
  contract_version STRING  COMMENT 'Semantic version of the contract',
  name             STRING,
  status           STRING  COMMENT 'proposed | draft | active | deprecated | retired',
  domain           STRING,
  layer            STRING  COMMENT 'silver | gold',
  table_name       STRING  COMMENT 'Unity Catalog table (schema.table) implementing the contract',
  purpose          STRING,
  usage            STRING,
  limitations      STRING,
  owner_team       STRING,
  team_members     ARRAY<STRUCT<username: STRING, name: STRING, role: STRING>>,
  support          ARRAY<STRUCT<channel: STRING, tool: STRING, url: STRING>>,
  sla              ARRAY<STRUCT<property: STRING, value: STRING, unit: STRING, element: STRING>>,
  roles            ARRAY<STRUCT<role: STRING, access: STRING, description: STRING>>,
  inputs           ARRAY<STRING> COMMENT 'Contracts this data product reads from',
  git_commit       STRING  COMMENT 'Commit the contract was deployed from',
  contract_path    STRING  COMMENT 'Path of the contract in the repository',
  published_at     TIMESTAMP,
  is_current       BOOLEAN,
  contract_yaml    STRING  COMMENT 'The full contract as deployed'
) COMMENT 'Data contract registry: who owns a data product, who to call, what is promised';

CREATE TABLE IF NOT EXISTS governance.contract_rules (
  contract_id      STRING,
  contract_version STRING,
  table_name       STRING,
  rule_name        STRING  COMMENT 'Equals the expectation name in the pipeline event log',
  scope            STRING  COMMENT 'Column name or table',
  dimension        STRING,
  expression       STRING,
  on_violation     STRING  COMMENT 'warn | quarantine | drop | fail',
  description      STRING,
  implicit         BOOLEAN COMMENT 'Generated from type or required',
  is_current       BOOLEAN
) COMMENT 'Row-level rules enforced as pipeline expectations, per contract version';

CREATE TABLE IF NOT EXISTS governance.contract_sla_results (
  run_id           STRING  COMMENT 'Job run that performed the check',
  checked_at       TIMESTAMP,
  contract_id      STRING,
  contract_version STRING,
  table_name       STRING,
  check_name       STRING,
  check_type       STRING  COMMENT 'freshness | sql',
  expected         STRING,
  actual           STRING,
  passed           BOOLEAN,
  owner_team       STRING,
  support_channel  STRING,
  support_url      STRING
) COMMENT 'Outcome of every SLA and table-level contract check';

GRANT SELECT ON TABLE governance.contract_registry    TO `account users`;
GRANT SELECT ON TABLE governance.contract_rules       TO `account users`;
GRANT SELECT ON TABLE governance.contract_sla_results TO `account users`;
