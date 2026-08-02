from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "vendor" / "zcurve_2.4.6.tar.gz"


def main() -> int:
    rscript = shutil.which("Rscript")
    if rscript is None:
        raise SystemExit("Rscript is missing from the managed environment.")
    r_home = Path(
        subprocess.check_output(["R", "RHOME"], text=True).strip()
    )
    library = r_home / "library"
    library.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["R_LIBS_USER"] = str(library)
    env["R_LIBS_SITE"] = str(library)
    check = subprocess.run(
        [rscript, "-e", "quit(status=ifelse(requireNamespace('zcurve', quietly=TRUE),0,1))"],
        check=False,
        env=env,
    )
    if check.returncode == 0:
        return 0
    if not ARCHIVE.is_file():
        raise SystemExit(f"Bundled zcurve archive is missing: {ARCHIVE}")
    subprocess.run(["R", "CMD", "INSTALL", "-l", str(library), str(ARCHIVE)], check=True, env=env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
