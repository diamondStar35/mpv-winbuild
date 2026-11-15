"""Headless regression checks against the packaged Windows media libraries."""
import argparse
import base64
import ctypes as c
import gzip
import io
import math
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
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
        # FFmpeg's AV_LOG_DEBUG messages map to mpv's trace level.
        assert library.mpv_request_log_messages(self.handle, b'trace') >= 0

    def command(self, *args):
        argv = (c.c_char_p * (len(args) + 1))(*[str(a).encode() for a in args], None)
        result = self.dll.mpv_command(self.handle, argv)
        assert result >= 0, (args[0], result)

    def command_async(self, *args):
        argv = (c.c_char_p * (len(args) + 1))(*[str(a).encode() for a in args], None)
        result = self.dll.mpv_command_async(self.handle, 0, argv)
        assert result >= 0, (args[0], result)

    def get_property(self, name):
        value = self.dll.mpv_get_property_string(self.handle, name.encode())
        if not value:
            return None
        try:
            return c.string_at(value).decode()
        finally:
            self.dll.mpv_free(value)

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

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


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
        self.addCleanup(self.player.close)
        self.player.command('loadfile', MEDIA)
        self.player.restart()
        self.initial_loads = self.player.loads
        self.assertGreater(self.initial_loads, 0)

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


class NormalizationTests(unittest.TestCase):
    def test_format_changes_keep_normalization_active(self):
        # SOFA processing can force a constant sample rate and hide this transition.
        p = Player(LIBRARY, '@normalize:lavfi=[dynaudnorm=f=150:g=15,alimiter=limit=0.95]')
        self.addCleanup(p.close)
        p.command('loadfile', MEDIA)
        p.restart()
        p.command('af-command', 'normalize', 'peak', '0.8', 'dynaudnorm')
        for rate, channels in [(8000, 1), (48000, 2), (44100, 1)]:
            p.command('loadfile', wave_url(rate, channels))
            p.restart()
            # A failed lavfi graph can be bypassed while playback still restarts normally.
            # Sending a command proves the normalizer remains in the active audio chain.
            p.pump()
            self.assertFalse(any('could not restore runtime option' in line
                                 or 'Disabling filter normalize' in line for line in p.logs),
                             '\n'.join(p.logs[-30:]))
            p.command('af-command', 'normalize', 'peak', '0.8', 'dynaudnorm')


class LiveControlTests(unittest.TestCase):
    def setUp(self):
        self.media = Path('live-controls.wav').resolve()
        second = b''.join(struct.pack('<h', int(2000 * math.sin(2 * math.pi * 440 * i / 48000)))
                          for i in range(48000))
        with wave.open(str(self.media), 'wb') as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(48000)
            output.writeframes(second * 15)

    def pump(self, player, seconds):
        deadline = time.perf_counter() + seconds
        while time.perf_counter() < deadline:
            player.pump()
            time.sleep(0.005)

    def start(self, crossfade, graph='', output_format='float', media=None):
        player = Player(LIBRARY, graph)
        # In-track controls must preserve output even when gapless file
        # transitions are disabled.
        player.command('set', 'gapless-audio', 'no')
        player.command('set', 'audio-crossfade-duration', crossfade)
        # Model the fixed floating-point output used by a Windows shared mixer.
        # Precision-upgrade tests leave this unset to exercise renegotiation.
        if output_format:
            player.command('set', 'audio-format', output_format)
        player.command('loadfile', str(media or self.media))
        player.restart()
        self.pump(player, 0.2)
        return player

    def test_crossfade_does_not_delay_runtime_filter_commands(self):
        graph = ('@level:lavfi=[volume=1,astats=metadata=1:reset=1,'
                 'ametadata=mode=print:key=lavfi.astats.Overall.RMS_level]')
        for crossfade, successor in [(0, False), (3, False), (3, True)]:
            with self.subTest(crossfade=crossfade, successor=successor), self.start(crossfade, graph) as player:
                if successor:
                    player.command('loadfile', str(self.media), 'append')
                before = float(player.get_property('audio-pts'))
                player.logs.clear()
                player.command('af-command', 'level', 'volume', '0', 'volume')
                self.pump(player, 0.6)
                muted = re.search(r'pts_time:([\d.]+)[^\n]*\n[^\n]*RMS_level=-inf',
                                  ''.join(player.logs))
                self.assertIsNotNone(muted, 'The runtime command never affected rendered samples')
                # The null device consumes at real-time speed. The first muted
                # frame's PTS identifies when the change reaches that output,
                # including its ordinary device and software buffers.
                delay = float(muted.group(1)) - before
                self.assertLess(delay, 0.75, f'Runtime filter command is {delay:.3f}s ahead of output')

    def test_spatial_commands_with_normalization_reach_output_promptly(self):
        graph = '@normalize:lavfi=[dynaudnorm=f=150:g=15,alimiter=limit=0.95],'
        graph += '@audiosofa:lavfi=[sofalizer=sofa=fixture.sofa:type=freq:rotation=90,'
        graph += 'astats=metadata=1:reset=1:measure_perchannel=RMS_level:measure_overall=none,ametadata=mode=print]'
        for crossfade in [0, 3]:
            with self.subTest(crossfade=crossfade), self.start(crossfade, graph) as player:
                for rotation in [-90, 90, -90]:
                    before = float(player.get_property('audio-pts'))
                    player.logs.clear()
                    player.command('af-command', 'audiosofa', 'rotation', rotation, 'sofalizer')
                    self.pump(player, 0.6)
                    frames = re.finditer(r'pts_time:([\d.]+)[^\n]*\n'
                                         r'[^\n]*\.1.RMS_level=([-\d.inf]+)\n'
                                         r'[^\n]*\.2.RMS_level=([-\d.inf]+)', ''.join(player.logs))
                    changed = next((float(frame[1]) for frame in frames
                                    if (float(frame[3]) - float(frame[2])) * (-rotation / 90) > 10), None)
                    self.assertIsNotNone(changed, 'The requested direction never reached rendered output')
                    self.assertLess(changed - before, 0.75, 'Direction is buffered ahead of audible output')

    def assert_continuous(self, player, change):
        player.logs.clear()
        before = float(player.get_property('audio-pts'))
        started = time.perf_counter()
        change()
        self.pump(player, 0.15)
        elapsed = time.perf_counter() - started
        advanced = float(player.get_property('audio-pts')) - before
        self.assertLess(abs(advanced - elapsed), 0.2,
                        f'Playback advanced {advanced:.3f}s in {elapsed:.3f}s')
        self.assertFalse(any('AO: [' in line for line in player.logs),
                         'A live control reopened the output and discarded queued samples')

    def test_crossing_normal_speed_keeps_audio_continuous(self):
        for crossfade in [0, 3]:
            with self.subTest(crossfade=crossfade), self.start(crossfade) as player:
                for speed in [0.975, 1, 0.975, 1]:
                    self.assert_continuous(player, lambda: player.command('set', 'speed', speed))

    def test_first_pitch_change_keeps_audio_continuous(self):
        for crossfade in [0, 3]:
            with self.subTest(crossfade=crossfade), self.start(crossfade) as player:
                def pitch():
                    player.command('set', 'pitch', 2 ** (1 / 12))
                self.assert_continuous(player, pitch)

    def test_required_precision_upgrade_drains_without_skipping(self):
        with self.start(0, output_format=None) as player:
            self.assertEqual(player.get_property('audio-out-params/format'), 's16')
            before = float(player.get_property('audio-pts'))
            started = time.perf_counter()
            player.command('set', 'pitch', 2 ** (1 / 12))
            self.pump(player, 0.9)
            advanced = float(player.get_property('audio-pts')) - before
            elapsed = time.perf_counter() - started
            self.assertLess(abs(advanced - elapsed), 0.2,
                            'Renegotiating a higher-precision output discarded unplayed samples')
            self.assertIn(player.get_property('audio-out-params/format'), ['float', 'floatp', 'double', 'doublep'])

    def test_lossless_conversion_keeps_higher_precision_output(self):
        with self.start(0, '@precision:lavfi=[aformat=sample_fmts=dblp]', output_format=None) as player:
            self.assertEqual(player.get_property('audio-out-params/format'), 'doublep')
            self.assert_continuous(player, lambda: player.command('af', 'remove', '@precision'))
            self.assertEqual(player.get_property('audio-out-params/format'), 'doublep')

    def test_channel_expansion_is_not_downmixed_by_gapless_policy(self):
        stereo = Path('live-stereo.wav').resolve()
        with wave.open(str(stereo), 'wb') as output:
            output.setnchannels(2)
            output.setsampwidth(2)
            output.setframerate(48000)
            output.writeframes(b''.join(struct.pack('<hh', sample, -sample)
                                       for sample in range(-2000, 2000)) * 180)
        for gapless in ['no', 'weak', 'yes']:
            with self.subTest(gapless=gapless), self.start(0, '@mono:lavfi=[pan=mono|c0=c0]', media=stereo) as player:
                player.command('set', 'gapless-audio', gapless)
                self.assertEqual(player.get_property('audio-out-params/channel-count'), '1')
                before = float(player.get_property('audio-pts'))
                started = time.perf_counter()
                player.command('af', 'remove', '@mono')
                self.pump(player, 0.9)
                self.assertEqual(player.get_property('audio-out-params/channel-count'), '2',
                                 'A live stereo signal was forced through the old mono output')
                advanced = float(player.get_property('audio-pts')) - before
                self.assertLess(abs(advanced - (time.perf_counter() - started)), 0.2,
                                'Changing the channel layout discarded unplayed samples')

    def test_explicit_output_changes_still_reconfigure_device(self):
        with self.start(0) as player:
            player.logs.clear()
            player.command('set', 'audio-samplerate', '44100')
            self.pump(player, 0.25)
            self.assertEqual(player.get_property('audio-out-params/samplerate'), '44100')
            self.assertTrue(any('AO: [' in line for line in player.logs))

    def test_enabling_crossfade_keeps_audio_continuous(self):
        with self.start(0) as player:
            self.assert_continuous(player, lambda: player.command('set', 'audio-crossfade-duration', '3'))

    def test_manual_overlap_prepares_audio_only_when_requested(self):
        with self.start(3) as player:
            self.assertLess(float(player.get_property('audio-crossfade/available')), 0.75)
            player.command('crossfade-loadfile', str(self.media), '3')
            player.restart()
            self.assertGreater(float(player.get_property('audio-crossfade/remaining')), 2.0,
                               'Preparing on demand must preserve the requested overlap')

    def test_stop_cancels_pending_manual_overlap(self):
        with self.start(3) as player:
            player.command_async('crossfade-loadfile', str(self.media), '3')
            started = time.perf_counter()
            player.command('stop')
            self.assertLess(time.perf_counter() - started, 0.25,
                            'Preparing an overlap blocked a later transport command')
            self.pump(player, 0.35)
            self.assertEqual(player.get_property('idle-active'), 'yes')
            self.assertIsNone(player.get_property('time-pos'))

    def test_automatic_overlap_prepares_the_outgoing_tail(self):
        with self.start(3) as player:
            player.command('loadfile', str(self.media), 'append')
            player.command('seek', '11', 'absolute+exact')
            player.restart()
            deadline = time.perf_counter() + 3
            while time.perf_counter() < deadline and player.get_property('playlist-pos') != '1':
                self.pump(player, 0.02)
            self.assertEqual(player.get_property('playlist-pos'), '1')
            self.assertGreater(float(player.get_property('audio-crossfade/remaining')), 2.0,
                               'Automatic overlap must retain the final outgoing audio')


class GaplessTests(unittest.TestCase):
    def setUp(self):
        self.boundary = 48000 * 2 + 1031
        self.samples = [int(2000 * math.sin(2 * math.pi * 523.25 * i / 48000))
                        for i in range(self.boundary * 2)]
        for name, samples in [('first', self.samples[:self.boundary]),
                              ('second', self.samples[self.boundary:]), ('whole', self.samples)]:
            with wave.open(name + '.wav', 'wb') as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(48000)
                output.writeframes(struct.pack('<' + 'h' * len(samples), *samples))

    def render(self, graph, mode, first='first.wav', next=None, channels=1):
        output = Path('gapless.f32').resolve()
        with Player(LIBRARY, graph) as player:
            for key, value in {'ao': 'pcm', 'ao-pcm-file': str(output), 'ao-pcm-waveheader': 'no',
                               'ao-pcm-append': 'no', 'audio-format': 'float',
                               'audio-channels': 'mono' if channels == 1 else 'stereo',
                               'audio-samplerate': '48000', 'gapless-audio': mode, 'pause': 'yes'}.items():
                player.command('set', key, value)
            player.command('loadfile', str(Path(first).resolve()))
            player.restart()
            if next:
                player.command('loadfile', str(Path(next).resolve()), 'append')
            player.command('set', 'pause', 'no')
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                player.pump()
                if player.get_property('eof-reached') == 'yes' and (
                        not next or player.get_property('playlist-pos') == '1'):
                    break
                time.sleep(0.005)
            else:
                self.fail('Gapless rendering did not finish')
        data = output.read_bytes()
        return struct.unpack('<' + 'f' * (len(data) // 4), data)

    def test_recording_boundaries_preserve_samples_and_signal_history(self):
        for mode in ['weak', 'yes']:
            for graph in ['', '@eq:lavfi=[equalizer=f=600:g=10]',
                          '@normalize:lavfi=[dynaudnorm=f=150:g=15,alimiter=limit=0.95]']:
                with self.subTest(mode=mode, graph=graph):
                    normalize = 'dynaudnorm' in graph
                    if normalize:
                        reference = self.render(graph, mode) + self.render(graph, mode, 'second.wav')
                    else:
                        reference = self.render(graph, mode, 'whole.wav')
                    joined = self.render(graph, mode, next='second.wav')
                    self.assertEqual(len(reference), len(self.samples))
                    self.assertEqual(len(joined), len(reference), 'The boundary dropped or inserted samples')
                    boundary = self.boundary
                    positions = list(range(boundary - 512, boundary))
                    start = boundary + (24000 if normalize else 0)
                    positions += list(range(start, start + 512))
                    error = max(abs(reference[i] - joined[i]) for i in positions)
                    self.assertLess(error, 2 / 32768, 'Processing lost signal history or per-track analysis')
                    regular_step = max(abs(reference[i] - reference[i - 1])
                                       for i in range(boundary + 480, boundary + 992))
                    boundary_step = max(abs(joined[i] - joined[i - 1])
                                        for i in range(boundary - 3, boundary + 4))
                    self.assertLessEqual(boundary_step, regular_step * 1.5,
                                         'The track boundary inserted a discontinuity')

    def test_strong_gapless_keeps_successor_stereo_and_precision(self):
        with wave.open('stereo.wav', 'wb') as output:
            output.setnchannels(2)
            output.setsampwidth(3)
            output.setframerate(48000)
            data = bytearray()
            for i in range(self.boundary):
                sample = int(64 * math.sin(2 * math.pi * 100 * i / 48000))
                data += struct.pack('<i', sample)[:3] + struct.pack('<i', -sample)[:3]
            output.writeframes(data)
        joined = self.render('', 'yes', next='stereo.wav', channels=2)
        self.assertEqual(len(joined), len(self.samples) * 2)
        tail = joined[-4800:]
        self.assertGreater(max(abs(value) for value in tail), 0.000005,
                           'The successor was quantized to the first decoder\'s integer precision')
        self.assertLess(max(abs(tail[i] + tail[i + 1]) for i in range(0, len(tail), 2)), 1e-8,
                        'The stereo successor was downmixed to the first decoder\'s mono layout')

    def test_strong_gapless_does_not_limit_successor_to_first_source_bandwidth(self):
        with wave.open('low-rate.wav', 'wb') as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(8000)
            output.writeframes(bytes(8000 * 2 * 2))
        samples = [int(2000 * math.sin(2 * math.pi * 10000 * i / 48000))
                   for i in range(self.boundary)]
        with wave.open('high-rate.wav', 'wb') as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(48000)
            output.writeframes(struct.pack('<' + 'h' * len(samples), *samples))
        joined = self.render('', 'yes', 'low-rate.wav', 'high-rate.wav')
        tail = joined[-4800:]
        self.assertGreater(math.sqrt(sum(value * value for value in tail) / len(tail)), 0.03,
                           'The successor lost frequencies above the first source\'s Nyquist limit')


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
    LIBRARY.mpv_command_async.argtypes = [c.c_void_p, c.c_uint64, c.POINTER(c.c_char_p)]
    LIBRARY.mpv_wait_event.argtypes = [c.c_void_p, c.c_double]
    LIBRARY.mpv_wait_event.restype = c.POINTER(Event)
    LIBRARY.mpv_request_log_messages.argtypes = [c.c_void_p, c.c_char_p]
    LIBRARY.mpv_terminate_destroy.argtypes = [c.c_void_p]
    LIBRARY.mpv_get_property_string.argtypes = [c.c_void_p, c.c_char_p]
    LIBRARY.mpv_get_property_string.restype = c.c_void_p
    LIBRARY.mpv_free.argtypes = [c.c_void_p]
    fixture = Path(__file__).with_name('hrtf.sofa.gz.base64').read_text()
    previous_directory = Path.cwd()
    with tempfile.TemporaryDirectory(prefix='filter-history-') as workspace:
        try:
            os.chdir(workspace)
            Path('fixture.sofa').write_bytes(gzip.decompress(base64.b64decode(fixture)))
            env = dict(os.environ, PATH=str(directory) + os.pathsep + os.environ['PATH'])
            subprocess.run([str(directory / 'history_reset.exe'), 'fixture.sofa'], env=env, check=True,
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=120)
            MEDIA = wave_url()
            result = unittest.main(argv=['filter_history'], verbosity=2, exit=False).result
        finally:
            # Windows cannot remove a directory that is still the process's cwd.
            os.chdir(previous_directory)
    sys.exit(0 if result.wasSuccessful() else 1)
