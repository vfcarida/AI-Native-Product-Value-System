.PHONY: help install test test-unit test-integration-real test-all lint validate-policies docker-build docker-build-sandbox docker-run demo demo-greenfield demo-brownfield clean

help:
	@echo "AI-native Product Value System - Development Commands"
	@echo ""
	@echo "  make install               Install dependencies"
	@echo "  make test                  Run standard test suite (unit + integration + eval + security)"
	@echo "  make test-unit             Run unit tests only"
	@echo "  make test-integration-real Run real integration tests (requires RUN_REAL_INTEGRATION_TESTS=1)"
	@echo "  make test-all              Run all test tiers including real integration tests"
	@echo "  make lint                  Lint code"
	@echo "  make validate-policies     Validate Cedar policies"
	@echo "  make docker-build          Build Docker image"
	@echo "  make docker-run            Run Docker container"
	@echo "  make demo                  Run greenfield/brownfield value stream demo"
	@echo "  make demo-greenfield       Run AcmeBank payment notification demo"
	@echo "  make demo-brownfield       Run AcmeBank legacy Customer API demo"
	@echo "  make clean                 Clean build artifacts"

install:
	pip install -e ".[dev]"

test:
	pytest tests/unit/ tests/integration/ tests/eval/ tests/security/ tests/policy/ -v

test-unit:
	pytest tests/unit/ -v

test-integration-real:
	RUN_REAL_INTEGRATION_TESTS=1 pytest tests/integration_real/ -v -s

test-all:
	RUN_REAL_INTEGRATION_TESTS=1 pytest tests/ -v

lint:
	ruff check src/ tests/ scripts/
	mypy src/

validate-policies:
	python scripts/validate-policy-sync.py --verbose
	pytest tests/policy/test_policy_fallback_sync.py -v

docker-build:
	docker build -t pvs:local .

docker-build-sandbox:
	docker build -t pvs/agent-sandbox:latest docker/agent-sandbox/

docker-run:
	docker run --rm -it pvs:local

demo-greenfield:
	@echo "=== Greenfield Example: Payment Notifications ==="
	@echo ""
	@echo "Problem: AcmeBank mobile app users don't receive timely payment notifications"
	@echo ""
	@echo "Running value stream..."
	pvs run examples/greenfield/problem_payment_flow.yaml
	@echo ""
	@echo "Greenfield demo complete!"
	@echo ""
	@echo "View results:"
	@echo "  pvs list"

demo-brownfield:
	@echo "=== Brownfield Example: Legacy Customer API ==="
	@echo ""
	@echo "Problem: Outdated documentation, 23% test coverage, frequent incidents"
	@echo ""
	@echo "Analyzing legacy code..."
	pvs run examples/brownfield/problem_document_legacy.yaml
	@echo ""
	@echo "Brownfield analysis complete!"
	@echo ""
	@echo "View results:"
	@echo "  pvs list"

demo:
	@if [ -z "$(EXAMPLE)" ] || [ "$(EXAMPLE)" = "greenfield/payment_flow" ]; then \
		$(MAKE) demo-greenfield; \
	elif [ "$(EXAMPLE)" = "brownfield/legacy_api" ]; then \
		$(MAKE) demo-brownfield; \
	else \
		echo "Usage: make demo EXAMPLE=<greenfield/payment_flow|brownfield/legacy_api>"; \
		exit 1; \
	fi

clean:
	rm -rf build/ dist/ *.egg-info
	rm -rf .pytest_cache/ .ruff_cache/ .mypy_cache/
	find . -type d -name __pycache__ -exec rm -rf {} +
