from __future__ import annotations

import argparse
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


def command_output(command: Sequence[str]) -> str:
    return subprocess.check_output(command, cwd=ROOT, text=True, stderr=subprocess.STDOUT).strip()


def declared_versions(root: Path = ROOT) -> dict[str, str]:
    patterns = {
        "pyproject.toml": re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE),
        "pixi.toml": re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE),
        "auto_zcurve/__init__.py": re.compile(r'^__version__\s*=\s*"([^"]+)"', re.MULTILINE),
    }
    versions: dict[str, str] = {}
    for relative, pattern in patterns.items():
        path = root / relative
        match = pattern.search(path.read_text(encoding="utf-8"))
        if match is None:
            raise SystemExit(f"Could not find a version declaration in {relative}.")
        versions[relative] = match.group(1)
    return versions


def project_version(root: Path = ROOT) -> str:
    versions = declared_versions(root)
    unique = set(versions.values())
    if len(unique) != 1:
        details = ", ".join(f"{path}={version}" for path, version in versions.items())
        raise SystemExit(f"Version declarations do not match: {details}")
    version = unique.pop()
    if not VERSION_PATTERN.fullmatch(version):
        raise SystemExit(f"Project version is not valid: {version}")
    return version


def require_clean_pushed_checkout() -> str:
    status = command_output(["git", "status", "--porcelain"])
    if status:
        raise SystemExit("The working tree is not clean. Commit or stash all changes before publishing.")

    head = command_output(["git", "rev-parse", "HEAD"])
    try:
        upstream = command_output(["git", "rev-parse", "@{upstream}"])
    except subprocess.CalledProcessError as exc:
        raise SystemExit("The current branch has no upstream. Push it before publishing.") from exc
    if head != upstream:
        raise SystemExit("The current commit is not pushed to the branch upstream.")
    return head


def require_github_cli(tag: str) -> None:
    if shutil.which("gh") is None:
        raise SystemExit("GitHub CLI is required. Install it from https://cli.github.com/ and run `gh auth login`.")
    try:
        subprocess.run(["gh", "auth", "status"], cwd=ROOT, check=True)
    except subprocess.CalledProcessError as exc:
        raise SystemExit("GitHub CLI is not authenticated. Run `gh auth login` and try again.") from exc
    existing = subprocess.run(
        ["gh", "release", "view", tag],
        cwd=ROOT,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
    )
    if existing.returncode == 0:
        raise SystemExit(f"GitHub release {tag} already exists. Bump the project version first.")
    if "release not found" not in existing.stderr.lower():
        raise SystemExit(f"Could not check for an existing GitHub release: {existing.stderr.strip()}")


def release_command(tag: str, head: str, *, draft: bool = False) -> list[str]:
    command = [
        "gh",
        "release",
        "create",
        tag,
        str(DIST / "auto-zcurve-bundle.tar.gz"),
        str(DIST / "auto-zcurve-bundle.zip"),
        "--title",
        f"Auto Z-Curve {tag}",
        "--generate-notes",
        "--target",
        head,
    ]
    if draft:
        command.append("--draft")
    return command


def run_checks_and_build(*, skip_checks: bool) -> None:
    if not skip_checks:
        if shutil.which("pixi") is None:
            raise SystemExit("Pixi is required to run the release checks.")
        subprocess.run(["pixi", "run", "--locked", "test"], cwd=ROOT, check=True)
        subprocess.run(["pixi", "run", "--locked", "release-smoke"], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "build_release.py")], cwd=ROOT, check=True)
    for asset in (DIST / "auto-zcurve-bundle.tar.gz", DIST / "auto-zcurve-bundle.zip"):
        if not asset.is_file() or asset.stat().st_size == 0:
            raise SystemExit(f"Release asset was not created: {asset}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and publish the current Auto Z-Curve release.")
    parser.add_argument("--draft", action="store_true", help="Create a draft release instead of publishing immediately.")
    parser.add_argument("--yes", action="store_true", help="Publish without an interactive confirmation prompt.")
    parser.add_argument("--skip-checks", action="store_true", help="Skip tests and the R/Quarto smoke test.")
    parser.add_argument("--dry-run", action="store_true", help="Build and validate without creating a GitHub release.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    version = project_version()
    tag = f"v{version}"
    if args.dry_run:
        head = command_output(["git", "rev-parse", "HEAD"])
    else:
        head = require_clean_pushed_checkout()
        require_github_cli(tag)

    action = "create a draft for" if args.draft else "publish"
    if not args.yes and not args.dry_run:
        if not sys.stdin.isatty():
            raise SystemExit("Interactive confirmation is unavailable. Rerun with --yes.")
        answer = input(f"Run release checks and {action} {tag} from {head[:12]}? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Release cancelled.")
            return 0

    run_checks_and_build(skip_checks=args.skip_checks)
    command = release_command(tag, head, draft=args.draft)
    if args.dry_run:
        print("Dry run complete. Release command:")
        print(shlex.join(command))
        return 0


    subprocess.run(command, cwd=ROOT, check=True)
    if args.draft:
        print(f"Created draft {tag}. Publish it on GitHub when ready; installers ignore draft releases.")
    else:
        print(f"Published {tag}. The installer will now resolve to this release.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
