"""
Read a table's metadata from Unity Catalog (used by `contractctl import` and `contractctl drift`).

Needs the Databricks SDK (`pip install "contractctl[databricks]"`) and the same authentication as the
Databricks CLI: a profile in ~/.databrickscfg (DATABRICKS_CONFIG_PROFILE) or DATABRICKS_HOST
plus a token. Inside a Databricks notebook it authenticates automatically.

Tags are read from information_schema through a SQL warehouse: pass --warehouse-id, set
DATABRICKS_WAREHOUSE_ID, or let it pick the "Serverless Starter Warehouse" (or the first one).
Without a warehouse, tags are skipped with a warning.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field


@dataclass
class Column:
    name: str
    type_text: str
    nullable: bool = True
    comment: str = ""
    mask: str | None = None
    tags: dict[str, str] = field(default_factory=dict)


@dataclass
class TableMetadata:
    """Everything contractctl needs to know about one Unity Catalog table."""
    catalog: str
    schema: str
    name: str
    exists: bool = True
    table_type: str = ""
    comment: str = ""
    owner: str = ""
    columns: list[Column] = field(default_factory=list)
    primary_key: list[str] = field(default_factory=list)
    tags: dict[str, str] = field(default_factory=dict)
    grants: dict[str, list[str]] = field(default_factory=dict)   # principal -> privileges
    row_filter: dict | None = None                               # {function, columns}
    view_definition: str = ""
    tags_read: bool = True

    @property
    def full_name(self) -> str:
        return f"{self.catalog}.{self.schema}.{self.name}"

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)

    @classmethod
    def from_dict(cls, d: dict) -> TableMetadata:
        """Build from plain data (tests, or metadata exported elsewhere)."""
        d = dict(d)
        d["columns"] = [c if isinstance(c, Column) else Column(**c) for c in d.get("columns", [])]
        return cls(**d)


def split_name(full_name: str) -> tuple[str, str, str]:
    parts = full_name.replace("`", "").split(".")
    if len(parts) != 3:
        sys.exit(f"'{full_name}' is not a three-level name (catalog.schema.table)")
    return parts[0], parts[1], parts[2]


# ── Databricks SDK ────────────────────────────────────────────────────────────
def client():
    try:
        from databricks.sdk import WorkspaceClient
    except ImportError:
        sys.exit('The Databricks SDK is not installed: pip install "contractctl[databricks]"')
    return WorkspaceClient()


def pick_warehouse(w, warehouse_id: str | None) -> str | None:
    warehouse_id = warehouse_id or os.environ.get("DATABRICKS_WAREHOUSE_ID")
    if warehouse_id:
        return warehouse_id
    try:
        warehouses = list(w.warehouses.list())
    except Exception:  # noqa: BLE001 - no permission to list: carry on without tags
        return None
    preferred = next((x for x in warehouses if x.name == "Serverless Starter Warehouse"), None)
    chosen = preferred or (warehouses[0] if warehouses else None)
    return chosen.id if chosen else None


def _query(w, warehouse_id: str, statement: str, params: dict[str, str]) -> list[list[str]]:
    from databricks.sdk.service.sql import StatementParameterListItem, StatementState
    resp = w.statement_execution.execute_statement(
        statement=statement, warehouse_id=warehouse_id, wait_timeout="50s",
        parameters=[StatementParameterListItem(name=k, value=v) for k, v in params.items()])
    if resp.status.state != StatementState.SUCCEEDED:
        raise RuntimeError(resp.status.error.message if resp.status.error else str(resp.status.state))
    return (resp.result.data_array if resp.result else None) or []


def read_table(full_name: str, warehouse_id: str | None = None, w=None) -> TableMetadata:
    from databricks.sdk.errors import NotFound
    catalog, schema, name = split_name(full_name)
    w = w or client()
    try:
        t = w.tables.get(full_name=f"{catalog}.{schema}.{name}")
    except NotFound:
        return TableMetadata(catalog, schema, name, exists=False)

    meta = TableMetadata(
        catalog, schema, name,
        table_type=str(t.table_type.value if t.table_type else ""),
        comment=t.comment or "", owner=t.owner or "",
        view_definition=t.view_definition or "",
        columns=[Column(c.name, (c.type_text or "").upper(), c.nullable is not False, c.comment or "",
                        c.mask.function_name if c.mask else None)
                 for c in sorted(t.columns or [], key=lambda c: c.position or 0)],
    )
    for con in t.table_constraints or []:
        if con.primary_key_constraint:
            meta.primary_key = list(con.primary_key_constraint.child_columns or [])
    if t.row_filter:
        meta.row_filter = {"function": t.row_filter.function_name,
                           "columns": list(t.row_filter.input_column_names or [])}

    try:
        perms = w.grants.get(securable_type="table", full_name=meta.full_name)
        for pa in perms.privilege_assignments or []:
            meta.grants[pa.principal] = [p.value if hasattr(p, "value") else str(p) for p in pa.privileges or []]
    except Exception as exc:  # noqa: BLE001 - grants are nice to have
        print(f"  ! grants not read: {exc}", file=sys.stderr)

    wh = pick_warehouse(w, warehouse_id)
    if not wh:
        meta.tags_read = False
        print("  ! no SQL warehouse available: tags not read (pass --warehouse-id)", file=sys.stderr)
        return meta
    params = {"schema": schema, "table": name}
    try:
        for tag, value in _query(w, wh, f"SELECT tag_name, tag_value FROM `{catalog}`.information_schema.table_tags "
                                        "WHERE schema_name = :schema AND table_name = :table", params):
            meta.tags[tag] = value or ""
        for col, tag, value in _query(w, wh, f"SELECT column_name, tag_name, tag_value FROM `{catalog}`.information_schema.column_tags "
                                             "WHERE schema_name = :schema AND table_name = :table", params):
            c = meta.column(col)
            if c:
                c.tags[tag] = value or ""
    except Exception as exc:  # noqa: BLE001
        meta.tags_read = False
        print(f"  ! tags not read: {exc}", file=sys.stderr)
    return meta
