import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from herramientas.preflight_release import ErrorPreflight, evaluar_preflight


GIT = shutil.which("git")


@unittest.skipUnless(GIT, "Git no esta disponible")
class PreflightReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporal = tempfile.TemporaryDirectory()
        self.raiz = Path(self.temporal.name)
        self._git("init", "--quiet")
        self._git("config", "user.name", "Preflight Tests")
        self._git("config", "user.email", "preflight@example.invalid")
        archivos = {
            "VERSION": "1.2.3-prueba.1\n",
            "pos/version.py": (
                'from pathlib import Path\n'
                '_VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"\n'
                'APP_VERSION = _VERSION_FILE.read_text(encoding="utf-8").strip()\n'
            ),
            "ventas/views.py": (
                "from pos.version import APP_VERSION\n"
                "ASSET_VERSION = APP_VERSION\n"
                'PWA_CACHE = f"tocayos-pos-{ASSET_VERSION}"\n'
                'CODIGO = """self.skipWaiting(); self.clients.claim();"""\n'
                'HEADERS = {"Cache-Control": "no-cache"}\n'
            ),
            "personas/modulos.py": (
                "from pos.version import APP_VERSION\nVERSION_MODULOS = APP_VERSION\n"
            ),
            "herramientas/validar_despliegue.py": (
                'shutil.copyfile(source / "VERSION", snapshot / "VERSION")\n'
            ),
            "actualizar-laboratorio-desde-release.ps1": (
                "$stagedVersionPath = Join-Path $staging 'VERSION'\n"
                "if ($stagedVersion -cne $ExpectedVersion) { throw 'version' }\n"
                "foreach ($relative in @('.env', '.venv', 'runtime', 'media', 'logs', 'backups'))\n"
            ),
            "instalar-servicio-lan.ps1": "function Assert-EnvironmentUnchanged {}\n",
        }
        for relativa, contenido in archivos.items():
            ruta = self.raiz / relativa
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_text(contenido, encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "--quiet", "-m", "fixture")
        self.commit = self._git("rev-parse", "HEAD").strip()

    def tearDown(self):
        self.temporal.cleanup()

    def _git(self, *argumentos):
        return subprocess.run(
            [GIT, *argumentos],
            cwd=self.raiz,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=True,
        ).stdout

    def test_devuelve_identidad_reproducible_desde_arbol_limpio(self):
        resultado = evaluar_preflight(
            self.raiz,
            version_esperada="1.2.3-prueba.1",
            commit_esperado=self.commit,
        )

        self.assertEqual(resultado["status"], "ok")
        self.assertEqual(resultado["version"], "1.2.3-prueba.1")
        self.assertEqual(resultado["commit"], self.commit)
        self.assertGreater(resultado["source_date_epoch"], 0)

    def test_rechaza_cambios_y_archivos_sin_rastrear(self):
        (self.raiz / "temporal.txt").write_text("no publicar", encoding="utf-8")

        with self.assertRaisesRegex(ErrorPreflight, "cambios o archivos sin rastrear"):
            evaluar_preflight(self.raiz)

    def test_rechaza_version_multilinea_aunque_el_arbol_aun_no_se_valide(self):
        (self.raiz / "VERSION").write_text("1.2.3\n1.2.4\n", encoding="utf-8")

        with self.assertRaisesRegex(ErrorPreflight, "una sola linea"):
            evaluar_preflight(self.raiz)

    def test_rechaza_segunda_fuente_version_rastreada(self):
        duplicada = self.raiz / "otro" / "VERSION"
        duplicada.parent.mkdir()
        duplicada.write_text("1.2.3-prueba.1\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "--quiet", "-m", "duplica version")

        with self.assertRaisesRegex(ErrorPreflight, "unico archivo VERSION"):
            evaluar_preflight(self.raiz)

    def test_rechaza_snapshot_que_omite_version(self):
        ruta = self.raiz / "herramientas" / "validar_despliegue.py"
        ruta.write_text("# VERSION omitida\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "--quiet", "-m", "rompe snapshot")

        with self.assertRaisesRegex(ErrorPreflight, "contrato de VERSION"):
            evaluar_preflight(self.raiz)

    def test_rechaza_actualizador_que_no_preserva_entorno(self):
        ruta = self.raiz / "actualizar-laboratorio-desde-release.ps1"
        contenido = ruta.read_text(encoding="utf-8")
        contenido = contenido.replace(
            "foreach ($relative in @('.env', '.venv', 'runtime', 'media', 'logs', 'backups'))",
            "# preservacion operativa omitida",
        )
        ruta.write_text(contenido, encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "--quiet", "-m", "rompe preservacion")

        with self.assertRaisesRegex(ErrorPreflight, "preservacion"):
            evaluar_preflight(self.raiz)

    def test_rechaza_instalador_sin_comprobar_hash_de_entorno(self):
        ruta = self.raiz / "instalar-servicio-lan.ps1"
        ruta.write_text("# sin invariante de entorno\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "--quiet", "-m", "rompe invariante env")

        with self.assertRaisesRegex(ErrorPreflight, "preservacion"):
            evaluar_preflight(self.raiz)


    def test_rechaza_contratos_pwa_presentes_solo_en_comentarios(self):
        ruta = self.raiz / "ventas" / "views.py"
        contenido = ruta.read_text(encoding="utf-8")
        contenido = contenido.replace(
            'CODIGO = """self.skipWaiting(); self.clients.claim();"""',
            "# self.skipWaiting()\n# self.clients.claim()",
        )
        ruta.write_text(contenido, encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "--quiet", "-m", "comenta contrato pwa")

        with self.assertRaisesRegex(ErrorPreflight, "contrato de VERSION"):
            evaluar_preflight(self.raiz)

if __name__ == "__main__":
    unittest.main()
