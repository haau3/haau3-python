"""scripts/check_public_text.py finds each kind of text this repository never publishes, and allows the
shapes its own text uses. The samples are made up."""

from __future__ import annotations

import subprocess
import sys

import pytest
from check_public_text import check, commit_range, term_rules

from .helpers import ROOT


def found(text: str) -> list[str]:
    return [f.match for f in check("sample", text)]


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("see haau3/private-thing for why", "haau3/private-thing"),
        ("at https://github.com/haau3/private-thing/issues", "github.com/haau3/private-thing"),
        ("git@github.com:haau3/private-thing.git", "github.com:haau3/private-thing.git"),
        ("it runs in haau3-example-project", "haau3-example-project"),
        ("decided in issue #123", "#123"),
        ("see issue 123", "issue 123"),
        ("this is ABC-4", "ABC-4"),
        ("part of the milestone", "milestone"),
        ("the owner approves it", "the owner"),
        ("after each deploy", "deploy"),
        ("the key is an org secret", "org secret"),
        ("Iv23liEXAMPLEexample00", "Iv23liEXAMPLEexample00"),
        ("pypi-AgEIcHlwaS5vcmcEXAMPLEexampleEXAMPLE", "pypi-AgEIcHlwaS5vcmcEXAMPLEexampleEXAMPLE"),
        ("write to someone@personal.example-mail.net", "someone@personal.example-mail.net"),
    ],
)
def test_it_finds_each_kind(text: str, match: str) -> None:
    assert match in found(text)


def test_it_allows_what_this_repositorys_own_text_uses() -> None:
    text = "\n".join(
        [
            "pip install haau3, from haau3/haau3-python#3, opened by haau3-bot[bot] as haau3-python/0.1.1.",
            "Also for TypeScript: haau3-node at https://github.com/haau3/haau3-node, and haau3-go to come.",
            "PHQ-9 and UTF-8, the plan H2406-013, pydantic/httpx2#1153 and uv 0.13.1.",
            "* the API's spec at 1.0.0-preview.4 ([#5](https://github.com/haau3/haau3-python/issues/5))",
            "Write to security@haau3.com. Co-Authored-By: Claude <noreply@anthropic.com>",
            "The version lives in src/haau3/_version.py, and https://proxy.example.test/haau3/v1 is a base URL.",
        ]
    )
    assert found(text) == []


def test_a_changelog_link_only_to_this_repository() -> None:
    assert found("([#5](https://github.com/haau3/haau3-python/issues/5))") == []
    assert found("([#5](https://github.com/haau3/haau3-node/issues/5))") == ["#5"]


def test_extra_terms_from_a_file_of_patterns_with_comments() -> None:
    rules = term_rules("# a comment\nsecret-codename\n\n  other\\d+  # trailing comment\n")
    assert [r.pattern.pattern for r in rules] == ["secret-codename", "other\\d+"]
    assert [f.match for f in check("sample", "the Secret-Codename and other42", rules)] == [
        "Secret-Codename",
        "other42",
    ]


def test_a_repositorys_first_push_checks_every_commit() -> None:
    assert commit_range("no-such-ref-anywhere..HEAD") == "HEAD"
    assert commit_range("HEAD..HEAD") == "HEAD..HEAD"


def test_this_repositorys_own_files_pass() -> None:
    # The real run, as CI and a pull request use it: it fails on any finding.
    result = subprocess.run(
        [sys.executable, "-I", "scripts/check_public_text.py"], cwd=ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
