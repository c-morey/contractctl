# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-10-16

First public release.

### Added
- Compiler from ODCS v3.1 contracts to Lakeflow Spark Declarative Pipelines SQL: bronze ingestion
  from a Unity Catalog volume, silver casting with expectations and a quarantine table, gold
  materialized views around hand-written business logic.
- Governance SQL per contract: contract registry, rules table, table and column tags,
  certification badge, grants from contract roles.
- SLA SQL per contract: freshness and table-level checks, results stored in Unity Catalog,
  job failure and owner notification on breach.
- Declarative Automation Bundle resources: one pipeline and one job per domain, scheduled from
  the contract's frequency SLA.
- `contractctl init`: project scaffold with platform setup SQL, CI workflow and an optional example.
- `contractctl lint`, `generate`, `check` and `diff` (breaking-change classification, semantic
  versioning guard and downstream impact).
- `contractctl import`: draft contracts from Unity Catalog table metadata (`status: draft`).
- `contractctl drift`: compare deployed tables with their contracts.
- Monitoring queries per domain in `generated/observability/<domain>.sql` (rule compliance, quarantine).

[Unreleased]: https://github.com/c-morey/contractctl/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/c-morey/contractctl/releases/tag/v0.1.0
