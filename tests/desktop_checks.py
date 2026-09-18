"""Run against a built Flatpak, with disposable XDG directories and test data."""
import argparse
import base64
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import threading

parser = argparse.ArgumentParser()
parser.add_argument('--target', action='store_true')
parser.add_argument('--target-backend', choices=['x11', 'wayland'], default='x11')
parser.add_argument('--cycles', type=int, default=100)
parser.add_argument('--monitoring', choices=['enabled', 'disabled'], default='enabled')
args = parser.parse_args()
sys.argv = [sys.argv[0]]

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gdk


if args.target:
    window = Gtk.Window(title='Clips paste test target')
    entry = Gtk.Entry()
    window.add(entry)
    window.set_default_size(480, 100)
    window.show_all()
    entry.grab_focus()

    def command(stream, condition):
        line = stream.readline()
        if not line:
            Gtk.main_quit()
            return False
        command = json.loads(line)
        if command == 'focus':
            entry.set_text('')
            window.present()
            entry.grab_focus()
            GLib.timeout_add(200, lambda: print(json.dumps({'focused': window.is_active()}), flush=True))
        elif command == 'copy':
            Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text('Clips background capture test', -1)
            print(json.dumps({'copied': True}), flush=True)
        elif command == 'read':
            print(json.dumps({'text': entry.get_text()}), flush=True)
        return True

    GLib.io_add_watch(sys.stdin, GLib.IO_IN | GLib.IO_HUP, command)
    print(json.dumps({'ready': Gdk.Display.get_default().__gtype__.name}), flush=True)
    Gtk.main()
    sys.exit(0)


profile = tempfile.TemporaryDirectory(prefix='clips-desktop-test-')
for name in ('config', 'cache', 'data'):
    path = Path(profile.name) / name
    path.mkdir()
    os.environ['XDG_' + name.upper() + '_HOME'] = str(path)
os.environ['GSETTINGS_BACKEND'] = 'keyfile'
sys.path.insert(0, '/app/share/com.github.hezral.clips')
from clips.main import Application

app = Application()
assert not Path('/tmp/.ydotool_socket').is_socket(), 'Test sandbox must not expose ydotool'
app.props.application_id = 'com.github.hezral.clips.DesktopTests'
for key, value in {'first-run': False, 'hide-on-startup': True, 'quick-paste': True,
                   'persistent-mode': True, 'auto-housekeeping': False,
                   'shake-reveal': False, 'debug-mode': False}.items():
    app.gio_settings.set_boolean(key, value)

environment = dict(os.environ, GDK_BACKEND=args.target_backend)
target = subprocess.Popen([sys.executable, __file__, '--target'], env=environment,
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
state = {'cycle': 0, 'errors': [], 'reading': False, 'deadline': 0}
payload = Path(profile.name) / 'payload.txt'
formats = [
    ('text/plain;charset=utf-8', 'plaintext', 'First line\nSecond line \u03bb \u4e2d'.encode()),
    ('text/html', 'html', b'<b>Clips rich text test</b>'),
    ('text/uri-list', 'files', b'file:///tmp/clips-test-file.txt\r\n'),
    ('image/png', 'image', base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')),
    ('text/plain;charset=utf-8', 'url', b'https://example.org/test\n'),
]


def check_format():
    if not formats:
        print('PASS: Unicode, multiline, HTML, file URI, PNG and URL clipboard formats', flush=True)
        state['threads'] = threading.active_count()
        state['fds'] = len(list(Path('/proc/self/fd').iterdir()))
        if args.monitoring == 'enabled':
            app.cache_manager.enable_clipboard_monitoring()
        GLib.idle_add(start_cycle)
        return False
    mime, kind, data = formats.pop(0)
    payload.write_bytes(data)
    expected = data.rstrip(b'\n') if kind == 'url' else data

    def published(success):
        try:
            assert success, f'{mime} publication failed'
            content = app.clipboard_manager.clipboard.wait_for_contents(Gdk.Atom.intern(mime, False))
            assert content is not None and content.get_data() == expected, f'{mime} content mismatch'
            GLib.idle_add(check_format)
        except Exception as error:
            fail(error)
    app.clipboard_manager.writer.copy_file(mime, str(payload), kind, published)
    return False


def fail(error):
    state['errors'].append(str(error))
    print('FAIL:', error, flush=True)
    app.quit()


def send(command):
    target.stdin.write(json.dumps(command) + '\n')
    target.stdin.flush()


def start_cycle():
    if state['cycle'] == args.cycles:
        app.cache_manager.enable_clipboard_monitoring()
        state['capture'] = 1
        send('copy')
        return False
    send('focus')
    return False


def check_capture():
    try:
        assert app.cache_manager.get_total_clips()[0][0] >= args.cycles + state['capture'], 'Background clipboard capture failed'
        if state['capture'] == 1:
            state['capture'] = 2
            subprocess.run(['xclip', '-selection', 'clipboard'], input=b'Clips legacy X11 capture test', check=True)
            GLib.timeout_add(1000, check_capture)
            return False
        print('PASS: GTK and legacy X11 background capture after paste cycles', flush=True)
        threads = threading.active_count()
        fds = len(list(Path('/proc/self/fd').iterdir()))
        assert threads <= state['threads'] + 2, 'Worker threads accumulated'
        assert fds <= state['fds'] + 12, 'File descriptors accumulated'
        print(f"PASS: resources threads {state['threads']}->{threads}, descriptors {state['fds']}->{fds}", flush=True)
        print(f"PASS: {state['cycle']} exact pastes into {args.target_backend} target", flush=True)
    except Exception as error:
        fail(error)
        return False
    app.quit()
    return False


def copy_item():
    try:
        app.activate()
        text = f"Clips test {state['cycle']} \u03bb \u4e2d"
        state['expected'] = text
        name = hashlib.sha256(text.encode()).hexdigest() + '.txt'
        cache_file = Path(app.cache_manager.cache_filedir) / name
        cache_file.write_text(text)
        protected = state['cycle'] == args.cycles - 1
        if protected:
            success, encrypted = app.utils.do_encryption('encrypt', 'test password', str(cache_file))
            assert success, 'Fixture encryption failed'
            cache_file.unlink()
            name = Path(encrypted).name
        app.cache_manager.add_record(('text/plain;charset=utf-8', datetime.now(), 'application',
                                     'Test editor', 'text-x-generic', name, 'plaintext', 'yes' if protected else 'no'))
        clip = app.cache_manager.select_record(app.cache_manager.db_cursor.lastrowid)[0]
        app.main_window.clips_view.new_clip(clip)
        container = next(child.get_children()[0] for child in app.main_window.clips_view.flowbox.get_children()
                         if child.get_children()[0].id == clip[0])
        if protected:
            container.on_clip_action(action='copy', validated=True, data='test password')
        else:
            event = Gdk.Event.new(Gdk.EventType.DOUBLE_BUTTON_PRESS)
            container.emit('button-press-event', event)
        state['deadline'] = time.monotonic() + 5
        GLib.timeout_add(500, poll_target)
    except Exception as error:
        fail(error)
    return False


def poll_target():
    if time.monotonic() > state['deadline']:
        fail(f"Paste timeout at cycle {state['cycle']}")
        return False
    send('read')
    return False


def target_response(stream, condition):
    line = stream.readline()
    if not line:
        fail('Target exited unexpectedly')
        return False
    response = json.loads(line)
    if 'ready' in response:
        print('Target:', response['ready'], flush=True)
        GLib.timeout_add(300, begin)
    elif 'focused' in response:
        if response['focused']:
            GLib.timeout_add(150, copy_item)
        else:
            fail('The test target did not receive focus; no paste attempted')
    elif 'copied' in response:
        GLib.timeout_add(1000, check_capture)
    elif 'text' in response:
        if response['text'] == state['expected']:
            if app.cache_manager.clipboard_monitoring != (args.monitoring == 'enabled'):
                fail('Quick Paste changed the monitoring preference')
                return True
            state['cycle'] += 1
            if state['cycle'] % 10 == 0:
                print('Completed cycles:', state['cycle'], flush=True)
            GLib.idle_add(start_cycle)
        elif response['text'] and not state['expected'].startswith(response['text']):
            fail(f"Wrong or duplicated paste at cycle {state['cycle']}")
        else:
            GLib.timeout_add(100, poll_target)
    return True


def begin():
    try:
        assert not app.main_window.is_visible(), 'Hidden startup displayed a window'
        window = app.main_window
        for _ in range(100):
            app.activate()
            assert app.main_window is window
            assert window.is_visible()
            app.activate()
            assert window.is_visible(), 'Repeated activation hid the window'
            app.on_hide_action(None, None)
            assert not window.is_visible()
        assert len(app.get_windows()) == 1
        print('PASS: hidden startup and 100 repeated activation/Escape cycles', flush=True)
        expired = Path(app.cache_manager.cache_filedir) / 'expired.txt'
        expired.write_text('Expired fixture')
        app.cache_manager.add_record(('text/plain', datetime(2000, 1, 1), 'application',
                                     'Test editor', 'text-x-generic', expired.name, 'plaintext', 'no'))
        app.cache_manager.main_window = None
        app.cache_manager.auto_housekeeping(2)
        assert not expired.exists()
        assert app.cache_manager.get_total_clips()[0][0] == 0
        app.cache_manager.main_window = window
        handler = app.cache_manager._clipboard_handler
        for _ in range(20):
            app.cache_manager.enable_clipboard_monitoring()
            assert app.cache_manager._clipboard_handler == handler
        print('PASS: housekeeping without a window and idempotent monitoring', flush=True)
        app.cache_manager.disable_clipboard_monitoring()
        GLib.idle_add(check_format)
    except Exception as error:
        fail(error)
    return False


GLib.io_add_watch(target.stdout, GLib.IO_IN | GLib.IO_HUP, target_response)
GLib.timeout_add_seconds(60 + args.cycles * 3, lambda: fail('Overall test timeout'))
try:
    result = app.run([sys.argv[0]])
finally:
    target.stdin.close()
    try:
        target.wait(timeout=5)
    except subprocess.TimeoutExpired:
        target.terminate()
        target.wait(timeout=5)
    profile.cleanup()
sys.exit(1 if state['errors'] else result)
