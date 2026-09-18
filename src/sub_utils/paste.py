# SPDX-License-Identifier: GPL-3.0-or-later
import uuid
import xml.etree.ElementTree as ET

from gi.repository import Gio, GLib

from .display_backend import is_wayland_session

PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
REMOTE = "org.freedesktop.portal.RemoteDesktop"
MUTTER = "org.gnome.Mutter.RemoteDesktop"
MUTTER_PATH = "/org/gnome/Mutter/RemoteDesktop"
CONTROL, SHIFT, V = 0xffe3, 0xffe1, 0x76
TERMINALS = {"io.elementary.terminal", "gnome-terminal", "org.gnome.terminal",
             "org.gnome.console", "konsole", "org.kde.konsole", "xterm",
             "kitty", "alacritty", "tilix", "terminal"}


class PasteController:
    def __init__(self, app):
        self.app = app
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.session = None
        self.session_ready = False
        self.backend = None
        self.closed_signal = None
        self.request = None
        self.request_signal = None
        self.timer = None
        self.generation = 0
        self.injecting = False
        self.xdisplay = None
        self.target = None

    def notify(self, message):
        self.app.logger.warning(message)
        notification = Gio.Notification.new("Clips")
        notification.set_body(message)
        self.app.send_notification("quick-paste", notification)

    def _call(self, destination, path, interface, method, parameters, callback):
        def done(bus, result):
            try:
                reply = bus.call_finish(result)
            except GLib.Error as error:
                callback(None, error)
            else:
                callback(reply.unpack() if reply else (), None)
        self.bus.call(destination, path, interface, method, parameters, None,
                      Gio.DBusCallFlags.NONE, 5000, None, done)

    def remember_target(self):
        if is_wayland_session():
            return
        try:
            from Xlib.display import Display
            if self.xdisplay is None:
                self.xdisplay = Display()
            root = self.xdisplay.screen().root
            prop = root.get_full_property(self.xdisplay.intern_atom("_NET_ACTIVE_WINDOW"), 0)
            self.target = int(prop.value[0]) if prop is not None else self.xdisplay.get_input_focus().focus.id
        except Exception:
            self.target = None

    def cancel(self):
        self.generation += 1
        if self.timer is not None:
            GLib.source_remove(self.timer)
            self.timer = None
        if self.request is not None:
            self._call(PORTAL, self.request, "org.freedesktop.portal.Request", "Close", None, lambda *_: None)
            self.request = None
        if self.request_signal is not None:
            self.bus.signal_unsubscribe(self.request_signal)
            self.request_signal = None
        if self.session is not None and not self.session_ready:
            self._stop_session()

    def paste(self):
        if not self.app.gio_settings.get_boolean("quick-paste") or self.injecting:
            return
        self.cancel()
        generation = self.generation
        if not is_wayland_session():
            if self.target is None:
                self.notify("Item copied. The previous window is unavailable for automatic paste.")
                return
            self._ready(generation)
        elif self.session is not None:
            self._ready(generation)
        else:
            self._call(PORTAL, PORTAL_PATH, "org.freedesktop.DBus.Introspectable", "Introspect", None,
                       lambda reply, error: self._discover(generation, reply, error))

    def _discover(self, generation, reply, error):
        if generation != self.generation:
            return
        if error:
            name = Gio.DBusError.get_remote_error(error)
            if name not in ("org.freedesktop.DBus.Error.ServiceUnknown", "org.freedesktop.DBus.Error.UnknownMethod"):
                self._failed(error)
                return
            interfaces = []
        else:
            try:
                interfaces = [item.get("name") for item in ET.fromstring(reply[0]).findall("interface")]
            except (ET.ParseError, IndexError) as error:
                self._failed(error)
                return
        if REMOTE in interfaces:
            self._portal_request("CreateSession", (), {
                "session_handle_token": GLib.Variant("s", "clips_" + uuid.uuid4().hex)
            }, generation, lambda data: self._portal_created(generation, data))
        else:
            self._call(MUTTER, MUTTER_PATH, "org.freedesktop.DBus.Properties", "GetAll",
                       GLib.Variant("(s)", (MUTTER,)),
                       lambda reply, error: self._mutter_available(generation, reply, error))

    def _portal_request(self, method, prefix, options, generation, callback):
        token = "clips_" + uuid.uuid4().hex
        options["handle_token"] = GLib.Variant("s", token)
        sender = self.bus.get_unique_name()[1:].replace(".", "_")
        path = "/org/freedesktop/portal/desktop/request/" + sender + "/" + token
        self.request = path

        def response(bus, sender, object_path, interface, signal, parameters, user_data):
            if generation != self.generation:
                return
            self.bus.signal_unsubscribe(self.request_signal)
            self.request_signal = None
            self.request = None
            status, data = parameters.unpack()
            if status:
                self._stop_session()
                self.notify("Item copied. Automatic paste permission was not granted.")
            else:
                callback(data)

        self.request_signal = self.bus.signal_subscribe(
            PORTAL, "org.freedesktop.portal.Request", "Response", path, None,
            Gio.DBusSignalFlags.NONE, response, None
        )
        signature = {"CreateSession": "(a{sv})", "SelectDevices": "(oa{sv})", "Start": "(osa{sv})"}[method]

        def requested(reply, error):
            if generation == self.generation and error:
                self.cancel()
                self._failed(error)
        self._call(PORTAL, PORTAL_PATH, REMOTE, method,
                   GLib.Variant(signature, (*prefix, options)), requested)

    def _portal_created(self, generation, data):
        self._set_session("portal", data["session_handle"])
        self._portal_request("SelectDevices", (self.session,), {"types": GLib.Variant("u", 1)},
                             generation, lambda _: self._portal_request(
                                 "Start", (self.session, ""), {}, generation,
                                 lambda data: self._portal_started(generation, data)))

    def _portal_started(self, generation, data):
        if not data.get("devices", 0) & 1:
            self._failed("Keyboard access unavailable")
            return
        self._ready(generation)

    def _mutter_available(self, generation, reply, error):
        if generation != self.generation:
            return
        if error or reply[0].get("Version") != 1 or not reply[0].get("SupportedDeviceTypes", 0) & 1:
            self._failed(error or "Unsupported Mutter input API")
            return

        def created(reply, error):
            if error:
                if generation == self.generation:
                    self._failed(error)
                return
            if generation != self.generation:
                self._call(MUTTER, reply[0], MUTTER + ".Session", "Stop", None, lambda *_: None)
                return
            self._set_session("mutter", reply[0])
            self._call(MUTTER, self.session, MUTTER + ".Session", "Start", None,
                       lambda reply, error: self._failed(error) if error else self._ready(generation))
        self._call(MUTTER, MUTTER_PATH, MUTTER, "CreateSession", None, created)

    def _set_session(self, backend, path):
        self.backend, self.session = backend, path
        self.app.logger.info("Quick Paste backend: %s", backend)
        destination = PORTAL if backend == "portal" else MUTTER
        interface = "org.freedesktop.portal.Session" if backend == "portal" else MUTTER + ".Session"
        self.closed_signal = self.bus.signal_subscribe(
            destination, interface, "Closed", path, None, Gio.DBusSignalFlags.NONE,
            lambda *_: self._session_closed(), None)

    def _session_closed(self):
        self.session = None
        self.session_ready = False
        self.backend = None
        self.cancel()
        if self.closed_signal is not None:
            self.bus.signal_unsubscribe(self.closed_signal)
            self.closed_signal = None

    def _stop_session(self):
        if self.session is not None:
            if self.backend == "portal":
                self._call(PORTAL, self.session, "org.freedesktop.portal.Session", "Close", None, lambda *_: None)
            else:
                self._call(MUTTER, self.session, MUTTER + ".Session", "Stop", None, lambda *_: None)
        self._session_closed()

    def _failed(self, error):
        self.app.logger.debug("Quick paste backend failed: %s", error)
        self._stop_session()
        self.notify("Item copied. Automatic paste is unavailable in this session.")

    def _ready(self, generation):
        if generation != self.generation:
            return
        self.session_ready = self.session is not None
        self.app.main_window.hide()

        def after_hide():
            self.timer = None
            if generation != self.generation or self.app.main_window.is_visible() or self.app.main_window.is_active():
                return GLib.SOURCE_REMOVE
            name = self.app.window_manager.last_seen.get("title") or ""
            keys = [CONTROL, SHIFT, V] if name.casefold() in TERMINALS else [CONTROL, V]
            if is_wayland_session():
                self._send_keys(keys, generation)
            else:
                self._x11_keys(keys)
            return GLib.SOURCE_REMOVE

        self.timer = GLib.timeout_add(300, after_hide)

    def _x11_keys(self, keys):
        from Xlib import X
        from Xlib.ext.xtest import fake_input
        pressed = []
        try:
            window = self.xdisplay.create_resource_object("window", self.target)
            window.get_attributes()
            window.set_input_focus(X.RevertToParent, X.CurrentTime)
            self.xdisplay.sync()
            for key in keys:
                code = self.xdisplay.keysym_to_keycode(key)
                if not code:
                    raise RuntimeError("Unavailable keycode")
                pressed.append(code)
                fake_input(self.xdisplay, X.KeyPress, code)
        except Exception as error:
            self._failed(error)
        finally:
            for code in reversed(pressed):
                try:
                    fake_input(self.xdisplay, X.KeyRelease, code)
                except Exception:
                    pass
            try:
                self.xdisplay.sync()
            except Exception as error:
                self.app.logger.debug("X11 connection closed during paste: %s", error)

    def _send_keys(self, keys, generation):
        backend, session = self.backend, self.session
        if session is None:
            return
        self.injecting = True
        events = [(key, True) for key in keys] + [(key, False) for key in reversed(keys)]
        pressed = []
        failures = []
        cleaning = False

        def step(reply=None, error=None):
            nonlocal cleaning
            if error:
                failures.append(error)
            if not cleaning and (failures or generation != self.generation):
                cleaning = True
                events[:] = [(key, False) for key in reversed(pressed)]
                pressed.clear()
            if not events:
                self.injecting = False
                if failures:
                    self._failed(failures[0])
                return
            key, down = events.pop(0)
            if down:
                pressed.append(key)

            def sent(reply, error):
                if not error and not down and key in pressed:
                    pressed.remove(key)
                step(reply, error)

            if backend == "portal":
                self._call(PORTAL, PORTAL_PATH, REMOTE, "NotifyKeyboardKeysym",
                           GLib.Variant("(oa{sv}iu)", (session, {}, key, int(down))), sent)
            else:
                self._call(MUTTER, session, MUTTER + ".Session", "NotifyKeyboardKeysym",
                           GLib.Variant("(ub)", (key, down)), sent)
        step()

    def close(self):
        self.cancel()
        self._stop_session()
        if self.xdisplay is not None:
            self.xdisplay.close()
            self.xdisplay = None
