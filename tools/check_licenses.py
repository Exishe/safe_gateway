"""License guard for code that ships (hard rule 1, ADR 0009).

Two modes:
- `python -m tools.check_licenses` checks every runtime dependency of the
  project (not dev tools) against tools/license_policy.toml.
- `python -m tools.check_licenses --command "uv add X"` refuses install
  commands that name a denied package. Used by the Claude Code hook.
"""

import argparse
import contextlib
import re
import sys
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import (
    InvalidSdistFilename,
    InvalidWheelFilename,
    canonicalize_name,
    parse_sdist_filename,
    parse_wheel_filename,
)

from tools.shell import simple_commands

Status = Literal["allowed", "forbidden", "review"]
_RANK: dict[Status, int] = {"allowed": 0, "review": 1, "forbidden": 2}

DEFAULT_POLICY = Path(__file__).with_name("license_policy.toml")
_PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
# Read, not repeated: a rename must not silently turn the check into a no-op.
PROJECT: str = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"]["name"]

# Permissive markers for full license texts shipped in the License field. They
# are consulted only after the whole text was searched for the policy's review
# and forbidden patterns, so "BSD, but non-commercial only" is never allowed.
_TEXT_MARKERS: tuple[tuple[tuple[str, ...], Status], ...] = (
    (("permission is hereby granted, free of charge",), "allowed"),
    (("apache license", "version 2.0"), "allowed"),
    (("redistribution and use in source and binary forms",), "allowed"),
    (("mozilla public license", "2.0"), "allowed"),
)
InstallMode = Literal["every-word", "files", "tool", "options"]
# How each install spelling names what it installs:
# every-word: every word and option value; files: requirements files;
# tool: the first word (the tool) and package options; options: only package options.
_INSTALL_VERBS: tuple[tuple[tuple[str, ...], InstallMode], ...] = (
    (("uv", "add"), "every-word"),
    (("uv", "pip", "install"), "every-word"),
    (("uv", "pip", "sync"), "files"),
    (("uv", "tool", "install"), "every-word"),
    (("uv", "tool", "run"), "tool"),
    (("uvx",), "tool"),
    (("uv", "run"), "options"),
    (("pip", "install"), "every-word"),
    (("python", "-m", "pip", "install"), "every-word"),
)
# Options whose value is the next word; the ones in _PACKAGE_OPTIONS name a package.
_VALUE_OPTIONS = frozenset(
    {
        "-c", "-f", "-i", "-p", "--cache-dir", "--config-file", "--constraint", "--constraints",
        "--default-index", "--directory", "--env-file", "--extra", "--extra-index-url",
        "--find-links", "--group", "--index", "--index-url", "--optional", "--override",
        "--overrides", "--package", "--project", "--python",
    }
)  # fmt: skip
_PACKAGE_OPTIONS = frozenset(
    {
        "-e", "-w", "-P", "--editable", "--from", "--reinstall-package", "--upgrade-package",
        "--with", "--with-editable",
    }
)  # fmt: skip
_REQUIREMENT_FILE_OPTIONS = frozenset(
    {"-r", "--requirement", "--requirements", "--with-requirements"}
)
_MAX_INCLUDE_DEPTH = 3


@dataclass(frozen=True)
class DistInfo:
    name: str
    license_expression: str | None
    license_text: str | None
    classifiers: tuple[str, ...]


@dataclass(frozen=True)
class Policy:
    allowed: frozenset[str]
    forbidden_patterns: tuple[str, ...]
    review_patterns: tuple[str, ...]
    denied_packages: frozenset[str]
    exceptions: dict[str, str] = field(default_factory=dict)


def load_policy(path: Path = DEFAULT_POLICY) -> Policy:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return Policy(
        allowed=frozenset(name.lower() for name in data["allowed"]["names"]),
        forbidden_patterns=tuple(p.lower() for p in data["forbidden"]["patterns"]),
        review_patterns=tuple(p.lower() for p in data["review"]["patterns"]),
        denied_packages=frozenset(canonicalize_name(n) for n in data["denied"]["packages"]),
        exceptions={canonicalize_name(k): v for k, v in data.get("exceptions", {}).items()},
    )


def _classify_name(name: str, policy: Policy) -> Status:
    lowered = name.strip().lower()
    if " with " in f" {lowered} ":
        return "review"  # e.g. GPL WITH a runtime exception: a person decides
    if any(pattern in lowered for pattern in policy.review_patterns):
        return "review"
    if any(pattern in lowered for pattern in policy.forbidden_patterns):
        return "forbidden"
    if lowered in policy.allowed:
        return "allowed"
    return "review"


def _strip_outer_parens(expression: str) -> str:
    text = expression.strip()
    while text.startswith("(") and text.endswith(")"):
        depth = 0
        for index, char in enumerate(text):
            depth += {"(": 1, ")": -1}.get(char, 0)
            if depth == 0 and index < len(text) - 1:
                return text  # the first "(" closes early: these are not outer parens
        text = text[1:-1].strip()
    return text


def _classify_expression(expression: str, policy: Policy) -> Status:
    text = _strip_outer_parens(expression)
    if "(" in text or ")" in text:
        return "review"  # nested expressions are rare; a person reads them
    alternatives = re.split(r"\s+OR\s+", text, flags=re.IGNORECASE)
    statuses: list[Status] = []
    for alternative in alternatives:
        parts = re.split(r"\s+AND\s+", alternative, flags=re.IGNORECASE)
        statuses.append(max((_classify_name(p, policy) for p in parts), key=_RANK.__getitem__))
    return min(statuses, key=_RANK.__getitem__)


def _classifier_licenses(classifiers: Iterable[str]) -> list[str]:
    names = []
    for classifier in classifiers:
        parts = [part.strip() for part in classifier.split("::")]
        if parts[0] == "License" and len(parts) >= 2 and parts[-1] != "OSI Approved":  # noqa: PLR2004
            names.append(parts[-1])
    return names


def _classify_classifiers(names: list[str], policy: Policy) -> Status:
    statuses = {_classify_name(name, policy) for name in names}
    if statuses == {"allowed"}:
        return "allowed"
    if "allowed" in statuses:
        return "review"  # permissive and copyleft listed together: a person decides
    return "forbidden" if "forbidden" in statuses else "review"


def _classify_text(text: str, policy: Policy) -> Status:
    if "\n" not in text.strip() and len(text) <= 200:  # noqa: PLR2004
        return _classify_expression(text, policy)
    lowered = text.lower()
    if any(pattern in lowered for pattern in policy.review_patterns):
        return "review"
    if any(pattern in lowered for pattern in policy.forbidden_patterns):
        return "forbidden"
    for markers, status in _TEXT_MARKERS:
        if all(marker in lowered for marker in markers):
            return status
    return "review"


def _classify_metadata(dist: DistInfo, policy: Policy) -> tuple[Status, str]:
    if dist.license_expression:
        expression = dist.license_expression
        return _classify_expression(expression, policy), f"License-Expression: {expression}"
    classifier_names = _classifier_licenses(dist.classifiers)
    if classifier_names:
        joined = ", ".join(classifier_names)
        return _classify_classifiers(classifier_names, policy), f"classifiers: {joined}"
    if dist.license_text and dist.license_text.strip():
        first_line = dist.license_text.strip().splitlines()[0][:80]
        return _classify_text(dist.license_text, policy), f"License: {first_line}"
    return "review", "no license metadata"


def classify(dist: DistInfo, policy: Policy) -> tuple[Status, str]:
    name = canonicalize_name(dist.name)
    if name in policy.denied_packages:
        return "forbidden", "on the deny list in tools/license_policy.toml"
    status, reason = _classify_metadata(dist, policy)
    if status == "review" and name in policy.exceptions:
        return "allowed", f"exception: {policy.exceptions[name]}"
    return status, reason


def load_dist_info(name: str) -> DistInfo:
    meta = metadata.metadata(name)
    return DistInfo(
        name=meta["Name"],
        license_expression=meta.get("License-Expression"),
        license_text=meta.get("License"),
        classifiers=tuple(meta.get_all("Classifier") or ()),
    )


def runtime_closure(root: str) -> list[str]:
    """Names of every installed runtime dependency of `root`, excluding `root` itself."""
    # Each package is visited with the extras it was asked for, so a dependency
    # needed only through an extra (uvicorn[standard], CacheControl[filecache])
    # is part of the closure.
    extras_seen: dict[str, set[str]] = {}
    queue: list[tuple[str, frozenset[str]]] = [(canonicalize_name(root), frozenset())]
    while queue:
        current, extras = queue.pop()
        try:
            requirements = metadata.requires(current) or []
        except metadata.PackageNotFoundError:
            continue  # reported later by load_dist_info as "not installed"
        environments = [{"extra": extra} for extra in {"", *extras}]
        for line in requirements:
            requirement = Requirement(line)
            marker = requirement.marker
            if marker and not any(marker.evaluate(env) for env in environments):
                continue
            name = canonicalize_name(requirement.name)
            first_visit = name not in extras_seen
            known = extras_seen.setdefault(name, set())
            new_extras = set(requirement.extras) - known
            if first_visit or new_extras:
                known.update(new_extras)
                queue.append((name, frozenset(known)))
    extras_seen.pop(canonicalize_name(root), None)
    return sorted(extras_seen)


def check(dists: Iterable[DistInfo], policy: Policy) -> list[str]:
    problems = []
    for dist in dists:
        status, reason = classify(dist, policy)
        if status == "forbidden":
            problems.append(f"{dist.name}: forbidden ({reason})")
        elif status == "review":
            problems.append(
                f"{dist.name}: needs review ({reason}); add a reasoned entry under "
                "[exceptions] in tools/license_policy.toml or pick another package"
            )
    return problems


def _install_arguments(command: list[str]) -> tuple[InstallMode, list[str]] | None:
    for verb, mode in _INSTALL_VERBS:
        for start in range(len(command) - len(verb) + 1):
            if tuple(command[start : start + len(verb)]) == verb:
                return mode, command[start + len(verb) :]
    return None


def _requirements_in_file(path: Path, depth: int = 0) -> list[str]:
    """Each requirement a requirements file names, following `-r` includes."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []  # nothing to install from a file that is not there
    found: list[str] = []
    for line in lines:
        text = line.split(" #", 1)[0].strip()
        if not text or text.startswith("#"):
            continue
        words = text.split()
        option, _, value = words[0].partition("=")
        value = value or (words[1] if len(words) > 1 else "")
        if option in _REQUIREMENT_FILE_OPTIONS and depth < _MAX_INCLUDE_DEPTH:
            found.extend(_requirements_in_file(path.parent / value, depth + 1))
        elif option in {"-e", "--editable"}:
            found.append(value)
        elif not text.startswith("-"):
            found.append(text)
    return found


def _install_specs(mode: InstallMode, arguments: list[str], base: Path) -> list[str]:
    """What an install command names: requirements, URLs, paths, wheel files."""
    specs: list[str] = []
    tool_seen = mode != "tool"
    words = iter(arguments)
    for word in words:
        if word.startswith("-"):
            option, has_value, value = word.partition("=")
            takes_value = option in _VALUE_OPTIONS | _PACKAGE_OPTIONS | _REQUIREMENT_FILE_OPTIONS
            if takes_value and not has_value:
                value = next(words, "")
            if option in _REQUIREMENT_FILE_OPTIONS:
                specs.extend(_requirements_in_file(base / value))
            elif option in _PACKAGE_OPTIONS or (mode == "every-word" and value):
                specs.append(value)
        elif mode == "files":
            specs.extend(_requirements_in_file(base / word))
        elif mode == "every-word":
            specs.append(word)
        elif not tool_seen:
            specs.append(word)
            tool_seen = True
        else:
            break  # the words after the tool are its own arguments
    return specs


def _candidate_names(spec: str) -> set[str]:
    """Every package name `spec` could install: `pkg==1`, a git URL, a wheel or a directory."""
    names: set[str] = set()
    with contextlib.suppress(InvalidRequirement):
        names.add(Requirement(spec).name)
    egg = re.search(r"[#&]egg=([\w.-]+)", spec)
    if egg:
        names.add(egg.group(1))
    path = urlsplit(spec).path if "://" in spec else spec
    for part in re.split(r"[/\\]", path):
        segment = part.split("@", 1)[0].removesuffix(".git")
        if segment.endswith(".whl"):
            with contextlib.suppress(InvalidWheelFilename):
                names.add(parse_wheel_filename(segment)[0])
        elif segment.endswith((".tar.gz", ".zip")):
            with contextlib.suppress(InvalidSdistFilename):
                names.add(parse_sdist_filename(segment)[0])
        names.add(segment)
    return {canonicalize_name(name) for name in names if name}


def denied_names_in_command(command: str, policy: Policy, base: Path | None = None) -> list[str]:
    """Denied packages that `command` would install; requirements files are read from `base`."""
    base = base or Path.cwd()
    denied: set[str] = set()
    for simple in simple_commands(command):
        found = _install_arguments(simple)
        if found is None:
            continue
        for spec in _install_specs(*found, base):
            denied.update(_candidate_names(spec) & policy.denied_packages)
    return sorted(denied)


def _check_command(command: str, policy: Policy) -> int:
    denied = denied_names_in_command(command, policy)
    for name in denied:
        print(
            f"blocked: {name} is on the license deny list (AGPL or non-commercial). "
            "Use the permissive alternative from the brief, or add it to "
            "docs/APPROVAL_NEEDED.md if a paid license is the only option."
        )
    return 1 if denied else 0


def _check_project(project: str, policy: Policy) -> int:
    try:
        metadata.distribution(project)
    except metadata.PackageNotFoundError:
        print(f"{project}: not installed (run `uv sync`); nothing was checked")
        return 1
    problems = []
    dists = []
    names = runtime_closure(project)
    for name in names:
        try:
            dists.append(load_dist_info(name))
        except metadata.PackageNotFoundError:
            problems.append(f"{name}: not installed (run `uv sync`)")
    problems.extend(check(dists, policy))
    for problem in problems:
        print(problem)
    if not problems:
        print(f"licenses ok: {len(names)} runtime packages checked")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="License guard for shipped code.")
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--project", default=PROJECT)
    parser.add_argument("--command", help="an install command to vet instead of the project")
    args = parser.parse_args(argv)

    policy = load_policy(args.policy)
    if args.command is not None:
        return _check_command(args.command, policy)
    return _check_project(args.project, policy)


if __name__ == "__main__":
    sys.exit(main())
