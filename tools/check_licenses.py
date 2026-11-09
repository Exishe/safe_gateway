"""License guard for code that ships (hard rule 1, ADR 0009).

Two modes:
- `python -m tools.check_licenses` checks every runtime dependency of the
  project (not dev tools) against tools/license_policy.toml.
- `python -m tools.check_licenses --command "uv add X"` refuses install
  commands that name a denied package. Used by the Claude Code hook.
"""

import argparse
import re
import sys
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Literal

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

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
_INSTALL_VERBS: tuple[tuple[str, ...], ...] = (
    ("uv", "add"),
    ("uv", "pip", "install"),
    ("pip", "install"),
    ("python", "-m", "pip", "install"),
)


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


def _install_arguments(command: list[str]) -> list[str]:
    for verb in _INSTALL_VERBS:
        for start in range(len(command) - len(verb) + 1):
            if tuple(command[start : start + len(verb)]) == verb:
                return command[start + len(verb) :]
    return []


def denied_names_in_command(command: str, policy: Policy) -> list[str]:
    denied: set[str] = set()
    for simple in simple_commands(command):
        for argument in _install_arguments(simple):
            if argument.startswith("-"):
                continue
            try:
                name = canonicalize_name(Requirement(argument).name)
            except InvalidRequirement:
                continue
            if name in policy.denied_packages:
                denied.add(name)
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
