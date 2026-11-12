"""The caller's app, named in the User-Agent after the SDK's own, and checked before anything is sent."""

from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import urlsplit

from typing_extensions import Required, TypedDict

from ._errors import Haau3Error
from ._transport import USER_AGENT


class AppInfo(TypedDict, total=False):
    """Your app, named in the User-Agent after the SDK's own, as `name/version (url)`.

    A library, plugin or command line built on this SDK names itself once, on the client, and every
    request the client sends says so:

    ```python
    client = Haau3(app_info={"name": "my-plugin", "version": "1.2.0"})
    # User-Agent: haau3-python/<version> (python 3.13; httpx2 <version>) my-plugin/1.2.0
    ```
    """

    name: Required[str]
    """Your app's name, such as `my-plugin`: letters, digits and the marks `- . _ ~ ! # $ % & ' * + ^ ` |`,
    with no space or slash, as a User-Agent product name takes."""
    version: str | None
    """Its version, such as `1.2.0`, in the same characters. None or an empty string sends none."""
    url: str | None
    """Where to find out about it: an http or https URL, such as `https://example.com/my-plugin`. None or
    an empty string sends none."""


_FIELDS = ("name", "version", "url")
_TOKEN = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
"""RFC 9110's `token`, which a User-Agent's product name and version each are."""
_COMMENT_TEXT = re.compile(r"[\x21-\x27\x2a-\x5b\x5d-\x7e]+")
"""What a User-Agent's comment can carry of a URL: printable ASCII with no space, parenthesis or backslash."""
_TOKEN_MARKS = "letters, digits and the marks - . _ ~ ! # $ % & ' * + ^ ` |, with no space or slash"


def _is_web_url(text: str) -> bool:
    try:
        parts = urlsplit(text)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname)


def user_agent(app_info: object) -> str:
    """The SDK's User-Agent with the caller's app after it, as `name/version (url)`.

    Each field is checked here, so a value the header cannot carry is refused before anything is sent,
    in a message that names the field.
    """
    if app_info is None:
        return USER_AGENT
    if not isinstance(app_info, Mapping):
        raise Haau3Error(
            'app_info must be a dict with a name, such as {"name": "my-plugin", "version": "1.2.0"}: '
            f"{app_info!r} is not one.",
            code="invalid-app-info",
        )
    given: Mapping[object, object] = app_info  # pyright: ignore[reportUnknownVariableType]
    unknown = [key for key in given if key not in _FIELDS]
    if unknown:
        raise Haau3Error(
            f"app_info takes name, version and url only: {', '.join(repr(key) for key in unknown)} is not one of them.",
            code="invalid-app-info",
        )
    name, version, url = given.get("name"), given.get("version"), given.get("url")
    if name is None or name == "":
        raise Haau3Error(
            'app_info["name"] is required: your app\'s name, such as "my-plugin".', code="invalid-app-info"
        )
    if not isinstance(name, str) or _TOKEN.fullmatch(name) is None:
        raise Haau3Error(
            f'app_info["name"] must be {_TOKEN_MARKS}, such as "my-plugin": {name!r} is not one.',
            code="invalid-app-info",
        )
    product = name
    if version is not None and version != "":
        if not isinstance(version, str) or _TOKEN.fullmatch(version) is None:
            raise Haau3Error(
                f'app_info["version"] must be {_TOKEN_MARKS}, such as "1.2.0": {version!r} is not one.',
                code="invalid-app-info",
            )
        product += f"/{version}"
    if url is not None and url != "":
        if not isinstance(url, str) or _COMMENT_TEXT.fullmatch(url) is None or not _is_web_url(url):
            raise Haau3Error(
                'app_info["url"] must be an http or https URL with a host and no space, parenthesis or backslash, '
                f'such as "https://example.com/my-plugin": {url!r} is not one.',
                code="invalid-app-info",
            )
        product += f" ({url})"
    return f"{USER_AGENT} {product}"
