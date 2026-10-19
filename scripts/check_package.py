"""Checks what `uv build` made before it is published: the wheel ships the package and its types, and
nothing else, and its metadata says what PyPI will show.

    python3 scripts/check_package.py dist
"""

from __future__ import annotations

import re
import sys
import tarfile
import zipfile
from email.parser import Parser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        raise SystemExit("usage: check_package.py <dist directory>")
    dist = Path(argv[0])
    version = re.search(r'__version__ = "(.+?)"', (ROOT / "src" / "haau3" / "_version.py").read_text())
    assert version is not None
    wheels = sorted(dist.glob(f"haau3-{version.group(1)}-py3-none-any.whl"))
    sdists = sorted(dist.glob(f"haau3-{version.group(1)}.tar.gz"))
    problems: list[str] = []
    if len(wheels) != 1 or len(sdists) != 1:
        raise SystemExit(f"expected one wheel and one sdist for {version.group(1)} in {dist}, found {wheels + sdists}")

    with zipfile.ZipFile(wheels[0]) as wheel:
        names = set(wheel.namelist())
        metadata = Parser().parsestr(wheel.read(next(n for n in names if n.endswith(".dist-info/METADATA"))).decode())
    modules = {
        f"haau3/{p.name}" for p in (ROOT / "src" / "haau3").iterdir() if p.suffix == ".py" or p.name == "py.typed"
    }
    shipped = {n for n in names if not n.startswith("haau3-")}
    if modules - shipped:
        problems.append(f"the wheel lacks {sorted(modules - shipped)}")
    if shipped - modules:
        problems.append(f"the wheel ships what is not the package: {sorted(shipped - modules)}")
    expected = {
        "Name": "haau3",
        "Version": version.group(1),
        "Requires-Python": ">=3.10",
        "License-Expression": "MIT",
        "Description-Content-Type": "text/markdown",
    }
    for field, value in expected.items():
        if metadata.get(field) != value:
            problems.append(f"METADATA {field} is {metadata.get(field)!r}, not {value!r}")
    urls = metadata.get_all("Project-URL") or []
    for label in ("Homepage", "Documentation", "Repository", "Issues", "Changelog"):
        if not any(u.startswith(f"{label}, ") for u in urls):
            problems.append(f"METADATA has no {label} URL")
    if "Typing :: Typed" not in (metadata.get_all("Classifier") or []):
        problems.append("METADATA does not say Typing :: Typed")
    if "haau3" not in (metadata.get_payload() or ""):
        problems.append("the README is not the long description")

    with tarfile.open(sdists[0]) as sdist:
        members = {m.name.split("/", 1)[1] for m in sdist.getmembers() if "/" in m.name}
    for needed in ("README.md", "LICENSE", "pyproject.toml", "src/haau3/py.typed"):
        if needed not in members:
            problems.append(f"the sdist lacks {needed}")
    for unwanted in ("tests", "scripts", "openapi", "fhir", ".github"):
        if any(m == unwanted or m.startswith(unwanted + "/") for m in members):
            problems.append(f"the sdist ships {unwanted}/")

    for problem in problems:
        print(problem, file=sys.stderr)
    print(
        f"{len(problems)} problems." if problems else f"{wheels[0].name} and {sdists[0].name} check out.",
        file=sys.stderr,
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
