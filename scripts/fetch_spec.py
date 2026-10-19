"""Pins the published spec: fetches https://api.haau3.com/openapi.json and writes it to
openapi/openapi.json, indented so a change reads as a diff, with the ETag it was served with in
openapi/etag. When the pinned ETag still matches, the API answers 304 and nothing is written.

    uv run python scripts/fetch_spec.py        then   uv run python scripts/generate.py

HAAU3_SPEC_URL fetches from somewhere else, such as a local platform API. It uses only the standard
library.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "openapi" / "openapi.json"
ETAG = ROOT / "openapi" / "etag"


def main() -> int:
    url = os.environ.get("HAAU3_SPEC_URL", "https://api.haau3.com/openapi.json")
    pinned = ETAG.read_text().strip() if ETAG.exists() else None
    request = urllib.request.Request(url, headers={"if-none-match": pinned} if pinned else {})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            etag = response.headers.get("etag")
            body = response.read()
    except urllib.error.HTTPError as error:
        if error.code == 304:
            print(f"Unchanged: {url} still answers ETag {pinned}.")
            return 0
        raise SystemExit(f"{url} answered {error.code}.") from None
    if not etag:
        raise SystemExit(f"{url} sent no ETag.")
    document = json.loads(body)
    if not str(document.get("openapi", "")).startswith("3.1"):
        raise SystemExit(f"{url} is not an OpenAPI 3.1 document.")
    # Written as JSON.stringify(document, null, 2) writes it, so both SDKs pin the same bytes.
    SPEC.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    ETAG.write_text(f"{etag}\n")
    print(f"Pinned {document.get('info', {}).get('version')} (ETag {etag}) from {url}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
