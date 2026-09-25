import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gi.repository import Gio, GLib

from src.active_window_manager import ActiveWindowManager


class FocusTests(unittest.TestCase):
    def setUp(self):
        settings = Mock()
        settings.get_boolean.return_value = False
        with patch.object(Gio.Settings, 'new', return_value=settings):
            self.manager = ActiveWindowManager(SimpleNamespace(gio_settings=settings, logger=Mock()))
        self.manager.callback = Mock()
        self.pending = []
        self.bus = Mock()
        self.bus.call.side_effect = lambda *args: self.pending.append(args)
        self.bus.call_finish.side_effect = lambda result: result

    def tearDown(self):
        self.manager._stop()

    def focus(self, bus=None, sender=':1.2'):
        self.manager._on_signal(bus or self.bus, sender, '/window',
                                'org.a11y.atspi.Event.Window', 'Activate',
                                GLib.Variant('(s)', ('',)), None)

    def reply(self, index, name):
        call = self.pending[index]
        call[-1](self.bus, GLib.Variant('(v)', (GLib.Variant('s', name),)))

    def test_unanswered_query_does_not_block_focus_events(self):
        started = time.monotonic()
        for _ in range(100):
            self.focus()
        self.assertLess(time.monotonic() - started, 0.5)
        self.bus.call_sync.assert_not_called()
        self.assertTrue(all(call[-2].is_cancelled() for call in self.pending[:-1]))
        self.reply(99, 'Editor')
        self.manager.callback.assert_called_once_with('Editor')

    def test_late_reply_cannot_replace_newer_focus(self):
        self.focus()
        self.focus(sender=':1.3')
        self.reply(1, 'Terminal')
        self.reply(0, 'Old editor')
        self.assertEqual(self.manager.last_seen['title'], 'Terminal')
        self.manager.callback.assert_called_once_with('Terminal')

    def test_shutdown_discards_pending_reply(self):
        self.focus()
        self.manager._stop()
        self.reply(0, 'Editor')
        self.manager.callback.assert_not_called()

    def test_missing_name_falls_back_without_blocking(self):
        self.focus()
        self.reply(0, '')
        self.assertEqual(self.pending[1][4].unpack(), ('org.a11y.atspi.Application', 'ToolkitName'))
        self.reply(1, 'Toolkit')
        self.manager.callback.assert_called_once_with('Toolkit')

    def test_same_main_loop_can_answer_its_own_property_query(self):
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        interface = Gio.DBusNodeInfo.new_for_xml('''<node>
          <interface name="org.a11y.atspi.Accessible">
            <property name="Name" type="s" access="read"/>
          </interface></node>''').interfaces[0]
        registration = bus.register_object('/org/a11y/atspi/accessible/root', interface, None,
                                           lambda *_: GLib.Variant('s', 'Clips'), None)
        try:
            started = time.monotonic()
            self.focus(bus, bus.get_unique_name())
            self.assertLess(time.monotonic() - started, 0.1)
            deadline = started + 2
            while not self.manager.callback.called and time.monotonic() < deadline:
                GLib.MainContext.default().iteration(False)
                time.sleep(0.001)
            self.manager.callback.assert_called_once_with('Clips')
        finally:
            bus.unregister_object(registration)
