"""Sphinx configuration for tilearray documentation."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tilearray import __version__  # noqa: E402

project = "tilearray"
author = "Matthew Whittle"
copyright = f"{datetime.now(tz=timezone.utc).year}, {author}"
version = __version__
release = __version__

extensions = [
    "myst_nb",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.intersphinx",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx_copybutton",
    "sphinx_design",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

html_theme = "pydata_sphinx_theme"
html_static_path = ["_static"]
html_title = "tilearray"
html_theme_options = {
    "github_url": "https://github.com/MatthewJWhittle/tilearray",
    "show_toc_level": 2,
    "navigation_with_keys": False,
    "logo": {"text": "tilearray"},
}

myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "fieldlist",
]
myst_heading_anchors = 3

# Execute vignettes in CI/local builds without hitting live tile hosts.
# Offline notebooks mock HTTP; live demos stay out of the docs build.
nb_execution_mode = "force"
nb_execution_timeout = 120
nb_execution_raise_on_error = True
nb_merge_streams = True

autosummary_generate = True
autodoc_typehints = "description"
autodoc_member_order = "bysource"
napoleon_google_docstring = True
napoleon_numpy_docstring = True

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "numpy": ("https://numpy.org/doc/stable/", None),
    "xarray": ("https://docs.xarray.dev/en/stable/", None),
    "dask": ("https://docs.dask.org/en/stable/", None),
}

copybutton_prompt_text = r">>> |\.\.\. |\$ |In \[\d*\]: | {2,5}\.\.\.: | {5,8}: "
copybutton_prompt_is_regexp = True
