"""The workflows: what runs where, what is pinned, and the traps already met."""

from __future__ import annotations

import re

import pytest

from .helpers import ROOT

WORKFLOWS = {p.name: p.read_text() for p in (ROOT / ".github" / "workflows").glob("*.yml")}


def test_there_are_four() -> None:
    assert sorted(WORKFLOWS) == ["ci.yml", "public-text.yml", "regenerate.yml", "release.yml"]


@pytest.mark.parametrize("name", sorted(WORKFLOWS))
def test_every_action_is_pinned_by_its_commit(name: str) -> None:
    for line in re.findall(r"uses: (\S+)", WORKFLOWS[name]):
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", line), f"{name}: {line}"


@pytest.mark.parametrize("name", sorted(WORKFLOWS))
def test_no_from_json_in_a_step_env_and_no_grep_in_a_pipe(name: str) -> None:
    text = WORKFLOWS[name]
    # GitHub evaluates a step's env before its `if`, so fromJSON of an empty output fails the job.
    for block in re.findall(r"\n\s+env:\n((?:\s{8,}.*\n)+)", text):
        assert "fromJSON" not in block, name
    # Under pipefail, a grep that stops early can fail the command feeding it.
    assert not re.search(r"\|\s*grep\b", text), name


def test_ci_runs_every_supported_python_and_the_required_check_names() -> None:
    ci = WORKFLOWS["ci.yml"]
    assert 'python: ["3.10", "3.11", "3.12", "3.13", "3.14", "3.15"]' in ci
    assert "name: Tests on Python ${{ matrix.python }}" in ci
    assert "name: Generated code, types, lint and package" in ci
    assert "name: Public text" in WORKFLOWS["public-text.yml"]
    assert "continue-on-error: true" in ci.split("name: ty (reports only)", 1)[1]
    assert "--resolution lowest-direct" in ci


def test_the_public_text_check_runs_on_edits_and_never_on_pull_request_target() -> None:
    text = WORKFLOWS["public-text.yml"]
    assert "types: [opened, edited, reopened, synchronize]" in text
    assert "pull_request_target" not in text
    assert "--text-env PR_TITLE --text-env PR_BODY" in text


def test_regeneration_mints_the_token_after_the_generator_and_checks_oasdiff() -> None:
    text = WORKFLOWS["regenerate.yml"]
    assert text.index("scripts/generate.py") < text.index("create-github-app-token")
    assert text.index("uv sync") < text.index("create-github-app-token")
    assert 'OASDIFF_VERSION: "1.33.0"' in text
    assert 'OASDIFF_SHA256: "43a4e328e2d13ba1552d760aa68d2485c75c5621f309f6ff64ae895188345247"' in text
    assert text.index("sha256sum --check --strict") < text.index('"$RUNNER_TEMP/oasdiff" changelog')


def test_publishing_runs_no_repository_code_while_it_holds_the_oidc_token() -> None:
    text = WORKFLOWS["release.yml"]
    publish = text.split("  publish:", 1)[1]
    assert "environment: pypi" in publish
    assert "id-token: write" in publish
    assert re.findall(r"uses: ([\w/.-]+)@", publish) == ["actions/download-artifact", "pypa/gh-action-pypi-publish"]
    assert "run:" not in publish
    assert text.count("id-token: write") == 1
    build = text.split("  build:", 1)[1].split("  publish:", 1)[0]
    assert "uv build" in build
    assert "id-token" not in build
