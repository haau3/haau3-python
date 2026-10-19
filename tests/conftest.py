from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

# The scripts and the examples import as their own top-level modules, as they run.
for _directory in ("scripts", "examples"):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / _directory))


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    # A test that takes `kind` runs the sync client once and the async client on asyncio and on trio;
    # any other async test runs on both.
    if "kind" in metafunc.fixturenames:
        metafunc.parametrize(
            ("kind", "anyio_backend"),
            [("sync", "asyncio"), ("async", "asyncio"), ("async", "trio")],
            ids=["sync", "asyncio", "trio"],
        )
    elif "anyio_backend" in metafunc.fixturenames:
        metafunc.parametrize("anyio_backend", ["asyncio", "trio"])


@pytest.fixture
def anyio_backend() -> str:
    # Replaces anyio's own fixture, whose parameters would clash with the ones above.
    return "asyncio"


@pytest.fixture(autouse=True)
def no_key_in_the_environment(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Every test passes the key it means to; only the live tests read the real one.
    if request.node.get_closest_marker("live") is None:
        monkeypatch.delenv("HAAU3_API_KEY", raising=False)
    yield


@pytest.fixture
def waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Every wait between attempts, in seconds, without waiting."""
    import haau3._transport as transport

    seen: list[float] = []

    async def async_sleep(seconds: float) -> None:
        seen.append(seconds)

    monkeypatch.setattr(transport, "_sleep", seen.append)
    monkeypatch.setattr(transport, "_async_sleep", async_sleep)
    return seen
