"""Split a shell command line into simple commands, well enough for guard rules.

This is not a shell parser. It splits on `&&`, `||`, `;`, `|`, `&` and unquoted
newlines, keeps quoted arguments together, and normalises `python3.12`/`pip3` to
`python`/`pip`. Commands the shell would also run are added as commands of their
own: `$(...)` and backticks outside single quotes, `bash -c PAYLOAD`, `eval ...`
and `find -exec ...`. Heredoc bodies are data, so they are dropped.
"""

import re
import shlex

# Runs of these characters separate commands. A newline separates commands in a
# shell script, so `cd /tmp\nrm -rf x` is two commands, not one.
_OPERATOR_CHARS = "();<>|&\n"
_STRIPPED_PREFIXES = frozenset({"sudo", "env", "command", "exec", "time", "nohup"})
_MAX_DEPTH = 4

# Commands that run another command: `sudo -u root rm`, `timeout 5 curl`, `uv run curl`.
WRAPPERS = frozenset(
    {
        "busybox", "caffeinate", "chronic", "command", "doas", "env", "exec", "ionice",
        "nice", "nohup", "npx", "run", "stdbuf", "strace", "sudo", "time", "timeout",
        "toybox", "uv", "uvx", "watch", "xargs",
    }
)  # fmt: skip
# Options of those wrappers that take the next word as their value.
_WRAPPER_VALUE_OPTIONS: dict[str, frozenset[str]] = {
    "caffeinate": frozenset({"-t", "-w"}),
    "doas": frozenset({"-u", "-C"}),
    "env": frozenset({"-C", "-u", "-S", "--chdir", "--unset", "--split-string"}),
    "exec": frozenset({"-a"}),
    "ionice": frozenset({"-c", "-n", "-p", "-P", "-u", "--class", "--classdata"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "npx": frozenset({"-p", "--package", "-c", "--call"}),
    "run": frozenset({"--with", "--python", "-p", "--from", "--project", "--directory"}),
    "stdbuf": frozenset({"-i", "-o", "-e", "--input", "--output", "--error"}),
    "strace": frozenset({"-o", "-e", "-p", "-s", "-u", "-E", "-P", "-a"}),
    "sudo": frozenset(
        {"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-T", "-U", "--user", "--group"}
    ),
    "time": frozenset({"-f", "-o", "--format", "--output"}),
    "timeout": frozenset({"-s", "-k", "--signal", "--kill-after"}),
    "uvx": frozenset({"--with", "--python", "-p", "--from"}),
    "watch": frozenset({"-n", "--interval"}),
    "xargs": frozenset({"-I", "-L", "-n", "-P", "-d", "-E", "-s", "-a"}),
}
SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh", "mksh", "fish", "ash"})
_FIND_EXEC = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
_HEREDOC = re.compile(r"(?<!<)<<(?!<)-?[ \t]*(['\"]?)([A-Za-z_][\w.-]*)\1")


def tokens(command: str) -> list[str]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=_OPERATOR_CHARS)
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return command.split()  # unbalanced quotes: fall back to a rough split


def normalise_program(token: str) -> str:
    base = token.rsplit("/", 1)[-1].lstrip("$")  # `$'curl'` reaches the lexer as `$curl`
    if re.fullmatch(r"python[\d.]*", base):
        return "python"
    if re.fullmatch(r"pip[\d.]*", base):
        return "pip"
    return base


def _matching_paren(text: str, start: int) -> int:
    """Index of the `)` closing the `(` at `start`, or the end of the text."""
    depth = 0
    for index in range(start, len(text)):
        depth += {"(": 1, ")": -1}.get(text[index], 0)
        if depth == 0:
            return index
    return len(text)


def substitutions(command: str) -> list[str]:
    """Bodies of `$(...)` and backticks outside single quotes: the shell runs them."""
    found: list[str] = []
    in_single = in_double = False
    index = 0
    while index < len(command):
        char = command[index]
        if in_single:
            in_single = char != "'"
        elif char == "\\":
            index += 1  # the next character is escaped
        elif char == "'" and not in_double:
            in_single = True
        elif char == '"':
            in_double = not in_double
        elif char == "`":
            end = command.find("`", index + 1)
            end = len(command) if end < 0 else end
            found.append(command[index + 1 : end])
            index = end
        elif command.startswith("$(", index) and not command.startswith("$((", index):
            end = _matching_paren(command, index + 1)
            found.append(command[index + 2 : end])
            index = end
        index += 1
    return found


def _strip_heredocs(command: str) -> str:
    """Drop heredoc bodies; an unquoted body keeps only the substitutions the shell runs."""
    kept: list[str] = []
    pending: list[tuple[str, bool]] = []  # (delimiter, quoted) still awaited, in order
    for line in command.split("\n"):
        if pending:
            delimiter, quoted = pending[0]
            if line.strip() == delimiter:
                pending.pop(0)
            elif not quoted:
                kept.extend(f"$({body})" for body in substitutions(line))
            continue
        kept.append(line)
        pending = [(m.group(2), bool(m.group(1))) for m in _HEREDOC.finditer(line)]
    return "\n".join(kept)


def _split(command: str) -> list[list[str]]:
    commands: list[list[str]] = [[]]
    for token in tokens(command):
        if token and all(char in _OPERATOR_CHARS for char in token):
            commands.append([])
        else:
            commands[-1].append(token)
    return [c for c in commands if c]


def _is_wrapper_argument(token: str) -> bool:
    return (
        token.startswith("-")
        or "=" in token
        or re.fullmatch(r"\d+(\.\d+)?[smhd]?", token) is not None
    )


def unwrap(simple: list[str]) -> list[str]:
    """The command a chain of wrappers runs, its program normalised.

    `sudo -u root timeout -s KILL 5 rm -rf x` runs `rm -rf x`.
    """
    index = 0
    while index < len(simple) and (
        "=" in simple[index] or normalise_program(simple[index]) in WRAPPERS
    ):
        wrapper = normalise_program(simple[index])
        index += 1
        value_options = _WRAPPER_VALUE_OPTIONS.get(wrapper, frozenset())
        while index < len(simple) and _is_wrapper_argument(simple[index]):
            index += 2 if simple[index] in value_options else 1
    inner = simple[index:]
    return [normalise_program(inner[0]), *inner[1:]] if inner else []


def _shell_payload(arguments: list[str]) -> str | None:
    """PAYLOAD of `bash [options] -c PAYLOAD`."""
    runs_payload = False
    skip_value = False
    for argument in arguments:
        if skip_value:
            skip_value = False
        elif argument in {"-o", "+o", "-O", "+O"}:
            skip_value = True
        elif argument.startswith("-") and not argument.startswith("--"):
            runs_payload = runs_payload or "c" in argument
        elif argument.startswith("--"):
            continue
        else:
            return argument if runs_payload else None
    return None


def _nested_commands(inner: list[str]) -> list[str]:
    """Command lines that this (unwrapped) simple command hands to a shell or runs."""
    if not inner:
        return []
    program, arguments = inner[0], inner[1:]
    if any(char.isspace() for char in program):
        return [" ".join(inner)]  # `watch 'rm -rf x'` hands its words to sh -c
    if program in SHELLS:
        payload = _shell_payload(arguments)
        return [payload] if payload is not None else []
    if program == "eval":
        return [" ".join(arguments)]
    if program == "find":
        nested = []
        for index, argument in enumerate(arguments):
            if argument in _FIND_EXEC:
                rest = arguments[index + 1 :]
                end = next((i for i, a in enumerate(rest) if a in {";", "+"}), len(rest))
                nested.append(shlex.join(rest[:end]))
        return nested
    return []


def raw_commands(command: str, depth: int = 0) -> list[list[str]]:
    """Each simple command as written, nested ones included; VAR=value and wrappers kept."""
    text = _strip_heredocs(command)
    found = _split(text)
    if depth >= _MAX_DEPTH:
        return found
    nested = list(substitutions(text))
    for simple in found:
        nested.extend(_nested_commands(unwrap(simple)))
    for line in nested:
        found.extend(raw_commands(line, depth + 1))
    return found


def simple_commands(command: str) -> list[list[str]]:
    """Each simple command as [program, *arguments], leading wrappers and VAR=value stripped."""
    commands = []
    for simple in raw_commands(command):
        index = 0
        while index < len(simple) and ("=" in simple[index] or simple[index] in _STRIPPED_PREFIXES):
            index += 1
        if index < len(simple):
            commands.append([normalise_program(simple[index]), *simple[index + 1 :]])
    return commands
