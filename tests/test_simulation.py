import json
import tempfile
import unittest
from pathlib import Path
from cpu_blackbox.query import query
from cpu_blackbox.simulation import simulate
from cpu_blackbox.storage import BlackboxError, Config, Store, parse_time

class SimulationTests(unittest.TestCase):
    def test_rotation_and_replay_after_hot_phase(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            cfg=Config(rotation_seconds=1,min_free_bytes=1,disk_check_seconds=.1)
            self.assertEqual(simulate(root/"data",cfg,1.2),0)
            manifest=json.loads((root/"data"/"simulation.json").read_text())
            self.assertGreaterEqual(manifest["segments"],2)
            store=Store(root/"data")
            try:
                report=query(store,cfg,parse_time(manifest["hot_window_start"]),parse_time(manifest["hot_window_end"]),root/"report")
            finally: store.close()
            stacks=(root/"report"/"stacks.folded").read_text(encoding="utf-8")
            self.assertIn("hot_leaf",stacks)
            self.assertNotIn("normal_work",stacks)
            self.assertGreater(report["sample_count"],0)
            self.assertIn("SYNTHETIC",report["source"])
            self.assertIn('source-banner',(root/"report"/"index.html").read_text(encoding="utf-8"))
    def test_disk_budget_stops_simulation(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(simulate(temp,Config(max_data_bytes=1,min_free_bytes=1),.1),75)
            state=json.loads((Path(temp)/"status.json").read_text())
            self.assertEqual(state["state"],"DISK_BUDGET")
            self.assertEqual(state["source"],"synthetic")
    def test_invalid_duration(self):
        for duration in (0,-1,float('nan'),float('inf')):
            with self.assertRaises(BlackboxError): simulate("unused",Config(),duration)
