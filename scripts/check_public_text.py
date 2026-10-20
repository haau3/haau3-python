"""Checks that what this repository publishes says only what this repository shows: its files, the
messages of commits not yet on main, and a pull request's title and description. Everything here is
public, so nothing in it refers to work, people, accounts or places outside this repository.

    python3 -I scripts/check_public_text.py                     the tracked files
    python3 -I scripts/check_public_text.py --commits origin/main..HEAD --text-env PR_TITLE --text-env PR_BODY
    python3 -I scripts/check_public_text.py --no-files --text-file body.md --terms <file>

--terms adds one regular expression per line (case-insensitive, `#` starts a comment), for words
that are fine to keep out of this file but must not be published either. It exits 1 on any match,
and 2 when it cannot run (a bad option or a term that is not a regular expression).

It uses only the standard library, so it runs before anything is installed. Its patterns are
compiled with re.ASCII, so \\w, \\d and \\b mean what they mean in the same check written for
JavaScript.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

THIS_REPOSITORY = "haau3/haau3-python"


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]
    # A match this returns True for is allowed: (match, the whole text, where the match starts).
    allow: Callable[[str, str, int], bool] | None = None


@dataclass(frozen=True)
class Finding:
    where: str
    rule: str
    match: str


# Words for standards and published identifiers that look like ticket numbers.
STANDARD_IDS = {"UTF-8", "UTF-16", "UTF-32", "PHQ-2", "PHQ-9", "GAD-2", "GAD-7", "SHA-1", "SHA-2", "SHA-3", "TLS-1"}

# Email domains a public commit or file may name.
EMAIL_DOMAINS = re.compile(
    r"(^|\.)(haau3\.com|users\.noreply\.github\.com|anthropic\.com|example\.(com|org|net))$", re.I | re.A
)

# The SDKs for other languages are public too, and each README names the others.
SDK_REPOSITORY = re.compile(r"^haau3/haau3-(node|python|go)(?![\w-])", re.A)
SDK_URL = re.compile(r"^github\.com[:/]haau3/haau3-(node|python|go)(?![\w-])", re.I | re.A)
SDK_NAME = re.compile(r"^haau3-(node|python|go|bot|sdk)\b", re.I | re.A)

# The haau3- names the published spec carries, which are public by being in it: the guide's profile
# ids, the demo publishers, the spec's own extensions. openapi/openapi.json is the spec as
# api.haau3.com serves it, pinned when the SDK regenerates, so the set follows the API and nothing
# here is kept by hand. Any other haau3- name is still refused, and the private terms still apply.
SPEC_NAMES = frozenset(
    m.group(0).lower()
    for m in re.finditer(
        r"\bhaau3-[a-z0-9][\w-]*",
        (Path(__file__).resolve().parent.parent / "openapi" / "openapi.json").read_text(encoding="utf-8"),
        re.I | re.A,
    )
)


def _changelog_link(match: str, text: str, index: int) -> bool:
    # A link release-please writes into CHANGELOG.md, to this repository's own issue or PR.
    return (
        match.startswith("#")
        and index > 0
        and text[index - 1] == "["
        and text.startswith(f"{match}](https://github.com/{THIS_REPOSITORY}/", index)
    )


RULES: list[Rule] = [
    # `haau3/<name>` on its own names a repository; after a `/` it is a path, as this package's
    # own `src/haau3/...` is. A GitHub URL names one wherever it is.
    Rule(
        "a repository of the haau3 organization other than the SDKs",
        re.compile(r"(?<![@\w./-])haau3/[\w.-]+", re.A),
        lambda match, _text, _index: bool(SDK_REPOSITORY.match(match)),
    ),
    Rule(
        "a link to a repository of the haau3 organization other than the SDKs",
        re.compile(r"github\.com[:/]haau3/[\w.-]+", re.I | re.A),
        lambda match, _text, _index: bool(SDK_URL.match(match)),
    ),
    Rule(
        "a haau3 project, repository or service name other than the SDKs' and the spec's",
        re.compile(r"\bhaau3-[a-z0-9][\w-]*", re.I | re.A),
        lambda match, _text, _index: bool(SDK_NAME.match(match)) or match.lower() in SPEC_NAMES,
    ),
    Rule(
        "an issue or pull request number without the repository it belongs to",
        re.compile(r"(?<![\w/&])#\d+\b|\bissues? \d+\b", re.I | re.A),
        _changelog_link,
    ),
    Rule(
        "a ticket or work-item id",
        re.compile(r"\b[A-Z]{2,10}-\d{1,2}[a-z]?\b", re.A),
        lambda match, _text, _index: match in STANDARD_IDS,
    ),
    Rule("a planning word", re.compile(r"\b(milestones?|epics?|backlog|roadmap)\b", re.I | re.A)),
    Rule("a person by role", re.compile(r"\b(the owner|owner's)\b", re.I | re.A)),
    Rule("how the API is run", re.compile(r"\b(deploy\w*|roll(ed|s)? out|production (build|release))\b", re.I | re.A)),
    Rule(
        "where secrets or settings are kept",
        re.compile(
            r"\b(org(anization)?|repo(sitory)?|actions|environment)[- ](level )?(secrets?|variables?)\b", re.I | re.A
        ),
    ),
    Rule(
        "a credential",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\bgh[pousr]_[A-Za-z0-9]{30,}|\bgithub_pat_\w{20,}"
            r"|\bnpm_[A-Za-z0-9]{36}\b|\bpypi-AgEIcHlwaS5vcmc[\w-]{20,}|\bIv\d{1,2}[.a-zA-Z0-9]{14,}\b"
            r"|\bsk_(live|test)_[A-Za-z0-9]{10,}|\bAIza[\w-]{35}|\bxox[abpr]-[\w-]{10,}",
            re.A,
        ),
    ),
    Rule(
        "an email address outside haau3's public ones",
        re.compile(r"[\w.+-]+@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}\b", re.I | re.A),
        lambda match, _text, _index: bool(EMAIL_DOMAINS.search(match[match.index("@") + 1 :])),
    ),
]

# Files whose text is not this repository's own words: the API's spec, HL7's schema, what is
# generated from them, the lockfile and recorded tool output.
NOT_OURS = [
    re.compile(p)
    for p in (
        r"^src/haau3/(_methods|_operations|_components|calls|fhir|types)\.py$",
        r"^openapi/(openapi|code-samples)\.json$",
        r"^fhir/",
        r"^api\.md$",
        r"^uv\.lock$",
        r"^tests/fixtures/",
    )
]
# This check and its tests, which hold the patterns and their samples.
THE_CHECK = [re.compile(r"^scripts/check_public_text\.py$"), re.compile(r"^tests/test_public_text\.py$")]


def check(where: str, text: str, rules: Sequence[Rule] = RULES) -> list[Finding]:
    findings: list[Finding] = []
    offset = 0
    for number, line in enumerate(text.split("\n"), start=1):
        for rule in rules:
            for m in rule.pattern.finditer(line):
                if rule.allow and rule.allow(m.group(0), text, offset + m.start()):
                    continue
                findings.append(Finding(f"{where}:{number}", rule.name, m.group(0)))
        offset += len(line) + 1
    return findings


def term_rules(source: str) -> list[Rule]:
    """Rules from a file of one regular expression per line."""
    terms = (re.sub(r"(^|\s)#.*$", "", line).strip() for line in source.split("\n"))
    return [Rule("a private term", re.compile(term, re.I | re.A)) for term in terms if term]


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def _resolves(ref: str) -> bool:
    return subprocess.run(["git", "rev-parse", "--verify", "--quiet", ref], capture_output=True).returncode == 0


def commit_range(given: str) -> str:
    """`A..B`, or every commit reachable from B when A does not exist yet (a repository's first push)."""
    base, dots, tip = given.partition("..")
    if dots and base and not _resolves(base):
        return tip or "HEAD"
    return given


def main(argv: Sequence[str]) -> int:
    args = list(argv)

    def option(name: str) -> list[str]:
        return [args[i + 1] for i, arg in enumerate(args) if arg == name and i + 1 < len(args)]

    try:
        terms: list[Rule] = []
        for path in option("--terms"):
            with open(path, encoding="utf-8") as f:
                terms += term_rules(f.read())
    except (OSError, re.error) as error:
        print(f"cannot check: {error}", file=sys.stderr)
        return 2
    rules = [*RULES, *terms]
    findings: list[Finding] = []

    if "--no-files" not in args:
        for path in filter(None, _git("ls-files", "-z").split("\0")):
            if any(p.search(path) for p in NOT_OURS) or not os.path.isfile(path):
                continue
            # The check's own patterns are left to the private terms alone.
            file_rules = terms if any(p.search(path) for p in THE_CHECK) else rules
            with open(path, "rb") as source:
                raw = source.read()
            if b"\0" in raw:
                continue
            findings += check(path, raw.decode("utf-8", errors="replace"), file_rules)
    for given in option("--commits"):
        log = _git("log", "--format=%H%x1f%an%x1f%B%x1e", commit_range(given))
        for entry in filter(None, (e.strip() for e in log.split("\x1e"))):
            sha, author, message = [*entry.split("\x1f"), "", ""][:3]
            if author == "dependabot[bot]":
                continue  # Its messages quote other projects' release notes.
            findings += check(f"commit {sha[:7]}", message, rules)
    for name in option("--text-env"):
        findings += check(name, os.environ.get(name, ""), rules)
    for path in option("--text-file"):
        with open(path, encoding="utf-8") as f:
            findings += check(path, f.read(), rules)

    for finding in findings:
        print(f"{finding.where}: {finding.rule}: {finding.match}", file=sys.stderr)
    print(f"{len(findings)} found." if findings else "Nothing found.", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
