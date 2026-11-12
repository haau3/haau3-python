"""Which env file in the current folder sets HAAU3_API_KEY when the program never loaded it.

The case of a key written to `.env` (by hand, or by `npx haau3 keys create`) and a program that reads
no env file, since Python loads none by itself. Asked only when a client is refused for having no key,
so the refusal can name the file and how to load it.

It looks for the variable's name and nothing else. The value is never parsed out, compared, kept or
shown. Anything unexpected (a file that is not a regular file, a read that fails, a folder that has
gone) is taken as "no such file", so the refusal is then the one it was before.
"""

from __future__ import annotations

import os
import re
import stat

ENV_FILES = (".env.local", ".env")
"""The files a key is written to, in the order Next.js, Vite and the haau3 command line read them."""

ENV_FILE_LIMIT = 1024 * 1024
"""The most of a file read: a key is one short line, and a larger file is not an env file to read."""

_SETS_THE_KEY = re.compile(
    rb"""^[ \t]*(?:export[ \t]+)?HAAU3_API_KEY[ \t]*=[ \t]*(?:"[^"\r\n]|'[^'\r\n]|[^\s#"'])""",
    re.MULTILINE,
)
"""A line that sets HAAU3_API_KEY to something: the name at the start of a line (after spaces and an
optional `export`), `=`, then a value that is not empty, a comment or an empty pair of quotes."""

_BOM = b"\xef\xbb\xbf"


def _sets_the_key(path: str) -> bool:
    """Whether a file sets HAAU3_API_KEY, reading at most ENV_FILE_LIMIT bytes, never blocking on a pipe."""
    # A non-blocking open, so a named pipe called `.env` cannot hold the program; then only a regular file.
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return False
        chunks: list[bytes] = []
        remaining = ENV_FILE_LIMIT
        while remaining > 0:
            chunk = os.read(fd, min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if data.startswith(_BOM):
            data = data[len(_BOM) :]
        return _SETS_THE_KEY.search(data) is not None
    finally:
        os.close(fd)


def unloaded_env_file(folder: str | None = None) -> str | None:
    """The first of ENV_FILES in `folder` that sets HAAU3_API_KEY, or None: none does, or anything went
    wrong. `folder` is the current folder unless given."""
    try:
        base = folder if folder is not None else os.getcwd()
        for name in ENV_FILES:
            try:
                if _sets_the_key(os.path.join(base, name)):
                    return name
            except OSError:
                # No such file, or one that cannot be read: the next one is still asked.
                continue
        return None
    except Exception:
        # Nothing here may change why the client was refused: any surprise is no file.
        return None
