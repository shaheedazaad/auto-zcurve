import subprocess
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

from scripts import build_release as build, install_r_package as install, publish_release as publish


class BundleTests(unittest.TestCase):
    def test_archives_contain_required_files_and_exclude_caches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / 'auto_zcurve'
            package.mkdir()
            (package / 'app.py').write_text('content')
            (package / '__pycache__').mkdir()
            (package / '__pycache__/cached.pyc').write_bytes(b'cache')
            (package / '.DS_Store').touch()
            (root / 'README.md').write_text('readme')
            dist = root / 'dist'
            with patch.object(build, 'ROOT', root), patch.object(build, 'DIST', dist), patch.object(build, 'INCLUDE', ('auto_zcurve', 'README.md', 'LICENSE')), patch('builtins.print'):
                self.assertEqual(build.main(), 0)
                with zipfile.ZipFile(dist / 'auto-zcurve-bundle.zip') as archive:
                    self.assertEqual(set(archive.namelist()), {'auto-zcurve/auto_zcurve/app.py', 'auto-zcurve/README.md'})
                    self.assertEqual(archive.read('auto-zcurve/auto_zcurve/app.py'), b'content')
                with tarfile.open(dist / 'auto-zcurve-bundle.tar.gz') as archive:
                    self.assertEqual(archive.extractfile('auto-zcurve/README.md').read(), b'readme')
                    self.assertFalse(any('__pycache__' in name for name in archive.getnames()))
                (root / 'README.md').unlink()
                with self.assertRaisesRegex(SystemExit, 'Required release file is missing'):
                    build.main()


class RInstallTests(unittest.TestCase):
    def test_missing_runtime_is_actionable(self):
        with patch.object(install.shutil, 'which', return_value=None), self.assertRaisesRegex(SystemExit, 'Rscript is missing'):
            install.main()

    def test_installed_missing_archive_and_install_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / 'zcurve.tar.gz'
            with patch.object(install.shutil, 'which', return_value='/bin/Rscript'), patch.object(install.subprocess, 'check_output', return_value=str(root)), patch.object(install, 'ARCHIVE', archive):
                with patch.object(install.subprocess, 'run', return_value=NS(returncode=0)) as run:
                    self.assertEqual(install.main(), 0)
                    run.assert_called_once()
                    self.assertEqual(run.call_args.kwargs['env']['R_LIBS_SITE'], str(root / 'library'))
                with patch.object(install.subprocess, 'run', return_value=NS(returncode=1)), self.assertRaisesRegex(SystemExit, 'archive is missing'):
                    install.main()
                archive.touch()
                with patch.object(install.subprocess, 'run', return_value=NS(returncode=1)) as run:
                    self.assertEqual(install.main(), 0)
                    self.assertEqual(run.call_args.args[0], ['R', 'CMD', 'INSTALL', '-l', str(root / 'library'), str(archive)])
                    self.assertTrue(run.call_args.kwargs['check'])


class PublishValidationTests(unittest.TestCase):
    def test_version_validation_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                publish.declared_versions(Path(tmp))
            (Path(tmp) / 'pyproject.toml').write_text('name = "test"')
            with self.assertRaisesRegex(SystemExit, 'Could not find a version'):
                publish.declared_versions(Path(tmp))
        with patch.object(publish, 'declared_versions', return_value={'a': 'bad'}), self.assertRaisesRegex(SystemExit, 'not valid'):
            publish.project_version()

    def test_checkout_guards(self):
        for outputs, message in [(['modified'], 'not clean'), (['', 'head', subprocess.CalledProcessError(1, 'git')], 'no upstream'), (['', 'head', 'different'], 'not pushed')]:
            with patch.object(publish, 'command_output', side_effect=outputs), self.assertRaisesRegex(SystemExit, message):
                publish.require_clean_pushed_checkout()
        with patch.object(publish, 'command_output', side_effect=['', 'head', 'head']):
            self.assertEqual(publish.require_clean_pushed_checkout(), 'head')
        with patch.object(publish.subprocess, 'check_output', return_value=' value \n'):
            self.assertEqual(publish.command_output(['git', 'status']), 'value')

    def test_github_cli_checks_without_contacting_github(self):
        with patch.object(publish.shutil, 'which', return_value=None), self.assertRaisesRegex(SystemExit, 'GitHub CLI is required'):
            publish.require_github_cli('v1.0.0')
        with patch.object(publish.shutil, 'which', return_value='/bin/gh'):
            with patch.object(publish.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'gh')), self.assertRaisesRegex(SystemExit, 'not authenticated'):
                publish.require_github_cli('v1.0.0')
            for code, stderr, message in [(0, '', 'already exists'), (1, 'offline', 'Could not check'), (1, 'release not found', None)]:
                with patch.object(publish.subprocess, 'run', return_value=NS(returncode=code, stderr=stderr)):
                    if message:
                        with self.assertRaisesRegex(SystemExit, message):
                            publish.require_github_cli('v1.0.0')
                    else:
                        publish.require_github_cli('v1.0.0')

    def test_build_checks_and_asset_validation(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(publish, 'DIST', Path(tmp)):
            with patch.object(publish.shutil, 'which', return_value=None), self.assertRaisesRegex(SystemExit, 'Pixi is required'):
                publish.run_checks_and_build(skip_checks=False)
            with patch.object(publish.subprocess, 'run'), self.assertRaisesRegex(SystemExit, 'asset was not created'):
                publish.run_checks_and_build(skip_checks=True)
            for name in ['auto-zcurve-bundle.tar.gz', 'auto-zcurve-bundle.zip']:
                (Path(tmp) / name).write_bytes(b'archive')
            for skip, count in [(True, 1), (False, 3)]:
                with patch.object(publish.shutil, 'which', return_value='/bin/pixi'), patch.object(publish.subprocess, 'run') as run:
                    publish.run_checks_and_build(skip_checks=skip)
                    self.assertEqual(run.call_count, count)

    def test_main_dry_run_cancel_confirm_draft_and_publish_are_isolated(self):
        with patch.object(publish, 'project_version', return_value='1.0.0'), patch.object(publish, 'require_clean_pushed_checkout', return_value='head'), patch.object(publish, 'require_github_cli'), patch.object(publish, 'command_output', return_value='head'), patch.object(publish, 'run_checks_and_build') as build, patch.object(publish.subprocess, 'run') as run, patch('builtins.print'):
            self.assertEqual(publish.main(['--dry-run']), 0)
            run.assert_not_called()
            with patch.object(publish.sys.stdin, 'isatty', return_value=False), self.assertRaisesRegex(SystemExit, 'confirmation is unavailable'):
                publish.main([])
            with patch.object(publish.sys.stdin, 'isatty', return_value=True), patch('builtins.input', return_value='no'):
                build.reset_mock()
                self.assertEqual(publish.main([]), 0)
                build.assert_not_called()
                run.assert_not_called()
            for args in [['--yes'], ['--yes', '--draft'], []]:
                with patch.object(publish.sys.stdin, 'isatty', return_value=True), patch('builtins.input', return_value='yes'):
                    self.assertEqual(publish.main(args), 0)
                self.assertEqual('--draft' in run.call_args.args[0], '--draft' in args)
                self.assertEqual(run.call_args.args[0][:4], ['gh', 'release', 'create', 'v1.0.0'])


class ScriptEntryPointTests(unittest.TestCase):
    def test_build_script_entry_creates_archives_in_fixture_root(self):
        import runpy
        script = str(build.ROOT / 'scripts/build_release.py')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for relative in build.INCLUDE:
                (root / relative).write_text('fixture')
            with patch.object(Path, 'resolve', return_value=root / 'scripts/build_release.py'), patch('builtins.print'), self.assertRaises(SystemExit) as caught:
                runpy.run_path(script, run_name='__main__')
            self.assertEqual(caught.exception.code, 0)
            self.assertTrue((root / 'dist/auto-zcurve-bundle.zip').is_file())

    def test_install_script_entry_uses_managed_library(self):
        import runpy
        script = str(install.ROOT / 'scripts/install_r_package.py')
        with tempfile.TemporaryDirectory() as tmp, patch.object(install.shutil, 'which', return_value='/bin/Rscript'), patch.object(install.subprocess, 'check_output', return_value=tmp), patch.object(install.subprocess, 'run', return_value=NS(returncode=0)), self.assertRaises(SystemExit) as caught:
            runpy.run_path(script, run_name='__main__')
        self.assertEqual(caught.exception.code, 0)

    def test_publish_script_entry_dry_run_never_creates_release(self):
        import runpy
        script = str(publish.ROOT / 'scripts/publish_release.py')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'auto_zcurve').mkdir()
            (root / 'dist').mkdir()
            for name in ['pyproject.toml', 'pixi.toml']:
                (root / name).write_text('version = "1.0.0"')
            (root / 'auto_zcurve/__init__.py').write_text('__version__ = "1.0.0"')
            for name in ['auto-zcurve-bundle.zip', 'auto-zcurve-bundle.tar.gz']:
                (root / 'dist' / name).write_bytes(b'archive')
            with patch.object(Path, 'resolve', return_value=root / 'scripts/publish_release.py'), patch.object(publish.sys, 'argv', [script, '--dry-run', '--skip-checks']), patch.object(publish.subprocess, 'check_output', return_value='head'), patch.object(publish.subprocess, 'run') as run, patch('builtins.print'), self.assertRaises(SystemExit) as caught:
                runpy.run_path(script, run_name='__main__')
            self.assertEqual(caught.exception.code, 0)
            run.assert_called_once()
            self.assertNotIn('gh', run.call_args.args[0])
