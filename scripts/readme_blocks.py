"""Turns the README's Python blocks into one module, so a type checker reads them and a test runs them.

    python3 scripts/readme_blocks.py <directory>     writes <directory>/readme_snippets.py, and each code
                                                    sample in openapi/code-samples.json as
                                                    <directory>/code_sample_<n>.py

Blocks are kept in order, so a later one uses what an earlier one made (`client`). A block that
awaits goes into `readme_async`, the others into `readme_sync`. A block after the line
`<!-- not run: <why> -->` is type-checked only, in `readme_not_run`. The names the README leaves to
the reader (`plan_id`, `records`, `message`, `sandbox_message`, `test_key`, `codes`, `job_id`,
`access_token`) are the functions' parameters.
"""

from __future__ import annotations

import json
import re
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_FENCE = re.compile(r"^```python\n(.*?)^```\n", re.M | re.S)
_NOT_RUN = re.compile(r"<!-- not run: [^>]+ -->\s*$")
_AWAITS = re.compile(r"\bawait\b|\basync (for|with)\b")

PARAMETERS = (
    "plan_id: str, records: ReconcileMedicationsBody, message: ProcessMessageBody,"
    " sandbox_message: SandboxProcessMessageBody, test_key: str, codes: list[str], job_id: str,"
    " access_token: str"
)


@dataclass(frozen=True)
class Block:
    code: str
    line: int
    run: bool

    @property
    def awaits(self) -> bool:
        return bool(_AWAITS.search(self.code))


def blocks(readme: str) -> list[Block]:
    found: list[Block] = []
    for match in _FENCE.finditer(readme):
        before = readme[: match.start()].rstrip("\n").rsplit("\n", 1)[-1]
        found.append(Block(match.group(1), readme.count("\n", 0, match.start()) + 2, not _NOT_RUN.search(before)))
    return found


def _body(chosen: list[Block]) -> str:
    if not chosen:
        return "    pass\n"
    parts = [f"    # README.md, line {b.line}\n{textwrap.indent(b.code, '    ')}" for b in chosen]
    return "\n".join(parts)


def module_source(readme: str) -> str:
    found = blocks(readme)
    run_sync = [b for b in found if b.run and not b.awaits]
    run_async = [b for b in found if b.run and b.awaits]
    not_run = [b for b in found if not b.run]
    if any(b.awaits for b in not_run):
        raise ValueError("a block that is not run must not await")
    return (
        '"""The README\'s Python blocks, written by scripts/readme_blocks.py."""\n\n'
        "# pyright: reportUnusedVariable=false, reportUnusedExpression=false, reportUnusedImport=false\n"
        "# ruff: noqa\n\n"
        "from __future__ import annotations\n\n"
        "from haau3 import Haau3\n"
        "from haau3.types import ProcessMessageBody, ReconcileMedicationsBody, SandboxProcessMessageBody\n\n\n"
        f"def readme_sync({PARAMETERS}) -> None:\n{_body(run_sync)}\n\n"
        f"async def readme_async({PARAMETERS}) -> None:\n{_body(run_async)}\n\n"
        f"def readme_not_run(client: Haau3) -> None:\n{_body(not_run)}"
    )


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        raise SystemExit("usage: readme_blocks.py <directory>")
    out = Path(argv[0])
    out.mkdir(parents=True, exist_ok=True)
    (out / "readme_snippets.py").write_text(module_source((ROOT / "README.md").read_text(encoding="utf-8")))
    for number, (source, deprecated) in enumerate(samples(), start=1):
        # A deprecated operation's sample calls its deprecated method, as its page shows it: checked for
        # its types like every other, and expected to be deprecated.
        allowed = "# pyright: reportDeprecated=false\n" if deprecated else ""
        (out / f"code_sample_{number:02}.py").write_text(allowed + source)
    return 0


def samples() -> list[tuple[str, bool]]:
    """The code samples in openapi/code-samples.json, one whole program per operation, each with
    whether the spec marks its operation deprecated."""
    overlay = json.loads((ROOT / "openapi" / "code-samples.json").read_text(encoding="utf-8"))
    spec = json.loads((ROOT / "openapi" / "openapi.json").read_text(encoding="utf-8"))
    found: list[tuple[str, bool]] = []
    for action in overlay["actions"]:
        target = re.fullmatch(r"\$\.paths\['(.+)'\]\.(get|post)", action["target"])
        if target is None:
            raise ValueError(f"an overlay action targets {action['target']!r}, which is no operation")
        path, method = target.groups()
        found.append(
            (action["update"]["x-codeSamples"][0]["source"], spec["paths"][path][method].get("deprecated") is True)
        )
    return found


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
