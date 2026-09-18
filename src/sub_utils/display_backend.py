# SPDX-License-Identifier: GPL-3.0-or-later
import os
from enum import Enum


class DisplayBackend(Enum):
    X11 = "x11"
    WAYLAND = "wayland"
    UNKNOWN = "unknown"


def detect_display_backend():
    from gi.repository import Gdk
    display = Gdk.Display.get_default()
    if display is not None:
        name = display.__gtype__.name
        if "X11" in name:
            return DisplayBackend.X11
        if "Wayland" in name:
            return DisplayBackend.WAYLAND
    return DisplayBackend.UNKNOWN


def is_wayland_session():
    session = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if session in ("x11", "wayland"):
        return session == "wayland"
    return bool(os.environ.get("WAYLAND_DISPLAY"))


def is_wayland():
    return detect_display_backend() == DisplayBackend.WAYLAND


def get_backend_name():
    return detect_display_backend().value


is_wayland_session_actual = is_wayland_session
