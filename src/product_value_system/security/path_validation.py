"""
Path traversal prevention.
"""

from pathlib import Path


class PathTraversalError(Exception):
    """Path traversal attack detected."""


class PathValidator:
    """
    Validates file paths to prevent traversal attacks.
    """

    def __init__(self, allowed_base_path: Path | str) -> None:
        """
        Initialize path validator.

        Args:
            allowed_base_path: Base path that all operations must stay within
        """
        self.allowed_base_path = Path(allowed_base_path).resolve()

    def validate_path(self, path: str) -> Path:
        """
        Validate and canonicalize a file path.

        Args:
            path: Path to validate (relative or absolute)

        Returns:
            Resolved path within allowed base

        Raises:
            PathTraversalError: If path escapes allowed base
        """
        if ".." in path:
            raise PathTraversalError("Path contains '..' (traversal attempt)")

        if path.startswith(("/", "\\")) or Path(path).is_absolute():
            raise PathTraversalError("Absolute paths not allowed")

        try:
            full_path = (self.allowed_base_path / path).resolve()
        except (ValueError, OSError) as e:
            raise PathTraversalError(f"Invalid path: {e}") from e

        try:
            full_path.relative_to(self.allowed_base_path)
        except ValueError as e:
            raise PathTraversalError(
                f"Path escapes allowed base: {full_path} not in {self.allowed_base_path}"
            ) from e

        return full_path

    def validate_read(self, path: str) -> Path:
        """Validate path for read operations."""
        full_path = self.validate_path(path)

        if not full_path.exists():
            raise PathTraversalError(f"Path does not exist: {full_path}")

        if not full_path.is_file():
            raise PathTraversalError(f"Path is not a file: {full_path}")

        return full_path

    def validate_write(self, path: str) -> Path:
        """Validate path for write operations."""
        full_path = self.validate_path(path)

        if not full_path.parent.exists():
            raise PathTraversalError(
                f"Parent directory does not exist: {full_path.parent}"
            )

        return full_path
