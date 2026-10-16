"""Linting, breaking-change classification and semantic versioning."""
from _project import CFG, ORDERS, REVENUE, ROOT, props, variant

from contractctl import validate
from contractctl.model import Contract, discover


def test_example_contracts_are_valid():
    assert validate.lint_all(discover(ROOT, CFG["data_products_dir"]), CFG) == {}


def test_removing_a_column_needs_a_major_bump():
    old = Contract(ORDERS, ROOT)

    def drop_currency(doc, version):
        doc["schema"][0]["properties"] = [p for p in props(doc) if p["name"] != "currency"]
        doc["version"] = version

    minor = variant(ORDERS, lambda d: drop_currency(d, "1.1.0"))
    breaking, _ = validate.compare(old, minor)
    assert "column removed: currency" in breaking
    assert validate.version_errors(old, minor, *validate.compare(old, minor))
    major = variant(ORDERS, lambda d: drop_currency(d, "2.0.0"))
    assert validate.version_errors(old, major, *validate.compare(old, major)) == []


def test_strengthening_a_rule_is_a_minor_change():
    def strengthen(doc):
        for p in props(doc):
            for q in p.get("quality") or []:
                if q["name"] == "country_in_scope":
                    q["implementation"]["onViolation"] = "quarantine"
        doc["version"] = "1.1.0"

    old, new = Contract(ORDERS, ROOT), variant(ORDERS, strengthen)
    breaking, additive = validate.compare(old, new)
    assert breaking == [] and additive == ["rule strengthened: country_in_scope warn -> quarantine"]
    assert validate.version_errors(old, new, breaking, additive) == []


def test_removing_pii_classification_is_breaking():
    def unclassify(doc):
        for p in props(doc):
            if p["name"] == "customer_email":
                p.pop("tags")
        doc["version"] = "1.0.1"

    breaking, _ = validate.compare(Contract(ORDERS, ROOT), variant(ORDERS, unclassify))
    assert any("pii classification removed" in b for b in breaking)


def test_gold_cannot_promise_fresher_data_than_its_input():
    def tighten(doc):
        doc["slaProperties"][0]["value"] = 1

    contracts = [Contract(ORDERS, ROOT), variant(REVENUE, tighten)]
    errors = validate.lint_all(contracts, CFG)
    assert any("tighter than input" in e for e in errors[contracts[1].rel_path])


def test_business_logic_may_only_read_declared_inputs():
    def no_inputs(doc):
        doc["customProperties"] = [cp for cp in doc["customProperties"] if cp["property"] != "inputs"]

    contracts = [Contract(ORDERS, ROOT), variant(REVENUE, no_inputs)]
    errors = validate.lint_all(contracts, CFG)
    assert any("not a declared input" in e for e in errors[contracts[1].rel_path])


def test_quarantine_is_not_allowed_in_gold():
    def quarantine(doc):
        props(doc)[2]["quality"][0]["implementation"]["onViolation"] = "quarantine"

    errors = validate.lint_contract(variant(REVENUE, quarantine))
    assert any("not allowed in gold" in e for e in errors)


def test_loosening_a_table_level_threshold_is_breaking():
    """The 'make the job green' fix an agent is most likely to propose."""
    def loosen(doc):
        doc["schema"][0]["quality"][1]["mustBeLessThan"] = 0.20
        doc["version"] = "1.0.1"

    old, new = Contract(ORDERS, ROOT), variant(ORDERS, loosen)
    breaking, additive = validate.compare(old, new)
    assert breaking == ["threshold loosened: quarantine_rate_below_5_pct mustBeLessThan 0.05 -> mustBeLessThan 0.2"]
    assert validate.version_errors(old, new, breaking, additive)


def test_removing_a_table_level_check_is_breaking():
    def remove(doc):
        doc["schema"][0]["quality"] = doc["schema"][0]["quality"][:1]
        doc["version"] = "1.0.1"

    breaking, _ = validate.compare(Contract(ORDERS, ROOT), variant(ORDERS, remove))
    assert "table-level check removed: quarantine_rate_below_5_pct" in breaking


def test_tightening_a_table_level_threshold_is_additive():
    def tighten(doc):
        doc["schema"][0]["quality"][1]["mustBeLessThan"] = 0.02
        doc["version"] = "1.1.0"

    old, new = Contract(ORDERS, ROOT), variant(ORDERS, tighten)
    breaking, additive = validate.compare(old, new)
    assert breaking == [] and additive and validate.version_errors(old, new, breaking, additive) == []
