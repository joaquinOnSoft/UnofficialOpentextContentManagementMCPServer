# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

PYTHON ?= python

.PHONY: install format lint type test test-security audit bandit check

install:
	$(PYTHON) -m pip install -e ".[dev]"

format:
	$(PYTHON) -m ruff format .

lint:
	$(PYTHON) -m ruff check .

type:
	$(PYTHON) -m mypy

test:
	$(PYTHON) -m pytest

# Security modules must keep 100 % line and branch coverage.
test-security:
	$(PYTHON) -m pytest -q --no-cov-on-fail --cov-fail-under=0 --cov-report= && \
	$(PYTHON) -m coverage report --include="*/otcs_mcp/security.py,*/otcs_mcp/config.py,*/otcs_mcp/tools/common.py,*/otcs_mcp/http_app.py,*/otcs_mcp/logging_setup.py,*/otcs_mcp/tls.py" --fail-under=100

audit:
	$(PYTHON) -m pip_audit

bandit:
	$(PYTHON) -m bandit -q -r src

check: format lint type test test-security bandit
