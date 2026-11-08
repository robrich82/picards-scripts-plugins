PYTHON ?= python

.PHONY: install install-plugin test

# Picard must be closed; tools/install.py checks.
install: install-plugin

install-plugin:
	$(PYTHON) tools/install.py plugin

test:
	$(PYTHON) tools/run_tests.py
