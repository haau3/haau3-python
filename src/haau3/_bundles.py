"""Reading a Bundle: the resource a reference names, and the resources a search matched.

`resolve_in()` follows FHIR R4's rules for resolving a reference inside a Bundle (Bundle, "Resolving
references in Bundles"), as FHIR's own clients do: fhir-kit-client's `resolve()`, HAPI's
`getResource()` for a resource received in the same response, SMART's Python client looking in
`contained` and then the owning Bundle. No request is ever made, and nothing is replaced in place.

Plain functions, with no client, as the readers in `_parameters` are. The typed `resolve()` and
`matches()` (generated, in `_bundle_reads`) and the clients' methods of the same names call them.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast
from urllib.parse import parse_qs, urlsplit

from ._errors import Haau3Error

if TYPE_CHECKING:
    from .fhir import Bundle, BundleEntry, Reference, Resource

# FHIR's RESTful address: a base, a resource type, an id, and a version after _history. Each is matched
# against the whole string (fullmatch), since `$` in Python also matches before a final newline.
_RESTFUL = re.compile(
    r"(https?://.+)/([A-Z][A-Za-z]+)/([A-Za-z0-9\-.]{1,64})(?:/_history/([A-Za-z0-9\-.]{1,64}))?", re.ASCII
)
_RELATIVE = re.compile(r"([A-Z][A-Za-z]+)/([A-Za-z0-9\-.]{1,64})(?:/_history/([A-Za-z0-9\-.]{1,64}))?", re.ASCII)
_ABSOLUTE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:", re.ASCII)


@dataclass(frozen=True)
class _Address:
    base: str
    type: str
    id: str
    version: str | None


def _restful(url: object) -> _Address | None:
    match = _RESTFUL.fullmatch(url) if isinstance(url, str) else None
    return _Address(match[1], match[2], match[3], match[4]) if match else None


def _held(bundle: Bundle) -> list[tuple[BundleEntry, Resource]]:
    """Each entry that holds a resource, with it; a deleted entry in a history holds none, and names nothing.

    An entry or a resource that is not a JSON object is passed over, as a reader of FHIR's JSON skips
    what it cannot read, so one malformed entry never fails a page.
    """
    held: list[tuple[BundleEntry, Resource]] = []
    for entry in _entries(bundle):
        resource = entry.get("resource")
        if isinstance(resource, dict):
            held.append((entry, resource))
    return held


def _entries(bundle: Bundle) -> list[BundleEntry]:
    entries: object = bundle.get("entry")
    if not isinstance(entries, list):
        return []
    return [cast("BundleEntry", e) for e in cast("list[object]", entries) if isinstance(e, dict)]


def _type_of(resource: Resource) -> object:
    return cast("Mapping[str, object]", resource).get("resourceType")


def _version(resource: Resource) -> object:
    meta = cast("Mapping[str, object]", resource).get("meta")
    return cast("Mapping[str, object]", meta).get("versionId") if isinstance(meta, Mapping) else None


def _one(found: list[Resource]) -> Resource | None:
    """The one of `found`, or None for none; two is a Bundle that does not say which it means."""
    if len(found) > 1:
        raise Haau3Error(
            f"The reference matches {len(found)} entries of the Bundle, so which one it names is ambiguous."
        )
    return found[0] if found else None


def _at_address(bundle: Bundle, reference: str) -> Resource | None:
    """An absolute reference: the entry with that fullUrl, and with that version when it names one."""
    address = _restful(reference)
    version = address.version if address is not None else None
    url = reference if address is None or version is None else f"{address.base}/{address.type}/{address.id}"
    return _one(
        [
            resource
            for entry, resource in _held(bundle)
            if entry.get("fullUrl") == url and (version is None or _version(resource) == version)
        ]
    )


def resolve_in(
    bundle: Bundle,
    reference: Reference | str | None,
    *,
    type: str | None = None,
    from_: Resource | None = None,
) -> Resource | None:
    """The resource a reference names, found in a Bundle as FHIR R4 resolves one, or None.

    An absolute reference is the entry with that `fullUrl` (a `urn:uuid:` one included), and with
    that version when it names one. A relative reference (`Organization/123`) is resolved against the
    address of the entry `from_` is in, as FHIR says; without `from_`, it is the one entry whose
    address ends in that type and id. `#id` is a resource `from_` contains, and `#` is `from_` itself.
    A reference by identifier alone names no entry. Raises a `Haau3Error` when two entries could be
    the one meant, when the resource found is not of `type`, for a `#` reference without `from_`,
    and for a `from_` that is not an entry of the Bundle.
    """
    given: object = reference
    target = (
        given
        if isinstance(given, str)
        else cast("Mapping[str, object]", given).get("reference")
        if isinstance(given, dict)
        else None
    )
    # A reference by identifier alone names no entry; FHIR leaves finding one to the reader.
    if not isinstance(target, str) or not target:
        return None

    found: Resource | None
    if target.startswith("#"):
        if from_ is None:
            raise Haau3Error("A reference to a contained resource is found in the resource that holds it: pass from_.")
        # `#` alone is the resource itself; `#id` is one it contains.
        held: object = cast("Mapping[str, object]", from_).get("contained")
        contained = (
            [cast("Resource", r) for r in cast("list[object]", held) if isinstance(r, dict)]
            if isinstance(held, list)
            else []
        )
        found = from_ if target == "#" else next((r for r in contained if r.get("id") == target[1:]), None)
    elif _ABSOLUTE.match(target):
        found = _at_address(bundle, target)
    else:
        relative = _RELATIVE.fullmatch(target)
        # Anything else has no meaning FHIR defines.
        if relative is None:
            return None
        if from_ is not None:
            holder = next((entry for entry, resource in _held(bundle) if resource is from_), None)
            if holder is None:
                raise Haau3Error("The resource passed as from_ is not an entry of this Bundle.")
            # FHIR resolves a relative reference only against an entry at a RESTful address.
            base = _restful(holder.get("fullUrl"))
            if base is None:
                return None
            found = _at_address(bundle, f"{base.base}/{target}")
        else:
            rtype, rid, version = relative[1], relative[2], relative[3]
            candidates: list[Resource] = []
            for entry, resource in _held(bundle):
                address = _restful(entry.get("fullUrl"))
                if address is None or address.version is not None or (address.type, address.id) != (rtype, rid):
                    continue
                if version is None or _version(resource) == version:
                    candidates.append(resource)
            found = _one(candidates)

    if found is not None and type is not None and _type_of(found) != type:
        raise Haau3Error(f"The reference names a resource of type {_type_of(found)}, not {type}.")
    return found


def is_match(entry: BundleEntry) -> bool:
    """Whether an entry is one a search matched: it holds a resource, and its search mode is `match` or says none."""
    search: object = entry.get("search")
    mode = cast("Mapping[str, object]", search).get("mode") if isinstance(search, dict) else None
    return isinstance(entry.get("resource"), dict) and mode in (None, "match")


def matches_in(page: Bundle, type: str | None = None) -> list[Resource]:
    """One page's matches, in page order, of `type` when one is given."""
    found: list[Resource] = []
    for entry in _entries(page):
        resource = entry.get("resource")
        if resource is not None and is_match(entry) and (type is None or _type_of(resource) == type):
            found.append(resource)
    return found


def next_cursor(page: Bundle) -> str | None:
    """The cursor of a search page's `next` link: its `page` value, or None on the last page.

    A search takes the cursor back as `page` for the page after this one. A next link that is not a
    URL, or that carries no cursor or two, raises a `Haau3Error`: reading it as the last page would
    end a caller's paging with no error.
    """
    links: object = page.get("link")
    next_url: object = None
    for link in cast("list[object]", links) if isinstance(links, list) else []:
        fields: Mapping[str, object] = cast("Mapping[str, object]", link) if isinstance(link, dict) else {}
        if fields.get("relation") == "next":
            next_url = fields.get("url")
            break
    # No next link: this is the last page.
    if next_url is None:
        return None
    if not isinstance(next_url, str):
        raise Haau3Error("The page's next link is not a URL, so it names no next page.")
    try:
        query = urlsplit(next_url).query
    except ValueError:
        raise Haau3Error("The page's next link is not a URL, so it names no next page.") from None
    cursors = parse_qs(query, keep_blank_values=True).get("page", [])
    if len(cursors) > 1:
        raise Haau3Error(
            f"The page's next link carries {len(cursors)} page cursors, so which one continues the search is ambiguous."
        )
    if not cursors or not cursors[0]:
        raise Haau3Error(
            "The page's next link carries no page cursor, so the next page is reached only by following the link,"
            " as pages() does."
        )
    return cursors[0]
