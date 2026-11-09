# Picard must be closed; tools/install.py checks.
PYTHON ?= python

.PHONY: install install-plugin test

install: install-plugin

install-plugin:
	$(PYTHON) tools/install.py plugin

test:
	$(PYTHON) tools/run_tests.py
