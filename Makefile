.PHONY: help install test test-unit lint validate-policies docker-build docker-build-sandbox docker-run demo demo-greenfield demo-brownfield clean

help:
	@echo "AI-native Product Value System - Development Commands"
	@echo ""
	@echo "  make install            Install dependencies"
	@echo "  make test               Run all tests"
	@echo "  make test-unit          Run unit tests only"
	@echo "  make lint               Lint code"
	@echo "  make validate-policies  Validate Cedar policies"
	@echo "  make docker-build       Build Docker image"
	@echo "  make docker-run         Run Docker container"
	@echo "  make demo               Run greenfield/brownfield value stream demo"
	@echo "  make demo-greenfield    Run AcmeBank payment notification demo"
	@echo "  make demo-brownfield    Run AcmeBank legacy Customer API demo"
	@echo "  make clean              Clean build artifacts"

install:
	pip install -e ".[dev]"

test:
	pytest tests/ -v

test-unit:
	pytest tests/unit/ -v

lint:
	ruff check src/ tests/
	mypy src/

validate-policies:
	python -c "from product_value_system.policy.engine import PolicyEngine; PolicyEngine('policies'); print('Cedar policies loaded and validated')"

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
