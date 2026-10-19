"""A Django view that calls the haau3 platform API through one `Haau3` for the whole process.

Route it in your urls.py: `path("plans/<str:plan_id>", django_view.plan)`.

`Haau3` is safe to share between threads, so one client serves every request and keeps its pool of
connections open. It is made on first use, when settings and the environment are ready. An async
view can call it through `asgiref.sync.sync_to_async`, as it would any blocking call.
"""

from __future__ import annotations

import logging
from functools import cache

from django.http import HttpRequest, JsonResponse

from haau3 import Haau3, Haau3Error, RefusedError

logger = logging.getLogger(__name__)


@cache
def haau3_client() -> Haau3:
    """The process's one client, made on first use. Reads HAAU3_API_KEY."""
    return Haau3()


def plan(request: HttpRequest, plan_id: str) -> JsonResponse:
    try:
        found = haau3_client().read_insurance_plan(plan_id)
    except RefusedError as error:
        if error.status == 404:
            return JsonResponse({"message": "No such plan.", "request_id": error.request_id}, status=404)
        logger.warning("the plan read was refused", extra={"haau3": error.to_dict()})
        return JsonResponse({"message": "The plan could not be read.", "request_id": error.request_id}, status=502)
    except Haau3Error as error:
        # No answer, or the key was refused: logged with the request id, never with a body.
        logger.warning("the plan read failed", extra={"haau3": error.to_dict()})
        return JsonResponse({"message": "The plan could not be read.", "request_id": error.request_id}, status=503)
    return JsonResponse({"id": found.get("id"), "name": found.get("name")})
