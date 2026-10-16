# contractctl

**Data contracts that enforce themselves on Databricks.**

`contractctl` compiles data contracts written in the [Open Data Contract Standard (ODCS) v3](https://bitol-io.github.io/open-data-contract-standard/) into a governed medallion pipeline on Databricks: Lakeflow Spark Declarative Pipelines in SQL, with expectations, a quarantine table, column masks, row filters, tags, grants, a contract registry and SLA checks that notify the owner. Contracts live in Git, changes go through pull requests, and CI blocks breaking changes that don't bump the major version.

```
data_products/sales/orders/orders.odcs.yaml ──contractctl generate──▶ generated/ SQL + bundle resources ──bundle deploy──▶ Databricks
```

Python runs only at build time, on a laptop or in CI. Databricks only runs the generated SQL.

## Install

```bash
pip install contractctl                 # compiler and CI checks
pip install "contractctl[databricks]"   # + import and drift, which read Unity Catalog
```

Requires Python 3.10+. To deploy you also need the [Databricks CLI](https://docs.databricks.com/dev-tools/cli/) or a Databricks Git folder, and a workspace with Unity Catalog, serverless pipelines and a SQL warehouse.

## Quick start

```bash
contractctl init my-data-products --catalog analytics --example
cd my-data-products
```

This creates a project with two example data products, already compiled:

```
contractctl.yml                     how contracts map onto your platform (schemas, masks, notifications)
databricks.yml                      the bundle; includes resources/generated/*.yml
data_products/<domain>/<product>/   one folder per data product
    <product>.odcs.yaml               the contract
    <product>.logic.sql               gold only: the business logic, a plain SELECT
generated/                          compiled SQL, committed, never edited by hand
    observability/<domain>.sql        ready-to-run monitoring queries (rule compliance, quarantine)
resources/generated/<domain>.yml    one pipeline and one job per domain
platform/                           one-time setup SQL: catalog, schemas, landing volume, masks, registry
.github/workflows/data-contracts.yml   CI: ODCS lint, contractctl lint, check and diff
```

Then:

1. Run `platform/00`, `01` and `02` once in the Databricks SQL editor (and `50` for the example's row filter and groups).
2. Set the workspace host and SQL warehouse name in `databricks.yml`.
3. `databricks bundle deploy`, then run the `<domain>-data-contracts` job.

Leave out `--example` to start from an empty project.

## What a contract controls

| Contract component | In the ODCS file | What the platform does with it |
|---|---|---|
| Schema | `properties`: types, `required`, `primaryKey` | Bronze reads everything as STRING; silver casts with `try_cast`; `NOT NULL`, primary key and column comments in the table DDL |
| Type conformance | `physicalType` | Implicit rule per typed column: a value that cannot be cast is quarantined with its raw payload |
| Schema drift | table rule on `_rescued_data` | Unknown fields are kept by Auto Loader and the record is quarantined, not lost |
| Row-level quality | `quality` rules (`lakeflow-expectations`) | One expectation per rule. Silver: warn / quarantine / fail. Gold: warn / drop / fail |
| Table-level quality | `quality` rules (`type: sql`, `mustBe*`) | Checked after every run; results stored in Unity Catalog; the job fails on breach |
| Quarantine | action `quarantine` | `silver.<product>_quarantine` with `_failed_rules`, `_raw`, `_source_file`, `_contract_version` |
| Sensitive data | property `tags: ["pii:email"]`, `classification`, `mask` | Column mask in the table DDL, column tags `pii` and `classification` (ABAC-ready) |
| Row-level access | `rowFilter` | `WITH ROW FILTER` on the product table |
| Access | `roles` (read / write) | Grants: read roles get the product, write roles also get bronze and quarantine |
| Ownership and support | `team`, `support` | Contract registry, tags, and the e-mail address that receives breach notifications |
| Freshness SLA | `slaProperties.freshness` | Checked after every run; a breach fails the job and notifies the owner |
| Frequency SLA | `slaProperties.frequency` | Becomes the job schedule |
| Retention | `slaProperties.retention` | Recorded in the registry and tags (not enforced) |
| Status | `status` | Certified or deprecated badge in Catalog Explorer; `draft` is never compiled |
| Dependencies | `inputs` | Business logic may only read declared inputs; breaking changes list affected consumers |
| Version | `version` (semver) | Stamped on every table, every quarantined row and in the registry |

### Conventions on top of ODCS

Everything below is valid ODCS, checked by `datacontract lint`. Platform-specific settings use ODCS `customProperties`.

| Where | Custom property | Meaning |
|---|---|---|
| Contract | `layer` | `silver` (source-aligned, from files) or `gold` (business logic on other products) |
| Contract | `source` | Silver: `{path, format}` of the source folder in the landing volume |
| Contract | `businessLogic` | Gold: file with the SELECT, relative to the contract |
| Contract | `inputs` | Gold: ids of the contracts it reads from |
| Contract | `rowFilter` | `{function, columns}`: Unity Catalog row filter on the product table |
| Property | `generatedBy` | `ingestion_timestamp` or `source_file`: added by the platform, not by the source |
| Property | `mask` | Column mask for this column, instead of `default_pii_mask` |

Row-level rules use `type: custom`, `engine: lakeflow-expectations` and `implementation: {expression, onViolation}`. A rule that evaluates to NULL counts as a violation.

## Commands

| Command | What it does |
|---|---|
| `contractctl init [DIR] --catalog NAME [--example]` | Create a project from the bundled templates |
| `contractctl lint` | Validate every contract and the dependencies between them |
| `contractctl generate` | Write `generated/` and `resources/generated/` from the contracts |
| `contractctl check` | Fail if the generated files don't match the contracts (for CI) |
| `contractctl diff --base origin/main` | Classify changes as breaking or additive, enforce the version bump, list affected consumers |
| `contractctl import CATALOG.SCHEMA.TABLE` | Draft a contract from an existing Unity Catalog table |
| `contractctl drift [--catalog NAME]` | Compare deployed tables with their contracts |

`diff --base-dir DIR` compares against a checkout on disk instead of a git ref, for example in a Databricks Git folder.

## Versioning rules

`contractctl diff` enforces semantic versioning from the consumer's point of view:

| Change | Classified as | Required bump |
|---|---|---|
| Remove or rename a column, change a type, drop `required`, change the primary key | breaking | MAJOR |
| Remove, loosen or rewrite a quality rule; weaken its action; loosen a threshold | breaking | MAJOR |
| Loosen the freshness SLA; remove a role; remove a `pii` tag; deprecate | breaking | MAJOR |
| Add a column or rule, strengthen an action, tighten an SLA or threshold, add a role | additive | MINOR |
| Descriptions and other clarifications | patch | PATCH |
| Delete a contract file | blocked | Retire it first (`status: retired`) |

Breaking schema changes also need a full refresh of the affected tables after deployment.

## Start from an existing table

```bash
contractctl import analytics.crm.customers --domain sales
contractctl lint
```

`import` writes a draft contract from what Unity Catalog knows: columns, types, NOT NULL, comments, primary key, `pii` and `classification` tags, column masks, row filter, owner and grants, and for a view its SQL and inputs. It uses the same authentication as the Databricks CLI, and reads tags through a SQL warehouse (`--warehouse-id`, `DATABRICKS_WAREHOUSE_ID`, or picked automatically).

The draft gets `status: draft` and version `0.1.0`. Lint checks only its structure and lists what is missing, and `generate` skips it, so a draft can be merged without breaking anything. Support channel, SLAs, quality rules and usage are promises only the owner can make: they are listed as TODOs in the file. Set `status: active` and `1.0.0` once they are filled in.

## Detect drift

```bash
contractctl drift            # every active contract; catalog from databricks.yml
```

`drift` reads each deployed product table and reports differences with its contract: missing or extra columns, changed types, a nullable required column, a different primary key, a removed `pii` tag, a dropped mask or row filter, principals that can read the table without a contract role, and a deployed contract version that lags behind Git. It exits non-zero when it finds any, so it can run in CI or on a schedule.

## Configuration

`contractctl.yml`, set once per platform:

| Key | Default in `init` | Meaning |
|---|---|---|
| `data_products_dir` | `data_products` | Where the contracts live |
| `generated_dir`, `resources_dir` | `generated`, `resources/generated` | Where compiled files are written |
| `schemas` | `bronze`, `silver`, `gold`, `governance` | Unity Catalog schema per layer |
| `landing_volume` | `landing` | Volume in the bronze schema that source files arrive in |
| `default_pii_mask` | `governance.mask_pii` | Mask for `pii` columns without an explicit `mask` |
| `pipeline.continuous`, `pipeline.serverless` | `false`, `true` | Pipeline mode and compute |
| `job.schedule_from_sla` | `true` | Schedule the job from the tightest `frequency` SLA; `false` for no schedule |
| `features.certification_badge` | `true` | Set `system.certification_status` (needs ASSIGN on that governed tag) |
| `notifications.platform_email` | `${var.platform_alert_email}` | Receives pipeline failures and a copy of every breach |
| `notifications.teams_destination_id` | `""` | Optional Microsoft Teams notification destination |

The catalog is not in this file: it comes from the bundle target (`variables.catalog` in `databricks.yml`), so the same contracts deploy to dev and prod catalogs.

## Continuous integration

`init` adds `.github/workflows/data-contracts.yml`. On every pull request it runs `datacontract lint` (ODCS schema), `contractctl lint`, `contractctl check` and `contractctl diff`. With `DATABRICKS_HOST`, `DATABRICKS_CLIENT_ID` and `DATABRICKS_CLIENT_SECRET` secrets it also validates the bundle, and with the repository variable `ENABLE_PROD_DEPLOY=true` it deploys to production on merge. Make the `contracts` check required on `main`, and use `CODEOWNERS` so each contract's owner approves changes to it.

## Scope and limitations

- Targets Databricks with Unity Catalog. The contracts are portable ODCS; the generated SQL is not.
- Silver products ingest files from a Unity Catalog volume with Auto Loader. Other sources (Kafka, CDC feeds, existing tables) are not generated yet.
- Not enforced: retention, revoking grants that are not in the contract (`drift` reports them), and consumer subscription workflows.
- Some features need account-level setup: account groups for the roles, and ASSIGN on `system.certification_status` for the certified badge.
- Built with the help of Claude Code. Design decisions, review and testing by the maintainer.

## Development

```bash
git clone https://github.com/c-morey/contractctl && cd contractctl
pip install -e ".[dev]"
pytest
ruff check .
```

See [CONTRIBUTING.md](CONTRIBUTING.md). Changes to the generated SQL are reviewed through the golden files in `tests/golden/`.

## License

[Apache License 2.0](LICENSE)
