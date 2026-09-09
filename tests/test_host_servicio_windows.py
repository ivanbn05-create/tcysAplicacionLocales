import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from herramientas import host_servicio_windows as host


@unittest.skipUnless(os.name == "nt", "Host exclusivo de Windows")
class ServiceHostTests(unittest.TestCase):
    def test_missing_dependency_does_not_overwrite_existing_host(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "Scripts" / "pythonservice.exe"
            executable.parent.mkdir()
            executable.write_bytes(b"existing")
            sources = [(root / "missing.dll", root / "python313.dll")]
            with patch.object(host, "sys", SimpleNamespace(prefix=str(root), base_prefix="C:\\Python")), \
                    patch.object(host, "_archivos_del_host", return_value=sources), \
                    patch.object(host, "comprobar_host") as check:
                with self.assertRaises(host.ServiceHostError):
                    host.preparar_host()
            self.assertEqual(executable.read_bytes(), b"existing")
            check.assert_not_called()

    def test_preserves_previously_moved_executable_and_copies_dll(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "Scripts" / "pythonservice.exe"
            executable.parent.mkdir()
            executable.write_bytes(b"host")
            source = root / "package.dll"
            source.write_bytes(b"dll")
            target = root / "python313.dll"
            sources = [(executable, executable), (source, target)]
            with patch.object(host, "sys", SimpleNamespace(prefix=str(root), base_prefix="C:\\Python")), \
                    patch.object(host, "_archivos_del_host", return_value=sources), \
                    patch.object(host, "comprobar_host") as check:
                self.assertEqual(host.preparar_host(), executable)
                self.assertEqual(host.preparar_host(), executable)
            self.assertEqual(executable.read_bytes(), b"host")
            self.assertEqual(target.read_bytes(), b"dll")
            self.assertEqual(check.call_count, 2)

    def test_preflight_does_not_inherit_python_path_or_secrets(self):
        result = subprocess.CompletedProcess([], 2, b"Python Service Manager", b"")
        with patch.dict(os.environ, {"DJANGO_SECRET_KEY": "fixture", "PYTHONPATH": "wrong"}), \
                patch.object(host.subprocess, "run", return_value=result) as run:
            host.comprobar_host(Path("C:/fixture/pythonservice.exe"))
        environment = run.call_args.kwargs["env"]
        self.assertNotIn("DJANGO_SECRET_KEY", environment)
        self.assertNotIn("PYTHONPATH", environment)
        self.assertNotIn("Python", environment["PATH"])

    def test_accepts_native_wide_character_help(self):
        result = subprocess.CompletedProcess([], 2, "Python Service Manager".encode("utf-16-le"), b"")
        with patch.object(host.subprocess, "run", return_value=result):
            host.comprobar_host(Path("C:/fixture/pythonservice.exe"))

    def test_loader_failure_and_timeout_stop_preflight(self):
        result = subprocess.CompletedProcess([], 0xC0000135, b"", b"")
        with patch.object(host.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(host.ServiceHostError, "0xC0000135"):
                host.comprobar_host(Path("C:/fixture/pythonservice.exe"))
        with patch.object(host.subprocess, "run", side_effect=subprocess.TimeoutExpired([], 15)):
            with self.assertRaisesRegex(host.ServiceHostError, "15 segundos"):
                host.comprobar_host(Path("C:/fixture/pythonservice.exe"))
