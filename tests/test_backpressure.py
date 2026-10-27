"""Validate the example under saturated, publicly configured event capacity."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class BackpressureTests(unittest.TestCase):
    def test_saturated_nested_graphs_and_tool_events(self):
        script = Path(__file__).parent / "scenarios" / "saturation.py"
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "config.yaml").write_text(
                "LOG:\n  show_event_dispatch: false\n"
                "PIPELINE:\n  event_loop_backpressure: 128\n  event_pipe_max_len: 8\n",
            )
            result = subprocess.run(
                [sys.executable, str(script)],
                cwd=folder,
                capture_output=True,
                text=True,
                timeout=25,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("160 concurrent turns passed", result.stdout)
