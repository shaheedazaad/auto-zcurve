import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class UnixInstallerTests(unittest.TestCase):
    def make_executable(self, path: Path, contents: str) -> None:
        path.write_text(contents, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def test_installer_adds_launcher_directory_to_shell_path_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            home = temp / "home"
            fake_bin = temp / "bin"
            pixi_home = home / "custom pixi's home"
            zdotdir = home / ".config" / "zsh"
            home.mkdir()
            fake_bin.mkdir()
            (home / ".profile").write_text("# Existing profile\n", encoding="utf-8")

            self.make_executable(fake_bin / "pixi", "#!/bin/sh\nexit 0\n")
            self.make_executable(
                fake_bin / "curl",
                "#!/bin/sh\n"
                "while [ \"$#\" -gt 0 ]; do\n"
                "  if [ \"$1\" = -o ]; then shift; : > \"$1\"; exit 0; fi\n"
                "  shift\n"
                "done\n"
                "exit 0\n",
            )
            self.make_executable(
                fake_bin / "tar",
                "#!/bin/sh\n"
                "while [ \"$#\" -gt 0 ]; do\n"
                "  if [ \"$1\" = -C ]; then shift; mkdir -p \"$1/auto-zcurve\"; exit 0; fi\n"
                "  shift\n"
                "done\n"
                "exit 1\n",
            )

            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(home),
                    "PATH": f"{fake_bin}:/usr/bin:/bin",
                    "PIXI_HOME": str(pixi_home),
                    "SHELL": "/bin/zsh",
                    "ZDOTDIR": str(zdotdir),
                }
            )

            for _ in range(2):
                subprocess.run(
                    ["/bin/sh", str(ROOT / "install.sh")],
                    check=True,
                    env=env,
                    capture_output=True,
                    text=True,
                )

            launcher = pixi_home / "bin" / "auto-zcurve"
            self.assertTrue(launcher.is_file())
            self.assertTrue(os.access(launcher, os.X_OK))

            zshrc_path = zdotdir / ".zshrc"
            zshrc = zshrc_path.read_text(encoding="utf-8")
            self.assertEqual(zshrc.count("# Added by the Auto Z-Curve installer."), 1)
            self.assertEqual(zshrc.count("AUTO_ZCURVE_BIN_DIR="), 1)
            self.assertIn("custom pixi", zshrc)

            clean_env = env.copy()
            clean_env["PATH"] = "/usr/bin:/bin"
            resolved = subprocess.run(
                [
                    "/bin/sh",
                    "-c",
                    '. "$1"; command -v auto-zcurve',
                    "sh",
                    str(zshrc_path),
                ],
                check=True,
                env=clean_env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(Path(resolved.stdout.strip()), launcher)

            bash_env = env.copy()
            bash_env["SHELL"] = "/bin/bash"
            subprocess.run(
                ["/bin/sh", str(ROOT / "install.sh")],
                check=True,
                env=bash_env,
                capture_output=True,
                text=True,
            )
            self.assertFalse((home / ".bash_profile").exists())
            self.assertIn("Existing profile", (home / ".profile").read_text(encoding="utf-8"))
            self.assertIn("AUTO_ZCURVE_BIN_DIR=", (home / ".profile").read_text(encoding="utf-8"))
            self.assertIn("AUTO_ZCURVE_BIN_DIR=", (home / ".bashrc").read_text(encoding="utf-8"))


class WindowsInstallerTests(unittest.TestCase):
    def test_installer_updates_persistent_and_current_user_path(self):
        script = (ROOT / "install.ps1").read_text(encoding="utf-8")
        self.assertIn(
            '[Environment]::SetEnvironmentVariable("Path", $NewUserPath, "User")',
            script,
        )
        self.assertIn('$env:Path = "$Directory;$env:Path"', script)
        self.assertIn("Add-DirectoryToUserPath $BinDir", script)
        self.assertIn('$PixiHome = if ($env:PIXI_HOME)', script)


if __name__ == "__main__":
    unittest.main()
