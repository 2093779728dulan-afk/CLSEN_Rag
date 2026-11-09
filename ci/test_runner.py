import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import run_private_checks as runner


class PrivacyContract(unittest.TestCase):
    def test_child_output_stays_in_private_log(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            public = io.StringIO()
            with contextlib.redirect_stdout(public):
                runner.run('synthetic', [sys.executable, '-c', 'print("PRIVATE_CONTENT_MARKER")'],
                    cwd=root, environment=os.environ, log=root/'private.log')
            self.assertEqual(public.getvalue(), 'synthetic PASS\n')
            self.assertIn('PRIVATE_CONTENT_MARKER', (root/'private.log').read_text())

    def test_nonzero_child_is_not_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(runner.CheckFailure):
                runner.run('synthetic', [sys.executable, '-c', 'raise SystemExit(7)'],
                    cwd=root, environment=os.environ, log=root/'private.log')

    def test_invalid_revision_is_rejected_before_credential_access(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(runner.CheckFailure) as caught:
                runner.checkout('$(unexpected)', Path(temporary), {})
            self.assertEqual(caught.exception.stage, 'source_revision')

    def test_checkout_failure_removes_temporary_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(runner.subprocess, 'run', return_value=SimpleNamespace(returncode=1)):
                with self.assertRaises(runner.CheckFailure):
                    runner.checkout('1'*40, root, {'CLSEN_SOURCE_READ_KEY': 'PRIVATE_KEY_MARKER'})
            self.assertFalse((root/'read-key').exists())
            self.assertFalse((root/'known-hosts').exists())

    def test_xml_only_exposes_numeric_totals(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'result.xml'
            path.write_text('<testsuites><testsuite tests="3" failures="1" errors="0" skipped="0"><testcase name="private-name"><failure>PRIVATE_CONTENT_MARKER</failure></testcase></testsuite></testsuites>')
            self.assertEqual(runner.counts(path), {'tests': 3, 'failures': 1, 'errors': 0, 'skipped': 0})

    def test_unexpected_error_does_not_print_private_details(self):
        public = io.StringIO()
        with patch.object(runner, 'checkout', side_effect=RuntimeError('PRIVATE_CONTENT_MARKER')):
            with patch.object(runner, 'workspace', return_value=Path('synthetic')):
                with contextlib.redirect_stdout(public):
                    result = runner.main('checkout')
        self.assertEqual(result, 2)
        self.assertEqual(public.getvalue(), 'runner_configuration FAIL\n')

    def test_command_files_and_credentials_are_not_inherited(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summary = root/'public-summary'
            inherited = {**os.environ, 'GITHUB_STEP_SUMMARY': str(summary), 'GITHUB_OUTPUT': 'public-output',
                'GITHUB_ENV': 'public-env', 'GITHUB_STATE': 'public-state',
                'CLSEN_SOURCE_READ_KEY': 'PRIVATE_KEY_MARKER', 'GH_TOKEN': 'PRIVATE_TOKEN_MARKER'}
            environment = runner.command_environment(inherited)
            for key in ['GITHUB_STEP_SUMMARY', 'GITHUB_OUTPUT', 'GITHUB_ENV', 'GITHUB_STATE', 'CLSEN_SOURCE_READ_KEY', 'GH_TOKEN']:
                self.assertNotIn(key, environment)
            code = 'import os; from pathlib import Path; p=os.environ.get("GITHUB_STEP_SUMMARY"); Path(p).write_text("PRIVATE_CONTENT_MARKER") if p else None'
            with contextlib.redirect_stdout(io.StringIO()):
                runner.run('synthetic', [sys.executable, '-c', code], cwd=root,
                    environment=environment, log=root/'private.log')
            self.assertFalse(summary.exists())

    @unittest.skipUnless(Path('/proc/self/environ').exists(), 'Linux process environment boundary')
    def test_separate_checkout_step_does_not_leak_to_next_process_tree(self):
        marker = 'SYNTHETIC_CHECKOUT_KEY_19483'
        subprocess.run([sys.executable, '-c', 'pass'], env={**os.environ, 'CLSEN_SOURCE_READ_KEY': marker}, check=True)
        code = '''
import os
from pathlib import Path
pid=os.getppid()
while pid > 1:
    root=Path('/proc')/str(pid)
    if b'SYNTHETIC_CHECKOUT_KEY_19483' in (root/'environ').read_bytes():
        raise SystemExit(1)
    pid=int((root/'status').read_text().split('PPid:')[1].split()[0])
'''
        result = subprocess.run([sys.executable, '-c', code], env=runner.command_environment(os.environ), capture_output=True)
        self.assertEqual(result.returncode, 0)
        workflow = (Path(__file__).resolve().parents[1]/'.github/workflows/product-knowledge-ci.yml').read_text()
        self.assertEqual(workflow.count('CLSEN_SOURCE_READ_KEY:'), 1)
        self.assertIn('run_private_checks.py checkout', workflow)
        check_step = workflow.split('- name: Check private source without credentials')[1].split('- name: Clean')[0]
        self.assertNotIn('CLSEN_SOURCE_READ_KEY', check_step)


if __name__ == '__main__':
    unittest.main()
