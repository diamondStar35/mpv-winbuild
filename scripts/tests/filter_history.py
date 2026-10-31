"""Headless regression checks against the packaged Windows media libraries."""
import argparse
import base64
import ctypes as c
import gzip
import io
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import wave


class Event(c.Structure):
    _fields_ = [('id', c.c_int), ('error', c.c_int), ('user', c.c_uint64), ('data', c.c_void_p)]


class Log(c.Structure):
    _fields_ = [('prefix', c.c_char_p), ('level', c.c_char_p), ('text', c.c_char_p)]


class Player:
    def __init__(self, library, graph):
        self.dll = library
        self.handle = library.mpv_create()
        self.logs = []
        for key, value in dict(config='no', terminal='no', ao='null', vo='null',
                               idle='yes', **{'keep-open': 'yes', 'media-controls': 'no',
                               'input-default-bindings': 'no', 'af': graph}).items():
            assert library.mpv_set_option_string(self.handle, key.encode(), value.encode()) >= 0, key
        assert library.mpv_initialize(self.handle) >= 0
        assert library.mpv_request_log_messages(self.handle, b'debug') >= 0

    def command(self, *args):
        argv = (c.c_char_p * (len(args) + 1))(*[str(a).encode() for a in args], None)
        result = self.dll.mpv_command(self.handle, argv)
        assert result >= 0, (args[0], result)

    def event(self, timeout=0.02):
        event = self.dll.mpv_wait_event(self.handle, timeout).contents
        if event.id == 2:
            log = c.cast(event.data, c.POINTER(Log)).contents
            self.logs.append(log.text.decode(errors='replace'))
        return event.id

    def restart(self):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.event() == 21:
                self.pump()
                return
        raise AssertionError('Timed out waiting for playback restart')

    def pump(self):
        while self.event(0):
            pass

    @property
    def loads(self):
        self.pump()
        return sum('Original IR length:' in line for line in self.logs)

    def close(self):
        self.dll.mpv_terminate_destroy(self.handle)


def wave_url(rate=48000, channels=2):
    data = io.BytesIO()
    with wave.open(data, 'wb') as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(bytes(rate * channels * 2 * 15))
    return 'hex://' + data.getvalue().hex()


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.player = Player(LIBRARY, '@audiosofa:lavfi=graph=[sofalizer=sofa=fixture.sofa:type=freq],'
                             '@normalize:lavfi=[dynaudnorm=f=150:g=15,alimiter=limit=0.95],'
                             '@eq:lavfi=[equalizer=f=1000:g=3]')
        self.player.command('loadfile', MEDIA)
        self.player.restart()
        self.initial_loads = self.player.loads
        self.assertGreater(self.initial_loads, 0)

    def tearDown(self):
        self.player.close()

    def test_seeks_retain_dataset_and_accept_directions(self):
        p = self.player
        for i in range(8):
            p.command('af-command', 'audiosofa', 'rotation', -(i + 1) * 5, 'sofalizer')
            p.command('af-command', 'audiosofa', 'elevation', 90 if i % 2 else -90, 'sofalizer')
            p.command('seek', 1 + i, 'absolute+exact')
            p.restart()
        self.assertEqual(self.initial_loads, p.loads)
        self.assertTrue(any('retaining filter instances' in line for line in p.logs))

    def test_compatible_reload_retains_dataset(self):
        p = self.player
        p.command('af-command', 'audiosofa', 'rotation', -355, 'sofalizer')
        for _ in range(3):
            p.command('loadfile', MEDIA)
            p.restart()
        self.assertEqual(self.initial_loads, p.loads)

    def test_pause_resume_retains_dataset(self):
        p = self.player
        for _ in range(3):
            p.command('set', 'pause', 'yes')
            p.command('set', 'pause', 'no')
        self.assertEqual(self.initial_loads, p.loads)

    def test_incompatible_reload_can_rebuild(self):
        p = self.player
        p.command('loadfile', wave_url(44100, 1))
        p.restart()
        p.command('af-command', 'audiosofa', 'rotation', -45, 'sofalizer')
        self.assertGreater(p.loads, self.initial_loads)

    def test_remove_releases_instance(self):
        p = self.player
        p.command('af', 'remove', '@audiosofa')
        p.command('af', 'pre', '@audiosofa:lavfi=graph=[sofalizer=sofa=fixture.sofa:type=freq]')
        p.pump()
        self.assertGreater(p.loads, self.initial_loads)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    directory = args.directory.resolve()
    cookies = [os.add_dll_directory(str(directory))]
    LIBRARY = c.CDLL(str(directory / 'libmpv-2.dll'))
    LIBRARY.mpv_create.restype = c.c_void_p
    LIBRARY.mpv_set_option_string.argtypes = [c.c_void_p, c.c_char_p, c.c_char_p]
    LIBRARY.mpv_initialize.argtypes = [c.c_void_p]
    LIBRARY.mpv_command.argtypes = [c.c_void_p, c.POINTER(c.c_char_p)]
    LIBRARY.mpv_wait_event.argtypes = [c.c_void_p, c.c_double]
    LIBRARY.mpv_wait_event.restype = c.POINTER(Event)
    LIBRARY.mpv_request_log_messages.argtypes = [c.c_void_p, c.c_char_p]
    LIBRARY.mpv_terminate_destroy.argtypes = [c.c_void_p]
    fixture = Path(__file__).with_name('hrtf.sofa.gz.base64').read_text()
    with tempfile.TemporaryDirectory(prefix='filter-history-') as workspace:
        os.chdir(workspace)
        Path('fixture.sofa').write_bytes(gzip.decompress(base64.b64decode(fixture)))
        env = dict(os.environ, PATH=str(directory) + os.pathsep + os.environ['PATH'])
        subprocess.run([str(directory / 'history_reset.exe'), 'fixture.sofa'], env=env, check=True,
                       creationflags=subprocess.CREATE_NO_WINDOW, timeout=120)
        MEDIA = wave_url()
        unittest.main(argv=['filter_history'], verbosity=2)
