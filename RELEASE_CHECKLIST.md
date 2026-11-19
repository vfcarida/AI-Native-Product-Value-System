# Release Checklist: v0.1.0

**Date**: 2026-11-18  
**Version**: 0.1.0 (Initial Release)

## Pre-Release Verification

### Code Quality

- [x] All tests passing (unit, integration, eval, security)
- [x] No failing CI jobs
- [x] Code linted (`ruff` clean)
- [x] Type checks passing (`mypy` clean)
- [x] Test coverage >= 70% (`79%` measured across `src/product_value_system`)

**Verification**:

```bash
pytest tests/ -v --cov=product_value_system --cov-report=term-missing
ruff check src/ tests/ scripts/
mypy src/
```

---

### Security

- [x] Threat model complete ([docs/THREAT_MODEL.md](docs/THREAT_MODEL.md))
- [x] No hardcoded secrets in source code
- [x] Pre-commit hooks installed and tested
- [x] CI secret scanning enabled (`.github/workflows/secret-scan.yml`)
- [x] Dependency versions pinned (`requirements.txt`, `pyproject.toml`)
- [x] Dockerfile uses non-root user (`pvs`, UID 1000)
- [x] Container security best practices followed

**Verification**:

```bash
./scripts/final-audit.sh
python scripts/pre-commit-secret-scan.py
```

---

### Documentation

- [x] `README.md` complete and accurate
- [x] `ARCHITECTURE.md` and `docs/ARCHITECTURE.md` complete
- [x] `docs/THREAT_MODEL.md` complete
- [x] API reference complete (`docs/reference/`)
- [x] How-to guides complete (`docs/howto/`)
- [x] Architecture diagrams created (`docs/architecture/diagrams/`)
- [x] `LICENSE` file present (Apache 2.0)
- [x] `CONTRIBUTING.md` created
- [x] `CODE_OF_CONDUCT.md` created
- [x] `CHANGELOG.md` created

**Verification**:

```bash
ls docs/ARCHITECTURE.md docs/THREAT_MODEL.md docs/SECURITY.md
ls docs/reference/README.md docs/howto/README.md
```

---

### Examples

- [x] Greenfield example works (`examples/greenfield/`)
- [x] Brownfield example works (`examples/brownfield/`)
- [x] Examples documented in `README.md`
- [x] Examples include expected outputs and golden datasets

**Verification**:

```bash
pvs run examples/greenfield/problem_payment_flow.yaml
pvs run examples/brownfield/problem_document_legacy.yaml
```

---

### Performance

- [x] Performance baseline established
- [x] Cost baseline documented (cost per run)
- [x] Latency baseline documented (time per stage)
- [x] Test suite runtime acceptable (<5 minutes; measured `1m 10s`)

**Baseline**:

- **Cost per run**: `$0.00` (deterministic `--mock` mode) / `~$2.45` estimated (live GPT-4 greenfield example)
- **Latency**: `<1.5s` (`--mock` mode) / `~15m 32s` estimated (live GPT-4 discovery → release)
- **Test suite**: `1m 09.92s` (`142 passed, 7 skipped`, `79%` coverage)

---

### Licensing

- [x] `LICENSE` file present (Apache 2.0)
- [x] All dependencies use compatible permissive licenses (MIT, BSD, Apache 2.0)
- [x] Third-party attributions documented
- [x] Copyright notices present

**Verification**:

```bash
grep "Apache" LICENSE
```

---

## Release Preparation

### Version Bump

- [x] Version updated in `pyproject.toml` (`0.1.0`)
- [x] Version updated in `src/product_value_system/__version__.py` (`0.1.0`)
- [x] Changelog updated ([CHANGELOG.md](CHANGELOG.md))

**Files verified**:

```bash
# pyproject.toml
version = "0.1.0"

# src/product_value_system/__version__.py
__version__ = "0.1.0"
```

---

### Git Tagging

- [x] All changes verified
- [x] Tag ready: `v0.1.0`

**Commands**:

```bash
git tag -a v0.1.0 -m "Release v0.1.0: Initial reference implementation"
git push origin v0.1.0
```

---

### GitHub Release

- [x] Release notes generated ([RELEASE_NOTES.md](RELEASE_NOTES.md))
- [ ] GitHub release published (`v0.1.0`)

---

## Post-Release Verification

### Fresh Installation

- [x] Fresh install script created (`scripts/test-fresh-install.sh`)
- [x] Examples run successfully
- [x] Documentation links verified

**Test**:

```bash
./scripts/test-fresh-install.sh
```

---

## Known Issues

1. **Issue**: Live Docker sandbox integration tests (`tests/integration/test_sandbox.py`) require a running local Docker daemon and `pvs/agent-sandbox:latest` image.
   - **Severity**: Low
   - **Workaround**: Tests automatically skip when the Docker daemon is offline, while unit and orchestrator tests use `FakeDockerClient` / `MockSandboxProvider`.

---

## Sign-Off

**Release Manager**: AI-Native Product Value System Team — Date: 2026-11-18  
**Security Reviewer**: AI-Native Product Value System Team — Date: 2026-11-18  
**Tech Lead**: AI-Native Product Value System Team — Date: 2026-11-18

---

**Release Status**: ⬜ Not Started | ⬜ In Progress | ✅ Ready | ⬜ Released
