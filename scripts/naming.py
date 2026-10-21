"""How the generator names things. The generator and the tests both import it, so they agree."""

from __future__ import annotations

import keyword
import re

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def method_name(operation_id: str) -> str:
    """An operationId as a Python method: `checkInsurancePlanNetwork` is `check_insurance_plan_network`."""
    return _CAMEL.sub("_", operation_id).lower()


def constant_name(operation_id: str) -> str:
    """An operationId as a constant: `READ_INSURANCE_PLAN`."""
    return method_name(operation_id).upper()


def class_name(operation_id: str) -> str:
    """An operationId as the start of a type name: `ReadInsurancePlan`."""
    return operation_id[:1].upper() + operation_id[1:]


def python_name(wire: str) -> str:
    """A query parameter's wire name as a keyword argument.

    A leading `_` is dropped, `-`, `:` and `.` become `_`, and camelCase becomes snake_case:
    `_count` is `count`, `name:contains` is `name_contains`, `address-city` is `address_city`,
    `serviceTypeCode` is `service_type_code`, and a FHIR chained search such as
    `location.address-state` is `location_address_state`.
    """
    name = wire[1:] if wire.startswith("_") else wire
    name = re.sub(r"[-:.]", "_", name)
    name = _CAMEL.sub("_", name).lower()
    if not name.isidentifier() or keyword.iskeyword(name):
        raise ValueError(f"the query parameter {wire!r} has no Python name: {name!r}")
    return name


def slug(text: str) -> str:
    """The slug docs.haau3.com gives a page: lower-cased, apostrophes dropped, anything else as hyphens."""
    text = re.sub(r"['’]", "", text.lower())
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


class DocsUrls:
    """The docs.haau3.com page for each operation, from its tag and summary. A repeat gets `-1`, `-2`."""

    def __init__(self) -> None:
        self._seen: dict[str, int] = {}

    def __call__(self, tag: str, summary: str) -> str:
        base = f"https://docs.haau3.com/api-reference/{slug(tag)}/{slug(summary)}"
        seen = self._seen.get(base, 0)
        self._seen[base] = seen + 1
        return base if seen == 0 else f"{base}-{seen}"
