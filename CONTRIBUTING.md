# Contributing to AI-Native Product Value System

Thank you for your interest in contributing to the **AI-Native Product Value System**!

This repository is an educational reference implementation demonstrating AI-native product development from problem statement to validated outcome with built-in governance, policy enforcement, sandboxed execution, and quality evaluation.

---

## Development Setup

### Prerequisites

- **Python**: 3.12 or higher
- **Git**: 2.30+
- **Docker**: Docker Desktop or Docker Engine (for live container sandbox tests)

### Local Installation

```bash
# 1. Clone the repository
git clone https://github.com/<org>/ai-native-product-value-system.git
cd ai-native-product-value-system

# 2. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1

# 3. Install package with development dependencies
pip install -e ".[dev]"

# 4. Install Git pre-commit secret scanning hook
./scripts/install-git-hooks.sh
```

---

## Quality Standards

Every pull request must satisfy these checks before merging:

1. **Linting (`ruff`)**:
   ```bash
   ruff check src/ tests/ scripts/
   ```
2. **Type Checking (`mypy`)**:
   ```bash
   mypy src/
   ```
3. **Unit, Integration, Evaluation, and Security Tests (`pytest`)**:
   ```bash
   pytest tests/ -v --cov=product_value_system
   ```
4. **Secret Scanning**:
   ```bash
   python scripts/pre-commit-secret-scan.py
   ```
5. **Policy Validation**:
   ```bash
   make validate-policies
   ```

---

## How to Extend the System

Follow our task-oriented [How-To Guides](docs/howto/README.md):

- [Create a Custom Agent](docs/howto/create-custom-agent.md)
- [Define Cedar Policies](docs/howto/define-policies.md)
- [Write Evaluations (Judges)](docs/howto/write-evaluations.md)
- [Integrate a New Model Provider](docs/howto/integrate-model-provider.md)
- [Extend the Policy Engine](docs/howto/extend-policy-engine.md)

---

## Pull Request Process

1. Create a feature branch from `main` (`git checkout -b feat/your-feature`).
2. Add or update unit, integration, or security tests covering your change.
3. Update relevant documentation in `docs/reference/` or `docs/howto/`.
4. Run `./scripts/final-audit.sh` to verify all checks pass.
5. Open a Pull Request with a clear summary of the problem and solution.

---

## Security & Vulnerability Reporting

- **Never commit API keys, tokens, or credentials.** Always use environment variables or synthetic placeholders.
- To report a security vulnerability, follow the instructions in [SECURITY.md](SECURITY.md).

---

## License

By contributing to this repository, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
