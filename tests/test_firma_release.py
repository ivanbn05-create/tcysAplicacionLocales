"""Firma propia: aceptación, manipulación y revocación con clave de laboratorio."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SIGNER = ROOT / "herramientas" / "release_firma.ps1"


@unittest.skipUnless(os.name == "nt", "PowerShell/RSA Windows")
class ReleaseSignatureTests(unittest.TestCase):
    def run_signer(self, *args, success=True):
        environment = os.environ.copy()
        environment["PSModulePath"] = str(
            Path(environment["WINDIR"]) / "System32" / "WindowsPowerShell" / "v1.0" / "Modules"
        )
        process = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SIGNER), *map(str, args)],
            capture_output=True, text=True, timeout=60, env=environment,
        )
        if success:
            self.assertEqual(process.returncode, 0, process.stderr)
        else:
            self.assertNotEqual(process.returncode, 0)
        return process

    def test_signature_binds_artifacts_and_revocation(self):
        with tempfile.TemporaryDirectory(prefix="tocayos-release-sign-") as temp:
            root = Path(temp)
            keys = root / "keys"
            self.run_signer("-Mode", "GenerateLabKey", "-LabKeyDirectory", keys)
            archive = root / "package.zip"
            manifest = root / "package.manifest.json"
            checksum = root / "package.sha256"
            signature = root / "package.signature.json"
            trust = keys / "release-trust.json"
            private = keys / "publisher-private.xml"
            archive.write_bytes(b"synthetic signed archive")
            manifest.write_text(json.dumps({
                "product": "LosTocayosPOS-Servidor",
                "release_version": "1.0.0-dev.1",
            }), encoding="utf-8")
            checksum.write_text("synthetic checksum", encoding="utf-8")
            common = [
                "-ArchivePath", archive, "-ManifestPath", manifest,
                "-ChecksumPath", checksum, "-SignaturePath", signature,
                "-ExpectedVersion", "1.0.0-dev.1",
            ]
            self.run_signer("-Mode", "Sign", *common, "-PrivateKeyPath", private)
            self.run_signer("-Mode", "Verify", *common, "-TrustStorePath", trust)
            archive.write_bytes(b"synthetic signed archive!")
            self.run_signer("-Mode", "Verify", *common, "-TrustStorePath", trust, success=False)
            archive.write_bytes(b"synthetic signed archive")
            data = json.loads(trust.read_text(encoding="utf-8"))
            data["keys"][0]["status"] = "revoked"
            trust.write_text(json.dumps(data), encoding="utf-8")
            self.run_signer("-Mode", "Verify", *common, "-TrustStorePath", trust, success=False)


if __name__ == "__main__":
    unittest.main()