"""Describes a regeneration pull request for .github/workflows/regenerate.yml:

    GENERATED_CHANGED=true python3 scripts/describe_regeneration.py <pinned spec> <changes> <body file>

<pinned spec> is the spec main had before fetch_spec.py, and <changes> is what
`oasdiff changelog --format json` reported between it and the new openapi/openapi.json. It prints
`title=`, `breaking=` and `releases=` lines for $GITHUB_OUTPUT and writes the description to
<body file>. HAAU3_SPEC_URL names where the spec was read from. It uses only the standard library.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from regeneration import Change, Regeneration, decide

ROOT = Path(__file__).resolve().parent.parent


def _version(path: Path) -> str:
    return str(json.loads(path.read_text(encoding="utf-8"))["info"]["version"])


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        raise SystemExit("usage: describe_regeneration.py <pinned spec> <changes> <body file>")
    pinned, changes_path, body_path = (Path(a) for a in argv)
    raw = changes_path.read_text(encoding="utf-8").strip() or "[]"
    decision = decide(
        Regeneration(
            from_version=_version(pinned),
            to_version=_version(ROOT / "openapi" / "openapi.json"),
            etag=(ROOT / "openapi" / "etag").read_text().strip(),
            spec_url=os.environ.get("HAAU3_SPEC_URL", "https://api.haau3.com/openapi.json"),
            changes=[Change.from_json(c) for c in json.loads(raw)],
            generated_changed=os.environ.get("GENERATED_CHANGED") == "true",
        )
    )
    body_path.write_text(decision.body, encoding="utf-8")
    print(f"title={decision.title}")
    print(f"breaking={str(decision.breaking).lower()}")
    print(f"releases={str(decision.releases).lower()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
