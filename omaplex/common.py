import contextlib
import datetime as dt
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from omaplex.constants import (
    MAX_FIELD,
)


class PlexError(Exception):
    pass


class ConfigurationError(PlexError):
    pass


class AuthenticationError(PlexError):
    pass


class ResponseError(PlexError):
    pass

def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def isoformat(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def clean_text(value: Any, maximum: int = MAX_FIELD) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()[:maximum]


def finite_integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default



def stop_process_group(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=0.5)
        return
    except subprocess.TimeoutExpired:
        pass
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=0.5)

@contextlib.contextmanager
def wall_deadline(seconds: int, message: str):
    def expired(signum: int, frame: Any) -> None:
        raise ResponseError(message)

    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, expired)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, *previous_timer)
        signal.signal(signal.SIGALRM, previous_handler)


def run_no_output(command: list[str], *, input_bytes: bytes | None = None, timeout: float = 10) -> int:
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        if input_bytes is not None and process.stdin is not None:
            process.stdin.write(input_bytes)
            process.stdin.close()
        return process.wait(timeout=timeout)
    except (BrokenPipeError, subprocess.TimeoutExpired):
        stop_process_group(process)
        return -1


def launch_detached(command: list[str]) -> None:
    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )


def run_bounded_output(command: list[str], *, maximum: int, timeout: float) -> tuple[int, bytes]:
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    if process.stdout is None:
        stop_process_group(process)
        raise ConfigurationError("Could not read the desktop secret service")
    descriptor = process.stdout.fileno()
    os.set_blocking(descriptor, False)
    selector = selectors.DefaultSelector()
    selector.register(descriptor, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    payload = bytearray()
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                stop_process_group(process)
                raise ConfigurationError("The desktop secret service timed out")
            for key, _ in selector.select(min(0.2, remaining)):
                try:
                    chunk = os.read(key.fd, min(4096, maximum + 1 - len(payload)))
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fd)
                    continue
                payload.extend(chunk)
                if len(payload) > maximum:
                    stop_process_group(process)
                    raise ConfigurationError("The desktop secret service returned too much data")
        remaining = max(0.0, deadline - time.monotonic())
        try:
            return process.wait(timeout=remaining), bytes(payload)
        except subprocess.TimeoutExpired as error:
            stop_process_group(process)
            raise ConfigurationError("The desktop secret service timed out") from error
    finally:
        selector.close()
        process.stdout.close()


def atomic_json_write(path: Path, value: dict[str, Any], maximum: int) -> None:
    payload = (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    if len(payload) > maximum:
        raise ResponseError("Plex data exceeded the local size limit")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def read_regular_file(path: Path, maximum: int) -> bytes:
    flags = os.O_RDONLY | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        raise
    except OSError as error:
        raise ResponseError(path.name + " could not be opened safely") from error
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise ResponseError(path.name + " is not a regular file")
        if details.st_size > maximum:
            raise ResponseError(path.name + " exceeded its size limit")
        payload = os.read(descriptor, maximum + 1)
        if len(payload) > maximum:
            raise ResponseError(path.name + " exceeded its size limit")
        return payload
    finally:
        os.close(descriptor)


def read_json_file(path: Path, maximum: int) -> Any:
    try:
        payload = read_regular_file(path, maximum)
    except FileNotFoundError:
        return None
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ResponseError(path.name + " is not valid JSON") from error
