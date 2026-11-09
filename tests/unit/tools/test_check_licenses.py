from dataclasses import replace
from pathlib import Path

import pytest

from tools.check_licenses import (
    DistInfo,
    Policy,
    check,
    classify,
    denied_names_in_command,
    load_dist_info,
    load_policy,
    main,
    runtime_closure,
)

POLICY = load_policy(Path(__file__).resolve().parents[3] / "tools" / "license_policy.toml")

MIT_TEXT = (
    "MIT License\n\nCopyright (c) 2020 Someone\n\n"
    "Permission is hereby granted, free of charge, to any person obtaining a copy..."
)
GPL_TEXT = "GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007\n\nEveryone is permitted to copy..."


def dist(
    name: str = "somepkg",
    expression: str | None = None,
    text: str | None = None,
    classifiers: tuple[str, ...] = (),
) -> DistInfo:
    return DistInfo(name, expression, text, classifiers)


def status(info: DistInfo, policy: Policy = POLICY) -> str:
    return classify(info, policy)[0]


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("MIT", "allowed"),
        ("Apache-2.0 OR BSD-3-Clause", "allowed"),
        ("(Apache-2.0 OR BSD-2-Clause)", "allowed"),
        ("MPL-2.0", "allowed"),
        ("AGPL-3.0-only", "forbidden"),
        ("GPL-3.0-or-later", "forbidden"),
        ("CC-BY-NC-4.0", "forbidden"),
        ("MIT AND GPL-2.0-only", "forbidden"),
        ("LGPL-3.0-only", "review"),
        ("GPL-2.0-or-later WITH Classpath-exception-2.0", "review"),
        ("(MIT OR Apache-2.0) AND GPL-2.0-only", "review"),
        ("Some-Unknown-License", "review"),
    ],
)
def test_license_expression(expression: str, expected: str) -> None:
    assert status(dist(expression=expression)) == expected


def test_mit_classifier_is_allowed() -> None:
    info = dist(classifiers=("License :: OSI Approved :: MIT License",))
    assert status(info) == "allowed"


def test_mixed_permissive_and_gpl_classifiers_need_review() -> None:
    info = dist(
        classifiers=(
            "License :: OSI Approved :: MIT License",
            "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
        )
    )
    assert status(info) == "review"


def test_gpl_only_classifier_is_forbidden() -> None:
    info = dist(classifiers=("License :: OSI Approved :: GNU Affero General Public License v3",))
    assert status(info) == "forbidden"


def test_short_license_text_is_read_as_a_name() -> None:
    assert status(dist(text="BSD-3-Clause")) == "allowed"
    assert status(dist(text="Apache 2.0")) == "allowed"


def test_full_license_texts_are_recognised() -> None:
    assert status(dist(text=MIT_TEXT)) == "allowed"
    assert status(dist(text=GPL_TEXT)) == "forbidden"


def test_no_license_metadata_needs_review() -> None:
    assert status(dist()) == "review"


def test_expression_wins_over_classifiers() -> None:
    info = dist(expression="MIT", classifiers=("License :: OSI Approved :: GNU GPL v3",))
    assert status(info) == "allowed"


@pytest.mark.parametrize("name", ["PyMuPDF", "pymupdf", "fitz", "pdftext"])
def test_denied_packages_are_forbidden_whatever_their_metadata(name: str) -> None:
    assert status(dist(name=name, expression="MIT")) == "forbidden"


def test_exception_turns_review_into_allowed() -> None:
    policy = replace(POLICY, exceptions={"somepkg": "reviewed: LGPL, dynamically linked"})
    assert status(dist(expression="LGPL-3.0-only"), policy) == "allowed"


def test_exception_never_overrides_forbidden() -> None:
    policy = replace(POLICY, exceptions={"somepkg": "please"})
    assert status(dist(expression="AGPL-3.0-only"), policy) == "forbidden"


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ('uv add "PyMuPDF[all]==1.24" rich', ["pymupdf"]),
        ("pip install pdftext", ["pdftext"]),
        ("python -m pip install --upgrade PyMuPDF", ["pymupdf"]),
        ("cd app && uv add --group dev pymupdf4llm", ["pymupdf4llm"]),
        ("uv pip install fitz; echo done", ["fitz"]),
        ("uv add rich", []),
        ("echo pymupdf", []),
        ("git commit -m 'drop pymupdf'", []),
    ],
)
def test_denied_names_in_command(command: str, expected: list[str]) -> None:
    assert denied_names_in_command(command, POLICY) == expected


def test_runtime_closure_follows_requirements_and_skips_inactive_markers() -> None:
    closure = runtime_closure("pytest")

    assert {"pluggy", "iniconfig", "packaging", "pygments"} <= set(closure)
    assert "pytest" not in closure  # the root itself is not its own dependency
    assert "exceptiongroup" not in closure  # only for Python < 3.11
    assert "hypothesis" not in closure  # only in the "dev" extra


def test_check_reports_forbidden_and_review_packages() -> None:
    problems = check(
        [
            dist("good", expression="MIT"),
            dist("bad", expression="AGPL-3.0-only"),
            dist("odd"),
        ],
        POLICY,
    )

    assert len(problems) == 2
    assert problems[0].startswith("bad: forbidden")
    assert problems[1].startswith("odd: needs review")


def test_project_runtime_dependencies_pass() -> None:
    dists = [load_dist_info(name) for name in runtime_closure("safeguard-gateway")]
    assert check(dists, POLICY) == []


def test_main_passes_on_this_project() -> None:
    assert main([]) == 0


def test_main_blocks_a_denied_install_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--command", "uv add PyMuPDF"]) == 1
    assert "pymupdf" in capsys.readouterr().out


def test_main_allows_a_clean_install_command() -> None:
    assert main(["--command", "uv add rich"]) == 0


# ---- review fixes (Important #7, #8, #9)

BSD_NON_COMMERCIAL = (
    "Copyright (c) 2021 Lab\n\nRedistribution and use in source and binary forms, with or "
    "without modification, are permitted for NON-COMMERCIAL research purposes only..."
)
MIT_COMMONS_CLAUSE = MIT_TEXT + '\n\n"Commons Clause" License Condition v1.0\nThe Software...'
APACHE_NC_WEIGHTS = (
    "Apache License\nVersion 2.0, January 2004\n...\nModel weights are released under CC-BY-NC-4.0."
)


@pytest.mark.parametrize("text", [BSD_NON_COMMERCIAL, MIT_COMMONS_CLAUSE, APACHE_NC_WEIGHTS])
def test_full_text_with_a_non_commercial_restriction_is_forbidden(text: str) -> None:
    assert status(dist(text=text)) == "forbidden"


def test_runtime_closure_follows_requirements_pulled_in_through_extras() -> None:
    # pip-audit needs CacheControl[filecache], and only that extra needs filelock.
    assert "filelock" in runtime_closure("pip-audit")


def test_main_fails_when_the_project_is_not_installed(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--project", "safeguard-gatewya"]) == 1
    assert "not installed" in capsys.readouterr().out


def test_project_name_comes_from_pyproject() -> None:
    import tomllib  # noqa: PLC0415

    pyproject = Path(__file__).resolve().parents[3] / "pyproject.toml"
    expected = tomllib.loads(pyproject.read_text())["project"]["name"]

    from tools import check_licenses  # noqa: PLC0415

    assert expected == check_licenses.PROJECT


# ---- security review 2 (M8): other install spellings


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("uv add git+https://github.com/pymupdf/PyMuPDF", ["pymupdf"]),
        ("uv add git+https://github.com/pymupdf/PyMuPDF.git@1.24.0", ["pymupdf"]),
        ("pip install 'https://example.com/x.zip#egg=pdftext'", ["pdftext"]),
        ("uv add ./PyMuPDF-1.0-py3-none-any.whl", ["pymupdf"]),
        ("pip install dist/pdftext-0.3.1.tar.gz", ["pdftext"]),
        ("uv add --editable ./PyMuPDF", ["pymupdf"]),
        ("uvx --from pymupdf python -c 1", ["pymupdf"]),
        ("uvx pymupdf4llm@0.1", ["pymupdf4llm"]),
        ("uv tool install pymupdf", ["pymupdf"]),
        ("uv tool run --with=pdftext rich", ["pdftext"]),
        ("uv run --with pymupdf python -c 1", ["pymupdf"]),
        ("uv pip install --upgrade-package=pymupdf rich", ["pymupdf"]),
        ("bash -c 'uv add pymupdf'", ["pymupdf"]),
        ("uv run pytest -k pymupdf", []),
        ("uv tool install ruff", []),
    ],
)
def test_denied_names_in_other_install_spellings(command: str, expected: list[str]) -> None:
    assert denied_names_in_command(command, POLICY) == expected


def test_requirements_files_are_read_and_vetted(tmp_path: Path) -> None:
    (tmp_path / "reqs.txt").write_text("rich>=13  # a comment\n-r more.txt\n\n")
    (tmp_path / "more.txt").write_text("--hash=sha256:00\nPyMuPDF==1.24 ; python_version>'3'\n")

    assert denied_names_in_command("pip install -r reqs.txt", POLICY, tmp_path) == ["pymupdf"]
    assert denied_names_in_command("uv add --requirements=more.txt", POLICY, tmp_path) == [
        "pymupdf"
    ]
    assert denied_names_in_command("uv pip sync more.txt", POLICY, tmp_path) == ["pymupdf"]


def test_a_missing_requirements_file_is_not_an_error(tmp_path: Path) -> None:
    assert denied_names_in_command("pip install -r nowhere.txt", POLICY, tmp_path) == []
