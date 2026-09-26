"""Shared scrape console log for admin/provoz (web + worker processes).

Install a stdout/stderr tee in the scrape worker so *every* terminal line
lands in DATA_DIR/scrape_console.log — same stream the admin UI polls.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any, TextIO

from app import config

_LOCK = threading.Lock()
_MAX_BYTES = 2_500_000
_TAIL_ON_OPEN = 250_000
_DEFAULT_LIMIT = 3000
_tee_installed = False


def _path() -> Path:
    return Path(config.DATA_DIR) / "scrape_console.log"


def _flock(fh: Any, exclusive: bool) -> None:
    try:
        import fcntl
    except ImportError:
        return
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
    except OSError:
        pass


def _funlock(fh: Any) -> None:
    try:
        import fcntl
    except ImportError:
        return
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def _should_log_line(line: str) -> bool:
    """Drop console/progress access noise so the admin view stays readable."""
    plain = line.replace("\x1b[", "")
    if "/api/admin/scrape-console" in plain:
        return False
    if "/api/admin/scrape-progress" in plain:
        return False
    return bool(line.strip())


def _append_file(text: str) -> None:
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            with path.open("a", encoding="utf-8") as fh:
                _flock(fh, True)
                try:
                    fh.write(text)
                    if not text.endswith("\n"):
                        fh.write("\n")
                    fh.flush()
                    size = fh.tell()
                finally:
                    _funlock(fh)
            if size > _MAX_BYTES:
                _trim(path)
    except OSError:
        pass


def emit(line: str) -> None:
    """Print a console line (tee captures it into the shared log when installed)."""
    text = str(line).rstrip("\n")
    if not text:
        return
    print(text, flush=True)
    if not _tee_installed:
        _append_file(text)


def _trim(path: Path) -> None:
    try:
        with path.open("r+b") as fh:
            _flock(fh, True)
            try:
                raw = fh.read()
                keep = raw[-_MAX_BYTES // 2 :]
                cut = keep.find(b"\n")
                if cut >= 0:
                    keep = keep[cut + 1 :]
                fh.seek(0)
                fh.truncate(0)
                fh.write(keep)
                fh.flush()
            finally:
                _funlock(fh)
    except OSError:
        pass


class _TeeStream:
    """Mirror writes to the original stream and the shared console file."""

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream
        self._buf = ""

    def write(self, data: str) -> int:
        if not isinstance(data, str):
            data = str(data)
        try:
            n = self._stream.write(data)
        except Exception:
            n = len(data)
        try:
            self._stream.flush()
        except Exception:
            pass
        if not data:
            return n
        self._buf += data
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if _should_log_line(line):
                _append_file(line)
        return n

    def flush(self) -> None:
        try:
            self._stream.flush()
        except Exception:
            pass
        if _should_log_line(self._buf):
            _append_file(self._buf)
        self._buf = ""

    def fileno(self) -> int:
        return self._stream.fileno()

    def isatty(self) -> bool:
        try:
            return bool(self._stream.isatty())
        except Exception:
            return False

    @property
    def encoding(self) -> str:
        return getattr(self._stream, "encoding", None) or "utf-8"

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


def install_stdout_tee() -> None:
    """Tee sys.stdout/stderr into the shared console file (call once per process)."""
    global _tee_installed
    if _tee_installed:
        return
    old_out, old_err = sys.stdout, sys.stderr
    out = _TeeStream(old_out)
    err = _TeeStream(old_err)
    sys.stdout = out  # type: ignore[assignment]
    sys.stderr = err  # type: ignore[assignment]
    _tee_installed = True
    # Uvicorn/logging often keep a reference to the pre-tee stream.
    try:
        import logging

        loggers: list[Any] = [logging.root]
        for obj in logging.Logger.manager.loggerDict.values():
            if isinstance(obj, logging.Logger):
                loggers.append(obj)
        for logger in loggers:
            for handler in logger.handlers:
                stream = getattr(handler, "stream", None)
                if stream is old_out:
                    handler.stream = out
                elif stream is old_err:
                    handler.stream = err
    except Exception:
        pass


def read_since(offset: int = 0, *, limit: int = _DEFAULT_LIMIT) -> dict[str, Any]:
    """Return new console lines after byte `offset` (in order, no skips)."""
    path = _path()
    try:
        size = path.stat().st_size if path.exists() else 0
    except OSError:
        return {"offset": 0, "lines": [], "size": 0, "reset": False, "more": False}
    if size <= 0:
        return {"offset": 0, "lines": [], "size": 0, "reset": False, "more": False}

    after = max(0, int(offset or 0))
    reset = False
    if after > size:
        after = 0
        reset = True

    try:
        with path.open("rb") as fh:
            _flock(fh, False)
            try:
                if after == 0 and size > _TAIL_ON_OPEN:
                    fh.seek(max(0, size - _TAIL_ON_OPEN))
                    fh.readline()
                    reset = True
                else:
                    fh.seek(after)
                start = fh.tell()
                raw = fh.read()
            finally:
                _funlock(fh)
    except OSError:
        return {"offset": size, "lines": [], "size": size, "reset": reset, "more": False}

    if not raw:
        return {"offset": start, "lines": [], "size": size, "reset": reset, "more": False}

    text = raw.decode("utf-8", errors="replace")
    if raw.endswith(b"\n"):
        complete, remainder_len = text, 0
    else:
        cut = text.rfind("\n")
        if cut < 0:
            return {"offset": start, "lines": [], "size": size, "reset": reset, "more": False}
        complete = text[: cut + 1]
        remainder_len = len(raw) - (cut + 1)

    lines = [ln for ln in complete.splitlines() if ln]
    if len(lines) > limit:
        kept = lines[:limit]
        consumed = ("\n".join(kept) + "\n").encode("utf-8")
        return {
            "offset": start + len(consumed),
            "lines": kept,
            "size": size,
            "reset": reset,
            "more": True,
        }

    return {
        "offset": start + len(raw) - remainder_len,
        "lines": lines,
        "size": size,
        "reset": reset,
        "more": False,
    }


def clear() -> None:
    path = _path()
    with _LOCK:
        try:
            with path.open("w", encoding="utf-8") as fh:
                _flock(fh, True)
                try:
                    fh.write("")
                finally:
                    _funlock(fh)
        except OSError:
            pass
