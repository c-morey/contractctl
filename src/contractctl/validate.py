"""
Validation.

  drafts   status: draft contracts (e.g. from `contractctl import`) only need a valid structure;
           everything else they still miss is reported as a TODO, and they are never compiled
  lint     every contract on its own (structure, names, actions, SLAs, files) and all
           contracts together (unique ids/tables, declared inputs exist, business logic reads
           only declared inputs, a product never promises fresher data than its inputs)
  compare  consumer-facing diff between two versions of a contract -> (breaking, additive)
  semver   breaking -> MAJOR bump, additive -> at least MINOR, any change -> higher version
"""
from __future__ import annotations

import copy
import re

from .model import ACTION_SEVERITY, LAYER_ACTIONS, LAYERS, Contract

SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")
STATUSES = ("proposed", "draft", "active", "deprecated", "retired")
REQUIRED = ("apiVersion", "kind", "id", "version", "status", "domain", "description", "team", "support",
            "slaProperties", "schema")


def lint_contract(c: Contract) -> list[str]:
    e: list[str] = []
    d = c.doc
    e += [f"missing top-level field '{k}'" for k in REQUIRED if not d.get(k)]
    if not str(d.get("apiVersion", "")).startswith("v3"):
        e.append("apiVersion must be ODCS v3.x")
    if not SEMVER.match(c.version):
        e.append(f"version '{c.version}' is not MAJOR.MINOR.PATCH")
    if c.status not in STATUSES:
        e.append(f"status must be one of {STATUSES}")
    if c.layer not in LAYERS:
        e.append(f"customProperty 'layer' must be one of {LAYERS}")
    if not c.description.get("purpose"):
        e.append("description.purpose is required (it becomes the table comment)")
    if not any("owner" in str(m.get("role", "")).lower() for m in c.members):
        e.append("team needs a member whose role contains 'Owner'")
    if not c.support:
        e.append("support needs at least one channel")
    if not c.support_emails:
        e.append("support needs an e-mail channel (mailto:) for breach notifications")
    if len(d.get("schema") or []) != 1:
        e.append("exactly one schema object per contract (one contract = one data product table)")
    if not SNAKE.match(c.table):
        e.append(f"table name '{c.table}' must be snake_case")

    if c.layer == "silver" and not c.source.get("path"):
        e.append("silver contracts need customProperty 'source' with a path")
    if c.layer == "gold":
        if not c.logic_path:
            e.append("gold contracts need customProperty 'businessLogic'")
        elif not c.logic_path.exists():
            e.append(f"business logic file not found: {c.logic_path.name}")

    cols: set[str] = set()
    for p in c.properties:
        name = p.get("name", "")
        if not SNAKE.match(name) or name.startswith("_"):
            e.append(f"column '{name}': snake_case, not starting with '_' (reserved for the platform)")
        if name in cols:
            e.append(f"column '{name}' is declared twice")
        cols.add(name)
        if not p.get("physicalType"):
            e.append(f"column '{name}': physicalType is required")
        if c.generated_by(p) and c.generated_by(p) not in ("ingestion_timestamp", "source_file"):
            e.append(f"column '{name}': generatedBy must be ingestion_timestamp or source_file")
    for pk in c.primary_key():
        if not next(p for p in c.properties if p["name"] == pk).get("required"):
            e.append(f"primary key column '{pk}' must be required")
    rf = c.row_filter
    if rf and not set(rf.get("columns", [])) <= cols:
        e.append("rowFilter columns must be contracted columns")

    try:
        rules = c.rules()
        c.sql_checks()
    except (KeyError, ValueError) as exc:
        return e + [f"malformed quality rule: {exc}"]
    seen: set[str] = set()
    for r in rules:
        if not SNAKE.match(r.name):
            e.append(f"rule '{r.name}': snake_case (it becomes an expectation name)")
        if r.name in seen:
            e.append(f"rule '{r.name}' is defined twice")
        seen.add(r.name)
        if r.action not in LAYER_ACTIONS.get(c.layer, ()):
            e.append(f"rule '{r.name}': onViolation '{r.action}' not allowed in {c.layer} "
                     f"(use {', '.join(LAYER_ACTIONS.get(c.layer, ()))})")

    try:
        fresh = c.freshness()
    except (ValueError, KeyError) as exc:
        return e + [f"freshness SLA invalid: {exc}"]
    if not fresh:
        e.append("slaProperties needs a 'freshness' entry")
    elif fresh.table != c.obj.get("name") or fresh.column not in cols:
        e.append(f"freshness element {fresh.table}.{fresh.column} is not a contracted column")
    return e


def is_draft(c: Contract) -> bool:
    return c.status == "draft"


def lint_draft(c: Contract) -> list[str]:
    """A draft only has to be a well-formed contract skeleton."""
    e: list[str] = []
    if not str(c.doc.get("apiVersion", "")).startswith("v3"):
        e.append("apiVersion must be ODCS v3.x")
    if not c.id:
        e.append("missing top-level field 'id'")
    if not SEMVER.match(c.version):
        e.append(f"version '{c.version}' is not MAJOR.MINOR.PATCH")
    if len(c.doc.get("schema") or []) != 1:
        e.append("exactly one schema object per contract (one contract = one data product table)")
    names = [p.get("name") for p in c.properties]
    e += [f"column '{n}' is declared twice" for n in sorted({n for n in names if names.count(n) > 1})]
    e += [f"column '{p.get('name')}': physicalType is required" for p in c.properties if not p.get("physicalType")]
    return e


def draft_todos(c: Contract) -> list[str]:
    """What a draft still misses before it can become active: the full lint of an active contract."""
    todos = lint_contract(c)
    missing = {k for k in ("support", "slaProperties") if not c.doc.get(k)}
    noise = {"support": ("support needs",), "slaProperties": ("slaProperties needs",)}
    return [t for t in todos if not any(t.startswith(n) for k in missing for n in noise[k])]


def lint_all(contracts: list[Contract], cfg: dict) -> dict[str, list[str]]:
    errors = {c.rel_path: (lint_draft(c) if is_draft(c) else lint_contract(c)) for c in contracts}
    by_id: dict[str, Contract] = {}
    tables: dict[str, str] = {}
    for c in contracts:
        if c.id in by_id:
            errors[c.rel_path].append(f"duplicate contract id {c.id} (also in {by_id[c.id].rel_path})")
        by_id[c.id] = c
        key = f"{c.layer}.{c.table}"
        if key in tables:
            errors[c.rel_path].append(f"table {key} is also produced by {tables[key]}")
        tables[key] = c.rel_path

    schemas = cfg["schemas"]
    product_tables = {f"{schemas[c.layer]}.{c.table}": c for c in contracts if c.layer in schemas}
    ref = re.compile(r"\b(" + "|".join(re.escape(schemas[x]) for x in ("bronze", "silver", "gold")) + r")\.([a-z_][a-z0-9_]*)",
                     re.IGNORECASE)
    for c in contracts:
        if is_draft(c):
            continue
        for dep in c.inputs:
            if dep not in by_id:
                errors[c.rel_path].append(f"input {dep} is not a known contract")
                continue
            upstream = by_id[dep]
            if is_draft(upstream):
                errors[c.rel_path].append(f"input {dep} is still a draft: publish it first")
                continue
            if upstream.status in ("deprecated", "retired"):
                errors[c.rel_path].append(f"input {dep} is {upstream.status}")
            f_in, f_out = upstream.freshness(), c.freshness()
            if f_in and f_out and f_out.seconds < f_in.seconds:
                errors[c.rel_path].append(
                    f"freshness {f_out.label} is tighter than input {dep} ({f_in.label}): cannot be guaranteed")
        if c.layer == "gold" and c.logic_path and c.logic_path.exists():
            allowed = {f"{schemas[by_id[d].layer]}.{by_id[d].table}".lower() for d in c.inputs if d in by_id}
            for schema, table in set(ref.findall(c.read_logic())):
                name = f"{schema}.{table}".lower()
                if name not in allowed:
                    hint = " (a contracted product: add it to 'inputs')" if name in {k.lower() for k in product_tables} else ""
                    errors[c.rel_path].append(f"business logic reads {name}, which is not a declared input{hint}")
    return {k: v for k, v in errors.items() if v}


# ── versions ──────────────────────────────────────────────────────────────────
def compare(old: Contract, new: Contract) -> tuple[list[str], list[str]]:
    """Breaking and additive changes, seen from the consumer's side."""
    breaking, additive = [], []
    oc = {p["name"]: p for p in old.properties}
    nc = {p["name"]: p for p in new.properties}
    if old.table != new.table or old.layer != new.layer:
        breaking.append(f"table moved {old.layer}.{old.table} -> {new.layer}.{new.table}")
    for col, p in oc.items():
        if col not in nc:
            breaking.append(f"column removed: {col}")
            continue
        q = nc[col]
        if str(p.get("physicalType", "")).upper() != str(q.get("physicalType", "")).upper():
            breaking.append(f"type changed: {col} {p.get('physicalType')} -> {q.get('physicalType')}")
        if p.get("required") and not q.get("required"):
            breaking.append(f"no longer required: {col} (may now be NULL)")
        if bool(p.get("primaryKey")) != bool(q.get("primaryKey")):
            breaking.append(f"primary key changed: {col}")
        if old.column_tags(p).get("pii") and not new.column_tags(q).get("pii"):
            breaking.append(f"pii classification removed from {col} (data would be exposed unmasked)")
    additive += [f"column added: {col}" for col in nc if col not in oc]

    orules = {r.name: r for r in old.rules()}
    nrules = {r.name: r for r in new.rules()}
    for name, r in orules.items():
        if name not in nrules:
            breaking.append(f"quality rule removed: {name}")
            continue
        n = nrules[name]
        if r.expression.strip() != n.expression.strip():
            breaking.append(f"quality rule changed: {name} (review: the guarantee may be weaker)")
        so, sn = ACTION_SEVERITY[r.action], ACTION_SEVERITY[n.action]
        if sn < so:
            breaking.append(f"rule weakened: {name} {r.action} -> {n.action}")
        elif sn > so:
            additive.append(f"rule strengthened: {name} {r.action} -> {n.action}")
    additive += [f"quality rule added: {n}" for n in nrules if n not in orules]

    ochecks = {c.name: c for c in old.sql_checks()}
    nchecks = {c.name: c for c in new.sql_checks()}
    for name, oc_ in ochecks.items():
        if name not in nchecks:
            breaking.append(f"table-level check removed: {name}")
            continue
        nc_ = nchecks[name]
        if " ".join(oc_.query.split()) != " ".join(nc_.query.split()):
            breaking.append(f"table-level check changed: {name} (review: the guarantee may be weaker)")
        if oc_.operator != nc_.operator:
            breaking.append(f"threshold changed: {name} {oc_.expected} -> {nc_.expected}")
        elif oc_.threshold != nc_.threshold:
            direction = _threshold_direction(oc_.operator, oc_.threshold, nc_.threshold)
            if direction == "tighter":
                additive.append(f"threshold tightened: {name} {oc_.expected} -> {nc_.expected}")
            else:
                breaking.append(f"threshold loosened: {name} {oc_.expected} -> {nc_.expected}")
    additive += [f"table-level check added: {n}" for n in nchecks if n not in ochecks]

    fo, fn = old.freshness(), new.freshness()
    if fo and fn and fn.seconds > fo.seconds:
        breaking.append(f"freshness SLA loosened {fo.label} -> {fn.label}")
    elif fo and fn and fn.seconds < fo.seconds:
        additive.append(f"freshness SLA tightened {fo.label} -> {fn.label}")
    elif fo and not fn:
        breaking.append("freshness SLA removed")

    oroles = {r["role"] for r in old.roles}
    nroles = {r["role"] for r in new.roles}
    breaking += [f"access removed for role {r}" for r in sorted(oroles - nroles)]
    additive += [f"access granted to role {r}" for r in sorted(nroles - oroles)]
    if old.status == "active" and new.status in ("deprecated", "retired"):
        breaking.append(f"status {old.status} -> {new.status}")
    return breaking, additive


def _threshold_direction(operator: str, old, new) -> str | None:
    """'looser' or 'tighter' for one-sided thresholds; None when it can't be judged (treated as breaking)."""
    try:
        old, new = float(old), float(new)
    except (TypeError, ValueError):
        return None
    if operator in ("mustBeLessThan", "mustBeLessOrEqualTo"):
        return "looser" if new > old else "tighter"
    if operator in ("mustBeGreaterThan", "mustBeGreaterOrEqualTo"):
        return "looser" if new < old else "tighter"
    return None


def _semver(v: str) -> tuple[int, int, int]:
    return tuple(int(x) for x in SEMVER.match(v).groups())  # type: ignore[union-attr]


def version_errors(old: Contract, new: Contract, breaking: list[str], additive: list[str]) -> list[str]:
    a, b = copy.deepcopy(old.doc), copy.deepcopy(new.doc)
    a.pop("version", None)
    b.pop("version", None)
    if a == b:
        return []
    if not (SEMVER.match(old.version) and SEMVER.match(new.version)):
        return ["version is not semantic"]
    ov, nv = _semver(old.version), _semver(new.version)
    if nv <= ov:
        return [f"contract changed but version {new.version} is not higher than {old.version}"]
    if breaking and nv[0] <= ov[0]:
        return [f"breaking changes require a MAJOR version bump ({old.version} -> {ov[0] + 1}.0.0)"]
    if additive and not breaking and nv[:2] <= ov[:2]:
        return [f"additive changes require at least a MINOR bump ({old.version} -> {ov[0]}.{ov[1] + 1}.0)"]
    return []


def dependents(contract_id: str, contracts: list[Contract]) -> list[Contract]:
    return [c for c in contracts if contract_id in c.inputs]
