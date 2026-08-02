from __future__ import annotations

import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
INCLUDE = (
    "auto_zcurve",
    "config",
    "R",
    "report",
    "scripts",
    "vendor",
    "pyproject.toml",
    "pixi.toml",
    "pixi.lock",
    "install.sh",
    "install.ps1",
    "README.md",
    "LEGACY.md",
    "LICENSE",
)


def main() -> int:
    DIST.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        bundle = Path(temporary) / "auto-zcurve"
        bundle.mkdir()
        for relative in INCLUDE:
            source = ROOT / relative
            if not source.exists():
                if relative == "LICENSE":
                    continue
                raise SystemExit(f"Required release file is missing: {relative}")
            target = bundle / relative
            if source.is_dir():
                shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
            else:
                shutil.copy2(source, target)

        tar_path = DIST / "auto-zcurve-bundle.tar.gz"
        with tarfile.open(tar_path, "w:gz") as archive:
            archive.add(bundle, arcname="auto-zcurve")
        zip_path = DIST / "auto-zcurve-bundle.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in bundle.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(bundle.parent))
    print(tar_path)
    print(zip_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
