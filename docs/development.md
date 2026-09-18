# Development and compatibility

This branch builds on upstream `wayland`. It retains GTK 3 and the existing
history database and settings. Clipboard utilities are bundled in the Flatpak;
users do not need ydotool, wtype, a privileged input device, or a new service.

## Build

Install `flatpak-builder`, `io.elementary.Platform//8.2`, and
`io.elementary.Sdk//8.2`. From the repository root:

```sh
flatpak-builder --user --force-clean --state-dir=build/flatpak-state --repo=build/repo --default-branch=wayland-fix-test build/app com.github.hezral.clips.yml
flatpak install --user --reinstall "$PWD/build/repo" com.github.hezral.clips//wayland-fix-test
```

The builder runs the regression tests and validates the desktop entry and
settings schema. Python dependencies have exact versions and download hashes;
native clipboard utilities have pinned commits. Build dependencies are fetched
before entering the build sandbox; pip does not use the network during builds.
The SDK branch remains maintained upstream, so identical binary output across
different SDK revisions is not promised.

Both Flatpak branches use the same application ID and normally share settings
and history. Installing a development branch can make it the default launcher.
To retain an existing stable installation as the default:

```sh
flatpak make-current --user com.github.hezral.clips stable
```

## Desktop support

| Environment | Background history | Automatic paste |
| --- | --- | --- |
| X11 | GTK clipboard monitoring | XTest, restoring the previous window |
| Pantheon Wayland with XWayland | GTK through the compositor clipboard bridge | Mutter RemoteDesktop fallback when no standard portal is exposed |
| Wayland with a RemoteDesktop portal and XWayland | Depends on the compositor clipboard bridge | Portal keyboard permission required |
| Wayland without XWayland | Background capture is not supported by this implementation | Available only when a supported input API is exposed |

The standard portal is preferred. Denying permission never triggers a fallback
to another input backend. A failed or unsupported paste leaves the item copied
and displays a notification. Mutter's API is private and version-dependent;
the fallback checks its version and keyboard capability. No claim is made that
it will work unchanged in future compositor versions.

Wayland focus restoration is controlled by the compositor. Clips hides before
injecting input and cancels delayed paste when reopened. Users switching to a
different application during that handoff can change the destination.
Application exclusions, protected-app matching, terminal shortcut detection,
and focus-based hiding depend on accessible application metadata. A missing
source name must not crash clipboard capture.

The manifest grants access specifically to Mutter RemoteDesktop and the AT-SPI
bus used by active-app tracking. It does not grant unrestricted session-bus
access. Existing permissions for keyring, file-manager integration, and file
previews remain in place.

## Tests

The unit suite uses a private D-Bus session and an actual mock portal service:

```sh
dbus-run-session -- python3 -m unittest discover -s tests -v
```

It covers portal response ordering, denial, cancellation, session closure,
missing backends, key-release cleanup, session detection, clipboard publication,
helper failures, overlapping copies, and shutdown.

`tests/desktop_checks.py` exercises the installed Flatpak with disposable
history and keyfile settings. It drives the double-click handler, checks
protected-copy completion after authentication, round-trips clipboard formats,
tests housekeeping without a window, and measures worker/file-descriptor growth.
It does not automate password entry or replace an interactive desktop test.

For headless X11, install the test-only `xvfb` package and run:

```sh
dbus-run-session -- xvfb-run -a flatpak run --branch=wayland-fix-test --nosocket=wayland --nofilesystem=/tmp/.ydotool_socket --env=XDG_SESSION_TYPE=x11 --env=GDK_BACKEND=x11 --filesystem="$PWD/tests":ro --command=python3 com.github.hezral.clips "$PWD/tests/desktop_checks.py" --cycles=100
```

For live Wayland, stop other clipboard-history instances first to avoid recording
test data. The test changes the desktop clipboard and presents test windows;
leave focus with the test until it finishes. Then restart the normal instance.

```sh
flatpak run --branch=wayland-fix-test --socket=wayland --nofilesystem=/tmp/.ydotool_socket --env=GDK_BACKEND=x11 --filesystem="$PWD/tests":ro --command=python3 com.github.hezral.clips "$PWD/tests/desktop_checks.py" --target-backend=wayland --cycles=100
```

Use `--monitoring=disabled` to check that Quick Paste also preserves a disabled
monitoring preference. Native GNOME/KDE portal tests and a real X11 login remain
separate from mock-portal and Xvfb coverage.
