# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2023 Adi Hezral <hezral@gmail.com>

"""Track application focus through the desktop AT-SPI accessibility bus."""

import os
from typing import Dict, Optional, Callable

import gi
gi.require_version('GLib', '2.0')
gi.require_version('Gio', '2.0')
from gi.repository import GLib, Gio
from .sub_utils.logging_utils import log_function_calls



class ActiveWindowManager():
    """Wayland-compatible active window manager using AT-SPI event subscription."""

    @log_function_calls
    def __init__(self, gtk_application=None):

        super().__init__()
        self.app = gtk_application
        self.last_seen = {'title': None}
        self.atspi_conn = None
        self.main_loop = None
        self._initialized = False  # Track if manager has been started
        self._focus_request = None
        self._focus_generation = 0
        
    @log_function_calls
    def _get_property(self, connection, destination, path, interface, prop_name, cancellable, callback):

        """Keep focus queries off the UI thread, including queries to ourselves."""
        def finished(bus, result):
            try:
                ret = bus.call_finish(result)
                value = ret.get_child_value(0).get_variant().unpack()
            except GLib.Error as error:
                if self.app and not cancellable.is_cancelled():
                    self.app.logger.debug("Error getting property %s: %s", prop_name, error)
                value = None
            if not cancellable.is_cancelled():
                callback(value)

        connection.call(
                destination,
                path,
                "org.freedesktop.DBus.Properties",
                "Get",
                GLib.Variant("(ss)", (interface, prop_name)),
                GLib.VariantType("(v)"),
                Gio.DBusCallFlags.NONE,
                1000,
                cancellable, finished
            )

    @log_function_calls
    def _on_signal(self, connection, sender_name, object_path, interface_name, signal_name, parameters, user_data):

        """Event callback for AT-SPI signals."""
        # Unpack parameters
        args = parameters.unpack()
        
        # Filter 1: Only care about Focus or Activate
        is_focused = (signal_name == "StateChanged" and args[0] == "focused" and args[1] == 1)
        is_activate = (signal_name == "Activate")
        
        if not (is_focused or is_activate):
            return

        self._focus_generation += 1
        generation = self._focus_generation
        if self._focus_request is not None:
            self._focus_request.cancel()
        request = self._focus_request = Gio.Cancellable()
        root = "/org/a11y/atspi/accessible/root"

        def resolved(name):
            if generation != self._focus_generation or request.is_cancelled():
                return
            self._focus_request = None
            if not name:
                return
            self.last_seen['title'] = name
            if self.app:
                self.app.logger.debug("Active app changed to: %s", name)
            self.handle_change(self.last_seen)

        def named(name):
            if name:
                resolved(name)
            else:
                self._get_property(connection, sender_name, root,
                                   "org.a11y.atspi.Application", "ToolkitName", request, resolved)

        self._get_property(connection, sender_name, root,
                           "org.a11y.atspi.Accessible", "Name", request, named)

    @log_function_calls
    def _run(self, callback):

        """Initialize AT-SPI event subscription."""
        self.callback = callback
        
        if self._initialized:
             return

        try:
            # A. Connect to Session Bus to find AT-SPI
            try:
                session_bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                reply = session_bus.call_sync(
                    "org.a11y.Bus", "/org/a11y/bus", "org.a11y.Bus", "GetAddress", None,
                    GLib.VariantType("(s)"), Gio.DBusCallFlags.NONE, 1000, None
                )
                atspi_address = reply.unpack()[0]
            except GLib.Error:
                atspi_address = os.environ.get("AT_SPI_BUS_ADDRESS")
                if not atspi_address:
                    raise
            
            if self.app:
                self.app.logger.info(f"Found AT-SPI bus at: {atspi_address}")

        except Exception as e:
            if self.app:
                self.app.logger.error(f"Could not find AT-SPI bus: {e}")
                self.app.logger.warning("Active window detection disabled")
            return

        # B. Connect to the AT-SPI Bus
        try:
            self.atspi_conn = Gio.DBusConnection.new_for_address_sync(
                atspi_address,
                Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
                None, None
            )
        except Exception as e:
            if self.app:
                self.app.logger.error(f"Could not connect to AT-SPI socket: {e}")
            return

        if self.atspi_conn:
            if self.app:
                self.app.logger.info("Connected to AT-SPI. Registering events...")

            # C. Register Event Types
            try:
                # 1. Register Object Focus
                self.atspi_conn.call_sync(
                    "org.a11y.atspi.Registry", "/org/a11y/atspi/registry", "org.a11y.atspi.Registry", 
                    "RegisterEvent", GLib.Variant("(sass)", ("object:state-changed:focused", [], "")),
                    None, Gio.DBusCallFlags.NONE, 1000, None
                )
                # 2. Register Window Activate
                self.atspi_conn.call_sync(
                    "org.a11y.atspi.Registry", "/org/a11y/atspi/registry", "org.a11y.atspi.Registry", 
                    "RegisterEvent", GLib.Variant("(sass)", ("window:activate", [], "")),
                    None, Gio.DBusCallFlags.NONE, 1000, None
                )
                
                if self.app:
                    self.app.logger.info("AT-SPI events registered successfully")
                    
            except Exception as e:
                if self.app:
                    self.app.logger.warning(f"Failed to register events: {e}")
                self.atspi_conn.close_sync(None)
                self.atspi_conn = None
                return

            # D. Subscribe to signals
            self.atspi_conn.signal_subscribe(
                None, "org.a11y.atspi.Event.Object", "StateChanged", 
                None, None, Gio.DBusSignalFlags.NONE, self._on_signal, None
            )
            self.atspi_conn.signal_subscribe(
                None, "org.a11y.atspi.Event.Window", "Activate", 
                None, None, Gio.DBusSignalFlags.NONE, self._on_signal, None
            )
            
            self._initialized = True  # Mark as initialized
            
            if self.app:
                self.app.logger.info("active_window_manager (AT-SPI event-driven) started")

    @log_function_calls
    def _stop(self):

        """Stop the active window manager."""
        self._focus_generation += 1
        if self._focus_request is not None:
            self._focus_request.cancel()
            self._focus_request = None
        if self.app:
            self.app.logger.info("active_window_manager (AT-SPI) stopped")
        
        # Unsubscribe from signals if connection exists
        if self.atspi_conn:
            try:
                # Note: signal_unsubscribe would require subscription IDs
                # For now, just close the connection
                self.atspi_conn.close_sync(None)
            except Exception as e:
                if self.app:
                    self.app.logger.debug(f"Error closing AT-SPI connection: {e}")
        
        self._initialized = False

    @log_function_calls
    def handle_change(self, new_state: dict):

        """This method is called when the active window changes."""
        if self.callback:
            self.callback(new_state['title'])
