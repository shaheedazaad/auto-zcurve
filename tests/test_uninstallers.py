import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'posix', 'requires a POSIX shell')
class UninstallerTests(unittest.TestCase):
    def test_preview_cancel_uninstall_and_repeat_preserve_user_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = root / 'install'
            pixi = root / 'pixi'
            for name in ['app', 'app.previous']:
                app = install / name
                app.mkdir(parents=True)
                (app / 'pyproject.toml').write_text('[project]\nname = "auto-zcurve"\n')
                (app / 'pixi.toml').write_text('[workspace]\n')
                (app / 'runtime').write_text('installed runtime')
            (install / 'projects').mkdir()
            (install / 'projects' / 'study.pdf').write_bytes(b'PDF')
            (pixi / 'bin').mkdir(parents=True)
            launcher = pixi / 'bin' / 'auto-zcurve'
            launcher.write_text(f'pixi run --manifest-path "{install}/app/pixi.toml" --frozen auto-zcurve "$@"\n')
            (pixi / 'bin' / 'pixi').write_text('shared tool')
            env = {**os.environ, 'AUTO_ZCURVE_INSTALL_ROOT': str(install), 'PIXI_HOME': str(pixi)}
            def run(*args, input=None):
                return subprocess.run(['sh', str(ROOT / 'uninstall.sh'), *args], env=env,
                                      input=input, text=True, capture_output=True)
            self.assertEqual(run('--dry-run').returncode, 0)
            self.assertTrue((install / 'app').exists())
            self.assertEqual(run(input='n\n').returncode, 0)
            self.assertTrue(launcher.exists())
            # Fail closed before removing the valid app if any target is unrelated.
            (install / 'app.previous' / 'pyproject.toml').write_text('name = "another-app"\n')
            self.assertNotEqual(run('--yes').returncode, 0)
            self.assertTrue((install / 'app').exists())
            (install / 'app.previous' / 'pyproject.toml').write_text('name = "auto-zcurve"\n')
            for _ in range(2):
                self.assertEqual(run('--yes').returncode, 0)
            self.assertFalse((install / 'app').exists())
            self.assertFalse((install / 'app.previous').exists())
            self.assertFalse(launcher.exists())
            self.assertTrue((install / 'projects' / 'study.pdf').exists())
            self.assertTrue((pixi / 'bin' / 'pixi').exists())

    def test_refuses_linked_app(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = root / 'install'
            install.mkdir()
            other = root / 'other'
            other.mkdir()
            (other / 'keep').write_text('data')
            (install / 'app').symlink_to(other, target_is_directory=True)
            result = subprocess.run(['sh', str(ROOT / 'uninstall.sh'), '--yes'],
                                    env={**os.environ, 'AUTO_ZCURVE_INSTALL_ROOT': str(install),
                                         'PIXI_HOME': str(root / 'pixi')}, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue((other / 'keep').exists())
