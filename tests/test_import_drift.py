"""Unity Catalog import and drift. No workspace needed: metadata is plain data."""
from types import SimpleNamespace

from _project import CFG, ORDERS, ROOT

from contractctl import cli, importer, validate
from contractctl.model import Contract, discover
from contractctl.uc import TableMetadata

CUSTOMERS = {
    "catalog": "analytics", "schema": "legacy", "name": "customers",
    "comment": "One record per web shop customer", "owner": "jane.peeters@example.com",
    "primary_key": ["customer_id"],
    "grants": {"sales_analysts": ["SELECT"], "crm_loaders": ["MODIFY", "SELECT"]},
    "columns": [
        {"name": "customer_id", "type_text": "STRING", "nullable": False, "comment": "CRM customer id"},
        {"name": "email", "type_text": "STRING", "comment": "E-mail address",
         "mask": "analytics.governance.mask_email", "tags": {"pii": "email", "classification": "confidential"}},
        {"name": "lifetime_value", "type_text": "DECIMAL(12,2)"},
        {"name": "updated_at", "type_text": "TIMESTAMP", "nullable": False},
        {"name": "_etl_batch", "type_text": "STRING"},
    ],
}


def imported(meta=CUSTOMERS, **kw):
    m = TableMetadata.from_dict(meta)
    doc, todos, notes, logic = importer.draft_contract(m, CFG, domain="sales", **kw)
    text = importer.render(doc, m, todos, notes)
    return Contract(ROOT / "data_products/sales/customers/customers.odcs.yaml", ROOT, text=text), todos, notes, logic


def test_import_maps_what_unity_catalog_knows():
    c, todos, notes, _ = imported()
    assert (c.id, c.version, c.status, c.layer) == ("urn:datacontract:sales:customers", "0.1.0", "draft", "silver")
    assert c.primary_key() == ["customer_id"]
    email = next(p for p in c.properties if p["name"] == "email")
    assert c.column_tags(email) == {"pii": "email", "classification": "confidential"}
    assert c.mask_for(email, CFG["default_pii_mask"]) == "governance.mask_email"
    assert next(p for p in c.properties if p["name"] == "updated_at")["required"] is True
    assert {r["role"]: r["access"] for r in c.roles} == {"crm_loaders": "write", "sales_analysts": "read"}
    assert "_etl_batch" not in [p["name"] for p in c.properties] and any("_etl_batch" in n for n in notes)
    assert any(t.startswith("support") for t in todos) and any("customers.updated_at" in t for t in todos)


def test_a_draft_is_linted_lightly_and_never_compiled():
    c, _, _, _ = imported()
    assert validate.lint_all([c], CFG) == {}                    # does not break CI
    todos = validate.draft_todos(c)                             # but says what is missing
    assert any("support" in t for t in todos) and any("slaProperties" in t for t in todos)
    contracts = discover(ROOT, CFG["data_products_dir"])
    assert cli.build(ROOT, CFG, contracts + [c]) == cli.build(ROOT, CFG, contracts)


def test_import_of_a_gold_view_keeps_logic_and_finds_inputs():
    view = dict(CUSTOMERS, name="revenue_by_country", schema="gold", primary_key=[], grants={},
                view_definition="SELECT country_code, sum(amount) FROM analytics.silver.orders GROUP BY 1",
                columns=[{"name": "country_code", "type_text": "STRING"}])
    c, _, _, logic = imported(view, known=discover(ROOT, CFG["data_products_dir"]))
    assert c.layer == "gold" and c.inputs == ["urn:datacontract:sales:orders"]
    assert "silver.orders" in logic


def deployed_orders(**changes) -> TableMetadata:
    """silver.orders as the pipeline and governance SQL deploy it."""
    c = Contract(ORDERS, ROOT)
    cols = []
    for p in c.properties:
        mask = c.mask_for(p, CFG["default_pii_mask"])
        cols.append({"name": p["name"], "type_text": p["physicalType"], "nullable": not p.get("required"),
                     "mask": f"analytics.{mask}" if mask else None, "tags": c.column_tags(p)})
    meta = {"catalog": "analytics", "schema": "silver", "name": "orders", "owner": "platform_sp",
            "primary_key": c.primary_key(), "columns": cols, "tags": {"contract_version": c.version},
            "grants": {r["role"]: ["SELECT"] for r in c.roles}}
    meta.update(changes)
    return TableMetadata.from_dict(meta)


def test_drift_is_empty_for_a_table_deployed_from_its_contract():
    assert importer.drift(Contract(ORDERS, ROOT), deployed_orders(), CFG) == []


def test_drift_catches_changes_made_outside_the_pipeline():
    meta = deployed_orders()
    meta.column("customer_email").mask = None                    # someone dropped the mask
    meta.column("amount").type_text = "DOUBLE"                   # ... changed a type
    meta.columns.append(meta.columns[0].__class__("discount", "DECIMAL(12,2)"))   # ... added a column
    meta.grants["marketing"] = ["SELECT"]                        # ... and granted access by hand
    found = "\n".join(importer.drift(Contract(ORDERS, ROOT), meta, CFG))
    assert "customer_email: mask governance.mask_email expected, NO MASK" in found
    assert "amount: type DOUBLE" in found
    assert "discount: in the table, not in the contract" in found
    assert "marketing can read the table but is not a role" in found


def test_read_table_maps_the_sdk_response():
    import pytest
    pytest.importorskip("databricks.sdk")
    from contractctl import uc
    ns = SimpleNamespace
    table = ns(table_type=ns(value="MANAGED"), comment="c", owner="o", view_definition=None,
               columns=[ns(name="b", type_text="int", nullable=True, comment=None, mask=None, position=1),
                        ns(name="a", type_text="string", nullable=False, comment="key", position=0,
                           mask=ns(function_name="cat.governance.mask_pii"))],
               table_constraints=[ns(primary_key_constraint=ns(child_columns=["a"]))],
               row_filter=ns(function_name="cat.governance.filter_country", input_column_names=["a"]))
    grants = ns(privilege_assignments=[ns(principal="g", privileges=[ns(value="SELECT")])])
    rows = {"table_tags": [["data_domain", "sales"]], "column_tags": [["a", "pii", "name"]]}

    def execute_statement(statement, **_):
        data = next(v for k, v in rows.items() if k in statement)
        from databricks.sdk.service.sql import StatementState
        return ns(status=ns(state=StatementState.SUCCEEDED), result=ns(data_array=data))

    w = ns(tables=ns(get=lambda full_name: table), grants=ns(get=lambda **_: grants),
           statement_execution=ns(execute_statement=execute_statement))
    m = uc.read_table("cat.s.t", warehouse_id="wh", w=w)
    assert [c.name for c in m.columns] == ["a", "b"] and m.columns[1].type_text == "INT"
    assert m.primary_key == ["a"] and m.grants == {"g": ["SELECT"]} and m.tags == {"data_domain": "sales"}
    assert m.column("a").tags == {"pii": "name"} and m.row_filter["columns"] == ["a"]
