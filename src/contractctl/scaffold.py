"""
`contractctl init`: write a new contracts project from the templates shipped with the package.

  templates/project   contractctl.yml, databricks.yml, platform SQL, CI workflow, editor settings
  templates/example   two example data products (a silver and a gold contract) and their row filter

Placeholders: {{catalog}}, {{bundle_name}}, {{version}}.
"""
from __future__ import annotations

from importlib import resources
from pathlib import Path

from . import __version__

# Package data cannot reliably contain dot-files: they are stored without the dot.
RENAMES = {"github": ".github", "vscode": ".vscode", "gitignore": ".gitignore"}


def _walk(node, prefix: tuple[str, ...] = ()):
    for child in sorted(node.iterdir(), key=lambda c: c.name):
        if child.name == "__pycache__":
            continue
        if child.is_dir():
            yield from _walk(child, (*prefix, child.name))
        else:
            yield (*prefix, child.name), child


def template_files(example: bool = False) -> dict[str, str]:
    """Relative path in the new project -> template text (placeholders not yet filled in)."""
    root = resources.files("contractctl") / "templates"
    out: dict[str, str] = {}
    for part in ("project", "example") if example else ("project",):
        for parts, file in _walk(root / part):
            rel = "/".join(RENAMES.get(p, p) if i == 0 else p for i, p in enumerate(parts))
            out[rel] = file.read_text(encoding="utf-8")
    return out


def render(text: str, catalog: str, bundle_name: str) -> str:
    return (text.replace("{{catalog}}", catalog)
                .replace("{{bundle_name}}", bundle_name)
                .replace("{{version}}", __version__))


def init(target: Path, catalog: str, bundle_name: str | None = None, example: bool = False,
         force: bool = False) -> list[str]:
    """Write the project. Returns the relative paths written. Refuses to overwrite unless force."""
    bundle_name = bundle_name or target.resolve().name
    files = {rel: render(text, catalog, bundle_name) for rel, text in template_files(example).items()}
    existing = sorted(rel for rel in files if (target / rel).exists())
    if existing and not force:
        raise FileExistsError(existing)
    for rel, text in files.items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return sorted(files)
