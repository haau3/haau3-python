"""Decides what a regeneration pull request says and does (.github/workflows/regenerate.yml), from
what oasdiff found between the pinned spec and the live one, and whether the generated code changed:

- a change oasdiff grades ERR or WARN (with openapi/oasdiff-levels.txt) can break callers: the PR is
  labelled `breaking` and titled `feat!:`, which release-please releases as a minor while the SDK is
  0.y.z;
- any other change it reports is `feat:`, a patch;
- generated code that changed with nothing reported (descriptions, which become docstrings) is
  `fix:`, a patch;
- generated code that did not change is `chore:`, which releases nothing: the new spec is pinned and
  rides with the next release. A breaking change still labels such a PR, because the API changed for
  its callers even though the SDK did not.

The PR is squash-merged with its description as the commit body, and release-please reads that body
too. So the description never starts a paragraph the way a Conventional Commit does, and never names
a breaking-change footer or Release-As.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

WARN = 2
"""oasdiff's levels: 3 is ERR, 2 is WARN, 1 is INFO."""
MAX_BODY = 60_000
"""GitHub refuses a PR description over 65,536 characters; this leaves room for its own text."""
LISTED = 200


@dataclass(frozen=True)
class Change:
    """One change as `oasdiff changelog --format json` reports it."""

    id: str
    text: str
    level: int
    operation: str | None = None
    path: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, object]) -> Change:
        operation, path = data.get("operation"), data.get("path")
        level = data.get("level")
        return cls(
            id=str(data.get("id", "")),
            text=str(data.get("text", "")),
            level=level if isinstance(level, int) else 0,
            operation=operation if isinstance(operation, str) else None,
            path=path if isinstance(path, str) else None,
        )


@dataclass(frozen=True)
class Regeneration:
    from_version: str
    """`info.version` of the pinned spec."""
    to_version: str
    """`info.version` of the live spec."""
    etag: str
    spec_url: str
    changes: list[Change] = field(default_factory=lambda: list[Change]())
    generated_changed: bool = False
    """Whether generating changed anything the package ships."""


@dataclass(frozen=True)
class Decision:
    title: str
    breaking: bool
    """Whether the PR carries the `breaking` label."""
    releases: bool
    """Whether merging it leads to a release."""
    body: str


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _describe(change: Change) -> str:
    """One change on one line: where, what, and oasdiff's rule."""
    where = f"`{change.operation} {change.path}`: " if change.operation and change.path else ""
    text = re.sub(r"\s+", " ", change.text).strip()
    return f"{where}{text} (`{change.id}`)"


def _list(heading: str, changes: list[Change]) -> list[str]:
    if not changes:
        return []
    lines = [f"- {_describe(c)}" for c in changes[:LISTED]]
    if len(changes) > LISTED:
        lines.append(f"- and {len(changes) - LISTED} more")
    return [f"### {heading}", "\n".join(lines)]


def decide(r: Regeneration) -> Decision:
    breaking_changes = [c for c in r.changes if c.level >= WARN]
    other_changes = [c for c in r.changes if c.level < WARN]
    breaking = bool(breaking_changes)
    kind = "chore" if not r.generated_changed else "feat!" if breaking else "feat" if r.changes else "fix"
    title = f"{kind}: the API's spec at {r.to_version}"

    if not r.generated_changed:
        effect = "Merging this pins the new spec and releases nothing, because the generated code did not change."
        if breaking:
            effect = (
                "Merging this pins the new spec and releases nothing, because the generated code did not change. "
                f"oasdiff still found {_count(len(breaking_changes), 'change')} that can break callers of the API, listed below."
            )
    elif breaking:
        effect = f"Merging this releases a minor version: oasdiff found {_count(len(breaking_changes), 'change')} that can break callers, listed below."
    elif r.changes:
        effect = "Merging this releases a patch version: the generated code changed, and nothing below breaks callers."
    else:
        effect = "Merging this releases a patch version: only descriptions or examples changed, and the generated docstrings with them."

    sections = [
        f"The API's spec changed from `{r.from_version}` to `{r.to_version}`, read from {r.spec_url} with ETag `{r.etag}`.",
        effect,
        "If a check fails, this spec needs a change by hand (a method the README calls is gone, say). "
        "Push the fix to this branch: haau3-bot never overwrites a commit it did not make.",
        "The title becomes the line in CHANGELOG.md, so rewrite it in plain words if you like, keeping its "
        "prefix. Taking the `!` out of `feat!:` releases a patch instead of a minor.",
        *_list(f"Changes that can break callers ({len(breaking_changes)})", breaking_changes),
        *_list(f"Other changes ({len(other_changes)})", other_changes),
    ]
    body = "\n\n".join(sections) + "\n"
    if len(body) > MAX_BODY:
        # The lists come last, so only their tail is lost.
        body = body[: body.rfind("\n", 0, MAX_BODY)] + "\n- and more, cut short to fit\n"
    return Decision(title=title, breaking=breaking, releases=r.generated_changed, body=body)
