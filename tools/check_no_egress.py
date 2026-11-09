"""Fail if code outside gateway/cloud/ can reach the network (ADR 0006).

The check parses each file with `ast`, so a word in a comment or string never
counts, while every import spelling (`import a.b as c`, `from a import b`) does.
Run: `python -m tools.check_no_egress [--root DIR] [PATH ...]` (default: gateway/).
"""

import argparse
import ast
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

NETWORK_MODULES = frozenset(
    {
        "aiohttp",
        "ftplib",
        "grpc",
        "http",
        "httplib2",
        "httpx",
        "huggingface_hub",
        "imaplib",
        "paramiko",
        "poplib",
        "pycurl",
        "requests",
        "smtplib",
        "socket",
        "socketserver",
        "ssl",
        "telnetlib",
        "urllib",
        "urllib3",
        "websocket",
        "websockets",
        "xmlrpc",
    }
)

# The engine must not start processes or load code by name either: any of these
# is a way to reach the network without importing a network module.
PROCESS_AND_DYNAMIC_MODULES = frozenset(
    {"asyncio", "ctypes", "importlib", "multiprocessing", "pty", "subprocess"}
)
DYNAMIC_BUILTINS = frozenset({"__import__", "eval", "exec"})
OS_PROCESS_FUNCTIONS = frozenset({"system", "popen", "startfile"})
OS_PROCESS_PREFIXES = ("exec", "spawn")


@dataclass(frozen=True)
class RuleSet:
    name: str
    forbidden_modules: frozenset[str]
    forbid_dynamic_code: bool


ENGINE_RULES = RuleSet(
    "engine", NETWORK_MODULES | PROCESS_AND_DYNAMIC_MODULES, forbid_dynamic_code=True
)
NO_NETWORK_RULES = RuleSet("no-network", NETWORK_MODULES, forbid_dynamic_code=False)

# First matching prefix wins. gateway/cloud/ is deliberately absent: it is the
# only package allowed to make outbound requests.
RULES_BY_PREFIX: tuple[tuple[str, RuleSet], ...] = (
    ("gateway/engine/", ENGINE_RULES),
    ("gateway/parsers/", NO_NETWORK_RULES),
    ("gateway/audit/", NO_NETWORK_RULES),
    ("gateway/proxy/", NO_NETWORK_RULES),
    ("gateway/cli.py", NO_NETWORK_RULES),
)


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.detail}"


def rules_for_path(path: Path, root: Path) -> RuleSet | None:
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None
    for prefix, rules in RULES_BY_PREFIX:
        if relative.startswith(prefix):
            return rules
    return None


def _top_level(module: str) -> str:
    return module.split(".", 1)[0]


def _is_os_process_function(name: str) -> bool:
    return name in OS_PROCESS_FUNCTIONS or name.startswith(OS_PROCESS_PREFIXES)


def _imported_modules(node: ast.AST) -> Iterator[str]:
    if isinstance(node, ast.Import):
        yield from (alias.name for alias in node.names)
    elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        yield node.module


def _dynamic_code_detail(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id in DYNAMIC_BUILTINS:
            return f"calls {func.id}()"
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "os"
            and _is_os_process_function(func.attr)
        ):
            return f"calls os.{func.attr}()"
    if isinstance(node, ast.ImportFrom) and node.module == "os":
        names = [alias.name for alias in node.names if _is_os_process_function(alias.name)]
        if names:
            return f"imports os.{names[0]}"
    return None


def check_source(source: str, path: str, rules: RuleSet) -> list[Violation]:
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as error:
        # Fail closed: a file we cannot read is a file we cannot vouch for.
        return [Violation(path, error.lineno or 1, f"cannot parse file: {error.msg}")]

    found: list[Violation] = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 1)
        for module in _imported_modules(node):
            if _top_level(module) in rules.forbidden_modules:
                detail = f"imports '{module}', not allowed in {rules.name} code"
                found.append(Violation(path, line, detail))
        if rules.forbid_dynamic_code:
            detail = _dynamic_code_detail(node)
            if detail:
                found.append(Violation(path, line, f"{detail}, not allowed in {rules.name} code"))
    return sorted(found, key=lambda v: v.line)


def _python_files(paths: list[Path]) -> Iterator[Path]:
    for path in paths:
        if path.is_dir():
            yield from sorted(path.rglob("*.py"))
        elif path.suffix == ".py":
            yield path


def check_paths(paths: list[Path], root: Path) -> list[Violation]:
    found: list[Violation] = []
    for file in _python_files(paths):
        rules = rules_for_path(file, root)
        if rules is None:
            continue
        relative = file.resolve().relative_to(root.resolve()).as_posix()
        found.extend(check_source(file.read_text(encoding="utf-8"), relative, rules))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "paths", nargs="*", type=Path, help="files or directories (default: gateway/)"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root")
    args = parser.parse_args(argv)

    paths = args.paths or [args.root / "gateway"]
    found = check_paths(paths, args.root)
    for violation in found:
        print(violation)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
