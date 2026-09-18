# Local validation

Test date: 2026-09-17. Host: elementary OS 8, Pantheon Wayland, x86_64.
Branch: `fix/wayland-x11-stability`, based on upstream `wayland` at
`a12b9bbb2862918086811d53b4ad61c94c01fbc8`.

## Build and automated checks

- Flatpak build completed with elementary SDK 8.2, revision
  `2f404f9944c30b51ce55aa77031a74c9f87351e27466338e4f8771e7e7196a6e`.
- All three Meson suites passed: desktop validation, schema validation, and
  the Python regression suite (18 tests).
- Python compilation and `git diff --check` passed.
- Final app revision installed for regression checks:
  `783dce9e1a0364f8d762d9f4ecd8492fd59c8db26505d836554858806deaa138`.

## Desktop checks

The packaged app ran with disposable history/settings and without access to the
ydotool socket. The Clips window used XWayland in the Wayland session.
Live Wayland input used Mutter RemoteDesktop; the standard portal path was
tested with a private mock D-Bus service, not a real GNOME/KDE permission dialog.

- 100 exact paste cycles into a native Wayland GTK target passed.
- 100 exact paste cycles into an X11 GTK target under Xvfb passed.
- Each run checked hidden startup, 100 activation/Escape cycles, repeated
  activation without hiding, and a single retained window.
- Unicode, multiline text, HTML, file URIs, PNG, and URL clipboard byte
  round-trips passed. This is not a full image/file paste test in every app.
- Protected-copy completion after authentication passed; password entry was
  not automated.
- Housekeeping without a window and repeated monitoring enable passed.
- Background capture from GTK and legacy X11 clipboard owners passed.
- Worker count remained 1. File descriptors remained 21 on Wayland and 23 on
  Xvfb across the 100-cycle runs.

The 100-cycle runs kept monitoring disabled during paste and enabled it for
the final capture check. Subsequent 10-cycle runs exercise monitoring enabled
throughout paste, including preference preservation, on the final app revision.
Both passed, with unchanged worker and file-descriptor counts.

Local logs are retained in ignored `build/`: `build.log`, `unit-tests.log`,
`desktop-wayland.log`, `desktop-x11.log`, `desktop-wayland-enabled.log`, and
`desktop-x11-enabled.log`. Xvfb's private bus produces host portal/keyring
startup warnings and shutdown messages; these are not represented as a clean
desktop-session test. The short-run timeout includes a startup allowance for
those host services. Flatpak can leave an empty placeholder at a denied socket
path; the test checks socket type, not merely path existence.

## Remaining release gates

- Real X11 login: GUI launch, existing autostart, Escape/reopen, clipboard
  capture, and double-click paste into normal applications.
- Real GNOME/KDE portal permission and denial workflows.
- Terminal application identification and actual terminal paste. The shortcut
  sequence itself has a unit test.
- Native Wayland without XWayland, other compositors, and aarch64 builds are
  not validated. Background capture without XWayland is not supported here.
- GitHub CI has not run because this branch has not been pushed.

See [development.md](development.md) for build commands and compatibility
limits. These results do not establish universal Wayland support.
