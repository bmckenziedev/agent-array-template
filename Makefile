PYTHON ?= python
.PHONY: render check validate lint test sanitize docs all
render:
	$(PYTHON) tools/render/render.py
check:
	$(PYTHON) tools/render/render.py --check
validate:
	@tmp=$$(mktemp -d); trap 'rm -rf "$$tmp"' EXIT; $(PYTHON) tools/render/render.py --out "$$tmp" && $(PYTHON) tools/ci/check_contracts.py --rendered "$$tmp" && bash tools/ci/validate_manifests.sh "$$tmp"
lint:
	$(PYTHON) tools/render/render.py --lint-placeholders .
test:
	$(PYTHON) tools/ci/run_tests.py
sanitize:
	$(PYTHON) tools/sanitize/scan.py --root .
docs:
	$(PYTHON) tools/ci/linkcheck.py --root .
all: render check validate lint test sanitize docs
