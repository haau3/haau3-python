"""How a request is written, the same way byte for byte in every haau3 SDK.

The query is written as JavaScript's `URLSearchParams` writes it (a space as `+`, every byte but
letters, digits and `*-._` as `%XX`, so `~` is `%7E` and `,` is `%2C`), and a path value as
`encodeURIComponent` writes it. `urllib.parse.quote` differs from both, so it is not used for the query.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from typing import TypeAlias, Union, cast
from urllib.parse import quote, urljoin, urlsplit

from ._errors import Haau3Error

QueryScalar: TypeAlias = Union[str, int, bool]
QueryValue: TypeAlias = Union[QueryScalar, Sequence[QueryScalar], None]
"""A query parameter's value. A list sends the parameter once per value, and `None` sends none."""

_QUERY_SAFE = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789*-._")
# A lone surrogate cannot be written as UTF-8; JavaScript writes U+FFFD for one, and so does this.
_SURROGATE = re.compile("[\ud800-\udfff]")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I | re.A)
_ABSOLUTE = re.compile(r"https?://", re.I | re.A)
_PLACEHOLDER = re.compile(r"\{(\w+)\}")

FHIR_BASE = "/v1/fhir/r4"


def _form(text: str) -> str:
    data = _SURROGATE.sub("�", text).encode("utf-8")
    return "".join(chr(b) if b in _QUERY_SAFE else "+" if b == 0x20 else f"%{b:02X}" for b in data)


def _scalar(value: object) -> str:
    # bool before int: True is an int in Python, and goes out as `true`.
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int)):
        return str(value)
    raise TypeError(f"a query value is a str, an int or a bool, or a list of them; not a {type(value).__name__}")


def encode_query(params: Iterable[tuple[str, QueryValue]]) -> str:
    """A query string from (wire name, value) pairs. `None` sends nothing, and a list repeats the name."""
    parts: list[str] = []
    for name, given in params:
        value: object = given
        if value is None:
            continue
        items: Sequence[object] = (
            [value]
            if isinstance(value, (str, bytes)) or not isinstance(value, Sequence)
            else cast("Sequence[object]", value)
        )
        for item in items:
            if item is not None:
                parts.append(f"{_form(name)}={_form(_scalar(item))}")
    return "&".join(parts)


def encode_path_value(value: str) -> str:
    """A path value as `encodeURIComponent` writes it: `a/b c` is `a%2Fb%20c`, and `!'()*~` stay."""
    return quote(_SURROGATE.sub("�", value), safe="!*'()")


def fill_path(template: str, values: Sequence[object]) -> str:
    """The operation's path with each `{name}` replaced by its value, encoded, in order."""
    names = _PLACEHOLDER.findall(template)
    if len(names) != len(values):
        raise TypeError(f"{template} takes {len(names)} path values, not {len(values)}")
    path = template
    for name, value in zip(names, values, strict=True):
        if not isinstance(value, str):
            raise TypeError(f"the path value {name} is a str, not a {type(value).__name__}")
        path = path.replace("{" + name + "}", encode_path_value(value), 1)
    return path


def checked_base_url(base_url: str, source: str = "base_url") -> str:
    """The base URL without trailing slashes, refused unless it is an http or https URL with a host.

    The error names where the URL came from: the `base_url` option, or `HAAU3_BASE_URL`.
    """
    stripped = base_url.rstrip("/")
    try:
        parts = urlsplit(stripped)
        valid = parts.scheme.lower() in ("http", "https") and bool(parts.hostname)
        if valid:
            _ = parts.port  # raises for a port that is not a number
    except ValueError:
        valid = False
    if not valid:
        raise Haau3Error(
            f"{source} must be an http or https URL with a host, such as https://api.haau3.com: {base_url!r} is not one."
        )
    return stripped


def origin(url: str) -> str:
    """The scheme, host and port of a URL, as a browser compares them: `https://api.haau3.com`."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    if port is None or (scheme, port) in (("https", 443), ("http", 80)):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def url_on_base(base_url: str, path_or_url: str, query: str = "") -> str:
    """A URL on the client's own base, with `query` appended. Any other host is refused, so the key is never sent there."""
    if _ABSOLUTE.match(path_or_url):
        target = path_or_url
    else:
        relative = path_or_url if path_or_url.startswith("/") else "/" + path_or_url
        target = urljoin(base_url + "/", urlsplit(base_url).path.rstrip("/") + relative)
    try:
        there, here = origin(target), origin(base_url)
    except ValueError:
        raise Haau3Error("Refusing to send the API key to a URL that is not valid.") from None
    if there != here:
        raise Haau3Error(f"Refusing to send the API key to {there}: the client's base_url is {here}.")
    if query:
        target += ("&" if urlsplit(target).query else "?") + query
    return target


def path_of(base_url: str, url: str) -> str:
    """The path of a URL on the client's base, without the base's own path: `/v1/fhir/r4/InsurancePlan`."""
    path = urlsplit(url).path
    prefix = urlsplit(base_url).path.rstrip("/")
    return path[len(prefix) :] if prefix and path.startswith(prefix) else path


def is_fhir(relative_path: str) -> bool:
    return relative_path == "/v1/fhir" or relative_path.startswith("/v1/fhir/")


def is_uuid(value: str) -> bool:
    return _UUID.fullmatch(value) is not None


def json_bytes(body: object) -> bytes:
    """A request body as JSON, written as `JSON.stringify` writes it: no spaces, characters as they are."""
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
