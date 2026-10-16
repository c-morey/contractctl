"""
Unity Catalog -> contract, and contract -> Unity Catalog comparison.

  draft_contract  bootstrap a DRAFT ODCS contract from what Unity Catalog already knows about a
                  table: columns, types, NOT NULL, comments, primary key, tags, masks, row filter,
                  owner and grants. What only the owner can promise (support channel, SLAs,
                  quality rules, usage and limitations) is left as a TODO list in the file.
  drift           compare a deployed product table with its contract and list every difference
                  made outside the pipeline: columns, types, NOT NULL, key, tags, masks, row
                  filter, grants and the deployed contract version.

Both work on a uc.TableMetadata, so they are tested without a workspace.
"""
from __future__ import annotations

import re

import yaml

from .model import Contract
from .uc import TableMetadata

DRAFT_VERSION = "0.1.0"
READ_PRIVILEGES = {"SELECT", "ALL_PRIVILEGES", "ALL PRIVILEGES"}
WRITE_PRIVILEGES = {"MODIFY", "ALL_PRIVILEGES", "ALL PRIVILEGES"}
SKIPPED_TAGS = {"classification"}            # mapped to ODCS `classification`, not to tags


def logical_type(type_text: str) -> str:
    t = type_text.upper()
    if t.startswith(("DECIMAL", "DOUBLE", "FLOAT", "REAL", "NUMERIC")):
        return "number"
    if t in ("INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "LONG", "SHORT", "BYTE"):
        return "integer"
    if t == "BOOLEAN":
        return "boolean"
    if t == "DATE":
        return "date"
    if t.startswith("TIMESTAMP"):
        return "timestamp"
    if t.startswith(("ARRAY",)):
        return "array"
    if t.startswith(("STRUCT", "MAP", "VARIANT")):
        return "object"
    return "string"


def _relative(function: str | None, catalog: str) -> str | None:
    """my_catalog.governance.mask_email -> governance.mask_email (contracts are catalog-free)."""
    if not function:
        return None
    return function[len(catalog) + 1:] if function.lower().startswith(catalog.lower() + ".") else function


def _guess_layer(meta: TableMetadata, cfg: dict) -> str | None:
    by_schema = {v: k for k, v in cfg["schemas"].items()}
    layer = by_schema.get(meta.schema)
    return layer if layer in ("silver", "gold") else None


def _freshness_column(meta: TableMetadata) -> str | None:
    stamps = [c.name for c in meta.columns if c.type_text.startswith("TIMESTAMP")]
    for hint in ("ingest", "load", "updated", "modified"):
        hit = next((s for s in stamps if hint in s), None)
        if hit:
            return hit
    return stamps[0] if stamps else None


def _inputs_from_sql(sql: str, known: list[Contract], cfg: dict) -> list[str]:
    schemas = cfg["schemas"]
    by_table = {f"{schemas[c.layer]}.{c.table}".lower(): c.id for c in known if c.layer in schemas}
    found = re.findall(r"(?=\b([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)\b)", sql.lower().replace("`", ""))
    return sorted({by_table[f"{s}.{t}"] for s, t in found if f"{s}.{t}" in by_table})


class _IndentedDumper(yaml.SafeDumper):
    """Indent lists under their key, like the hand-written contracts."""

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


# ── import ────────────────────────────────────────────────────────────────────
def draft_contract(meta: TableMetadata, cfg: dict, domain: str | None = None, layer: str | None = None,
                   known: list[Contract] | None = None) -> tuple[dict, list[str], list[str], str | None]:
    """Returns (ODCS document, TODO list for the owner, notes on what was not mapped, gold logic SQL)."""
    domain = domain or meta.tags.get("data_domain") or meta.schema
    layer = layer or _guess_layer(meta, cfg) or "silver"
    table = meta.name
    todos: list[str] = []
    notes: list[str] = []

    doc: dict = {
        "apiVersion": "v3.1.0",
        "kind": "DataContract",
        "id": f"urn:datacontract:{domain}:{table}",
        "name": table,
        "version": DRAFT_VERSION,
        "status": "draft",
        "domain": domain,
        "dataProduct": table,
    }

    description = {}
    if meta.comment:
        description["purpose"] = meta.comment
    else:
        todos.append("description.purpose: what is one record, and what is this product for (the table had no comment)")
    doc["description"] = description or None
    todos.append("description.usage and description.limitations: what consumers may and may not do with it")

    # ownership: Unity Catalog knows the owner principal, not the team or its roles
    team: dict = {"name": meta.tags.get("owner_team") or (meta.owner if "@" not in meta.owner else "")}
    if meta.owner:
        member = {"username": meta.owner, "role": "Data Product Owner"}
        team["members"] = [member]
        todos.append(f"team: confirm the Data Product Owner (taken from the Unity Catalog owner {meta.owner}) and the team name")
    else:
        todos.append("team: name the team and a member with role 'Data Product Owner'")
    doc["team"] = {k: v for k, v in team.items() if v} or None

    roles = []
    for principal, privileges in sorted(meta.grants.items()):
        if principal == meta.owner:
            continue
        privs = {p.upper() for p in privileges}
        if privs & WRITE_PRIVILEGES:
            roles.append({"role": principal, "access": "write"})
        elif privs & READ_PRIVILEGES:
            roles.append({"role": principal, "access": "read"})
    if roles:
        doc["roles"] = roles
        todos.append("roles: check the groups taken from the current grants (user grants should become groups)")
    else:
        todos.append("roles: which groups may read (read) and who produces the data (write)")

    todos.append("support: at least one mailto: channel (it receives the breach alerts)")
    fresh_col = _freshness_column(meta)
    todos.append(f"slaProperties: freshness (element {table}.{fresh_col or '<timestamp column>'}) and frequency")

    custom: list[dict] = [{"property": "layer", "value": layer}]
    logic_sql = None
    if layer == "silver":
        custom.append({"property": "source", "value": {"path": table, "format": "json"}})
        todos.append(f"customProperties.source: the landing folder and file format of the source (guessed: {table}, json)")
    else:
        custom.append({"property": "businessLogic", "value": f"{table}.logic.sql"})
        if meta.view_definition:
            logic_sql = meta.view_definition.strip()
            todos.append(f"{table}.logic.sql: taken from the view definition; replace catalog-qualified names by <schema>.<table>")
        else:
            logic_sql = f"-- TODO: the SELECT that produces {table}\nSELECT 1 AS todo\n"
            todos.append(f"{table}.logic.sql: write the SELECT that produces this table")
        inputs = _inputs_from_sql(meta.view_definition, known or [], cfg) if meta.view_definition else []
        if inputs:
            custom.append({"property": "inputs", "value": inputs})
        else:
            todos.append("customProperties.inputs: the contracts this product reads from")
    if meta.row_filter:
        custom.append({"property": "rowFilter", "value": {
            "function": _relative(meta.row_filter["function"], meta.catalog),
            "columns": list(meta.row_filter.get("columns") or [])}})
    doc["customProperties"] = custom

    doc["servers"] = [{"server": "imported", "type": "databricks", "catalog": meta.catalog, "schema": meta.schema}]

    properties = []
    for col in meta.columns:
        if col.name.startswith("_"):
            notes.append(f"column {col.name} skipped: names starting with '_' are reserved for the platform")
            continue
        prop: dict = {"name": col.name}
        if col.comment:
            prop["description"] = col.comment
        prop["logicalType"] = logical_type(col.type_text)
        prop["physicalType"] = col.type_text
        if col.name in meta.primary_key:
            prop["primaryKey"] = True
            prop["primaryKeyPosition"] = meta.primary_key.index(col.name) + 1
        if not col.nullable or col.name in meta.primary_key:
            prop["required"] = True
        if col.tags.get("classification"):
            prop["classification"] = col.tags["classification"]
        tags = [f"{k}:{v}" if v else k for k, v in sorted(col.tags.items()) if k not in SKIPPED_TAGS]
        if tags:
            prop["tags"] = tags
        if col.mask:
            prop["customProperties"] = [{"property": "mask", "value": _relative(col.mask, meta.catalog)}]
        if col.mask and "pii" not in col.tags:
            notes.append(f"column {col.name} has a mask but no pii tag: add tags: [\"pii:<type>\"]")
        properties.append(prop)
    missing_comments = [p["name"] for p in properties if not p.get("description")]
    if missing_comments:
        todos.append(f"descriptions for {', '.join(missing_comments)}")
    todos.append("quality rules: what must hold for every row (lakeflow-expectations) and for the table (sql)")
    if not meta.tags_read:
        notes.append("tags were not read (no SQL warehouse): pii tags and classification may be missing")

    doc["schema"] = [{
        "name": table,
        "physicalName": table,
        "physicalType": "table",
        **({"description": meta.comment} if meta.comment else {}),
        "properties": properties,
    }]
    doc = {k: v for k, v in doc.items() if v is not None}
    return doc, todos, notes, logic_sql


def render(doc: dict, meta: TableMetadata, todos: list[str], notes: list[str]) -> str:
    table = meta.name
    fresh_col = _freshness_column(meta) or "<timestamp column>"
    header = [
        "# " + "─" * 77,
        f"# DRAFT data contract, bootstrapped from Unity Catalog table {meta.full_name}",
        "# by `contractctl import`. It describes the table as it is today, not yet a promise.",
        "#",
        "# status: draft  ->  `contractctl lint` lists what is missing; `generate` skips it.",
        "# To publish: complete the TODOs at the bottom, set status: active and version: 1.0.0,",
        "# run `contractctl generate` and open a pull request for the owner to approve.",
        "# " + "─" * 77,
    ]
    body = yaml.dump(doc, Dumper=_IndentedDumper, sort_keys=False, allow_unicode=True, width=110,
                     default_flow_style=False)
    body = re.sub(r"^(\S)", r"\n\1", body, flags=re.MULTILINE).lstrip("\n")       # blank line per section
    body = re.sub(r"\n\n(?=(kind|id|name|version|status|domain|dataProduct):)", "\n", body)
    tail = ["", "# " + "─" * 77, "# TODO before status: active (only the owner can promise these)"]
    tail += [f"#   - {t}" for t in todos]
    if notes:
        tail += ["#", "# Notes from the import"] + [f"#   - {n}" for n in notes]
    tail += [
        "#",
        "# Templates to copy:",
        "# support:",
        f"#   - channel: {doc['domain']}-{table}-mailbox",
        "#     tool: email",
        "#     url: mailto:<team>@example.com",
        "# slaProperties:",
        "#   - property: freshness",
        "#     value: 24",
        "#     unit: h",
        f"#     element: {table}.{fresh_col}",
        "#   - property: frequency",
        "#     value: 1",
        "#     unit: h",
        "# quality (under a property):",
        "#   - type: custom",
        "#     engine: lakeflow-expectations",
        "#     name: <column>_<rule>",
        "#     implementation:",
        "#       expression: \"<SQL boolean>\"",
        "#       onViolation: quarantine      # silver: warn | quarantine | fail; gold: warn | drop | fail",
        "# " + "─" * 77,
    ]
    return "\n".join(header) + "\n" + body + "\n".join(tail) + "\n"


# ── drift ─────────────────────────────────────────────────────────────────────
def _norm(type_text: str) -> str:
    return re.sub(r"\s+", "", type_text.upper())


def _qualified(function: str, catalog: str) -> str:
    return (f"{catalog}.{function}" if function.count(".") == 1 else function).lower()


def drift(c: Contract, meta: TableMetadata, cfg: dict) -> list[str]:
    """Differences between the deployed table and its contract (empty list = in sync)."""
    if not meta.exists:
        return [f"table {meta.full_name} does not exist (not deployed yet, or dropped)"]
    out: list[str] = []
    contracted = {p["name"]: p for p in c.properties}
    for name, p in contracted.items():
        col = meta.column(name)
        if col is None:
            out.append(f"column {name}: in the contract, missing from the table")
            continue
        if _norm(col.type_text) != _norm(str(p.get("physicalType", ""))):
            out.append(f"column {name}: type {col.type_text} in the table, {p.get('physicalType')} in the contract")
        if p.get("required") and col.nullable:
            out.append(f"column {name}: required in the contract, nullable in the table")
        if meta.tags_read:
            for key, value in Contract.column_tags(p).items():
                if col.tags.get(key) != value:
                    actual = col.tags.get(key)
                    out.append(f"column {name}: tag {key}={value} expected, "
                               + (f"found {key}={actual}" if actual is not None else "tag missing"))
        expected_mask = c.mask_for(p, cfg["default_pii_mask"])
        if expected_mask and (col.mask or "").lower() != _qualified(expected_mask, meta.catalog):
            out.append(f"column {name}: mask {expected_mask} expected, "
                       + (f"found {col.mask}" if col.mask else "NO MASK: personal data is visible"))
        if not expected_mask and col.mask:
            out.append(f"column {name}: masked with {col.mask}, the contract has no mask")
    for col in meta.columns:
        if col.name not in contracted and not col.name.startswith("_"):
            out.append(f"column {col.name}: in the table, not in the contract")

    if meta.primary_key != c.primary_key():
        out.append(f"primary key {meta.primary_key or 'none'} in the table, {c.primary_key() or 'none'} in the contract")

    rf = c.row_filter
    if rf:
        actual = (meta.row_filter or {}).get("function", "")
        if actual.lower() != _qualified(rf["function"], meta.catalog):
            out.append(f"row filter {rf['function']} expected, "
                       + (f"found {actual}" if actual else "NO ROW FILTER: every row is visible"))
    elif meta.row_filter:
        out.append(f"row filter {meta.row_filter['function']} on the table, none in the contract")

    if meta.grants:
        allowed = {r["role"] for r in c.roles} | {meta.owner}
        for principal, privileges in sorted(meta.grants.items()):
            if principal not in allowed and {p.upper() for p in privileges} & READ_PRIVILEGES:
                out.append(f"access: {principal} can read the table but is not a role in the contract")
        for role in sorted({r["role"] for r in c.roles}):
            if not {p.upper() for p in meta.grants.get(role, [])} & READ_PRIVILEGES:
                out.append(f"access: role {role} is in the contract but has no SELECT")

    if meta.tags_read:
        deployed = meta.tags.get("contract_version")
        if deployed != c.version:
            out.append(f"contract version {deployed or 'unknown'} is deployed, the contract says {c.version} "
                       "(deploy and run the job)")
    return out
