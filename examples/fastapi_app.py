"""A FastAPI app that calls the haau3 platform API through one `AsyncHaau3` for the app's life.

    HAAU3_API_KEY=... uvicorn examples.fastapi_app:app

The client is made in the app's lifespan, so it lives on the server's event loop and its pool of
connections is shared by every request; it is closed when the app stops. Each route gets it from a
dependency, so a test can hand the app a client of its own (`create_app(make_client=...)`).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request

from haau3 import AsyncHaau3, Haau3Error, RefusedError

logger = logging.getLogger(__name__)


def haau3_client(request: Request) -> AsyncHaau3:
    """The app's one client, for a route to depend on."""
    client: AsyncHaau3 = request.app.state.haau3
    return client


def create_app(make_client: Callable[[], AsyncHaau3] = AsyncHaau3) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        async with make_client() as client:
            app.state.haau3 = client
            yield

    app = FastAPI(lifespan=lifespan)

    @app.get("/plans/{plan_id}")
    async def read_plan(plan_id: str, client: Annotated[AsyncHaau3, Depends(haau3_client)]) -> dict[str, str | None]:
        try:
            plan = await client.read_insurance_plan(plan_id)
        except RefusedError as error:
            if error.status == 404:
                raise HTTPException(404, {"message": "No such plan.", "request_id": error.request_id}) from None
            logger.warning("the plan read was refused", extra={"haau3": error.to_dict()})
            raise HTTPException(
                502, {"message": "The plan could not be read.", "request_id": error.request_id}
            ) from None
        except Haau3Error as error:
            # No answer, or the key was refused: logged with the request id, never with a body.
            logger.warning("the plan read failed", extra={"haau3": error.to_dict()})
            raise HTTPException(
                503, {"message": "The plan could not be read.", "request_id": error.request_id}
            ) from None
        return {"id": plan.get("id"), "name": plan.get("name")}

    return app


app = create_app()
