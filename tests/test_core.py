import io
import json
import os
import tempfile
import unittest
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch
from cpu_blackbox.collector import command, doctor
from cpu_blackbox.query import fold, gaps, layout, query, render, samples
from cpu_blackbox.storage import BlackboxError, Config, NS, Store, parse_time, safe_path

FIXTURE = '''worker 42 [001] 11.000000000: cpu-clock: 100 leaf (app)
        100 leaf (app)
        200 root (app)

worker 42 [001] 11.500000000: cpu-clock: 100 other (app)
        100 other (app)
        200 root (app)

worker 42 [001] 12.000000000: cpu-clock: 100 after (app)
        100 after (app)
        200 root (app)

'''

class ParsingTests(unittest.TestCase):
    def test_timezone_equivalence(self):
        self.assertEqual(parse_time("2026-10-10T03:00:00+08:00"), parse_time("2026-10-09T19:00:00Z"))
    def test_timezone_required(self):
        with self.assertRaises(BlackboxError): parse_time("2026-10-10T03:00:00")
    def test_nanosecond_half_open_boundary(self):
        counts, stats = fold(io.StringIO(FIXTURE), 11 * NS, 12 * NS, 42)
        self.assertEqual(stats["sample_count"], 2)
        self.assertFalse(any("after" in stack for stack in counts))
        self.assertEqual(counts[("worker [42]", "root", "leaf")], 1)
    def test_pid_filter(self):
        counts, stats = fold(io.StringIO(FIXTURE), 0, 100 * NS, 99)
        self.assertEqual(stats["sample_count"], 0)
    def test_empty_callchain_is_visible(self):
        counts, stats = fold(["worker 42 11.000000000: cpu-clock:\n"], 0, 20 * NS)
        self.assertEqual(stats["unknown_samples"], 1)
    def test_multiple_events_rejected(self):
        with self.assertRaises(BlackboxError):
            fold(io.StringIO(FIXTURE + FIXTURE.replace("cpu-clock", "cycles")), 0, 20 * NS)
    def test_recursion_preserved(self):
        text = "worker 42 11.000000000: cpu-clock: 100 recur (app)\n 100 recur (app)\n 200 recur (app)\n\n"
        counts, _ = fold(io.StringIO(text), 0, 20 * NS)
        self.assertIn(("worker [42]", "recur", "recur"), counts)
    def test_config_limits(self):
        for values in ({"frequency_hz": 0}, {"rotation_seconds": 0.5}, {"call_graph": "bad"}, {"event": "x"}):
            with self.assertRaises(BlackboxError): Config(**values).validate()
    def test_perf_command_no_shell(self):
        args = command("perf", 42, Config(), "/tmp/data with spaces")
        self.assertIn("--switch-output=60s", args)
        self.assertEqual(args[-1], "/tmp/data with spaces")
        self.assertIn("CLOCK_MONOTONIC", args)
    def test_windows_doctor_explicit_failure(self):
        with patch("cpu_blackbox.collector.platform.system", return_value="Windows"):
            self.assertFalse(doctor(42, Config())["ready"])

class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "data"
        self.store = Store(self.root)
        self.cfg = Config(min_free_bytes=1)
        self.store.db.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?)", ("s", "boot", 42, "start", 10*NS, 15*NS, json.dumps(asdict(self.cfg)), "STOPPED"))
        self.store.db.commit()
        self.store.add_anchor("s", {"mono":10*NS,"wall":100*NS,"error_ns":5}, self.cfg.clock_jump_ns)
    def tearDown(self):
        self.store.close()
        self.temp.cleanup()
    def segment(self, name, first, last, content=FIXTURE, state="AVAILABLE"):
        path = self.root / name
        path.write_text(content)
        self.store.segment(path, "s", first, last, state)
        return path
    def test_mapping(self):
        a,b,error = self.store.map_window("s",101*NS,102*NS)
        self.assertEqual((a,b,error),(11*NS,12*NS,5))
    def test_clock_jump_rejected(self):
        self.store.add_anchor("s", {"mono":11*NS,"wall":105*NS,"error_ns":5}, self.cfg.clock_jump_ns)
        with self.assertRaises(BlackboxError): self.store.map_window("s",101*NS,102*NS)
    def test_cross_segment_selection(self):
        self.segment("one", 11*NS, 11*NS+NS//2)
        self.segment("two", 11*NS+NS//2, 12*NS)
        self.segment("after", 13*NS, 14*NS)
        self.assertEqual(len(self.store.select("s",11*NS,12*NS)),2)
    def test_active_and_corrupt_not_queryable(self):
        self.segment("one",11*NS,12*NS,state="CORRUPT")
        self.assertEqual(len(self.store.select("s",11*NS,12*NS)),0)
    def test_expiration_does_not_remove_active(self):
        sealed = self.segment("one",11*NS,12*NS)
        active = self.root / "perf.data"
        active.write_text("active")
        self.store.cleanup(self.cfg, now=200000*NS)
        self.assertFalse(sealed.exists())
        self.assertTrue(active.exists())
    def test_path_escape_rejected(self):
        with self.assertRaises(BlackboxError): safe_path(self.root, self.root.parent / "outside")
    def test_query_cross_segment_real_report_fake_perf(self):
        self.segment("one",11*NS,11*NS+NS//2,content=FIXTURE.split("worker 42 [001] 11.500")[0])
        self.segment("two",11*NS+NS//2,12*NS,content="worker 42 [001] 11.500" + FIXTURE.split("worker 42 [001] 11.500")[1])
        out = self.root.parent / "report"
        def fake_decode(perf, source, target, cfg, budget_root=None):
            Path(target).write_text(Path(source).read_text())
            return ""
        with patch("cpu_blackbox.query.decode", side_effect=fake_decode):
            report = query(self.store,self.cfg,101*NS,102*NS,out)
        self.assertEqual(report["sample_count"],2)
        self.assertEqual(len(report["segments"]),2)
        self.assertTrue((out/"index.html").exists())
        self.assertFalse(list(self.root.glob("query-pinned-*")))
        self.assertEqual(json.loads((out/"report.json").read_text())["sample_count"],2)
    def test_query_budget_prevents_copy(self):
        self.segment("one",11*NS,12*NS)
        with self.assertRaises(BlackboxError): query(self.store,Config(min_free_bytes=1,max_data_bytes=1),101*NS,102*NS,self.root.parent/"report")
    def test_query_no_data(self):
        with self.assertRaises(BlackboxError): query(self.store,self.cfg,101*NS,102*NS,self.root.parent/"report")
    def test_reports_not_in_collection(self):
        with self.assertRaises(BlackboxError): query(self.store,self.cfg,101*NS,102*NS,self.root/"report")

class RenderingTests(unittest.TestCase):
    def test_weight_partition(self):
        nodes = layout(Counter({("a","x"):3,("a","y"):1,("b",):2}))
        self.assertEqual(nodes[0]["value"],6)
        self.assertAlmostEqual(sum(n["width"] for n in nodes if n["depth"]==1),1)
        self.assertTrue(all(n["x"]+n["width"] <= 1.000001 for n in nodes))
    def test_html_and_script_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)/"report"
            render(Counter({("</script><script>alert(1)</script>",):1}),{"message":"<unsafe>"},out,100000)
            page = (out/"index.html").read_text(encoding="utf-8")
            self.assertNotIn("</script><script>alert(1)</script>",page)
            self.assertIn("&lt;unsafe&gt;",page)
    def test_gap_union(self):
        self.assertEqual(gaps([(1,4),(3,7),(9,12)],0,10),[[0,1],[7,9]])
    def test_report_budget_and_existing_output(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)/"report"
            with self.assertRaises(BlackboxError): render(Counter({("a",):1}),{},out,1)
            self.assertFalse(out.exists())
            out.mkdir()
            with self.assertRaises(BlackboxError): render(Counter({("a",):1}),{},out,100000)

if __name__ == "__main__": unittest.main()
