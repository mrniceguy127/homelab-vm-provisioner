"""Sphinx configuration for Homelab VM Provisioner."""

from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

project = "Homelab VM Provisioner"
author = "Homelab VM Provisioner"
release = "0.1.0"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

autodoc_default_options = {
    "members": True,
    "member-order": "bysource",
}
autodoc_preserve_defaults = True

napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_init_with_doc = True

html_theme = "furo"
html_title = "Homelab VM Provisioner Docs"
html_static_path = ["_static"]
html_css_files = ["custom.css"]

_DARK_CSS = {
    "color-background-primary": "#0b1020",
    "color-background-secondary": "#111827",
    "color-background-hover": "#172033",
    "color-background-border": "#22314d",
    "color-foreground-primary": "#e5e7eb",
    "color-foreground-secondary": "#cbd5e1",
    "color-foreground-muted": "#94a3b8",
    "color-brand-primary": "#7dd3fc",
    "color-brand-content": "#7dd3fc",
    "color-link": "#7dd3fc",
    "color-link-hover": "#bae6fd",
    "color-admonition-background": "#111827",
    "color-code-background": "#0f172a",
    "color-code-foreground": "#e5e7eb",
    "color-sidebar-background": "#020617",
    "color-sidebar-item-background--current": "#0f172a",
}

html_theme_options = {
    "light_css_variables": _DARK_CSS,
    "dark_css_variables": _DARK_CSS,
}


def copy_coverage_site(app, exception):
    """Copy the HTML coverage site into the built docs when available."""
    if exception is not None or app.builder.format != "html":
        return

    coverage_source = ROOT / "coverage-html"
    if not coverage_source.exists():
        return

    coverage_target = Path(app.outdir) / "coverage"
    if coverage_target.exists():
        shutil.rmtree(coverage_target)

    shutil.copytree(coverage_source, coverage_target)


def setup(app):
    """Register custom Sphinx build hooks."""
    app.connect("build-finished", copy_coverage_site)
