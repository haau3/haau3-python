"""A client refused for having no key names an env file in the current folder that sets HAAU3_API_KEY,
when the program never loaded it, and says how to load it. Only the variable's name is looked for: the
value is never in the message or anywhere on the error. Anything unexpected leaves the refusal as it
was, and nothing is read unless the client is refused for having no key."""

from __future__ import annotations

import json
import os
import pickle
import sys
import time
from pathlib import Path

import pytest

import haau3._client
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
    """Every way an error can be shown or kept, as one text."""
    return "\n".join(
        [str(error), repr(error), json.dumps(error.to_dict(), default=str), repr(pickle.loads(pickle.dumps(error)))]
    ) + repr(pickle.dumps(error))


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
    ],
    ids=["plain", "export", "spaces and double quotes", "single quotes", "CRLF after other lines", "BOM", "no newline"],
)
def test_a_line_that_sets_the_key_is_found(tmp_path: Path, text: str) -> None:
    assert unloaded_env_file(str(_folder(tmp_path, {".env": text}))) == ".env"


@pytest.mark.parametrize(
    "text",
    [
        "HAAU3_API_KEY=\n",
        'HAAU3_API_KEY=""\n',
        "HAAU3_API_KEY=''\n",
        "HAAU3_API_KEY= # later\n",
        f"# HAAU3_API_KEY={PLANTED}\n",
        f"HAAU3_API_KEY_OLD={PLANTED}\n",
        f"MY_HAAU3_API_KEY={PLANTED}\n",
        "HAAU3_ENVIRONMENT=sandbox\n",
    ],
    ids=[
        "empty",
        "empty double quotes",
        "empty single quotes",
        "a comment",
        "commented out",
        "longer name",
        "ends in it",
        "other variables",
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
def test_a_named_pipe_called_env_is_never_waited_on(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / ".env")
    started = time.monotonic()
    assert unloaded_env_file(str(tmp_path)) is None
    assert time.monotonic() - started < 1


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


# --- the refusal for no key --------------------------------------------------------------------


@pytest.mark.parametrize("cls", [Haau3, AsyncHaau3], ids=["sync", "async"])
def test_the_refusal_names_the_file_and_how_to_load_it_and_never_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cls: type
) -> None:
    monkeypatch.chdir(_folder(tmp_path, {".env": f"OTHER=1\nHAAU3_API_KEY={PLANTED}\n"}))
    error = _refused(cls)
    assert error.code == "no-api-key"
    assert str(error) == (
        "No API key. HAAU3_API_KEY is not set, but .env in this folder sets it, and this program has not loaded "
        'that file. Load it first: call load_dotenv(".env") from python-dotenv before making the client, or run '
        "set -a; . ./.env; set +a in the shell before the program."
    )
    assert MARKER not in _shown(error)


def test_the_refusal_names_env_local_when_that_is_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(_folder(tmp_path, {".env.local": f"HAAU3_API_KEY={PLANTED}\n"}))
    error = _refused(Haau3)
    assert "but .env.local in this folder sets it" in str(error)
    assert 'load_dotenv(".env.local")' in str(error)
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


def test_nothing_is_read_when_a_key_is_given_or_the_sandbox_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control: the check runs only on the refusal for no key."""
    monkeypatch.chdir(_folder(tmp_path, {".env": f"HAAU3_API_KEY={PLANTED}\n"}))

    def asked() -> str | None:
        raise AssertionError("an env file was read")

    monkeypatch.setattr(haau3._client, "unloaded_env_file", asked)
    assert Haau3(api_key=KEY).environment == "production"
    assert Haau3(environment="sandbox").environment == "sandbox"
    monkeypatch.setenv("HAAU3_ENVIRONMENT", "sandbox")
    assert Haau3().environment == "sandbox"
