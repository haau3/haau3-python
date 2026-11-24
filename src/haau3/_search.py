"""A value a person typed, escaped for a FHIR search."""

from __future__ import annotations

import re

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
    `system|code` token: escaping would make it one literal value.
    """
    return _SPECIAL.sub(lambda match: "\\" + match.group(0), value)
