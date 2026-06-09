# Minimal makefile for Sphinx documentation

SPHINXBUILD ?= sphinx-build
SOURCEDIR = .
BUILDDIR = _build

.PHONY: help Makefile html clean

help:
	@$(SPHINXBUILD) -M help "$(SOURCEDIR)" "$(BUILDDIR)"

html:
	@$(SPHINXBUILD) -M html "$(SOURCEDIR)" "$(BUILDDIR)"

clean:
	@$(SPHINXBUILD) -M clean "$(SOURCEDIR)" "$(BUILDDIR)"
