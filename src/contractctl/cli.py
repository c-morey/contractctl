"""
contractctl: compile data contracts (ODCS v3) into a governed Databricks medallion pipeline.

  contractctl init [DIR] --catalog NAME     start a new contracts project (--example adds samples)
  contractctl lint                          validate every contract and their dependencies
  contractctl generate                      write pipeline SQL, job SQL and bundle resources
  contractctl check                         fail if generated files are out of date (CI)
  contractctl diff --base main              breaking-change guard, semver and impact analysis
  contractctl diff --base-dir DIR           same, against a checkout on disk (Databricks Git folder)
  contractctl import CATALOG.SCHEMA.TABLE   draft a contract from Unity Catalog metadata
  contractctl drift                         compare deployed tables with their contracts
Contracts with status: draft are linted for structure only and never compiled.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import yaml

from . import __version__, bundlegen, importer, scaffold, sqlgen, uc, validate
from .model import Contract, discover, load_config


def find_root() -> Path:
    here = Path.cwd().resolve()
    for candidate in (here, *here.parents):
        if (candidate / "contractctl.yml").exists():
            return candidate
    sys.exit("contractctl.yml not found. Run this inside a contracts project, or create one with `contractctl init`.")


# ── generation ────────────────────────────────────────────────────────────────
def build(root: Path, cfg: dict, contracts: list[Contract]) -> dict[str, str]:
    """Every generated file: repo-relative path -> content. Drafts are not compiled."""
    contracts = [c for c in contracts if not validate.is_draft(c)]
    gen, res = cfg["generated_dir"], cfg["resources_dir"]
    files: dict[str, str] = {}
    for domain, group in bundlegen.group_by_domain(contracts).items():
        group = sorted(group, key=lambda c: (c.layer != "silver", c.table))
        sql_files = []
        for c in group:
            for rel, sql in sqlgen.pipeline_sql(c, cfg).items():
                path = f"{gen}/pipelines/{domain}/{rel}"
                files[path] = sql
                sql_files.append(path)
            files[f"{gen}/governance/{domain}/{c.table}.sql"] = sqlgen.governance_sql(c, cfg)
            files[f"{gen}/sla/{domain}/{c.table}.sql"] = sqlgen.sla_sql(c, cfg)
        files[f"{res}/{domain}.yml"] = bundlegen.domain_resources(domain, group, sql_files, cfg)
        files[f"{gen}/observability/{domain}.sql"] = sqlgen.observability_sql(domain, group, cfg)
    return files


def existing_generated(root: Path, cfg: dict) -> dict[str, str]:
    out = {}
    for base in (cfg["generated_dir"], cfg["resources_dir"]):
        for p in (root / base).rglob("*"):
            if p.is_file():
                out[p.relative_to(root).as_posix()] = p.read_text(encoding="utf-8")
    return out


def _lint_or_exit(contracts: list[Contract], cfg: dict) -> None:
    problems = validate.lint_all(contracts, cfg)
    for path, errors in problems.items():
        print(f"✖ {path}")
        for e in errors:
            print(f"    {e}")
    if problems:
        sys.exit(f"\n{sum(len(v) for v in problems.values())} problem(s) in {len(problems)} contract(s).")


def cmd_lint(root: Path, cfg: dict, _args) -> None:
    contracts = discover(root, cfg["data_products_dir"])
    _lint_or_exit(contracts, cfg)
    for c in contracts:
        if validate.is_draft(c):
            todos = validate.draft_todos(c)
            print(f"◌ {c.rel_path}  {c.id} v{c.version}  (draft, not compiled: {len(todos)} to do before active)")
            for t in todos:
                print(f"    · {t}")
        else:
            print(f"✔ {c.rel_path}  {c.id} v{c.version}  ({c.layer}, {len(c.rules())} rules)")


def cmd_generate(root: Path, cfg: dict, _args) -> None:
    contracts = discover(root, cfg["data_products_dir"])
    _lint_or_exit(contracts, cfg)
    expected, actual = build(root, cfg, contracts), existing_generated(root, cfg)
    for rel in sorted(set(actual) - set(expected)):
        (root / rel).unlink()
        print(f"  removed {rel}")
    changed = 0
    for rel, content in expected.items():
        if actual.get(rel) != content:
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            print(f"  wrote {rel}")
            changed += 1
    print(f"✔ {changed} file(s) written, {len(expected) - changed} unchanged")


def cmd_check(root: Path, cfg: dict, _args) -> None:
    contracts = discover(root, cfg["data_products_dir"])
    _lint_or_exit(contracts, cfg)
    expected, actual = build(root, cfg, contracts), existing_generated(root, cfg)
    stale = sorted(p for p in expected if actual.get(p) != expected[p])
    orphaned = sorted(p for p in actual if p not in expected)
    for p in stale:
        print(f"✖ out of date: {p}")
    for p in orphaned:
        print(f"✖ not produced by any contract: {p}")
    if stale or orphaned:
        sys.exit("\nGenerated files do not match the contracts. Run `contractctl generate` and commit.")
    drafts = sum(validate.is_draft(c) for c in contracts)
    print(f"✔ {len(expected)} generated files match {len(contracts) - drafts} contracts"
          + (f" ({drafts} draft(s) not compiled)" if drafts else ""))


# ── diff against a git ref ────────────────────────────────────────────────────
def _git(root: Path, *args: str) -> str | None:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else None


def cmd_diff(root: Path, cfg: dict, args) -> None:
    contracts = discover(root, cfg["data_products_dir"])
    _lint_or_exit(contracts, cfg)
    if args.base_dir:
        base_root = Path(args.base_dir)
        if not (base_root / cfg["data_products_dir"]).is_dir():
            sys.exit(f"no {cfg['data_products_dir']}/ under {base_root}")
        base_paths = {p.relative_to(base_root).as_posix()
                      for p in (base_root / cfg["data_products_dir"]).rglob("*.odcs.yaml")}

        def baseline(path: str) -> str:
            return (base_root / path).read_text(encoding="utf-8")
    else:
        listed = _git(root, "ls-tree", "-r", "--name-only", args.base, "--", cfg["data_products_dir"])
        if listed is None:
            sys.exit(f"cannot read git ref '{args.base}' (in a Databricks Git folder, use --base-dir)")
        base_paths = {p for p in listed.splitlines() if p.endswith(".odcs.yaml")}

        def baseline(path: str) -> str | None:
            return _git(root, "show", f"{args.base}:{path}")
    current = {c.rel_path: c for c in contracts}
    errors: list[str] = []

    for path in sorted(base_paths - set(current)):
        errors.append(f"{path}: contract deleted. Set status: retired in a release first, remove it later.")

    for path, new in sorted(current.items()):
        text = baseline(path) if path in base_paths else None
        if text is None:
            print(f"＋ {path}: new {'draft ' if validate.is_draft(new) else ''}contract {new.id} v{new.version}")
            continue
        old = Contract(root / path, root, text=text)
        if validate.is_draft(old):
            if old.doc != new.doc:
                print(f"\n{path}: draft v{old.version} -> {new.status} v{new.version} (drafts promise nothing yet)")
            continue
        breaking, additive = validate.compare(old, new)
        if not breaking and not additive and old.doc == new.doc:
            continue
        print(f"\n{path}: v{old.version} -> v{new.version}")
        for change in breaking:
            print(f"    BREAKING  {change}")
        for change in additive:
            print(f"    additive  {change}")
        if not breaking and not additive:
            print("    no schema, rule, SLA or access changes")
        errors += [f"{path}: {e}" for e in validate.version_errors(old, new, breaking, additive)]
        if breaking:
            for d in validate.dependents(new.id, contracts):
                print(f"    IMPACT    {d.id} ({d.rel_path}) reads this product. Owner: {d.team_name}, "
                      f"{', '.join(d.support_emails)}")

    print()
    if errors:
        for e in errors:
            print(f"✖ {e}")
        sys.exit(f"\n{len(errors)} versioning problem(s).")
    print("✔ versioning is consistent")


# ── Unity Catalog: import and drift ──────────────────────────────────────────
def cmd_import(root: Path, cfg: dict, args) -> None:
    contracts = discover(root, cfg["data_products_dir"])
    print(f"Reading {args.table} from Unity Catalog ...", file=sys.stderr)
    meta = uc.read_table(args.table, args.warehouse_id)
    if not meta.exists:
        sys.exit(f"table {args.table} not found (or no permission to see it)")
    doc, todos, notes, logic = importer.draft_contract(meta, cfg, args.domain, args.layer, contracts)
    text = importer.render(doc, meta, todos, notes)
    if args.print:
        print(text)
        return
    target = Path(args.out) if args.out else (
        root / cfg["data_products_dir"] / doc["domain"] / meta.name / f"{meta.name}.odcs.yaml")
    if target.exists() and not args.force:
        sys.exit(f"{target} already exists (use --force to overwrite)")
    clash = next((c for c in contracts if c.id == doc["id"] and c.path.resolve() != target.resolve()), None)
    if clash:
        sys.exit(f"{doc['id']} already has a contract: {clash.rel_path}. Use `contractctl drift` to compare instead.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    rel = target.resolve().relative_to(root) if target.resolve().is_relative_to(root) else target
    print(f"✔ wrote {rel}  ({doc['id']} v{doc['version']}, status: draft, "
          f"{len(doc['schema'][0]['properties'])} columns)")
    if logic is not None:
        logic_path = target.parent / f"{meta.name}.logic.sql"
        if not logic_path.exists() or args.force:
            logic_path.write_text(logic.rstrip() + "\n", encoding="utf-8")
            print(f"✔ wrote {logic_path.name}")
    for n in notes:
        print(f"  note: {n}")
    print(f"\n{len(todos)} to do before it can become active:")
    for t in todos:
        print(f"  · {t}")


def _bundle_catalog(root: Path) -> str | None:
    """Default of the `catalog` variable in databricks.yml, if there is one."""
    path = root / "databricks.yml"
    if not path.exists():
        return None
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    var = (doc.get("variables") or {}).get("catalog") or {}
    return str(var["default"]) if isinstance(var, dict) and var.get("default") else None


def cmd_drift(root: Path, cfg: dict, args) -> None:
    catalog = args.catalog or _bundle_catalog(root)
    if not catalog:
        sys.exit("no catalog: pass --catalog, or set a default for the `catalog` variable in databricks.yml")
    contracts = [c for c in discover(root, cfg["data_products_dir"]) if not validate.is_draft(c)]
    if args.contracts:
        wanted = set(args.contracts)
        contracts = [c for c in contracts if {c.id, c.name, c.table, c.rel_path} & wanted]
        if not contracts:
            sys.exit(f"no contract matches {', '.join(sorted(wanted))}")
    w = uc.client()
    warehouse = uc.pick_warehouse(w, args.warehouse_id)
    total = 0
    for c in contracts:
        full = f"{catalog}.{cfg['schemas'][c.layer]}.{c.table}"
        findings = importer.drift(c, uc.read_table(full, warehouse, w), cfg)
        total += len(findings)
        mark = "✖" if findings else "✔"
        print(f"{mark} {full}  ({c.id} v{c.version}){'' if findings else '  in sync with the contract'}")
        for f in findings:
            print(f"    DRIFT  {f}")
    if total:
        sys.exit(f"\n{total} difference(s) between Unity Catalog and the contracts. "
                 "Fix the table (redeploy and run the job), or change the contract through a pull request.")


# ── new project ───────────────────────────────────────────────────────────────
def cmd_init(args) -> None:
    target = Path(args.directory)
    if not validate.SNAKE.match(args.catalog):
        sys.exit(f"catalog '{args.catalog}' must be lower snake_case")
    try:
        written = scaffold.init(target, args.catalog, args.bundle_name, args.example, args.force)
    except FileExistsError as exc:
        sys.exit("These files already exist (use --force to overwrite):\n  " + "\n  ".join(exc.args[0]))
    for rel in written:
        print(f"  wrote {rel}")
    if args.example:
        root = target.resolve()
        cfg = load_config(root)
        for rel, content in build(root, cfg, discover(root, cfg["data_products_dir"])).items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(content, encoding="utf-8")
            print(f"  wrote {rel}")
    steps = [] if target.resolve() == Path.cwd().resolve() else [f"cd {args.directory}"]
    steps += [
        "Run platform/00, 01 and 02 in the Databricks SQL editor"
        + (", then platform/50 for the example." if args.example else "."),
        "Set your workspace host and SQL warehouse in databricks.yml.",
        "Write contracts under data_products/<domain>/<product>/,\n"
        "     or start from an existing table: contractctl import <catalog.schema.table>",
        "contractctl generate && databricks bundle deploy",
    ]
    print(f"\n✔ contracts project created for catalog {args.catalog}\n\nNext steps:")
    for i, step in enumerate(steps, 1):
        print(f"  {i}. {step}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="contractctl", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    ini = sub.add_parser("init", help="start a new contracts project")
    ini.add_argument("directory", nargs="?", default=".", help="where to create it (default: here)")
    ini.add_argument("--catalog", required=True, help="Unity Catalog catalog for the data products")
    ini.add_argument("--bundle-name", help="bundle name in databricks.yml (default: the directory name)")
    ini.add_argument("--example", action="store_true", help="add two example data products (silver and gold)")
    ini.add_argument("--force", action="store_true", help="overwrite existing files")
    sub.add_parser("lint", help="validate all contracts")
    sub.add_parser("generate", help="write generated SQL and bundle resources")
    sub.add_parser("check", help="fail if generated files are stale")
    diff = sub.add_parser("diff", help="breaking-change guard against a git ref")
    diff.add_argument("--base", default="origin/main", help="git ref with the previous contract versions")
    diff.add_argument("--base-dir", help="directory with the previous versions instead of git, e.g. a Git folder on main")
    imp = sub.add_parser("import", help="draft a contract from a Unity Catalog table")
    imp.add_argument("table", help="catalog.schema.table")
    imp.add_argument("--domain", help="data domain (default: the data_domain tag, else the schema name)")
    imp.add_argument("--layer", choices=("silver", "gold"), help="default: from the schema, else silver")
    imp.add_argument("--warehouse-id", help="SQL warehouse used to read tags (default: picked automatically)")
    imp.add_argument("--out", help="output file (default: data_products/<domain>/<table>/<table>.odcs.yaml)")
    imp.add_argument("--force", action="store_true", help="overwrite an existing file")
    imp.add_argument("--print", action="store_true", help="print the draft instead of writing it")
    dr = sub.add_parser("drift", help="compare deployed tables in Unity Catalog with their contracts")
    dr.add_argument("contracts", nargs="*", help="contract ids, names or paths (default: all)")
    dr.add_argument("--catalog", help="catalog to inspect (default: the `catalog` variable in databricks.yml)")
    dr.add_argument("--warehouse-id", help="SQL warehouse used to read tags (default: picked automatically)")
    args = parser.parse_args(argv)
    if args.command == "init":
        cmd_init(args)
        return

    root = find_root()
    cfg = load_config(root)
    {"lint": cmd_lint, "generate": cmd_generate, "check": cmd_check, "diff": cmd_diff,
     "import": cmd_import, "drift": cmd_drift}[args.command](root, cfg, args)


if __name__ == "__main__":
    main()
