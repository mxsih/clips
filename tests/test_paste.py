import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gi.repository import Gio, GLib

from src.sub_utils.display_backend import is_wayland_session
from src.sub_utils.paste import PasteController, PORTAL, PORTAL_PATH, REMOTE, CONTROL, SHIFT, V


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    context = GLib.MainContext.default()
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("Timed out waiting for asynchronous operation")
        context.iteration(False)
        time.sleep(0.001)


class Portal:
    XML = '''<node><interface name="org.freedesktop.portal.RemoteDesktop">
      <method name="CreateSession"><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
      <method name="SelectDevices"><arg type="o" direction="in"/><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
      <method name="Start"><arg type="o" direction="in"/><arg type="s" direction="in"/><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
      <method name="NotifyKeyboardKeysym"><arg type="o" direction="in"/><arg type="a{sv}" direction="in"/><arg type="i" direction="in"/><arg type="u" direction="in"/></method>
    </interface></node>'''
    SESSION_XML = '''<node><interface name="org.freedesktop.portal.Session">
      <method name="Close"/><signal name="Closed"/>
    </interface></node>'''

    def __init__(self, bus):
        self.bus = bus
        self.events = []
        self.denied = False
        self.failure_at = None
        self.on_key = None
        self.closed = False
        self.path = "/org/freedesktop/portal/desktop/session/test/clips"
        self.registrations = [bus.register_object(PORTAL_PATH, Gio.DBusNodeInfo.new_for_xml(self.XML).interfaces[0], self.call, None, None)]
        self.registrations.append(bus.register_object(self.path, Gio.DBusNodeInfo.new_for_xml(self.SESSION_XML).interfaces[0], self.call, None, None))
        bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "RequestName",
                      GLib.Variant("(su)", (PORTAL, 0)), None, Gio.DBusCallFlags.NONE, 1000, None)

    def call(self, bus, sender, path, interface, method, parameters, invocation):
        values = parameters.unpack()
        if method == "Close":
            self.closed = True
            invocation.return_value(None)
        elif method == "NotifyKeyboardKeysym":
            self.events.append((values[2], bool(values[3])))
            if self.on_key is not None:
                self.on_key()
            if len(self.events) == self.failure_at:
                invocation.return_dbus_error("org.freedesktop.portal.Error.Failed", "Simulated input failure")
            else:
                invocation.return_value(None)
        else:
            options = values[-1]
            request = "/org/freedesktop/portal/desktop/request/" + sender[1:].replace(".", "_") + "/" + options['handle_token']
            data = {}
            if method == "CreateSession":
                data["session_handle"] = GLib.Variant("s", self.path)
            elif method == "SelectDevices":
                assert options['types'] == 1
            else:
                data["devices"] = GLib.Variant("u", 1)
            # Respond before the method reply to cover the portal signal race.
            bus.emit_signal(sender, request, "org.freedesktop.portal.Request", "Response",
                            GLib.Variant("(ua{sv})", (1 if self.denied else 0, data)))
            invocation.return_value(GLib.Variant("(o)", (request,)))

    def close(self):
        for registration in self.registrations:
            self.bus.unregister_object(registration)


class PasteTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland"})
        self.environment.start()
        self.app = SimpleNamespace(gio_settings=Mock(), main_window=Mock(), logger=Mock(),
                                   window_manager=SimpleNamespace(last_seen={"title": "Editor"}), send_notification=Mock())
        self.app.gio_settings.get_boolean.return_value = True
        self.app.main_window.is_visible.return_value = False
        self.app.main_window.is_active.return_value = False
        self.controller = PasteController(self.app)
        self.portal = Portal(self.controller.bus)

    def tearDown(self):
        self.controller.close()
        wait_for(lambda: not self.controller.injecting)
        for _ in range(20):
            GLib.MainContext.default().iteration(False)
        self.portal.close()
        self.environment.stop()

    def test_portal_paste(self):
        self.controller.paste()
        wait_for(lambda: len(self.portal.events) == 4 and not self.controller.injecting)
        self.assertEqual(self.portal.events, [(CONTROL, True), (V, True), (V, False), (CONTROL, False)])
        self.app.main_window.hide.assert_called_once()
        self.app.send_notification.assert_not_called()

    def test_permission_denial_never_falls_back(self):
        with patch.object(self.controller, "_mutter_available") as fallback:
            self.portal.denied = True
            self.controller.paste()
            wait_for(lambda: self.app.send_notification.called)
            fallback.assert_not_called()
        self.assertEqual(self.portal.events, [])
        self.app.main_window.hide.assert_not_called()

    def test_failure_releases_all_attempted_keys(self):
        self.portal.failure_at = 2
        self.controller.paste()
        wait_for(lambda: self.app.send_notification.called)
        self.assertEqual(self.portal.events, [(CONTROL, True), (V, True), (V, False), (CONTROL, False)])

    def test_reopening_cancels_delayed_paste(self):
        self.controller.paste()
        wait_for(lambda: self.controller.timer is not None)
        self.controller.cancel()
        self.assertIsNone(self.controller.timer)
        self.assertEqual(self.portal.events, [])

    def test_failed_release_is_retried(self):
        self.portal.failure_at = 4
        self.controller.paste()
        wait_for(lambda: self.app.send_notification.called)
        self.assertEqual(self.portal.events, [(CONTROL, True), (V, True),
                                             (V, False), (CONTROL, False), (CONTROL, False)])

    def test_terminal_uses_shift(self):
        self.app.window_manager.last_seen['title'] = 'io.elementary.terminal'
        self.controller.paste()
        wait_for(lambda: len(self.portal.events) == 6 and not self.controller.injecting)
        self.assertEqual(self.portal.events, [(CONTROL, True), (SHIFT, True), (V, True),
                                             (V, False), (SHIFT, False), (CONTROL, False)])

    def test_cancellation_during_injection_releases_modifier(self):
        self.portal.on_key = self.controller.cancel
        self.controller.paste()
        wait_for(lambda: len(self.portal.events) == 2 and not self.controller.injecting)
        self.assertEqual(self.portal.events, [(CONTROL, True), (CONTROL, False)])

    def test_closed_session_is_not_reused(self):
        self.controller.paste()
        wait_for(lambda: self.controller.timer is not None)
        self.portal.bus.emit_signal(None, self.portal.path, 'org.freedesktop.portal.Session', 'Closed', None)
        wait_for(lambda: self.controller.session is None)
        self.assertIsNone(self.controller.timer)
        self.assertFalse(self.controller.session_ready)

    def test_missing_backends_leave_item_copied(self):
        self.portal.close()
        self.portal.registrations = []
        self.controller.paste()
        wait_for(lambda: self.app.send_notification.called)
        self.app.main_window.hide.assert_not_called()
        self.assertEqual(self.portal.events, [])

    def test_disabled_quick_paste_does_nothing(self):
        self.app.gio_settings.get_boolean.return_value = False
        self.controller.paste()
        self.assertIsNone(self.controller.request)
        self.app.main_window.hide.assert_not_called()

    def test_visible_window_cannot_receive_paste(self):
        self.app.main_window.is_visible.return_value = True
        self.controller.paste()
        wait_for(lambda: self.controller.session_ready and self.controller.timer is None)
        self.assertEqual(self.portal.events, [])

    def test_xwayland_is_still_wayland_session(self):
        with patch.dict(os.environ, {"GDK_BACKEND": "x11"}):
            self.assertTrue(is_wayland_session())

    def test_x11_ignores_stale_wayland_environment(self):
        with patch.dict(os.environ, {"XDG_SESSION_TYPE": "x11", "WAYLAND_DISPLAY": "wayland-0"}):
            self.assertFalse(is_wayland_session())


if __name__ == "__main__":
    unittest.main()
