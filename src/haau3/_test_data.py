"""Labelling a request as test data, the way the operations that receive patient data read it."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeVar, cast

_TEST_DATA_LABEL = {
    "system": "http://terminology.hl7.org/CodeSystem/v3-ActReason",
    "code": "HTEST",
    "display": "test health data",
}
"""HL7's label for test data, as the API's examples carry it."""

Body = TypeVar("Body", bound=Mapping[str, object])

_Json = dict[str, object]


def mark_as_test_data(body: Body) -> Body:
    """A copy of a request labelled as test data, on the request and on every resource in it.

    The label is HL7's `HTEST`, from `v3-ActReason`, in `meta.security`. It goes on the request and
    on every resource the API reads in it: each Bundle entry, each Parameters resource part at any
    depth, and the same again inside a Bundle or Parameters found there. A resource contained in
    another goes with the one that contains it, so it is left as it is. Labels already there are
    kept, and a resource that already carries the label gets no second one. The request you pass is
    never changed.

    Without an agreement for patient data, the operations that receive it accept only test data
    labelled as such, and refuse anything else with a `ForbiddenError` whose `code` is
    `"phi-agreement-required"`.

    Label only made-up data. The label says a record is not about a real person, and it is never a
    way to send patient data without an agreement.

    ```python
    reconciled = client.reconcile_medications(mark_as_test_data(records))
    ```
    """
    copy = _copy(body)
    # A stack rather than recursion, as the API's own check walks it.
    stack: list[object] = [copy]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        resource = cast(_Json, node)
        _label(resource)
        entry, parameter = resource.get("entry"), resource.get("parameter")
        if resource.get("resourceType") == "Bundle" and isinstance(entry, list):
            stack.extend(cast(_Json, e).get("resource") for e in cast(list[object], entry) if isinstance(e, dict))
        elif resource.get("resourceType") == "Parameters" and isinstance(parameter, list):
            parameters: list[object] = list(cast(list[object], parameter))
            while parameters:
                each = parameters.pop()
                if not isinstance(each, dict):
                    continue
                part = cast(_Json, each)
                stack.append(part.get("resource"))
                parts = part.get("part")
                if isinstance(parts, list):
                    parameters.extend(cast(list[object], parts))
    return cast(Body, copy)


def _label(resource: _Json) -> None:
    meta = resource.get("meta")
    if not isinstance(meta, dict):
        meta = resource["meta"] = {}
    meta = cast(_Json, meta)
    security = meta.get("security")
    if not isinstance(security, list):
        security = meta["security"] = []
    codings = cast(list[object], security)
    labelled = any(
        isinstance(coding, dict)
        and cast(_Json, coding).get("system") == _TEST_DATA_LABEL["system"]
        and cast(_Json, coding).get("code") == _TEST_DATA_LABEL["code"]
        for coding in codings
    )
    if not labelled:
        codings.append(dict(_TEST_DATA_LABEL))


def _copy(value: object) -> object:
    """A copy of every dict, list and tuple in a value, made with a stack.

    `copy.deepcopy` and the JSON encoder are both recursive, so a stack keeps this from being the
    first to give out on a deep request. A tuple becomes a list, as the JSON encoder sends it, so the
    walk reads it as the API does. Anything else is kept as it is, so the copy encodes as the
    original would.
    """
    if not isinstance(value, (dict, list, tuple)):
        return value
    root: object = {} if isinstance(value, dict) else []
    stack: list[tuple[object, object]] = [(value, root)]
    while stack:
        source, target = stack.pop()
        items = cast(_Json, source).items() if isinstance(source, dict) else enumerate(cast(list[object], source))
        for key, child in items:
            made: object = child
            if isinstance(child, (dict, list, tuple)):
                made = {} if isinstance(child, dict) else []
                stack.append((cast(object, child), made))
            if isinstance(target, dict):
                cast(_Json, target)[cast(str, key)] = made
            else:
                cast(list[object], target).append(made)
    return root
