"""contractctl: compile ODCS data contracts into a governed Databricks medallion pipeline."""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("contractctl")
except PackageNotFoundError:  # running from a source checkout without installing
    __version__ = "0.0.0"
