# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib

from gi.repository import Gio, GLib

from .display_backend import is_wayland


class ClipboardWriter:
    def __init__(self):
        self.process = None
        self.timeout = None
        self.own_digest = None

    def owns_content(self, data):
        return hashlib.sha256(data).digest() == self.own_digest

    def copy_file(self, target, path, content_type, callback):
        if self.process is not None:
            callback(False)
            return
        try:
            with open(path, "rb") as source:
                data = source.read()
            if "url" in (content_type or ""):
                data = data.splitlines()[0] if data else b""
            if is_wayland():
                command = ["wl-copy", "--type", str(target)]
            else:
                command = ["xclip", "-selection", "clipboard", "-target", str(target)]
            process = Gio.Subprocess.new(
                command, Gio.SubprocessFlags.STDIN_PIPE
                | Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE
            )
        except (OSError, GLib.Error):
            callback(False)
            return
        self.process = process
        previous_digest = self.own_digest
        self.own_digest = hashlib.sha256(data).digest()

        def timed_out():
            self.timeout = None
            process.force_exit()
            return GLib.SOURCE_REMOVE

        def finished(process, result):
            if self.timeout is not None:
                GLib.source_remove(self.timeout)
                self.timeout = None
            try:
                process.communicate_finish(result)
                success = process.get_successful()
            except GLib.Error:
                success = False
            self.process = None
            if not success:
                self.own_digest = previous_digest
            callback(success)

        self.timeout = GLib.timeout_add_seconds(5, timed_out)
        process.communicate_async(GLib.Bytes.new(data), None, finished)

    def close(self):
        if self.process is not None:
            self.process.force_exit()
