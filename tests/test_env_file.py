"""A client refused for having no key from HAAU3_API_KEY names an env file in the current folder that sets
it, and says how to load it: when the environment has no such variable, and when it has one set to
nothing, which load_dotenv would keep. The value is never in the message, anywhere on the error, or in a
frame's locals. Anything unexpected leaves the refusal as it was, nothing but a regular file is opened,
and nothing is read unless the client is refused for having no key from the variable."""

from __future__ import annotations

import json
import os
import pickle
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

import pytest

from haau3 import AsyncHaau3, Haau3, Haau3Error
from haau3._env_file import ENV_FILE_LIMIT, unloaded_env_file

from .helpers import KEY

PLANTED = "haau3_sk_live_PLANTEDmarkerNEVERshown000000000000000000"
"""A made-up key planted in the files, which must never be repeated."""
MARKER = "PLANTEDmarker"
POSIX = sys.platform != "win32"


def _folder(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        (tmp_path / name).write_bytes(text.encode())
    return tmp_path


def _refused(cls: type, **options: object) -> Haau3Error:
    with pytest.raises(Haau3Error) as caught:
        cls(**options)
    return caught.value


def _shown(error: Haau3Error) -> str:
    """Every way an error can be shown or kept, its frames' locals included, as one text."""
    with_locals = "".join(traceback.TracebackException.from_exception(error, capture_locals=True).format())
    return "\n".join(
        [
            str(error),
            repr(error),
            json.dumps(error.to_dict(), default=str),
            repr(pickle.loads(pickle.dumps(error))),
            repr(pickle.dumps(error)),
            with_locals,
        ]
    )


# --- which file sets the key -------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        f"HAAU3_API_KEY={PLANTED}\n",
        f"export HAAU3_API_KEY={PLANTED}\n",
        f'  HAAU3_API_KEY = "{PLANTED}"\n',
        f"HAAU3_API_KEY='{PLANTED}'\n",
        f"# keys\r\nOTHER=1\r\nHAAU3_API_KEY={PLANTED}\r\n",
        f"\ufeffHAAU3_API_KEY={PLANTED}\n",
        f"HAAU3_API_KEY={PLANTED}",
        f"HAAU3_API_KEY=\nHAAU3_API_KEY={PLANTED}\n",
    ],
    ids=[
        "plain",
        "export",
        "spaces and double quotes",
        "single quotes",
        "CRLF after other lines",
        "BOM, which python-dotenv skips",
        "no newline",
        "set again after empty",
    ],
)
def test_a_line_that_sets_the_key_is_found(tmp_path: Path, text: str) -> None:
    assert unloaded_env_file(str(_folder(tmp_path, {".env": text}))) == ".env"


@pytest.mark.parametrize(
    "text",
    [
        "HAAU3_API_KEY=\n",
        'HAAU3_API_KEY=""\n',
        "HAAU3_API_KEY=''\n",
        'HAAU3_API_KEY="   "\n',
        "HAAU3_API_KEY= # later\n",
        "HAAU3_API_KEY=$OTHER\n",
        "HAAU3_API_KEY=${OTHER}\n",
        f"# HAAU3_API_KEY={PLANTED}\n",
        f"HAAU3_API_KEY_OLD={PLANTED}\n",
        f"MY_HAAU3_API_KEY={PLANTED}\n",
        "HAAU3_ENVIRONMENT=sandbox\n",
        f"HAAU3_API_KEY={PLANTED}\nHAAU3_API_KEY=\n",
    ],
    ids=[
        "empty",
        "empty double quotes",
        "empty single quotes",
        "quotes holding spaces",
        "a comment",
        "another variable",
        "another variable braced",
        "commented out",
        "longer name",
        "ends in it",
        "other variables",
        "emptied after it is set",
    ],
)
def test_a_line_that_does_not_set_the_key_is_not_counted(tmp_path: Path, text: str) -> None:
    assert unloaded_env_file(str(_folder(tmp_path, {".env": text}))) is None


def test_env_local_is_named_before_env(tmp_path: Path) -> None:
    both = tmp_path / "both"
    both.mkdir()
    _folder(both, {".env.local": f"HAAU3_API_KEY={PLANTED}\n", ".env": f"HAAU3_API_KEY={PLANTED}\n"})
    assert unloaded_env_file(str(both)) == ".env.local"
    only_env = tmp_path / "only"
    only_env.mkdir()
    _folder(only_env, {".env.local": "OTHER=1\n", ".env": f"HAAU3_API_KEY={PLANTED}\n"})
    assert unloaded_env_file(str(only_env)) == ".env"


def test_a_folder_with_neither_file_has_none(tmp_path: Path) -> None:
    assert unloaded_env_file(str(_folder(tmp_path, {"other.env": f"HAAU3_API_KEY={PLANTED}\n"}))) is None


def test_at_most_one_mib_of_a_file_is_read(tmp_path: Path) -> None:
    padding = ("# " + "x" * 1022 + "\n") * (ENV_FILE_LIMIT // 1024)
    late = tmp_path / "late"
    late.mkdir()
    _folder(late, {".env": f"{padding}HAAU3_API_KEY={PLANTED}\n"})
    assert unloaded_env_file(str(late)) is None
    early = tmp_path / "early"
    early.mkdir()
    _folder(early, {".env": f"HAAU3_API_KEY={PLANTED}\n{padding}"})
    assert unloaded_env_file(str(early)) == ".env"


def test_only_a_regular_file_is_asked_and_the_next_still_is(tmp_path: Path) -> None:
    _folder(tmp_path, {".env": f"HAAU3_API_KEY={PLANTED}\n"})
    (tmp_path / ".env.local").mkdir()
    assert unloaded_env_file(str(tmp_path)) == ".env"


@pytest.mark.skipif(not POSIX, reason="named pipes are POSIX")
def test_a_named_pipe_called_env_is_never_opened(tmp_path: Path) -> None:
    """Password managers can serve .env as a named pipe, where an open counts as a read or asks for approval."""
    pipe = tmp_path / ".env"
    os.mkfifo(pipe)
    opened = threading.Event()

    def writer() -> None:
        fd = os.open(pipe, os.O_WRONLY)  # waits until a reader opens the pipe
        opened.set()
        os.close(fd)

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    time.sleep(0.05)
    found: list[str | None] = []
    asking = threading.Thread(target=lambda: found.append(unloaded_env_file(str(tmp_path))), daemon=True)
    asking.start()
    asking.join(timeout=2)
    assert not asking.is_alive(), "waited on .env"
    assert found == [None]
    assert not opened.wait(0.2), "the pipe was opened: its writer was let through"
    # Let the writer go, as the reader it waits for.
    fd = os.open(pipe, os.O_RDONLY | os.O_NONBLOCK)
    opened.wait(2)
    os.close(fd)
    thread.join(timeout=2)


@pytest.mark.skipif(not POSIX or os.geteuid() == 0, reason="a file's mode does not stop root, or Windows")
def test_a_file_that_cannot_be_read_is_no_file(tmp_path: Path) -> None:
    _folder(tmp_path, {".env": f"HAAU3_API_KEY={PLANTED}\n"})
    (tmp_path / ".env").chmod(0)
    try:
        assert unloaded_env_file(str(tmp_path)) is None
    finally:
        (tmp_path / ".env").chmod(0o600)


def test_a_current_folder_that_has_gone_is_no_file(monkeypatch: pytest.MonkeyPatch) -> None:
    def gone() -> str:
        raise FileNotFoundError("the current folder has gone")

    monkeypatch.setattr(os, "getcwd", gone)
    assert unloaded_env_file() is None


def test_every_file_opened_is_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _folder(tmp_path, {".env.local": "OTHER=1\n", ".env": f"HAAU3_API_KEY={PLANTED}\n"})
    opened: list[int] = []
    closed: list[int] = []
    real_open, real_close = os.open, os.close

    def counting_open(*args: Any, **kwargs: Any) -> int:
        fd = real_open(*args, **kwargs)
        opened.append(fd)
        return fd

    def counting_close(fd: int) -> None:
        closed.append(fd)
        real_close(fd)

    monkeypatch.setattr(os, "open", counting_open)
    monkeypatch.setattr(os, "close", counting_close)
    assert unloaded_env_file(str(tmp_path)) == ".env"
    assert len(opened) == 2
    assert sorted(closed) == sorted(opened)


def test_an_interrupt_mid_read_leaves_no_copy_in_the_reading_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The file's bytes go before anything is raised, as the key's own copies do in `_held_key`."""
    _folder(tmp_path, {".env": f"HAAU3_API_KEY={PLANTED}\n" + "# padding\n" * 10_000})
    real_read = os.read
    reads = 0

    def interrupted(fd: int, n: int) -> bytes:
        nonlocal reads
        reads += 1
        if reads == 2:
            raise KeyboardInterrupt
        return real_read(fd, n)

    monkeypatch.setattr(os, "read", interrupted)
    with pytest.raises(KeyboardInterrupt) as caught:
        unloaded_env_file(str(tmp_path))
    tb = caught.value.__traceback__
    frames = []
    while tb is not None:
        frames.append(tb.tb_frame)
        tb = tb.tb_next
    assert any(f.f_code.co_name == "_sets_the_key" for f in frames)
    for frame in frames:
        assert MARKER not in repr(frame.f_locals), frame.f_code.co_name


# --- the refusal for no key --------------------------------------------------------------------


@pytest.mark.parametrize("cls", [Haau3, AsyncHaau3], ids=["sync", "async"])
def test_the_refusal_says_the_environment_has_no_value_and_how_to_load_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cls: type
) -> None:
    monkeypatch.delenv("HAAU3_API_KEY", raising=False)
    monkeypatch.chdir(_folder(tmp_path, {".env": f"OTHER=1\nHAAU3_API_KEY={PLANTED}\n"}))
    error = _refused(cls)
    assert error.code == "no-api-key"
    assert str(error) == (
        "No API key. .env in this folder sets HAAU3_API_KEY, but this program's environment has no value for "
        'it. Load the file before making the client: call load_dotenv(".env") from python-dotenv.'
    )
    assert MARKER not in _shown(error)


def test_the_refusal_names_env_local_when_that_is_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HAAU3_API_KEY", raising=False)
    monkeypatch.chdir(_folder(tmp_path, {".env.local": f"HAAU3_API_KEY={PLANTED}\n"}))
    error = _refused(Haau3)
    assert ".env.local in this folder sets HAAU3_API_KEY" in str(error)
    assert 'load_dotenv(".env.local")' in str(error)
    assert MARKER not in _shown(error)


def test_the_refusal_says_an_empty_variable_hides_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A variable set to nothing, as a compose file passing an unset variable through leaves it."""
    monkeypatch.setenv("HAAU3_API_KEY", "")
    monkeypatch.chdir(_folder(tmp_path, {".env": f"HAAU3_API_KEY={PLANTED}\n"}))
    error = _refused(Haau3)
    assert str(error) == (
        "No API key. HAAU3_API_KEY is set but empty in this program's environment, and .env in this folder "
        "sets it. load_dotenv keeps a variable the environment already has: unset HAAU3_API_KEY where it is "
        'set to nothing, or call load_dotenv(".env", override=True).'
    )
    assert MARKER not in _shown(error)


def test_with_no_such_file_the_refusal_names_the_command_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(_folder(tmp_path, {".env": "HAAU3_ENVIRONMENT=\n"}))
    error = _refused(Haau3)
    assert error.code == "no-api-key"
    assert str(error) == (
        "No API key. Set HAAU3_API_KEY, or pass it as api_key. Create a free key at https://platform.haau3.com, "
        "or run npx haau3 login, then npx haau3 keys create, which writes one to this project's env file "
        "(see https://docs.haau3.com/authentication). To call the Sandbox with no key, set "
        'HAAU3_ENVIRONMENT=sandbox, or pass environment="sandbox".'
    )


@pytest.mark.parametrize("cls", [Haau3, AsyncHaau3], ids=["sync", "async"])
def test_no_file_is_opened_unless_the_variable_gave_no_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cls: type
) -> None:
    """The control: a key, the Sandbox named, or an empty api_key, which is the caller's own choice."""
    monkeypatch.delenv("HAAU3_API_KEY", raising=False)
    monkeypatch.chdir(_folder(tmp_path, {".env": f"HAAU3_API_KEY={PLANTED}\n"}))
    opened: list[str] = []
    real_open = os.open

    def counting_open(path: Any, *args: Any, **kwargs: Any) -> int:
        opened.append(str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", counting_open)
    assert cls(api_key=KEY).environment == "production"
    assert cls(environment="sandbox").environment == "sandbox"
    empty = _refused(cls, api_key="")
    assert empty.code == "no-api-key"
    assert ".env" not in str(empty)
    monkeypatch.setenv("HAAU3_ENVIRONMENT", "sandbox")
    assert cls().environment == "sandbox"
    assert opened == []
    monkeypatch.delenv("HAAU3_ENVIRONMENT")
    _refused(cls)
    assert [Path(p).name for p in opened] == [".env"]
