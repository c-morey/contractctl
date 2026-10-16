# Working in this repository (for people and coding agents)

Data contracts (ODCS v3 YAML) are compiled by `contractctl` into a governed Databricks pipeline.

## How a change works
1. The source of truth is `data_products/<domain>/<product>/*.odcs.yaml` (plus `*.logic.sql` for gold).
2. After changing a contract or logic file, run `contractctl generate`. Never edit files under
   `generated/` or `resources/generated/` by hand: they are compiled from the contracts.
3. Before opening a pull request, run `contractctl lint`, `contractctl check` and
   `contractctl diff --base origin/main`.
4. Bump the contract `version` (semantic versioning): MAJOR for breaking changes, MINOR for
   additions or stricter guarantees, PATCH for wording only. `contractctl diff` tells you which.

## Drafts and Unity Catalog
- `contractctl import <catalog.schema.table>` writes a contract with `status: draft` from Unity Catalog
  metadata. Drafts are not compiled. Never set a draft to `active` without the owner filling in the
  TODO list at the bottom of the file (support, SLAs, quality rules): those are promises, not metadata.
- `contractctl drift` compares deployed tables with their contracts. Fix drift by redeploying, or by
  changing the contract in a pull request; never by editing generated files.
