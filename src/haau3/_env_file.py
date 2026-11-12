"""Which env file in the current folder sets HAAU3_API_KEY when the program's environment has no value
for it.

The case of a key written to `.env` (by hand, or by `npx haau3 keys create`) and a program that reads
no env file, since Python loads none by itself. Asked only when a client is refused for having no key
from HAAU3_API_KEY, so the refusal can name the file and how to load it.

It never keeps or shows the value: the bytes read are dropped from this frame before it returns, so a
traceback that records local variables does not see them. Anything unexpected (a file that is not a
regular file, a read that fails, a folder that has gone) is taken as "no such file", so the refusal is
then the one it was before.
"""

from __future__ import annotations

import os
import re
import stat

ENV_FILES = (".env.local", ".env")
"""The files a key is written to, in the order Next.js, Vite and the haau3 command line read them."""

ENV_FILE_LIMIT = 1024 * 1024
"""The most of a file read: a key is one short line, and a larger file is not an env file to read."""

_ASSIGNS_THE_KEY = re.compile(rb"[ \t]*(?:export[ \t]+)?HAAU3_API_KEY[ \t]*=")
"""A line that assigns HAAU3_API_KEY: the name at the start of the line (after spaces and an optional
`export`), then `=`."""

_SETS_THE_KEY = re.compile(rb"""[ \t]*(?:export[ \t]+)?HAAU3_API_KEY[ \t]*=[ \t]*(?:"[^"\s]|'[^'\s]|[^\s#"'$])""")
"""Such a line that sets it to something: a value that is not empty, a comment, quotes holding nothing
but spaces, or a reference to another variable."""

_BOM = b"\xef\xbb\xbf"


def _last_assignment_sets_the_key(data: bytes) -> bool:
    """Whether the last line assigning HAAU3_API_KEY sets it, since a loader keeps the last."""
    found = False
    for line in data.split(b"\n"):
        if _ASSIGNS_THE_KEY.match(line):
            found = _SETS_THE_KEY.match(line) is not None
    return found


def _sets_the_key(path: str) -> bool:
    """Whether a file sets HAAU3_API_KEY, reading at most ENV_FILE_LIMIT bytes.

    The path is asked what it is before it is opened, so a named pipe is never opened: password managers
    can serve `.env` as one, and an open would count as a read, or ask the person to approve one. The open
    never blocks, and the open file is asked again, so a pipe swapped in between is not read either."""
    if not stat.S_ISREG(os.stat(path).st_mode):
        return False
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags)
    chunk = b""
    chunks: list[bytes] = []
    data = b""
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return False
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
        return _last_assignment_sets_the_key(data)
    finally:
        # The file holds the key and any other secret in it: no copy outlives this frame.
        del chunk, chunks, data
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
