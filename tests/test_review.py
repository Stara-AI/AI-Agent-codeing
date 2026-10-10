"""Regression checks for delivery review; synthetic/mocked, not Linux evidence."""
import io
import json
import sqlite3
import shutil
import signal
from collections import Counter
from unittest.mock import Mock
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cpu_blackbox.cli import main
from cpu_blackbox.collector import collect, index_sealed, open_perf_paths, recover, stop_perf
from cpu_blackbox.query import decode, fold, render
from cpu_blackbox.storage import BlackboxError, Config, DecodeResourceError, NS, Store

TEXT = 'worker 42 11.000000000: cpu-clock: 100 leaf (app)\n 100 leaf (app)\n 200 root (app)\n\n'


def session(store, sid='s', boot='boot', mono=10*NS, wall=None, state='STOPPED'):
    store.db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?)',
                     (sid, boot, 42, 'start', mono, None, json.dumps(asdict(Config())), state))
    store.db.commit()
    if wall is not None:
        store.add_anchor(sid, {'mono': mono, 'wall': wall, 'error_ns': 1}, Config().clock_jump_ns)


class ReviewTests(unittest.TestCase):
    def test_complete_demo_from_another_working_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = Path(__file__).resolve().parents[1]/'tools'/'run_simulation_demo.py'
            run = subprocess.run([sys.executable, str(script), '--output-root', str(root/'demo')], cwd=root, capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stderr)
            result = json.loads((root/'demo'/'result.json').read_text(encoding='utf-8'))
            self.assertTrue(result['hotspot_replayed_after_end'])
            self.assertTrue(result['normal_phase_excluded'])
            self.assertGreaterEqual(result['segments'], 2)
            comparison = json.loads((root/'demo'/'report'/'comparison.json').read_text(encoding='utf-8'))
            self.assertGreater(comparison['baseline_samples'], 0)
            self.assertGreater(comparison['full_samples'], comparison['target_samples'])
            self.assertEqual(comparison['target_samples'], result['sample_count'])
            self.assertIn('normal_work', (root/'demo'/'report'/'full'/'stacks.folded').read_text())

    def test_thread_pid_is_process_id(self):
        counts, stats = fold(io.StringIO(TEXT.replace('worker 42 ', 'worker 42/43 ')), 0, 20*NS, 42)
        self.assertEqual(stats['sample_count'], 1)
        self.assertIn(('worker [42]', 'root', 'leaf'), counts)

    def test_recursive_header_same_symbol_different_ip(self):
        text = 'worker 42 11.000000000: cpu-clock: 100 recur (app)\n 200 recur (app)\n\n'
        counts, _ = fold(io.StringIO(text), 0, 20*NS)
        self.assertIn(('worker [42]', 'recur', 'recur'), counts)

    def test_decoder_explicit_process_fields(self):
        real_popen = subprocess.Popen
        commands = []
        def fake_perf(command, **kwargs):
            commands.append(command)
            child = real_popen([sys.executable, '-c', 'print("sample")'], **kwargs)
            child.wait(timeout=5)
            return child
        with tempfile.TemporaryDirectory() as temp, patch('cpu_blackbox.query.subprocess.Popen', side_effect=fake_perf):
            decode('perf', 'data', Path(temp)/'out', Config())
        self.assertEqual(commands[0][commands[0].index('-F')+1], 'comm,pid,time,event,ip,sym,dso')

    def test_decoder_spawn_failure_removes_temporary_files(self):
        with tempfile.TemporaryDirectory() as temp, patch('cpu_blackbox.query.subprocess.Popen', side_effect=OSError('missing perf')):
            output = Path(temp)/'out'
            with self.assertRaisesRegex(OSError, 'missing perf'):
                decode('perf', 'data', output, Config())
            self.assertFalse(output.exists())
            self.assertFalse(output.with_suffix('.stderr').exists())

    def test_fast_exiting_decoder_stderr_budget(self):
        real_popen = subprocess.Popen
        def fake_perf(command, **kwargs):
            child = real_popen([sys.executable, '-c', 'import sys; sys.stderr.write("x"*4096)'], **kwargs)
            child.wait(timeout=5)
            return child
        with tempfile.TemporaryDirectory() as temp, patch('cpu_blackbox.query.subprocess.Popen', side_effect=fake_perf):
            output = Path(temp)/'out'
            with self.assertRaises(DecodeResourceError):
                decode('perf', 'data', output, Config(max_decode_bytes=128))
            self.assertFalse(output.exists())
            self.assertFalse(output.with_suffix('.stderr').exists())

    def test_fresh_corrupt_record_not_immediately_expired(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(temp)
            try:
                session(store, wall=100*NS)
                path = store.root/'perf.data.bad'
                path.write_bytes(b'corrupt')
                store.segment(path, 's', 0, 0, 'CORRUPT')
                store.cleanup(Config(min_free_bytes=1), now=time.time_ns())
                self.assertTrue(path.exists())
            finally:
                store.close()

    def test_deferred_index_preserved_and_retryable(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(temp)
            try:
                session(store)
                folder = store.root/'s'
                folder.mkdir()
                path = folder/'perf.data.1'
                path.write_bytes(b'recording')
                with patch('cpu_blackbox.collector.decode', side_effect=DecodeResourceError('perf script timeout')):
                    index_sealed(store, 's', folder, Config(), 'perf')
                self.assertEqual(store.db.execute('SELECT state FROM segments').fetchone()[0], 'UNINDEXED')
                store.cleanup(Config(min_free_bytes=1))
                self.assertTrue(path.exists())
                def decode_ok(perf, source, output, cfg, budget_root=None):
                    output.write_text(TEXT)
                    return ''
                with patch('cpu_blackbox.collector.decode', side_effect=decode_ok) as decoder:
                    index_sealed(store, 's', folder, Config(), 'perf')
                    decoder.assert_not_called()
                    index_sealed(store, 's', folder, Config(), 'perf', retry_pending=True)
                self.assertEqual(store.db.execute('SELECT state FROM segments').fetchone()[0], 'AVAILABLE')
            finally:
                store.close()

    def test_cleanup_oldest_wall_time_across_boots(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(temp)
            try:
                session(store, 'old', 'boot-old', 9000*NS, 9000*NS)
                session(store, 'new', 'boot-new', 100*NS, 9900*NS)
                old, new = store.root/'old.data', store.root/'new.data'
                old.write_bytes(b'o'*256)
                new.write_bytes(b'n'*256)
                store.segment(old, 'old', 9000*NS, 9001*NS)
                store.segment(new, 'new', 100*NS, 101*NS)
                with patch('cpu_blackbox.storage.disk_bytes', return_value=1000) as usage:
                    removed = store.cleanup(Config(max_data_bytes=900, min_free_bytes=1), now=10000*NS)
                    usage.assert_called_once()
                self.assertEqual(removed, [str(old)])
                self.assertTrue(new.exists())
            finally:
                store.close()

    def test_existing_index_schema_migrates(self):
        with tempfile.TemporaryDirectory() as temp:
            db = sqlite3.connect(str(Path(temp)/'index.sqlite'))
            db.execute('CREATE TABLE segments(path TEXT PRIMARY KEY,session TEXT,first_ns INTEGER,last_ns INTEGER,size INTEGER,state TEXT,error TEXT)')
            db.commit()
            db.close()
            store = Store(temp)
            try:
                self.assertIn('sealed_wall_ns', {r[1] for r in store.db.execute('PRAGMA table_info(segments)')})
            finally:
                store.close()

    def test_recovery_does_not_confuse_reboot_with_orphan(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(temp)
            try:
                session(store, boot='previous-boot', state='RUNNING')
                folder = store.root/'s'
                folder.mkdir()
                (folder/'process.json').write_text(json.dumps({'perf_pid': 42, 'perf_start': 'same-ticks'}))
                with patch('cpu_blackbox.collector.boot_id', return_value='current-boot'), patch('cpu_blackbox.collector.identity') as identity, patch('cpu_blackbox.collector.index_sealed'):
                    recover(store, Config(), 'perf')
                    identity.assert_not_called()
                self.assertEqual(store.db.execute('SELECT state FROM sessions').fetchone()[0], 'INTERRUPTED')
            finally:
                store.close()

    def test_live_perf_fd_unavailable_fails_closed(self):
        child = SimpleNamespace(pid=999999999, poll=lambda: None)
        with self.assertRaisesRegex(BlackboxError, 'file descriptors'):
            open_perf_paths(child)

    def test_invalid_collect_duration_rejected_before_attach(self):
        for value in ('nan', 'inf', '0'):
            with patch('cpu_blackbox.cli.collect') as capture, patch('sys.stderr', new_callable=io.StringIO):
                self.assertEqual(main(['collect', '--pid', '42', '--duration', value]), 2)
                capture.assert_not_called()

    def test_perf_spawn_failure_publishes_failed_session(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch('cpu_blackbox.collector.platform.system', return_value='Linux'), patch('cpu_blackbox.collector.doctor', return_value={'ready': True}), patch('cpu_blackbox.collector.recover'), patch('cpu_blackbox.collector.identity', return_value='start'), patch('cpu_blackbox.collector.boot_id', return_value='boot'), patch('cpu_blackbox.collector.subprocess.Popen', side_effect=OSError('spawn failed')):
                with self.assertRaisesRegex(OSError, 'spawn failed'):
                    collect(temp, 42, Config())
            state = json.loads((Path(temp)/'status.json').read_text())
            self.assertEqual(state['state'], 'FAILED')
            store = Store(temp)
            try:
                row = store.db.execute('SELECT state,end_mono FROM sessions').fetchone()
                self.assertEqual(row['state'], 'FAILED')
                self.assertIsNotNone(row['end_mono'])
            finally:
                store.close()

    def test_pid_reuse_stops_perf_and_seals_last_file(self):
        with tempfile.TemporaryDirectory() as temp:
            child = Mock()
            child.pid = 100
            child.returncode = None
            child.poll.side_effect = lambda: child.returncode
            def stop(sig):
                self.assertEqual(sig, signal.SIGINT)
                child.returncode = 0
            child.send_signal.side_effect = stop
            def start(args, **kwargs):
                Path(args[-1]).write_bytes(b'perf recording')
                return child
            def decode_ok(perf, source, output, cfg, budget_root=None):
                output.write_text(TEXT)
                return ''
            with patch('cpu_blackbox.collector.platform.system', return_value='Linux'), patch('cpu_blackbox.collector.doctor', return_value={'ready': True}), patch('cpu_blackbox.collector.recover'), patch('cpu_blackbox.collector.identity', side_effect=['start', 'perf-start', 'changed']), patch('cpu_blackbox.collector.boot_id', return_value='boot'), patch('cpu_blackbox.collector.subprocess.Popen', side_effect=start), patch('cpu_blackbox.collector.decode', side_effect=decode_ok):
                with self.assertRaisesRegex(BlackboxError, 'TARGET_CHANGED'):
                    collect(temp, 42, Config())
            self.assertEqual(json.loads((Path(temp)/'status.json').read_text())['state'], 'TARGET_CHANGED')
            store = Store(temp)
            try:
                row = store.db.execute('SELECT state,path FROM segments').fetchone()
                self.assertEqual(row['state'], 'AVAILABLE')
                self.assertTrue(row['path'].endswith('perf.data.final'))
                child.send_signal.assert_called_once_with(signal.SIGINT)
            finally:
                store.close()

    def test_perf_stop_timeout_kills_and_reaps_child(self):
        child = Mock()
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired('perf', 10), 0]
        self.assertFalse(stop_perf(child))
        child.send_signal.assert_called_once_with(signal.SIGINT)
        child.kill.assert_called_once()
        self.assertEqual(child.wait.call_count, 2)

    @unittest.skipUnless(shutil.which('node'), 'Node required for optional frontend script check')
    def test_generated_page_interactions(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)/'report'
            render(Counter({('worker', 'hot_leaf', 'work_a'): 3, ('worker', 'hot_leaf', 'work_b'): 1,
                            ('worker', 'normal_work'): 2, ('other', 'hot_leaf'): 2}), {'source': 'SYNTHETIC'}, output, 100000)
            script = Path(__file__).resolve().parents[1]/'tools'/'check_report.js'
            run = subprocess.run(['node', str(script), str(output/'index.html')], capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertTrue(json.loads(run.stdout)['zoom'])

    def test_systemd_graceful_stop_and_disk_backoff_contract(self):
        unit = (Path(__file__).resolve().parents[1]/'deploy'/'cpu-blackbox.service').read_text()
        self.assertIn('KillMode=mixed', unit)
        self.assertIn('RestartPreventExitStatus=75', unit)
        self.assertIn('StateDirectoryMode=0700', unit)
        self.assertIn('--pid ${TARGET_PID}', unit)


if __name__ == '__main__':
    unittest.main()
