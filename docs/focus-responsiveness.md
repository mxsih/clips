# Focus responsiveness follow-up

On 2026-09-25, a Pantheon Wayland user reported a desktop freeze after Win+V.
The journal recorded two Clips launcher activations, zero-timestamp window
activation warnings, and an eventual reboot forced by repeated Ctrl+Alt+Delete.
Background services continued to log during the interval. The saved evidence
does not establish the exact cause of the desktop freeze.

The investigation identified two concrete application defects:

- AT-SPI focus callbacks synchronously queried accessibility properties on the
  GTK thread. Queries to slow clients or the application's own accessibility
  service could stall that thread. Queries now complete asynchronously, cancel
  obsolete requests, and ignore replies after shutdown or newer focus events.
  The unused widget-name lookup was removed.
- Shortcut activation called `present()` without a current GTK event, sending
  timestamp zero to the X11 window manager. Clips now obtains the X server's
  timestamp when necessary, with property-change events enabled as required by
  GDK, then calls `present_with_time()`. Native Wayland keeps GTK's event time.

Five new tests cover a same-main-loop D-Bus property service, 100 focus events
with unanswered queries, stale responses, shutdown cancellation, and toolkit
fallback. All 23 Python tests and all three Flatpak build validation suites
passed. Desktop checks use the installed package and disposable history.

The final installed build passed 10 exact paste cycles into native Wayland GTK
and Xvfb/X11 targets, background capture, clipboard format round-trips, and
100 activation/Escape cycles in each environment. Worker and descriptor counts
were unchanged. Ten additional `gtk-launch`/hide cycles against the user's
normal instance passed with a maximum measured D-Bus response of 9 ms. These
exercise the configured shortcut command, not physical keyboard events.
No zero-timestamp warnings occurred during final validation. Gala still logged
one reused-ping-serial warning during the rapid activation stress test.

Installed Flatpak revision:
`7970246055b1af211187305d1ec5e40b8dd47ee0ea6d9a6f16d23162afae6f2d`.
Logs are retained locally under ignored `build/` as `build-focus.log`,
`unit-tests-focus.log`, `desktop-focus-fix-final.log`, and
`desktop-focus-fix-x11.log`.

These changes address reproducible blocking and activation defects. They do
not establish that every possible compositor or desktop freeze is resolved.
