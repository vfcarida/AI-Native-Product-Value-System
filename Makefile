.PHONY: help install test test-unit lint validate-policies docker-build docker-run clean

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

clean:
	rm -rf build/ dist/ *.egg-info
	rm -rf .pytest_cache/ .ruff_cache/ .mypy_cache/
	find . -type d -name __pycache__ -exec rm -rf {} +
