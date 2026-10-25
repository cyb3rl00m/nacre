"""Runs gui_smoke.py (fake GTK) in its own process."""

import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


class GuiSmokeTest(unittest.TestCase):
    def test_gui_flows(self):
        res = subprocess.run([sys.executable, str(HERE / "gui_smoke.py")],
                             capture_output=True, text=True, timeout=180)
        self.assertIn("GUI SMOKE OK", res.stdout, res.stdout + res.stderr)


if __name__ == "__main__":
    unittest.main()
