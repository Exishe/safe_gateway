"""Split a shell command line into simple commands, well enough for guard rules.

This is not a shell parser. It splits on `&&`, `||`, `;`, `|` and `&`, keeps
quoted arguments together, and normalises `python3.12`/`pip3` to `python`/`pip`.
"""

import re
import shlex

_OPERATORS = frozenset({"&&", "||", ";", "|", "&", ";;", "(", ")"})
_WRAPPERS = frozenset({"sudo", "env", "command", "exec", "time", "nohup"})


def tokens(command: str) -> list[str]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return command.split()  # unbalanced quotes: fall back to a rough split


def _normalise_program(token: str) -> str:
    base = token.rsplit("/", 1)[-1]
    if re.fullmatch(r"python[\d.]*", base):
        return "python"
    if re.fullmatch(r"pip[\d.]*", base):
        return "pip"
    return base


def simple_commands(command: str) -> list[list[str]]:
    """Each simple command as [program, *arguments], wrappers and VAR=value stripped."""
    commands: list[list[str]] = [[]]
    for token in tokens(command):
        if token in _OPERATORS:
            commands.append([])
            continue
        current = commands[-1]
        if not current and ("=" in token or token in _WRAPPERS):
            continue
        current.append(_normalise_program(token) if not current else token)
    return [c for c in commands if c]
