"""A value for a FHIR search: one a person typed, escaped, or a coding written as a token."""

from __future__ import annotations

import re
from collections.abc import Mapping

from ._errors import Haau3Error

_SPECIAL = re.compile(r"[\\,$|]")


def escape_search_value(value: str) -> str:
    """A value for a FHIR search parameter, with FHIR's special characters escaped.

    `\\`, `,`, `$` and `|` each take a `\\` before them, as FHIR R4 says ("Escaping Search
    Parameters", https://hl7.org/fhir/R4/search.html#escaping), so the API reads the value as the one
    value it is. Unescaped, a comma separates values the API reads as "or" (so `Austin, TX` asks for
    two cities), a value with an empty part is refused (a trailing comma), and a `|` splits a token's
    system from its code.

    ```python
    page = client.search_slot_with_includes(location_address_city=haau3.escape_search_value(typed_city))
    ```

    Never escape a value you built with `,` or `|` on purpose, such as a list of codes or a
    `system|code` token: escaping would make it one literal value. Build a token with `search_token()`.
    """
    return _SPECIAL.sub(lambda match: "\\" + match.group(0), value)


def search_token(coding: Mapping[str, object], *more: Mapping[str, object]) -> str:
    """The value a FHIR token search parameter takes for a code in its code system.

    `system|code`, with each part escaped (`escape_search_value()`), as FHIR R4 writes a token
    ("token", https://hl7.org/fhir/R4/search.html#token). Given several codings, it is any of them:
    their tokens joined by commas.

    It takes a `Coding`, an entry of a `ValueSet` expansion (`expansion["contains"]`), or any mapping
    with a `system` and a `code`, so a code picked from an expansion goes straight into a search:

    ```python
    service_types = client.value_set_expand(url="https://fhir.haau3.com/platform/ValueSet/scheduling-service-types")
    for chosen in service_types.get("expansion", {}).get("contains", [])[:1]:
        page = client.search_slot_with_includes(schedule_service_type=haau3.search_token(chosen))
    ```

    Each coding needs both a system and a code: it raises a `Haau3Error` naming the one missing, since
    a token without its system would match the code in every system. For a code in any system, send
    `escape_search_value(code)` instead.
    """
    codings = (coding, *more)
    tokens: list[str] = []
    for index, each in enumerate(codings):
        which = f"coding {index + 1}" if more else "the coding"
        system = each.get("system")
        code = each.get("code")
        if not isinstance(system, str) or not system:
            raise Haau3Error(f"search_token() needs a system: {which} has none.")
        if not isinstance(code, str) or not code:
            raise Haau3Error(f"search_token() needs a code: {which} has none.")
        tokens.append(f"{escape_search_value(system)}|{escape_search_value(code)}")
    return ",".join(tokens)
