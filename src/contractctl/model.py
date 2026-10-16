"""
Contract model: reads ODCS v3 contracts and exposes what the platform needs.

Conventions on top of ODCS (all ODCS-valid, checked by `datacontract lint`):

  contract customProperties
    layer          silver | gold
    source         silver only: {path, format} under the landing volume
    businessLogic  gold only: file with the SELECT, relative to the contract
    inputs         gold only: contract ids this product reads from
    rowFilter      {function, columns}: Unity Catalog row filter on the product table

  property
    tags           "pii:<type>" -> column tag pii=<type> and a column mask
    customProperties
      generatedBy  ingestion_timestamp | source_file  (added by the platform, not the source)
      mask         column mask function overriding the default pii mask

  quality rules
    type: custom, engine: lakeflow-expectations,
    implementation: {expression: <SQL boolean>, onViolation: <action>}
      silver actions: warn | quarantine | fail
      gold actions:   warn | drop | fail
    type: sql + mustBe* threshold  -> table-level check after each run
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

EXPECTATION_ENGINE = "lakeflow-expectations"
LAYERS = ("silver", "gold")
LAYER_ACTIONS = {"silver": ("warn", "quarantine", "fail"), "gold": ("warn", "drop", "fail")}
ACTION_SEVERITY = {"warn": 0, "drop": 1, "quarantine": 1, "fail": 2}
GENERATED_COLUMNS = {"ingestion_timestamp": "_ingested_at", "source_file": "_source_file"}

UNIT_SECONDS = {
    "min": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
}

# ODCS threshold operators -> SQL comparison templates ({v} = measured value)
OPERATORS = {
    "mustBe": "{v} = {t}",
    "mustNotBe": "{v} <> {t}",
    "mustBeGreaterThan": "{v} > {t}",
    "mustBeGreaterOrEqualTo": "{v} >= {t}",
    "mustBeLessThan": "{v} < {t}",
    "mustBeLessOrEqualTo": "{v} <= {t}",
    "mustBeBetween": "{v} BETWEEN {t0} AND {t1}",
    "mustNotBeBetween": "{v} NOT BETWEEN {t0} AND {t1}",
}


@dataclass(frozen=True)
class Rule:
    name: str
    expression: str
    action: str
    dimension: str = ""
    description: str = ""
    scope: str = "table"
    implicit: bool = False

    @property
    def safe_expression(self) -> str:
        """A rule that evaluates to NULL is a violation, not a pass."""
        return f"coalesce(({self.expression}), false)"


@dataclass(frozen=True)
class SqlCheck:
    name: str
    query: str
    operator: str
    threshold: object
    description: str = ""

    def condition(self, value_sql: str) -> str:
        t = self.threshold
        if isinstance(t, (list, tuple)):
            return OPERATORS[self.operator].format(v=value_sql, t0=t[0], t1=t[1])
        return OPERATORS[self.operator].format(v=value_sql, t=t)

    @property
    def expected(self) -> str:
        return f"{self.operator} {self.threshold}"


@dataclass(frozen=True)
class Freshness:
    seconds: float
    table: str
    column: str
    label: str


class Contract:
    """One ODCS contract = one data product = one published table."""

    def __init__(self, path: Path, root: Path, text: str | None = None):
        self.path = path
        self.root = root
        self.text = text if text is not None else path.read_text(encoding="utf-8")
        self.doc: dict = yaml.safe_load(self.text) or {}

    # ── identity ──────────────────────────────────────────────────────────────
    @property
    def id(self) -> str:
        return str(self.doc.get("id", ""))

    @property
    def version(self) -> str:
        return str(self.doc.get("version", ""))

    @property
    def name(self) -> str:
        return str(self.doc.get("name", self.path.stem))

    @property
    def status(self) -> str:
        return str(self.doc.get("status", ""))

    @property
    def domain(self) -> str:
        return str(self.doc.get("domain", ""))

    @property
    def rel_path(self) -> str:
        return self.path.relative_to(self.root).as_posix()

    def custom(self, key: str, default=None):
        return _custom(self.doc, key, default)

    @property
    def layer(self) -> str:
        return str(self.custom("layer", ""))

    @property
    def obj(self) -> dict:
        objs = self.doc.get("schema") or []
        return objs[0] if objs else {}

    @property
    def table(self) -> str:
        return str(self.obj.get("physicalName") or self.obj.get("name") or self.name)

    @property
    def properties(self) -> list[dict]:
        return list(self.obj.get("properties") or [])

    @property
    def description(self) -> dict:
        return self.doc.get("description") or {}

    # ── ownership, support, access ────────────────────────────────────────────
    @property
    def team_name(self) -> str:
        team = self.doc.get("team") or {}
        return str(team.get("name", "")) if isinstance(team, dict) else ""

    @property
    def members(self) -> list[dict]:
        team = self.doc.get("team") or {}
        return list(team if isinstance(team, list) else team.get("members") or [])

    @property
    def support(self) -> list[dict]:
        return list(self.doc.get("support") or [])

    @property
    def support_emails(self) -> list[str]:
        return [str(s["url"])[len("mailto:"):] for s in self.support
                if str(s.get("url", "")).startswith("mailto:")]

    @property
    def primary_channel(self) -> dict:
        return self.support[0] if self.support else {}

    @property
    def roles(self) -> list[dict]:
        return list(self.doc.get("roles") or [])

    def role_groups(self, access: str) -> list[str]:
        return [str(r["role"]) for r in self.roles if r.get("access", "read") == access]

    @property
    def inputs(self) -> list[str]:
        return [str(i) for i in (self.custom("inputs") or [])]

    @property
    def row_filter(self) -> dict | None:
        return self.custom("rowFilter")

    @property
    def source(self) -> dict:
        return self.custom("source") or {}

    @property
    def logic_path(self) -> Path | None:
        logic = self.custom("businessLogic")
        return (self.path.parent / logic) if logic else None

    def read_logic(self) -> str:
        return self.logic_path.read_text(encoding="utf-8").strip().rstrip(";").strip()

    # ── columns ───────────────────────────────────────────────────────────────
    @staticmethod
    def generated_by(prop: dict) -> str | None:
        return _custom(prop, "generatedBy")

    def source_properties(self) -> list[dict]:
        return [p for p in self.properties if not self.generated_by(p)]

    def primary_key(self) -> list[str]:
        pk = [p for p in self.properties if p.get("primaryKey")]
        return [p["name"] for p in sorted(pk, key=lambda p: p.get("primaryKeyPosition", 0))]

    @staticmethod
    def column_tags(prop: dict) -> dict[str, str]:
        tags = {}
        for tag in prop.get("tags") or []:
            key, _, value = str(tag).partition(":")
            tags[key] = value or "true"
        if prop.get("classification"):
            tags["classification"] = str(prop["classification"])
        return tags

    def mask_for(self, prop: dict, default_mask: str) -> str | None:
        explicit = _custom(prop, "mask")
        if explicit:
            return str(explicit)
        return default_mask if "pii" in self.column_tags(prop) else None

    # ── rules ─────────────────────────────────────────────────────────────────
    def rules(self) -> list[Rule]:
        """Row-level rules in contract order: implicit (type, required) + explicit."""
        rules: list[Rule] = []
        for p in self.properties:
            col = p["name"]
            generated = self.generated_by(p)
            if self.layer == "silver" and not generated and str(p.get("physicalType", "")).upper() != "STRING":
                rules.append(Rule(
                    f"{col}_type_conforms", f"_raw.{col} IS NULL OR {col} IS NOT NULL", "quarantine",
                    "conformity", f"{col} must be castable to {p.get('physicalType')}", col, True))
            if p.get("required") and not generated:
                action = "quarantine" if self.layer == "silver" else "fail"
                rules.append(Rule(f"{col}_required", f"{col} IS NOT NULL", action,
                                  "completeness", f"{col} is required", col, True))
            rules += _explicit_rules(p.get("quality"), col)
        rules += _explicit_rules(self.obj.get("quality"), "table")
        return rules

    def sql_checks(self) -> list[SqlCheck]:
        quality = list(self.obj.get("quality") or [])
        for p in self.properties:
            quality += p.get("quality") or []
        checks = []
        for q in quality:
            if q.get("type") != "sql":
                continue
            op = next((o for o in OPERATORS if o in q), None)
            if op is None:
                raise ValueError(f"{self.rel_path}: SQL rule '{q.get('name')}' has no mustBe* threshold")
            checks.append(SqlCheck(q.get("name", "sql_check"), str(q["query"]).strip().rstrip(";"),
                                   op, q[op], q.get("description", "")))
        return checks

    # ── service levels ────────────────────────────────────────────────────────
    def sla(self, prop: str) -> dict | None:
        return next((s for s in self.doc.get("slaProperties") or [] if s.get("property") == prop), None)

    def freshness(self) -> Freshness | None:
        s = self.sla("freshness")
        if not s:
            return None
        unit = str(s.get("unit", "h")).lower()
        if unit not in UNIT_SECONDS:
            raise ValueError(f"{self.rel_path}: unsupported freshness unit '{unit}'")
        table, column = str(s["element"]).split(".", 1)
        return Freshness(float(s["value"]) * UNIT_SECONDS[unit], table, column, f"{s['value']}{s.get('unit', 'h')}")

    def frequency_seconds(self) -> float | None:
        s = self.sla("frequency")
        if not s:
            return None
        return float(s["value"]) * UNIT_SECONDS[str(s.get("unit", "h")).lower()]


def _custom(item: dict, key: str, default=None):
    for cp in item.get("customProperties") or []:
        if cp.get("property") == key:
            return cp.get("value")
    return default


def _explicit_rules(quality: list | None, scope: str) -> list[Rule]:
    rules = []
    for q in quality or []:
        if q.get("type") != "custom" or q.get("engine") != EXPECTATION_ENGINE:
            continue
        impl = q.get("implementation") or {}
        rules.append(Rule(str(q["name"]), str(impl["expression"]), str(impl.get("onViolation", "warn")),
                          str(q.get("dimension", "")), str(q.get("description", "")), scope))
    return rules


def discover(root: Path, data_products_dir: str) -> list[Contract]:
    base = root / data_products_dir
    return [Contract(p, root) for p in sorted(base.rglob("*.odcs.yaml"))]


def load_config(root: Path) -> dict:
    return yaml.safe_load((root / "contractctl.yml").read_text(encoding="utf-8"))
