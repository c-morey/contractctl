"""Compilation of the example contracts."""
import os

from _project import CFG, GOLDEN, ORDERS, ROOT

from contractctl import cli, sqlgen
from contractctl.model import Contract, discover


def test_generated_output_matches_the_golden_files():
    """Any change to the generated SQL is deliberate: rerun with UPDATE_GOLDEN=1 and review the diff."""
    built = cli.build(ROOT, CFG, discover(ROOT, CFG["data_products_dir"]))
    if os.environ.get("UPDATE_GOLDEN"):
        for rel, content in built.items():
            (GOLDEN / rel).parent.mkdir(parents=True, exist_ok=True)
            (GOLDEN / rel).write_text(content, encoding="utf-8")
    golden = {p.relative_to(GOLDEN).as_posix(): p.read_text(encoding="utf-8")
              for p in GOLDEN.rglob("*") if p.is_file()}
    assert built == golden


def test_every_rule_becomes_an_expectation():
    c = Contract(ORDERS, ROOT)
    sql = sqlgen.silver_sql(c, CFG)
    for rule in c.rules():
        assert f"CONSTRAINT {rule.name} EXPECT" in sql
    assert "ON VIOLATION FAIL UPDATE" in sql and "MASK ${contracts.catalog}.governance.mask_email" in sql


def test_null_is_a_violation_not_a_pass():
    c = Contract(ORDERS, ROOT)
    assert all(r.safe_expression.startswith("coalesce((") for r in c.rules())
