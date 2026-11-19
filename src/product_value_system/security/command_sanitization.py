"""
Command injection prevention.
"""

import shlex


class CommandInjectionError(Exception):
    """Command injection attack detected."""


class CommandSanitizer:
    """
    Sanitizes shell commands to prevent injection.
    """

    ALLOWED_COMMANDS = {
        "git",
        "pytest",
        "python3",
        "make",
        "docker",
        "ls",
        "cat",
        "echo",
        "mkdir",
        "rm",
    }

    SHELL_METACHARACTERS = {
        ";",
        "&",
        "|",
        "`",
        "$",
        "(",
        ")",
        "<",
        ">",
        "\n",
        "\r",
        "&&",
        "||",
        ">>",
        "<<",
        "$(",
    }

    def sanitize_command(self, command: str) -> list[str]:
        """
        Sanitize and parse a shell command.

        Args:
            command: Command string to sanitize

        Returns:
            List of command arguments (safe to pass to subprocess)

        Raises:
            CommandInjectionError: If command is unsafe
        """
        for metachar in self.SHELL_METACHARACTERS:
            if metachar in command:
                raise CommandInjectionError(
                    f"Command contains shell metacharacter: {metachar}"
                )

        try:
            parts = shlex.split(command)
        except ValueError as e:
            raise CommandInjectionError(f"Invalid command syntax: {e}") from e

        if not parts:
            raise CommandInjectionError("Empty command")

        base_command = parts[0]
        if base_command not in self.ALLOWED_COMMANDS:
            raise CommandInjectionError(
                f"Command not allowed: {base_command} (allowlist: {self.ALLOWED_COMMANDS})"
            )

        return parts

    def build_safe_command(self, base: str, args: list[str]) -> list[str]:
        """
        Build a safe command from base and arguments.

        Args:
            base: Base command (must be in allowlist)
            args: Command arguments

        Returns:
            Safe command list (for subprocess.run)

        Raises:
            CommandInjectionError: If command is unsafe
        """
        if base not in self.ALLOWED_COMMANDS:
            raise CommandInjectionError(f"Command not allowed: {base}")

        for arg in args:
            for metachar in self.SHELL_METACHARACTERS:
                if metachar in arg:
                    raise CommandInjectionError(
                        f"Argument contains shell metacharacter: {arg}"
                    )

        return [base, *args]
