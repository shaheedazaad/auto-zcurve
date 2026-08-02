from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path


class RParallelTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("Rscript"), "Rscript is not installed")
    def test_zcurve_parallel_detection_and_fallbacks(self):
        root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        conda_prefix = os.environ.get("CONDA_PREFIX", "").strip()
        if conda_prefix:
            library = Path(conda_prefix) / "lib" / "R" / "library"
            if sys.platform == "win32":
                library = Path(conda_prefix) / "Lib" / "R" / "library"
            env["R_LIBS_USER"] = str(library)
            env["R_LIBS_SITE"] = str(library)
        completed = subprocess.run(
            ["Rscript", "tests/r/test_report_parallel.R"],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"R parallel tests failed.\n{completed.stdout}\n{completed.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
