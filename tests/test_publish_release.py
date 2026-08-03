import tempfile
import unittest
from pathlib import Path

from scripts.publish_release import project_version, release_command


class PublishReleaseTests(unittest.TestCase):
    def write_versions(self, root: Path, pyproject: str, pixi: str, package: str) -> None:
        (root / "auto_zcurve").mkdir()
        (root / "pyproject.toml").write_text(f'[project]\nversion = "{pyproject}"\n', encoding="utf-8")
        (root / "pixi.toml").write_text(f'[workspace]\nversion = "{pixi}"\n', encoding="utf-8")
        (root / "auto_zcurve" / "__init__.py").write_text(
            f'__version__ = "{package}"\n', encoding="utf-8"
        )

    def test_project_version_requires_matching_declarations(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_versions(root, "1.2.3", "1.2.3", "1.2.3")
            self.assertEqual(project_version(root), "1.2.3")

    def test_project_version_rejects_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_versions(root, "1.2.3", "1.2.4", "1.2.3")
            with self.assertRaisesRegex(SystemExit, "do not match"):
                project_version(root)

    def test_release_command_includes_both_assets_and_draft_flag(self):
        command = release_command("v1.2.3", "abc123", draft=True)
        self.assertEqual(command[:4], ["gh", "release", "create", "v1.2.3"])
        self.assertIn("auto-zcurve-bundle.tar.gz", " ".join(command))
        self.assertIn("auto-zcurve-bundle.zip", " ".join(command))
        self.assertIn("--generate-notes", command)
        self.assertIn("--draft", command)


if __name__ == "__main__":
    unittest.main()
