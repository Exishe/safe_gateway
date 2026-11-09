"""Fail if code outside gateway/cloud/ can reach the network (ADR 0006, ADR 0010).

The check parses each file with `ast`, so a word in a comment or string never
counts, while every import spelling (`import a.b as c`, `from a import b`,
`from ..cloud import x`) does. Every path under gateway/ gets rules except
gateway/cloud/, so a new module is covered the moment it exists.
Run: `python -m tools.check_no_egress [--root DIR] [PATH ...]` (default: gateway/).
"""

import argparse
import ast
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

# Entries match a module and its submodules: "google.genai" covers "google.genai.types".
NETWORK_MODULES = frozenset(
    {
        "_socket",
        "aiohttp",
        "anthropic",
        "azure",
        "boto3",
        "botocore",
        "cohere",
        "fsspec",
        "ftplib",
        "google.cloud",
        "google.genai",
        "google.generativeai",
        "grpc",
        "http",
        "httplib2",
        "httpx",
        "huggingface_hub",
        "imaplib",
        "litellm",
        "logging.config",
        "logging.handlers",
        "mistralai",
        "ollama",
        "openai",
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
        "webbrowser",
        "websocket",
        "websockets",
        "xmlrpc",
    }
)

# The engine must not start processes, load code by name, serve HTTP, or call
# into the packages that do talk to the network.
ENGINE_ONLY_MODULES = frozenset(
    {
        "asyncio",
        "builtins",
        "concurrent.futures",
        "ctypes",
        "fastapi",
        "gateway.cloud",
        "gateway.proxy",
        "importlib",
        "marshal",
        "multiprocessing",
        "pickle",
        "_pickle",
        "pkgutil",
        "pty",
        "runpy",
        "shelve",
        # spacy.cli.download pip-installs a model; tldextract fetches the suffix list.
        "spacy.cli",
        "starlette",
        "subprocess",
        "tldextract",
        "uvicorn",
    }
)
DYNAMIC_BUILTINS = frozenset({"__import__", "eval", "exec"})
# Presidio's NlpEngineProvider downloads a spaCy model it cannot find (ADR 0012).
ENGINE_FORBIDDEN_NAMES = frozenset({"NlpEngineProvider"})
BUILTINS_NAMES = frozenset({"__builtins__", "__import__"})
OS_PROCESS_FUNCTIONS = frozenset({"system", "popen", "startfile"})
OS_PROCESS_PREFIXES = ("exec", "spawn", "posix_spawn", "fork")


@dataclass(frozen=True)
class RuleSet:
    name: str
    forbidden_modules: frozenset[str]
    forbid_dynamic_code: bool


# Third-party wrappers that start a process. pytesseract runs the tesseract binary;
# only OCR_MODULE may import it (ADR 0017 point 6).
PROCESS_WRAPPERS = frozenset({"pytesseract"})
OCR_MODULE = "gateway/parsers/ocr.py"

ENGINE_RULES = RuleSet(
    "engine", NETWORK_MODULES | ENGINE_ONLY_MODULES | PROCESS_WRAPPERS, forbid_dynamic_code=True
)
# Parsers read untrusted files: the engine's rules, no process, no dynamic code.
PARSER_RULES = RuleSet(
    "parser", NETWORK_MODULES | ENGINE_ONLY_MODULES | PROCESS_WRAPPERS, forbid_dynamic_code=True
)
OCR_RULES = RuleSet("OCR", NETWORK_MODULES | ENGINE_ONLY_MODULES, forbid_dynamic_code=True)
NO_NETWORK_RULES = RuleSet(
    "no-network", NETWORK_MODULES | PROCESS_WRAPPERS, forbid_dynamic_code=False
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
    if not relative.startswith("gateway/") or relative.startswith("gateway/cloud/"):
        return None  # outside the product, or the one package allowed outbound calls
    if relative.startswith("gateway/engine/"):
        return ENGINE_RULES
    if relative == OCR_MODULE:
        return OCR_RULES
    if relative.startswith("gateway/parsers/"):
        return PARSER_RULES
    return NO_NETWORK_RULES


def _is_forbidden(module: str, forbidden: frozenset[str]) -> bool:
    return any(module == entry or module.startswith(f"{entry}.") for entry in forbidden)


def _is_os_process_function(name: str) -> bool:
    return name in OS_PROCESS_FUNCTIONS or name.startswith(OS_PROCESS_PREFIXES)


def _resolve_from(node: ast.ImportFrom, path: str) -> str:
    if node.level == 0:
        return node.module or ""
    package = list(PurePosixPath(path).parts[:-1])
    base = package[: max(len(package) - (node.level - 1), 0)]
    return ".".join([*base, node.module] if node.module else base)


def _imported_names(node: ast.AST, path: str) -> Iterator[list[str]]:
    """One list of candidate module names per imported thing."""
    if isinstance(node, ast.Import):
        for alias in node.names:
            yield [alias.name]
    elif isinstance(node, ast.ImportFrom):
        module = _resolve_from(node, path)
        # `from gateway import cloud` imports the module gateway.cloud.
        yield [module, *(f"{module}.{alias.name}" for alias in node.names)]


def _module_aliases(tree: ast.AST) -> dict[str, str]:
    """Local name -> module for every `import a.b [as c]`: `import a.b` binds `a`."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
                else:
                    top = alias.name.split(".", 1)[0]
                    aliases[top] = top
    return aliases


def _dotted_name(node: ast.Attribute, aliases: dict[str, str]) -> str | None:
    """`sp.cli.download` -> "spacy.cli.download" when `import spacy as sp`."""
    parts: list[str] = []
    current: ast.AST = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name) or current.id not in aliases:
        return None
    return ".".join([aliases[current.id], *reversed(parts)])


def _attribute_detail(node: ast.Attribute, aliases: dict[str, str], rules: RuleSet) -> str | None:
    """`spacy.cli.download(...)` after `import spacy`: no import line names spacy.cli."""
    if node.attr in ENGINE_FORBIDDEN_NAMES:
        return f"uses {node.attr}"
    dotted = _dotted_name(node, aliases)
    if dotted is None or _is_forbidden(dotted.split(".", 1)[0], rules.forbidden_modules):
        return None  # the import itself is already reported
    if dotted == "sys.modules" or dotted.startswith("sys.modules."):
        return "uses sys.modules"
    hits = [entry for entry in rules.forbidden_modules if _is_forbidden(dotted, frozenset({entry}))]
    return f"uses '{min(hits)}'" if hits else None


def _hidden_route_detail(node: ast.AST, aliases: dict[str, str], rules: RuleSet) -> str | None:
    """Routes to the network or to code loading that a plain import check misses."""
    if isinstance(node, ast.Attribute):
        return _attribute_detail(node, aliases, rules)
    if isinstance(node, ast.ImportFrom):
        names = {alias.name for alias in node.names}
        if names & ENGINE_FORBIDDEN_NAMES:
            return f"imports {min(names & ENGINE_FORBIDDEN_NAMES)}"
        if node.module == "sys" and "modules" in names:
            return "imports sys.modules"
    if isinstance(node, ast.Name) and node.id == "__builtins__":
        return "uses __builtins__"
    if isinstance(node, ast.Constant) and node.value in BUILTINS_NAMES:
        return f"names {node.value} in a string"
    return None


def _dynamic_code_detail(node: ast.AST, os_names: set[str]) -> str | None:
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id in DYNAMIC_BUILTINS:
            return f"calls {func.id}()"
        if (
            isinstance(func, ast.Name)
            and func.id == "getattr"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in os_names
        ):
            return "calls getattr() on os"
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id in os_names
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
    except (SyntaxError, ValueError) as error:
        # Fail closed: a file we cannot read is a file we cannot vouch for.
        line = getattr(error, "lineno", None) or 1
        return [Violation(path, line, f"cannot parse file: {error}")]

    aliases = _module_aliases(tree)
    os_names = {name for name, module in aliases.items() if module == "os"}
    found: set[Violation] = set()
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 1)
        for candidates in _imported_names(node, path):
            hit = next((m for m in candidates if _is_forbidden(m, rules.forbidden_modules)), None)
            if hit:
                detail = f"imports '{hit}', not allowed in {rules.name} code"
                found.add(Violation(path, line, detail))
        if rules.forbid_dynamic_code:
            detail = _dynamic_code_detail(node, os_names) or _hidden_route_detail(
                node, aliases, rules
            )
            if detail:
                found.add(Violation(path, line, f"{detail}, not allowed in {rules.name} code"))
    return sorted(found, key=lambda v: (v.line, v.detail))


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
    # A gate that checks nothing must not pass: typos and empty targets fail.
    missing = [path for path in paths if not path.exists()]
    for path in missing:
        print(f"{path}: does not exist")
    if not missing and not any(_python_files(paths)):
        print(f"no Python files to check under {', '.join(map(str, paths))}")
        return 1

    found = check_paths([p for p in paths if p.exists()], args.root)
    for violation in found:
        print(violation)
    return 1 if found or missing else 0


if __name__ == "__main__":
    sys.exit(main())
