"""
Linux iptables chain and rule manager for Docker sandbox egress filtering (EVO-029).

Features:
- Strict chain name validation (alphanumeric/underscore/hyphen, <= 28 chars)
- Safe ``shlex.split()`` argument tokenization (preserving quoted ``--log-prefix``)
- Automatic ``DOCKER-USER`` hook attachment and clean detachment before chain deletion
- Platform detection (Linux vs. non-Linux) and graceful error reporting
- Injectable command runner for deterministic cross-platform unit & integration tests
"""

from __future__ import annotations

import hashlib
import logging
import platform
import re
import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_CHAIN_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,28}$")
_SAFE_ARG_RE = re.compile(r"^[A-Za-z0-9_.:/\- ,]+$")


class IptablesError(RuntimeError):
    """Raised when an iptables command fails or receives invalid arguments."""


@dataclass
class CommandResult:
    """Normalized subprocess execution outcome."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


CommandRunner = Callable[[list[str]], CommandResult]


def sanitize_chain_name(container_id: str, prefix: str = "SANDBOX_") -> str:
    """Derive a collision-free, valid iptables chain name (<= 28 chars) for ``container_id``."""
    digest = hashlib.sha256(container_id.encode("utf-8")).hexdigest()[:12]
    clean_prefix = re.sub(r"[^A-Za-z0-9_-]", "", prefix)[:16] or "SANDBOX_"
    chain_name = f"{clean_prefix}{digest}"
    validate_chain_name(chain_name)
    return chain_name


def validate_chain_name(chain_name: str) -> None:
    """Validate that ``chain_name`` is safe for iptables CLI invocation."""
    if not _CHAIN_NAME_RE.match(chain_name):
        raise IptablesError(
            f"Invalid iptables chain name {chain_name!r}: must match ^[A-Za-z0-9_-]{{1,28}}$"
        )


def _default_subprocess_runner(cmd: list[str]) -> CommandResult:
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )
    except FileNotFoundError as exc:
        return CommandResult(returncode=127, stdout="", stderr=str(exc))
    except subprocess.TimeoutExpired as exc:
        return CommandResult(returncode=124, stdout="", stderr=f"Command timed out: {exc}")
    except OSError as exc:
        return CommandResult(returncode=1, stdout="", stderr=str(exc))


class IptablesManager:
    """Manage dedicated iptables chains and rules for sandbox egress isolation."""

    def __init__(
        self,
        *,
        iptables_bin: str = "iptables",
        runner: CommandRunner | None = None,
        require_linux: bool = True,
    ) -> None:
        self.iptables_bin = iptables_bin
        self._runner: CommandRunner = runner or _default_subprocess_runner
        self._require_linux = require_linux
        self.chains_created: list[str] = []
        self._attached_jumps: dict[str, list[tuple[str, list[str]]]] = {}
        self.command_history: list[list[str]] = []

    def _run(self, args: list[str], *, check: bool = True) -> CommandResult:
        cmd = [self.iptables_bin, *args]
        self.command_history.append(list(cmd))
        result = self._runner(cmd)
        if check and result.returncode != 0:
            raise IptablesError(
                f"iptables command failed (rc={result.returncode}): {' '.join(cmd)} | "
                f"{result.stderr.strip()}"
            )
        return result

    def is_available(self) -> bool:
        """Return True if iptables is installed and runnable on this platform."""
        if self._require_linux and platform.system().lower() != "linux":
            return False
        res = self._run(["--version"], check=False)
        return res.returncode == 0

    def create_chain(self, chain_name: str) -> None:
        """Create ``chain_name`` or flush it if it already exists."""
        validate_chain_name(chain_name)
        exists = self._run(["-L", chain_name, "-n"], check=False)
        if exists.returncode == 0:
            logger.warning("Chain %s already exists; flushing existing rules", chain_name)
            self.flush_chain(chain_name)
        else:
            self._run(["-N", chain_name], check=True)
            logger.info("Created iptables chain: %s", chain_name)

        if chain_name not in self.chains_created:
            self.chains_created.append(chain_name)

    def add_rule(self, chain_name: str, rule: str | list[str]) -> None:
        """Append a validated rule to ``chain_name`` (supports quoted tokens via ``shlex``)."""
        validate_chain_name(chain_name)
        tokens = list(rule) if isinstance(rule, list) else shlex.split(rule)
        if not tokens:
            raise IptablesError("Cannot add an empty iptables rule")
        for tok in tokens:
            if not _SAFE_ARG_RE.match(tok):
                raise IptablesError(f"Unsafe character in iptables rule token: {tok!r}")

        self._run(["-A", chain_name, *tokens], check=True)
        logger.debug("Added rule to %s: %s", chain_name, tokens)

    def attach_to_parent_chain(
        self,
        parent_chain: str,
        target_chain: str,
        match_args: list[str],
    ) -> None:
        """Insert a jump rule from ``parent_chain`` (e.g. ``DOCKER-USER``) to ``target_chain``."""
        validate_chain_name(parent_chain)
        validate_chain_name(target_chain)
        rule_args = [*match_args, "-j", target_chain]
        self._run(["-I", parent_chain, "1", *rule_args], check=True)
        self._attached_jumps.setdefault(target_chain, []).append((parent_chain, rule_args))

    def detach_from_parent_chains(self, target_chain: str) -> None:
        """Remove any previously inserted jump rules referencing ``target_chain``."""
        validate_chain_name(target_chain)
        jumps = self._attached_jumps.pop(target_chain, [])
        for parent_chain, rule_args in jumps:
            self._run(["-D", parent_chain, *rule_args], check=False)

    def flush_chain(self, chain_name: str) -> None:
        """Remove all rules from ``chain_name``."""
        validate_chain_name(chain_name)
        self._run(["-F", chain_name], check=True)
        logger.info("Flushed iptables chain: %s", chain_name)

    def list_rules(self, chain_name: str) -> list[str]:
        """Return rule lines currently present in ``chain_name``."""
        validate_chain_name(chain_name)
        res = self._run(["-S", chain_name], check=True)
        return [line.strip() for line in res.stdout.splitlines() if line.strip()]

    def delete_chain(self, chain_name: str) -> None:
        """Detach jump hooks, flush, and delete ``chain_name``."""
        validate_chain_name(chain_name)
        try:
            self.detach_from_parent_chains(chain_name)
            self.flush_chain(chain_name)
            self._run(["-X", chain_name], check=True)
            logger.info("Deleted iptables chain: %s", chain_name)
        except IptablesError as exc:
            logger.error("Failed to delete iptables chain %s: %s", chain_name, exc)
            raise
        finally:
            if chain_name in self.chains_created:
                self.chains_created.remove(chain_name)

    def cleanup_all(self) -> None:
        """Clean up every chain created by this manager."""
        for chain_name in list(self.chains_created):
            try:
                self.delete_chain(chain_name)
            except Exception as exc:
                logger.error("Error cleaning up chain %s: %s", chain_name, exc)

    def get_summary(self) -> dict[str, Any]:
        return {
            "iptables_bin": self.iptables_bin,
            "active_chains": list(self.chains_created),
            "commands_executed": len(self.command_history),
        }
