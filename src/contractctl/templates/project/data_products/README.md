# Data products

One folder per data product: `<domain>/<product>/`.

- `<product>.odcs.yaml`: the data contract (ODCS v3). Silver contracts set `layer: silver` and a `source`.
- `<product>.logic.sql`: gold contracts only, a plain SELECT with the business logic.

After every change run `contractctl generate` and commit the contract together with the generated files.
Start from an existing table with `contractctl import <catalog.schema.table>`.
