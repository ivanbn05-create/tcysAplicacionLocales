import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "herramientas" / "configurar_impresion_instalada.ps1"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("powershell")


class ConfiguradorImpresionTests(unittest.TestCase):
    def test_respaldo_es_privado_y_vive_bajo_backups(self):
        contenido = SCRIPT.read_text(encoding="utf-8-sig")
        self.assertIn("Join-Path $rootFull 'backups'", contenido)
        self.assertIn("Security.AccessControl.FileSecurity", contenido)
        self.assertIn("@('S-1-5-18', 'S-1-5-32-544')", contenido)
        self.assertNotIn("Join-Path $raiz ('.env.impresion-backup-", contenido)

    def test_detiene_worker_y_acredita_cola_antes_de_cambiar_entorno(self):
        contenido = SCRIPT.read_text(encoding="utf-8-sig")
        flujo = contenido[contenido.index("$mutexMantenimiento = Enter-MaintenanceMutex") :]
        pasos = [
            "Stop-PosService",
            "Get-BlockingPrintQueueState",
            "Write-EnvironmentAtomically",
            "Start-PosService",
            "Invoke-SafePrinterDiagnostic",
        ]
        posiciones = [flujo.index(paso) for paso in pasos]
        self.assertEqual(posiciones, sorted(posiciones))

    @unittest.skipUnless(POWERSHELL, "Windows PowerShell no está disponible")
    def test_powershell_tiene_sintaxis_valida(self):
        comando = (
            "$t=$null;$e=$null;"
            "[System.Management.Automation.Language.Parser]::ParseFile("
            f"'{SCRIPT}',[ref]$t,[ref]$e)|Out-Null;"
            "if($e.Count){$e|ForEach-Object{$_.Message};exit 1}"
        )
        resultado = subprocess.run(
            [POWERSHELL, "-NoProfile", "-Command", comando],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        self.assertEqual(resultado.returncode, 0, resultado.stdout + resultado.stderr)


if __name__ == "__main__":
    unittest.main()
