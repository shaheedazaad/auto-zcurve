from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


class RParallelTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("Rscript"), "Rscript is not installed")
    def test_zcurve_parallel_detection_and_fallbacks(self):
        root = Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            ["Rscript", "tests/r/test_report_parallel.R"],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"R parallel tests failed.\n{completed.stdout}\n{completed.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
