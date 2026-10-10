import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from cpu_blackbox.query import decode
from cpu_blackbox.storage import BlackboxError, Config
from tools.benchmark import evaluate
from tools.export_conversation import export

class EvidenceTests(unittest.TestCase):
    def test_overhead_confidence_and_duration(self):
        base={"throughput":100,"duration_seconds":120,"rotation_seconds":60}
        good={"throughput":99,"duration_seconds":120}
        bad={"throughput":95,"duration_seconds":120}
        self.assertEqual(evaluate([(base,good)]*5)["verdict"],"PASS_FOR_THIS_WORKLOAD")
        self.assertEqual(evaluate([(base,bad)]*5)["verdict"],"FAIL")
        self.assertEqual(evaluate([(base,good)])["verdict"],"INSUFFICIENT")
        short=dict(good,duration_seconds=1)
        self.assertEqual(evaluate([(base,short)]*5)["verdict"],"INCONCLUSIVE")
    def test_noisy_measurements_not_claimed_pass(self):
        base={"throughput":100,"duration_seconds":120,"rotation_seconds":60}
        pairs=[(base,{"throughput":value,"duration_seconds":120}) for value in (90,110,95,105,99)]
        self.assertEqual(evaluate(pairs)["verdict"],"INCONCLUSIVE")
    def test_export_visible_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            events=[{"type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"question"}]}},
                    {"type":"response_item","payload":{"type":"message","role":"assistant","channel":"analysis","content":[{"type":"output_text","text":"excluded"}]}},
                    {"type":"response_item","payload":{"type":"message","role":"developer","content":[{"type":"input_text","text":"excluded"}]}},
                    {"type":"response_item","payload":{"type":"reasoning","summary":"excluded"}},
                    {"type":"response_item","payload":{"type":"function_call","name":"tool","arguments":"{}"}},
                    {"type":"response_item","payload":{"type":"function_call_output","call_id":"tool-output","output":[{"type":"input_text","text":"visible result"},{"type":"input_image","image_url":"data:image/png;base64,BINARY_SENTINEL"}]}},
                    {"type":"response_item","payload":{"type":"custom_tool_call_output","output":{"content":[{"type":"image","data":"BINARY_SENTINEL"}],"text":"visible nested result"}}}]
            source=root/"source.jsonl"
            source.write_text("\n".join(json.dumps(e) for e in events),encoding="utf-8")
            meta=export(source,root/"out")
            self.assertEqual(meta["visible_record_count"],4)
            for name in ("ai-conversation.jsonl", "ai-conversation.md"):
                output=(root/"out"/name).read_text(encoding="utf-8")
                self.assertNotIn("excluded",output)
                self.assertNotIn("BINARY_SENTINEL",output)
                self.assertIn("visible result",output)
                self.assertIn("visible nested result",output)
                self.assertIn("binary data omitted",output)
    def test_decoder_timeout_stops_real_child(self):
        real_popen=subprocess.Popen
        def fake_perf(command,**kwargs):
            return real_popen([sys.executable,"-c","import time; time.sleep(5)"],**kwargs)
        with tempfile.TemporaryDirectory() as temp,patch("cpu_blackbox.query.subprocess.Popen",side_effect=fake_perf):
            output=Path(temp)/"out"
            with self.assertRaisesRegex(BlackboxError,"timeout"):
                decode("unused","data",output,Config(perf_timeout_seconds=0.1))
            self.assertFalse(output.exists())
    def test_decoder_failure_reports_stderr(self):
        real_popen=subprocess.Popen
        def fake_perf(command,**kwargs):
            return real_popen([sys.executable,"-c","import sys; sys.stderr.write('bad profile');sys.exit(1)"],**kwargs)
        with tempfile.TemporaryDirectory() as temp,patch("cpu_blackbox.query.subprocess.Popen",side_effect=fake_perf):
            with self.assertRaisesRegex(BlackboxError,"bad profile"):
                decode("unused","data",Path(temp)/"out",Config())
