"""Input validation for raw debugger and GUI escape hatches."""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Iterable

from .config import Settings

_DANGEROUS_SHELL = re.compile(r"[;&|`$<>]|\$\(|\n|\r|\x00")
_COMMAND_NAME = re.compile(r"^[A-Za-z?][A-Za-z0-9?_-]*$")
_GDB_COMMAND_NAME = re.compile(r"^-?[A-Za-z?][A-Za-z0-9?_-]*$")
_GDB_FORBIDDEN = re.compile(
    r"^(?:!|shell\b|pipe\b|make\b|python(?:-interactive)?\b|guile\b|"
    r"source\b|define\b|document\b|commands\b|while\b|if\b|compile\b|"
    r"edit\b|set\s+environment\b|unset\s+environment\b)",
    re.IGNORECASE,
)
_GDB_INTERPRETER_ESCAPE = re.compile(
    r"^-interpreter-exec\s+(?:console|mi)\s+.*(?:shell|python|guile|source|pipe|!)",
    re.IGNORECASE,
)
_GDB_ALLOWED_MI = (
    "-break-",
    "-data-",
    "-exec-",
    "-stack-",
    "-thread-",
    "-var-",
    "-symbol-",
    "-list-",
    "-gdb-show",
    "-gdb-set",
)
_GDB_ALLOWED_CLI = {
    "advance", "backtrace", "bt", "break", "clear", "continue", "delete",
    "disable", "disassemble", "display", "down", "enable", "finish", "frame",
    "info", "interrupt", "jump", "next", "nexti", "print", "ptype", "return",
    "run", "set", "step", "stepi", "tbreak", "thread", "undisplay", "until",
    "up", "watch", "rwatch", "awatch", "where", "x", "monitor",
}


class UnsafeCommand(ValueError):
    pass


def validate_raw_command(command: str) -> str:
    command = command.strip()
    if not command:
        raise UnsafeCommand("raw command cannot be empty")
    if _DANGEROUS_SHELL.search(command):
        raise UnsafeCommand("shell control characters are not allowed")
    first = command.split(maxsplit=1)[0]
    if not _COMMAND_NAME.fullmatch(first):
        raise UnsafeCommand(f"invalid command name: {first!r}")
    return command


def validate_gdb_command(command: str) -> str:
    """Validate a GDB CLI/MI command without involving a host shell.

    GDB/MI commands intentionally start with ``-`` (for example
    ``-exec-continue``), while ordinary GDB commands do not. Keeping this
    validator separate prevents the broader spelling from leaking into the
    Commander escape hatch.
    """

    command = command.strip()
    if not command:
        raise UnsafeCommand("GDB command cannot be empty")
    if len(command) > 8192 or any(character in command for character in "\n\r\x00"):
        raise UnsafeCommand("GDB command is too large or contains control characters")
    # GDB expressions legitimately contain $, &, *, <, >, and C operators.
    # No shell is used by the backend, so block GDB's own host-execution entry
    # points instead of applying Commander shell-character rules.
    if _GDB_FORBIDDEN.search(command) or _GDB_INTERPRETER_ESCAPE.search(command):
        raise UnsafeCommand("GDB host-code and shell escape commands are not allowed")
    first = command.split(maxsplit=1)[0]
    command_name = "x" if re.fullmatch(r"x/[0-9]*[a-zA-Z]+", first) else first
    if not _GDB_COMMAND_NAME.fullmatch(command_name):
        raise UnsafeCommand(f"invalid GDB command name: {first!r}")
    if command_name == "-interpreter-exec":
        if not re.fullmatch(
            r'-interpreter-exec\s+console\s+"monitor(?:\s+[^"\r\n]*)?"',
            command,
            re.IGNORECASE,
        ):
            raise UnsafeCommand("only remote monitor interpreter commands are allowed")
    elif command_name.startswith("-"):
        if not any(command_name.startswith(prefix) for prefix in _GDB_ALLOWED_MI):
            raise UnsafeCommand(f"GDB/MI command is not allowlisted: {first!r}")
    elif command_name.lower() not in _GDB_ALLOWED_CLI:
        raise UnsafeCommand(f"GDB command is not allowlisted: {first!r}")
    if re.match(r"^set\s+(?:environment|exec-wrapper|auto-load|sysroot|solib-search-path)\b", command, re.I):
        raise UnsafeCommand("GDB host environment/path mutation is not allowed")
    return command


def validate_raw_commands(
    commands: Iterable[str], *, settings: Settings | None = None, limit: int = 256
) -> list[str]:
    validated = [validate_raw_command(command) for command in commands]
    if not validated:
        raise UnsafeCommand("at least one command is required")
    if len(validated) > limit:
        raise UnsafeCommand(f"too many commands; maximum is {limit}")
    if settings:
        for command in validated:
            _validate_embedded_paths(command, settings)
    return validated


def _validate_embedded_paths(command: str, settings: Settings) -> None:
    """Confine absolute and relative file operands without interpreting a shell."""

    try:
        tokens = shlex.split(command, posix=True)
    except ValueError as exc:
        raise UnsafeCommand(f"invalid command quoting: {exc}") from exc
    for token in tokens[1:]:
        candidate = token.rstrip(",")
        if "=" in candidate:
            candidate = candidate.split("=", 1)[1]
        if candidate.startswith(("/", "./", "../")):
            path = Path(candidate)
            try:
                settings.resolve_allowed_path(path, must_exist=path.exists())
            except (OSError, ValueError) as exc:
                raise UnsafeCommand(f"path is outside configured roots: {candidate}") from exc


def validate_application_args(args: Iterable[str], settings: Settings) -> list[str]:
    result: list[str] = []
    for arg in args:
        if _DANGEROUS_SHELL.search(arg) or "\x00" in arg:
            raise UnsafeCommand(f"unsafe application argument: {arg!r}")
        candidate = arg.split("=", 1)[1] if "=" in arg else arg
        if candidate.startswith("/") or candidate.startswith("."):
            path = Path(candidate)
            # Paths may point to either user firmware or persistent evidence.
            # Never pass an unresolved absolute/relative path to a SEGGER
            # application merely because it does not exist yet.
            settings.resolve_allowed_path(path, must_exist=path.exists())
        result.append(arg)
    if len(result) > 256:
        raise UnsafeCommand("too many application arguments")
    return result
