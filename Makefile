# AI-Native Product Value System - Makefile (EVO-033)
# Portable, self-documenting developer workflow commands (GNU Make & BSD Make).
# Run `make` or `make help` to list all available targets.

.PHONY: help install install-poetry dev-setup test test-unit test-integration \
	test-integration-real test-all test-coverage watch-test lint format type-check \
	validate-policies security-check clean demo demo-greenfield demo-brownfield \
	demo-custom docker-check docker-build docker-build-sandbox docker-run \
	env-check build publish-test publish update-deps docs-serve benchmark platform-info

# Default target shows self-documented help
.DEFAULT_GOAL := help

# Cross-platform Python detection (prefers python3, falls back to python)
PYTHON := $(shell command -v python3 2>/dev/null || command -v python 2>/dev/null || echo python)

# Operating system detection (Linux, Darwin, or Windows/Git Bash/WSL)
UNAME_S := $(shell uname -s 2>/dev/null || echo Unknown)

# Optional ANSI output colors (disabled automatically when NO_COLOR is set)
ifndef NO_COLOR
BLUE   := \033[0;34m
GREEN  := \033[0;32m
YELLOW := \033[0;33m
RED    := \033[0;31m
NC     := \033[0m
else
BLUE   :=
GREEN  :=
YELLOW :=
RED    :=
NC     :=
endif

help: ## Show this help message with all available targets
	@printf "$(BLUE)AI-Native Product Value System - Available Commands$(NC)\n\n"
	@awk 'BEGIN {FS = ":[^#]*## "} /^[a-zA-Z0-9_-]+:[^#]*## / {printf "  $(GREEN)%-24s$(NC) %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@printf "\n$(YELLOW)Quick Start:$(NC)\n"
	@printf "  1. make dev-setup               # Install dependencies & create .env\n"
	@printf "  2. make demo USE_MOCK=1         # Run offline greenfield demo ($$0)\n"
	@printf "  3. make demo                    # Run greenfield demo with API key\n"
	@printf "  4. make test                    # Run standard test suite\n\n"

# ------------------------------------------------------------------------------
# Setup & Dependency Management
# ------------------------------------------------------------------------------

install: ## Install package and development dependencies (idempotent)
	@printf "$(BLUE)Installing dependencies with $(PYTHON)...$(NC)\n"
	@$(PYTHON) -m pip install --upgrade pip
	@$(PYTHON) -m pip install -e ".[dev]"
	@printf "$(GREEN)✓ Installation complete$(NC)\n"

install-poetry: ## Install dependencies using Poetry
	@printf "$(BLUE)Installing dependencies with Poetry...$(NC)\n"
	@poetry install
	@printf "$(GREEN)✓ Poetry installation complete$(NC)\n"

dev-setup: install ## Complete local development setup (install + .env + git hooks)
	@printf "$(BLUE)Setting up development environment...$(NC)\n"
	@if [ ! -f .env ] && [ -f .env.example ]; then \
		cp .env.example .env; \
		printf "$(YELLOW)Created .env from .env.example — edit .env to add API keys.$(NC)\n"; \
	fi
	@if [ -x scripts/install-git-hooks.sh ]; then \
		./scripts/install-git-hooks.sh || true; \
	fi
	@printf "$(GREEN)✓ Development setup complete$(NC)\n"

update-deps: ## Upgrade pip and installed dependencies from requirements.txt
	@printf "$(BLUE)Updating dependencies...$(NC)\n"
	@$(PYTHON) -m pip install --upgrade pip
	@$(PYTHON) -m pip install --upgrade -r requirements.txt
	@printf "$(GREEN)✓ Dependencies updated$(NC)\n"

# ------------------------------------------------------------------------------
# Testing & Quality Checks
# ------------------------------------------------------------------------------

test: ## Run standard test suite (all non-live tests, $0)
	@printf "$(BLUE)Running standard test suite...$(NC)\n"
	@$(PYTHON) -m pytest -m "not integration_real" -v
	@printf "$(GREEN)✓ Tests passed$(NC)\n"

test-unit: ## Run unit tests only
	@printf "$(BLUE)Running unit tests...$(NC)\n"
	@$(PYTHON) -m pytest tests/unit/ -v

test-integration: ## Run mocked integration tests
	@printf "$(BLUE)Running mocked integration tests...$(NC)\n"
	@$(PYTHON) -m pytest tests/integration/ -m "not integration_real" -v

test-integration-real: docker-check env-check ## Run live integration tests (requires Docker + API key)
	@printf "$(BLUE)Running live integration tests...$(NC)\n"
	@RUN_REAL_INTEGRATION_TESTS=1 $(PYTHON) -m pytest tests/integration_real/ -v -s

test-all: ## Run all test tiers including opt-in live integration tests
	@printf "$(BLUE)Running full test suite...$(NC)\n"
	@RUN_REAL_INTEGRATION_TESTS=1 $(PYTHON) -m pytest tests/ -v

test-coverage: ## Run tests with HTML and terminal coverage report
	@printf "$(BLUE)Running tests with coverage...$(NC)\n"
	@$(PYTHON) -m pytest -m "not integration_real" --cov=product_value_system --cov-report=html --cov-report=term-missing
	@printf "$(GREEN)✓ Coverage report generated in htmlcov/index.html$(NC)\n"

watch-test: ## Watch for file changes and re-run tests automatically
	@printf "$(BLUE)Watching tests for changes...$(NC)\n"
	@$(PYTHON) -m pytest_watch tests/

lint: ## Run static linting (ruff) and type checking (mypy)
	@printf "$(BLUE)Running linters (ruff + mypy)...$(NC)\n"
	@$(PYTHON) -m ruff check src/ tests/ scripts/
	@$(PYTHON) -m mypy src/
	@printf "$(GREEN)✓ Linting and type checks passed$(NC)\n"

format: ## Format Python code using ruff
	@printf "$(BLUE)Formatting code...$(NC)\n"
	@$(PYTHON) -m ruff format src/ tests/ scripts/
	@printf "$(GREEN)✓ Code formatted$(NC)\n"

type-check: ## Run static type checker (mypy)
	@printf "$(BLUE)Running mypy type checks...$(NC)\n"
	@$(PYTHON) -m mypy src/
	@printf "$(GREEN)✓ Type checking passed$(NC)\n"

validate-policies: ## Validate Cedar policy synchronization with Python fallback
	@printf "$(BLUE)Validating Cedar policies...$(NC)\n"
	@$(PYTHON) scripts/validate-policy-sync.py --verbose
	@$(PYTHON) -m pytest tests/policy/test_policy_fallback_sync.py -v
	@printf "$(GREEN)✓ Policy validation passed$(NC)\n"

security-check: ## Run hybrid ML secret scan and adversarial security tests
	@printf "$(BLUE)Running security checks...$(NC)\n"
	@$(PYTHON) -m product_value_system.security.detect_secrets src/ config/
	@$(PYTHON) -m pytest tests/security/ -v
	@printf "$(GREEN)✓ Security checks passed$(NC)\n"

benchmark: ## Run performance benchmark tests
	@printf "$(BLUE)Running performance benchmarks...$(NC)\n"
	@$(PYTHON) -m pytest tests/ -k "performance or benchmark" -v
	@printf "$(GREEN)✓ Benchmarks complete$(NC)\n"

# ------------------------------------------------------------------------------
# Demos & Value Stream Examples
# ------------------------------------------------------------------------------

demo: env-check ## Run value stream demo (set EXAMPLE=brownfield/legacy_api or USE_MOCK=1)
	@if [ -z "$(EXAMPLE)" ] || [ "$(EXAMPLE)" = "greenfield/payment_flow" ]; then \
		$(MAKE) demo-greenfield; \
	elif [ "$(EXAMPLE)" = "brownfield/legacy_api" ]; then \
		$(MAKE) demo-brownfield; \
	else \
		printf "$(RED)Usage: make demo EXAMPLE=<greenfield/payment_flow|brownfield/legacy_api>$(NC)\n"; \
		exit 1; \
	fi

demo-greenfield: ## Run AcmeBank greenfield payment notification demo
	@printf "$(BLUE)=== Greenfield Demo: Payment Notifications ===$(NC)\n"
	@$(PYTHON) -m product_value_system.cli.main run examples/greenfield/problem_payment_flow.yaml
	@printf "$(GREEN)✓ Greenfield demo complete (inspect with: pvs list)$(NC)\n"

demo-brownfield: ## Run AcmeBank brownfield legacy Customer API demo
	@printf "$(BLUE)=== Brownfield Demo: Legacy Customer API ===$(NC)\n"
	@$(PYTHON) -m product_value_system.cli.main run examples/brownfield/problem_document_legacy.yaml
	@printf "$(GREEN)✓ Brownfield demo complete (inspect with: pvs list)$(NC)\n"

demo-custom: env-check ## Run a custom problem YAML (prompts for file path)
	@printf "$(BLUE)Running custom value stream demo...$(NC)\n"
	@printf "Enter path to problem YAML [examples/greenfield/problem_payment_flow.yaml]: "; \
	read problem_path; \
	problem_path=$${problem_path:-examples/greenfield/problem_payment_flow.yaml}; \
	$(PYTHON) -m product_value_system.cli.main run "$$problem_path"
	@printf "$(GREEN)✓ Custom demo complete$(NC)\n"

# ------------------------------------------------------------------------------
# Environment, Docker & Packaging
# ------------------------------------------------------------------------------

docker-check: ## Verify Docker daemon is running and reachable
	@printf "$(BLUE)Checking Docker daemon...$(NC)\n"
	@docker info >/dev/null 2>&1 || \
		(printf "$(RED)✗ Docker is not running. Please start Docker Desktop or Docker Engine.$(NC)\n" && exit 1)
	@printf "$(GREEN)✓ Docker is running$(NC)\n"

env-check: ## Verify OPENAI_API_KEY or ANTHROPIC_API_KEY is set (bypass with USE_MOCK=1)
	@printf "$(BLUE)Checking environment configuration...$(NC)\n"
	@if [ "$(USE_MOCK)" = "1" ]; then \
		printf "$(YELLOW)Running with deterministic Mock adapter (USE_MOCK=1)$(NC)\n"; \
	elif [ -z "$$OPENAI_API_KEY" ] && [ -z "$$ANTHROPIC_API_KEY" ]; then \
		printf "$(RED)✗ No LLM API keys detected.$(NC)\n"; \
		printf "$(YELLOW)  Set OPENAI_API_KEY or ANTHROPIC_API_KEY (see .env.example), or pass USE_MOCK=1.$(NC)\n"; \
		exit 1; \
	else \
		printf "$(GREEN)✓ API environment configured$(NC)\n"; \
	fi

docker-build: docker-check ## Build main Product Value System Docker image
	@printf "$(BLUE)Building Docker image (pvs:local)...$(NC)\n"
	@docker build -t pvs:local .
	@printf "$(GREEN)✓ Docker image built$(NC)\n"

docker-build-sandbox: docker-check ## Build isolated agent sandbox Docker image
	@printf "$(BLUE)Building agent sandbox image (pvs/agent-sandbox:latest)...$(NC)\n"
	@docker build -t pvs/agent-sandbox:latest docker/agent-sandbox/
	@printf "$(GREEN)✓ Sandbox image built$(NC)\n"

docker-run: docker-check ## Run Product Value System inside Docker container
	@printf "$(BLUE)Running Docker container...$(NC)\n"
	@docker run --rm -it \
		-v "$(CURDIR):/workspace" \
		-e OPENAI_API_KEY \
		-e ANTHROPIC_API_KEY \
		pvs:local

build: clean ## Build sdist and wheel distribution packages
	@printf "$(BLUE)Building distribution packages...$(NC)\n"
	@$(PYTHON) -m build
	@printf "$(GREEN)✓ Build complete (dist/)$(NC)\n"

publish-test: build ## Upload distribution packages to TestPyPI
	@printf "$(BLUE)Publishing to TestPyPI...$(NC)\n"
	@$(PYTHON) -m twine upload --repository testpypi dist/*
	@printf "$(GREEN)✓ Published to TestPyPI$(NC)\n"

publish: build ## Upload distribution packages to production PyPI (with confirmation)
	@printf "$(RED)Publishing to production PyPI...$(NC)\n"
	@printf "Are you sure? (y/N): "; \
	read confirm; \
	if [ "$$confirm" = "y" ] || [ "$$confirm" = "Y" ]; then \
		$(PYTHON) -m twine upload dist/*; \
		printf "$(GREEN)✓ Published to PyPI$(NC)\n"; \
	else \
		printf "$(YELLOW)Cancelled.$(NC)\n"; \
	fi

docs-serve: ## Serve project documentation locally
	@printf "$(BLUE)Serving documentation at http://localhost:8000$(NC)\n"
	@$(PYTHON) -m mkdocs serve

clean: ## Remove build artifacts, caches, and temporary files (idempotent)
	@printf "$(BLUE)Cleaning build artifacts and caches...$(NC)\n"
	@rm -rf build/ dist/ *.egg-info src/*.egg-info .pytest_cache/ .coverage htmlcov/ .mypy_cache/ .ruff_cache/ .cache/
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@printf "$(GREEN)✓ Cleanup complete$(NC)\n"

platform-info: ## Display host operating system, Python, and Make versions
	@printf "$(BLUE)Platform: $(UNAME_S)$(NC)\n"
	@$(PYTHON) --version
	@$(MAKE) --version | head -n 1
