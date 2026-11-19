"""
Path traversal prevention tests (P020).
"""

from pathlib import Path

import pytest

from product_value_system.security.path_validation import (
    PathTraversalError,
    PathValidator,
)


@pytest.fixture
def path_validator(tmp_path: Path) -> PathValidator:
    """Path validator with temporary workspace."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return PathValidator(workspace)


@pytest.mark.security
def test_rejects_parent_directory_traversal(path_validator: PathValidator) -> None:
    """Test rejection of '../' in path."""
    with pytest.raises(PathTraversalError, match=r"contains '\.\.'"):
        path_validator.validate_path("../etc/passwd")


@pytest.mark.security
def test_rejects_absolute_path(path_validator: PathValidator) -> None:
    """Test rejection of absolute paths."""
    with pytest.raises(PathTraversalError, match="Absolute paths"):
        path_validator.validate_path("/etc/passwd")


@pytest.mark.security
def test_rejects_escape_via_symlink(
    path_validator: PathValidator,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test rejection of escape via symlink."""
    workspace = path_validator.allowed_base_path
    escape = (tmp_path / "escape").resolve()
    escape.mkdir()

    symlink = workspace / "link"
    try:
        symlink.symlink_to(escape)
    except OSError:
        # On Windows without admin/developer symlink privilege, simulate symlink resolution
        orig_resolve = Path.resolve

        def fake_resolve(self: Path, strict: bool = False) -> Path:
            if self.name == "link":
                return escape
            return orig_resolve(self, strict=strict)

        monkeypatch.setattr(Path, "resolve", fake_resolve)

    with pytest.raises(PathTraversalError, match="escapes allowed base"):
        path_validator.validate_path("link")


@pytest.mark.security
def test_accepts_valid_relative_path(path_validator: PathValidator) -> None:
    """Test valid relative path accepted."""
    workspace = path_validator.allowed_base_path
    test_file = workspace / "test.txt"
    test_file.write_text("test", encoding="utf-8")

    validated = path_validator.validate_read("test.txt")
    assert validated == test_file
