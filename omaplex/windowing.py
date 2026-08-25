from __future__ import annotations

import json
import time

from omaplex.common import (
    PlexError,
    finite_integer,
    run_bounded_output,
    run_no_output,
)
from omaplex.config import validate_window_geometry
from omaplex.constants import MAX_HYPR_BYTES, SCHEMA_VERSION


def hypr_fullscreen_script(pid: int) -> str:
    if pid <= 0:
        raise ValueError("player PID must be positive")
    return (
        "local target=nil; "
        "for _,w in ipairs(hl.get_windows()) do "
        "if w.pid == " + str(pid) + " then target=w; break end end; "
        "if not target then error('missing') end; "
        "hl.dispatch(hl.dsp.focus({ window = target })); "
        "hl.dispatch(hl.dsp.window.fullscreen_state({ internal = 2, client = 2 })); "
        "return 'ok'"
    )


def ensure_hypr_fullscreen(pid: int) -> None:
    script = hypr_fullscreen_script(pid)
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        try:
            return_code = run_no_output(["hyprctl", "eval", script], timeout=0.75)
        except FileNotFoundError:
            return
        if return_code == 0:
            return
        time.sleep(0.1)


def hypr_geometry_script(pid: int, geometry: dict[str, int]) -> str:
    if pid <= 0:
        raise ValueError("player PID must be positive")
    value = validate_window_geometry(geometry)
    return (
        "local target=nil; "
        "for _,w in ipairs(hl.get_windows()) do "
        "if w.pid == " + str(pid) + " then target=w; break end end; "
        "if not target then error('missing') end; "
        "hl.dispatch(hl.dsp.window.resize({ x = "
        + str(value["width"])
        + ", y = "
        + str(value["height"])
        + ", relative = false, window = target })); "
        "hl.dispatch(hl.dsp.window.move({ x = "
        + str(value["x"])
        + ", y = "
        + str(value["y"])
        + ", relative = false, window = target })); "
        "return 'ok'"
    )


def geometry_is_visible(geometry: dict[str, int]) -> bool:
    value = validate_window_geometry(geometry)
    try:
        return_code, output = run_bounded_output(
            ["hyprctl", "-j", "monitors"], maximum=MAX_HYPR_BYTES, timeout=2
        )
    except (FileNotFoundError, PlexError):
        return False
    if return_code != 0:
        return False
    try:
        monitors = json.loads(output.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(monitors, list) or len(monitors) > 64:
        return False
    right = value["x"] + value["width"]
    bottom = value["y"] + value["height"]
    for monitor in monitors:
        if not isinstance(monitor, dict):
            continue
        x = finite_integer(monitor.get("x"), 100001)
        y = finite_integer(monitor.get("y"), 100001)
        width = finite_integer(monitor.get("width"), 0)
        height = finite_integer(monitor.get("height"), 0)
        if width <= 0 or height <= 0 or width > 100000 or height > 100000:
            continue
        overlap_width = max(0, min(right, x + width) - max(value["x"], x))
        overlap_height = max(0, min(bottom, y + height) - max(value["y"], y))
        if overlap_width >= min(64, value["width"]) and overlap_height >= min(
            64, value["height"]
        ):
            return True
    return False


def restore_hypr_geometry(pid: int, geometry: dict[str, int]) -> None:
    if not geometry_is_visible(geometry):
        return
    script = hypr_geometry_script(pid, geometry)
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        try:
            return_code = run_no_output(["hyprctl", "eval", script], timeout=0.75)
        except FileNotFoundError:
            return
        if return_code == 0:
            return
        time.sleep(0.1)


def read_hypr_geometry(pid: int) -> dict[str, int] | None:
    if pid <= 0:
        return None
    try:
        return_code, output = run_bounded_output(
            ["hyprctl", "-j", "clients"], maximum=MAX_HYPR_BYTES, timeout=2
        )
    except (FileNotFoundError, PlexError):
        return None
    if return_code != 0:
        return None
    try:
        clients = json.loads(output.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(clients, list) or len(clients) > 4096:
        return None
    for client in clients:
        if not isinstance(client, dict) or finite_integer(client.get("pid"), -1) != pid:
            continue
        position = client.get("at")
        size = client.get("size")
        if (
            client.get("mapped") is not True
            or client.get("floating") is not True
            or finite_integer(client.get("fullscreen"), -1) != 0
            or not isinstance(position, list)
            or len(position) != 2
            or not isinstance(size, list)
            or len(size) != 2
        ):
            return None
        try:
            return validate_window_geometry(
                {
                    "schemaVersion": SCHEMA_VERSION,
                    "x": int(position[0]),
                    "y": int(position[1]),
                    "width": int(size[0]),
                    "height": int(size[1]),
                }
            )
        except (TypeError, ValueError, OverflowError, PlexError):
            return None
    return None
