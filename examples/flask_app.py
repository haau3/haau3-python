"""A Flask app that calls the haau3 platform API through one `Haau3` held on the app.

    HAAU3_API_KEY=... flask --app examples.flask_app run

`Haau3` is safe to share between threads, so one client serves every request. Flask runs each async
view in an event loop of its own, which an `AsyncHaau3` must not outlive, so an async view uses this
sync client too.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from flask import Flask

from haau3 import Haau3, Haau3Error, RefusedError

logger = logging.getLogger(__name__)


def create_app(make_client: Callable[[], Haau3] = Haau3) -> Flask:
    app = Flask(__name__)
    app.extensions["haau3"] = make_client()

    @app.get("/plans/<plan_id>")
    def read_plan(plan_id: str) -> tuple[dict[str, str | None], int]:
        client: Haau3 = app.extensions["haau3"]
        try:
            plan = client.read_insurance_plan(plan_id)
        except RefusedError as error:
            if error.status == 404:
                return {"message": "No such plan.", "request_id": error.request_id}, 404
            logger.warning("the plan read was refused", extra={"haau3": error.to_dict()})
            return {"message": "The plan could not be read.", "request_id": error.request_id}, 502
        except Haau3Error as error:
            # No answer, or the key was refused: logged with the request id, never with a body.
            logger.warning("the plan read failed", extra={"haau3": error.to_dict()})
            return {"message": "The plan could not be read.", "request_id": error.request_id}, 503
        return {"id": plan.get("id"), "name": plan.get("name")}, 200

    return app
