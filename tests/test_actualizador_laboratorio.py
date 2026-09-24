from pathlib import Path
import shutil
import subprocess
import unittest

from herramientas.release_servidor import (
    ARCHIVOS_CONTRATO_REQUERIDOS,
    RUTAS_REQUERIDAS,
)


RAIZ = Path(__file__).resolve().parents[1]
RUTA_SCRIPT = RAIZ / "actualizar-laboratorio-desde-release.ps1"


class ActualizadorLaboratorioTests(unittest.TestCase):
    def test_script_forma_parte_del_contrato_de_release(self):
        nombre = RUTA_SCRIPT.name
        self.assertIn(nombre, RUTAS_REQUERIDAS)
        self.assertIn(nombre, ARCHIVOS_CONTRATO_REQUERIDOS)

    def test_powershell_tiene_sintaxis_valida(self):
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            self.skipTest("Windows PowerShell no está disponible")
        path = str(RUTA_SCRIPT).replace("'", "''")
        command = (
            "$errors=$null; $tokens=$null; "
            "[void][Management.Automation.Language.Parser]::ParseFile("
            f"'{path}', [ref]$tokens, [ref]$errors); "
            "if($errors.Count){$errors | ForEach-Object {$_.ToString()}; exit 1}"
        )
        result = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_verifica_y_extrae_antes_de_detener_sin_borrar_respaldo(self):
        text = RUTA_SCRIPT.read_text(encoding="utf-8")
        verification = text.index("verify --archive")
        extraction = text.index("ExtractToDirectory")
        stop = text.index("Stop-LabService\n    $serviceStopped")
        self.assertLess(verification, stop)
        self.assertLess(extraction, stop)
        mutex = text.index("$copyBackupMutex = Enter-LabBackupMutex")
        release = text.index("Release-LabMutex -Mutex $copyBackupMutex", mutex)
        updater = text.index("& (Join-Path $installation 'actualizar-servidor.ps1')")
        self.assertLess(mutex, stop)
        self.assertLess(release, updater)
        self.assertNotIn("Remove-Item", text)
        self.assertIn("El respaldo completo mantiene código y estado previos", text)
        self.assertIn("Move-Item -LiteralPath $backup -Destination $installation", text)

    def test_snapshot_y_rollback_de_tareas_administradas(self):
        text = RUTA_SCRIPT.read_text(encoding="utf-8")
        for name in (
            "LosTocayosPOS-RespaldoSQLite",
            "LosTocayosPOS-PurgasFisicas",
        ):
            self.assertIn(name, text)
        snapshot = text.index("$taskSnapshots = @(Get-ManagedTaskSnapshots)")
        stop = text.index("Stop-LabService\n    $serviceStopped")
        updater = text.index("& (Join-Path $installation 'actualizar-servidor.ps1')")
        restore = text.index("Restore-ManagedTaskSnapshots -Snapshots $taskSnapshots")
        self.assertLess(snapshot, stop)
        self.assertLess(stop, updater)
        self.assertLess(updater, restore)
        self.assertIn("Export-ScheduledTask", text)
        self.assertIn("Register-ScheduledTask", text)
        self.assertIn("Unregister-ScheduledTask", text)
        success_path = text[updater:text.index("\ncatch {", updater)]
        self.assertNotIn("Restore-ManagedTaskSnapshots", success_path)

    def test_env_se_valida_y_copia_bajo_acl_privada_antes_de_escribir_secretos(self):
        text = RUTA_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("function Get-VerifiedEnvironmentSnapshot", text)
        self.assertIn("function Copy-VerifiedEnvironment", text)
        self.assertIn(".env no tiene exactamente las tres ACE privadas canónicas", text)
        self.assertIn("-ExpectedEnvironmentHash $environmentHashBeforeSwap", text)

        initial_snapshot = text.index("$environmentBeforeSwap = Get-VerifiedEnvironmentSnapshot")
        stop = text.index("Stop-LabService\n    $serviceStopped")
        final_snapshot = text.index("$environmentImmediatelyBeforeStop = Get-VerifiedEnvironmentSnapshot")
        copy_call = text.index("Copy-OperationalState `", stop)
        updater = text.index("& (Join-Path $installation 'actualizar-servidor.ps1')")
        self.assertLess(initial_snapshot, final_snapshot)
        self.assertLess(final_snapshot, stop)
        self.assertLess(stop, copy_call)
        self.assertLess(copy_call, updater)

        protected_copy = text[text.index("function Copy-VerifiedEnvironment"):text.index("function Copy-DirectoryContents")]
        create = protected_copy.index("New-Item -ItemType File -Path $Destination")
        acl = protected_copy.index("Set-Acl -LiteralPath $Destination")
        write = protected_copy.index("[IO.File]::WriteAllBytes($Destination, $snapshot.Bytes)")
        verify = protected_copy.index("Get-VerifiedEnvironmentSnapshot -Path $Destination")
        self.assertLess(create, acl)
        self.assertLess(acl, write)
        self.assertLess(write, verify)
    def test_sella_y_revalida_artefactos_antes_de_extraer(self):
        text = RUTA_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("function New-PrivateDirectory", text)
        self.assertIn("$artifactHashes = @{}", text)
        self.assertIn("$sealedArchive", text)
        self.assertIn("$sealedVerifier", text)
        self.assertIn("La copia privada cambió después de su verificación", text)
        self.assertIn(
            "[IO.Compression.ZipFile]::ExtractToDirectory($sealedArchive, $staging)",
            text,
        )
        self.assertNotIn(
            "[IO.Compression.ZipFile]::ExtractToDirectory($archive, $staging)",
            text,
        )

        initial_verify = text.index("$verificationOutput")
        private_copy = text.index("Copy-Item -LiteralPath $archive")
        sealed_verify = text.index("$sealedVerificationOutput")
        extraction = text.index(
            "[IO.Compression.ZipFile]::ExtractToDirectory($sealedArchive, $staging)"
        )
        stop = text.index("Stop-LabService\n    $serviceStopped")
        self.assertLess(initial_verify, private_copy)
        self.assertLess(private_copy, sealed_verify)
        self.assertLess(sealed_verify, extraction)
        self.assertLess(extraction, stop)
    def test_preserva_estado_y_ejecuta_el_motor_oficial(self):
        text = RUTA_SCRIPT.read_text(encoding="utf-8")
        for required in (".env", ".venv", "runtime", "media", "logs", "backups"):
            self.assertIn(required, text)
        self.assertIn("db.sqlite3-wal", text)
        self.assertIn("actualizar-servidor.ps1", text)
        self.assertIn("Start-LabServiceAndVerify", text)
        self.assertIn("ExpectedVersion", text)
        self.assertIn("Ejecuta este actualizador de laboratorio", text)


if __name__ == "__main__":
    unittest.main()