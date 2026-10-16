"""The example project, created once per test session with `contractctl init --example`."""
import tempfile
from pathlib import Path

import yaml

from contractctl import scaffold
from contractctl.model import Contract, load_config

CATALOG = "analytics"
ROOT = Path(tempfile.mkdtemp(prefix="contractctl-")) / "project"
scaffold.init(ROOT, CATALOG, example=True)
CFG = load_config(ROOT)
ORDERS = ROOT / "data_products/sales/orders/orders.odcs.yaml"
REVENUE = ROOT / "data_products/sales/daily_revenue/daily_revenue.odcs.yaml"
GOLDEN = Path(__file__).parent / "golden"


def variant(path: Path, change) -> Contract:
    """A modified copy of a contract, as it would look on a feature branch."""
    doc = yaml.safe_load(path.read_text())
    change(doc)
    return Contract(path, ROOT, text=yaml.safe_dump(doc, sort_keys=False))


def props(doc):
    return doc["schema"][0]["properties"]
