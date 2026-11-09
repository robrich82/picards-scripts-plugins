# Picard must be closed; tools/install.py checks.
PYTHON ?= python

.PHONY: install install-plugin install-scripts test

install:
	$(PYTHON) tools/install.py all

install-plugin:
	$(PYTHON) tools/install.py plugin

install-scripts:
	$(PYTHON) tools/install.py scripts

test:
	$(PYTHON) tools/run_tests.py
