import contextlib
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

from openpyxl import Workbook

import herramientas.release_servidor as release_servidor
from herramientas.release_servidor import (
    ARCHIVOS_CONTRATO_REQUERIDOS,
    ENTRADA_MANIFIESTO,
    ErrorRelease,
    PREFIJOS_CONTRATO_REQUERIDOS,
    RUTA_CATALOGO_HISTORICO,
    _parser,
    crear_release,
    verificar_release,
)


COMMIT_PRUEBA = "a" * 40
EPOCH_PRUEBA = 1_788_969_600


class ReleaseServidorTests(unittest.TestCase):
    def _crear_fuente(self, raiz: Path) -> Path:
        fuente = raiz / "fuente"
        (fuente / "aplicacion").mkdir(parents=True)
        for relativa in ARCHIVOS_CONTRATO_REQUERIDOS:
            ruta = fuente / relativa
            ruta.parent.mkdir(parents=True, exist_ok=True)
            if relativa == RUTA_CATALOGO_HISTORICO:
                libro = Workbook()
                hoja = libro.active
                hoja.title = "Productos"
                hoja.append(
                    ("Codigo", "Nombre", "Categoria", "Precio", "Destino impresion")
                )
                hoja.append(("P01", "Producto de prueba", "Taco", "10", "cocina"))
                libro.save(ruta)
                libro.close()
                continue
            if relativa == "VERSION":
                contenido = "1.2.3-prueba.1\n"
            elif relativa == "requirements-lock.txt":
                contenido = 'demo==1.0\npywin32==311; sys_platform == "win32"\n'
            else:
                contenido = f"fixture de {relativa}\n"
            ruta.write_text(contenido, encoding="ascii")
        for prefijo in PREFIJOS_CONTRATO_REQUERIDOS:
            ruta = fuente / prefijo / "fixture_contrato.py"
            ruta.parent.mkdir(parents=True, exist_ok=True)
            if not ruta.exists():
                ruta.write_text("# fixture\n", encoding="ascii")
        (fuente / "aplicacion" / "codigo.py").write_text(
            "VALOR = 'incluido'\n", encoding="utf-8"
        )
        return fuente

    def _inicializar_git(self, fuente: Path) -> str:
        subprocess.run(["git", "init", str(fuente)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(fuente), "config", "user.email", "tests@example.invalid"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(fuente), "config", "user.name", "Pruebas"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(fuente), "add", "."], check=True, capture_output=True
        )
        subprocess.run(
            ["git", "-C", str(fuente), "commit", "-m", "fixture"],
            check=True,
            capture_output=True,
        )
        return subprocess.run(
            ["git", "-C", str(fuente), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def _construir_git(self, fuente: Path, destino: Path, commit: str):
        return crear_release(
            origen=fuente,
            destino=destino,
            version="1.2.3-prueba.1",
            commit=commit,
            source_date_epoch=EPOCH_PRUEBA,
            rutas_incluidas=self._rutas_contrato(),
        )

    def _reemplazar_entrada_en_release(
        self, artefactos, ruta_objetivo: str, contenido: bytes
    ) -> None:
        manifiesto = json.loads(artefactos.manifiesto.read_bytes())
        item_objetivo = next(
            item for item in manifiesto["files"]
            if item["path"] == ruta_objetivo
        )
        diferencia = len(contenido) - item_objetivo["size"]
        item_objetivo["size"] = len(contenido)
        item_objetivo["sha256"] = hashlib.sha256(contenido).hexdigest()
        manifiesto["payload_size"] += diferencia
        bytes_manifiesto = (
            json.dumps(
                manifiesto,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")

        temporal = artefactos.zip.with_suffix(".reescrito.zip")
        with zipfile.ZipFile(artefactos.zip, "r") as origen, zipfile.ZipFile(
            temporal, "w"
        ) as destino:
            for info in origen.infolist():
                datos = origen.read(info)
                if info.filename == ruta_objetivo:
                    datos = contenido
                elif info.filename == ENTRADA_MANIFIESTO:
                    datos = bytes_manifiesto
                destino.writestr(info, datos)
        os.replace(temporal, artefactos.zip)
        artefactos.manifiesto.write_bytes(bytes_manifiesto)
        artefactos.sumas.write_text(
            (
                f"{hashlib.sha256(artefactos.zip.read_bytes()).hexdigest()}  "
                f"{artefactos.zip.name}\n"
                f"{hashlib.sha256(bytes_manifiesto).hexdigest()}  "
                f"{artefactos.manifiesto.name}\n"
            ),
            encoding="ascii",
        )

    def _reemplazar_catalogo_en_release(self, artefactos, contenido: bytes) -> None:
        self._reemplazar_entrada_en_release(
            artefactos, RUTA_CATALOGO_HISTORICO, contenido
        )

    def _actualizar_sumas(self, artefactos) -> None:
        artefactos.sumas.write_text(
            (
                f"{hashlib.sha256(artefactos.zip.read_bytes()).hexdigest()}  "
                f"{artefactos.zip.name}\n"
                f"{hashlib.sha256(artefactos.manifiesto.read_bytes()).hexdigest()}  "
                f"{artefactos.manifiesto.name}\n"
            ),
            encoding="ascii",
        )

    def _rutas_contrato(self) -> tuple[str, ...]:
        return (
            *ARCHIVOS_CONTRATO_REQUERIDOS,
            *(prefijo.rstrip("/") for prefijo in PREFIJOS_CONTRATO_REQUERIDOS),
            "aplicacion",
        )

    def _crear_wheel_demo(
        self,
        ruta: Path,
        nombre: str = "demo",
        version: str = "1.0",
        requires_dist: tuple[str, ...] = (),
    ) -> None:
        prefijo = nombre.replace("-", "_")
        dependencias = "".join(
            f"Requires-Dist: {requisito}\n" for requisito in requires_dist
        )
        archivos = {
            f"{prefijo}/__init__.py": f"__version__ = '{version}'\n",
            f"{prefijo}-{version}.dist-info/METADATA": (
                f"Metadata-Version: 2.1\nName: {nombre}\nVersion: {version}\n"
                f"{dependencias}"
            ),
            f"{prefijo}-{version}.dist-info/WHEEL": (
                "Wheel-Version: 1.0\nGenerator: prueba\n"
                "Root-Is-Purelib: true\nTag: py3-none-any\n"
            ),
        }
        ruta_record = f"{prefijo}-{version}.dist-info/RECORD"
        filas_record = []
        for nombre_archivo, contenido in archivos.items():
            datos = contenido.encode("utf-8")
            digest = base64.urlsafe_b64encode(hashlib.sha256(datos).digest()).rstrip(b"=")
            filas_record.append(
                f"{nombre_archivo},sha256={digest.decode('ascii')},{len(datos)}"
            )
        archivos[ruta_record] = "\n".join((*filas_record, f"{ruta_record},,")) + "\n"
        with zipfile.ZipFile(ruta, "w", compression=zipfile.ZIP_DEFLATED) as wheel:
            for nombre, contenido in archivos.items():
                wheel.writestr(nombre, contenido)

    def _agregar_pywin32_fixture(self, wheelhouse: Path) -> None:
        self._crear_wheel_demo(
            wheelhouse / "pywin32-311-py3-none-any.whl",
            nombre="pywin32",
            version="311",
        )

    def _construir(self, fuente: Path, destino: Path):
        return crear_release(
            origen=fuente,
            destino=destino,
            version="1.2.3-prueba.1",
            commit=COMMIT_PRUEBA,
            source_date_epoch=EPOCH_PRUEBA,
            rutas_incluidas=self._rutas_contrato(),
        )

    def test_construye_verifica_y_excluye_estado_y_secretos(self):
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            fuente = self._crear_fuente(raiz)
            excluidos = {
                "aplicacion/.env": "TOKEN=no-debe-salir",
                "aplicacion/.env.production": "PASSWORD=no-debe-salir",
                "aplicacion/db.sqlite3": "datos",
                "aplicacion/credencial.key": "secreto",
                "aplicacion/logs/django.log": "registro",
                "aplicacion/runtime/cache.bin": "cache",
                "aplicacion/backups/db.bak": "respaldo",
                "aplicacion/media/cliente.txt": "privado",
                "aplicacion/certs/private.pem": "privado",
                "aplicacion/data/ventas.csv": "datos",
                "aplicacion/datos/clientes.txt": "datos",
                "aplicacion/__pycache__/codigo.pyc": "cache",
                "aplicacion/.git/config": "git",
            }
            for relativa, contenido in excluidos.items():
                ruta = fuente / relativa
                ruta.parent.mkdir(parents=True, exist_ok=True)
                ruta.write_text(contenido, encoding="utf-8")
            sibling_datos = fuente / "datos" / "clientes-locales.txt"
            sibling_datos.write_text("no distribuir\n", encoding="utf-8")

            artefactos = self._construir(fuente, raiz / "salida")
            manifiesto = verificar_release(archivo_zip=artefactos.zip)

            self.assertEqual(manifiesto["release_version"], "1.2.3-prueba.1")
            self.assertEqual(manifiesto["source_commit"], COMMIT_PRUEBA)
            self.assertTrue(manifiesto["source_dirty"])
            rutas = {item["path"] for item in manifiesto["files"]}
            self.assertIn("aplicacion/codigo.py", rutas)
            self.assertTrue(set(ARCHIVOS_CONTRATO_REQUERIDOS).issubset(rutas))
            for excluida in excluidos:
                self.assertNotIn(excluida, rutas)
            self.assertNotIn("datos/clientes-locales.txt", rutas)
            with zipfile.ZipFile(artefactos.zip) as paquete:
                self.assertEqual(set(paquete.namelist()), rutas | {ENTRADA_MANIFIESTO})
                interno = json.loads(paquete.read(ENTRADA_MANIFIESTO))
            self.assertEqual(interno, manifiesto)
            self.assertEqual(len(artefactos.sumas.read_text("ascii").splitlines()), 2)

    def test_dos_construcciones_producen_bytes_identicos(self):
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            fuente = self._crear_fuente(raiz)
            primera = self._construir(fuente, raiz / "salida-a")
            segunda = self._construir(fuente, raiz / "salida-b")

            self.assertEqual(primera.zip.read_bytes(), segunda.zip.read_bytes())
            self.assertEqual(
                primera.manifiesto.read_bytes(), segunda.manifiesto.read_bytes()
            )
            self.assertEqual(primera.sumas.read_bytes(), segunda.sumas.read_bytes())

    def test_detecta_zip_modificado_antes_de_abrirlo(self):
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            artefactos = self._construir(self._crear_fuente(raiz), raiz / "salida")
            contenido = bytearray(artefactos.zip.read_bytes())
            contenido[len(contenido) // 2] ^= 1
            artefactos.zip.write_bytes(contenido)

            with self.assertRaisesRegex(ErrorRelease, "SHA-256 del ZIP"):
                verificar_release(archivo_zip=artefactos.zip)

    def test_rechaza_bytes_anexos_aunque_se_actualice_la_suma(self):
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            artefactos = self._construir(self._crear_fuente(raiz), raiz / "salida")
            with artefactos.zip.open("ab") as archivo:
                archivo.write(b"contenido-anexo")
            self._actualizar_sumas(artefactos)

            with self.assertRaisesRegex(ErrorRelease, "bytes anexos"):
                verificar_release(archivo_zip=artefactos.zip)

    def test_detecta_manifiesto_externo_modificado(self):
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            artefactos = self._construir(self._crear_fuente(raiz), raiz / "salida")
            artefactos.manifiesto.write_bytes(
                artefactos.manifiesto.read_bytes() + b" "
            )

            with self.assertRaisesRegex(ErrorRelease, "SHA-256 del manifiesto"):
                verificar_release(archivo_zip=artefactos.zip)

    def test_rechaza_catalogo_que_no_es_xlsx_al_construir(self):
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            fuente = self._crear_fuente(raiz)
            (fuente / RUTA_CATALOGO_HISTORICO).write_bytes(b"no es un XLSX")

            with self.assertRaisesRegex(ErrorRelease, "XLSX/OPC"):
                self._construir(fuente, raiz / "salida")

    def test_rechaza_catalogo_con_columnas_incompatibles_al_construir(self):
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            fuente = self._crear_fuente(raiz)
            catalogo = fuente / RUTA_CATALOGO_HISTORICO
            libro = Workbook()
            hoja = libro.active
            hoja.title = "Productos"
            hoja.append(("Nombre", "Codigo", "Precio", "Categoria", "Destino"))
            hoja.append(("Producto", "P01", "10", "Taco", "cocina"))
            libro.save(catalogo)
            libro.close()

            with self.assertRaisesRegex(ErrorRelease, "columnas esperadas"):
                self._construir(fuente, raiz / "salida")

    def test_rechaza_xlsx_que_excede_el_presupuesto_descomprimido(self):
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            fuente = self._crear_fuente(raiz)

            with mock.patch.object(
                release_servidor, "TAMANO_MAXIMO_XLSX_DESCOMPRIMIDO", 1
            ), self.assertRaisesRegex(ErrorRelease, "íntegro y acotado"):
                self._construir(fuente, raiz / "salida")

    def test_verificador_rechaza_catalogo_corrupto_aunque_hashes_coincidan(self):
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            artefactos = self._construir(
                self._crear_fuente(raiz), raiz / "salida"
            )
            self._reemplazar_catalogo_en_release(
                artefactos, b"contenido corrupto pero firmado por el manifiesto"
            )

            with self.assertRaisesRegex(ErrorRelease, "XLSX/OPC"):
                verificar_release(archivo_zip=artefactos.zip)

    def test_rechaza_inclusion_explicita_de_secreto(self):
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            fuente = self._crear_fuente(raiz)
            (fuente / ".env").write_text("TOKEN=secreto", encoding="utf-8")

            with self.assertRaisesRegex(ErrorRelease, "archivo sensible"):
                crear_release(
                    origen=fuente,
                    destino=raiz / "salida",
                    version="1.2.3-prueba.1",
                    commit=COMMIT_PRUEBA,
                    source_date_epoch=EPOCH_PRUEBA,
                    rutas_incluidas=(".env",),
                )

    def test_no_sobrescribe_una_release_existente(self):
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            fuente = self._crear_fuente(raiz)
            destino = raiz / "salida"
            primera = self._construir(fuente, destino)
            sha_antes = hashlib.sha256(primera.zip.read_bytes()).hexdigest()

            with self.assertRaisesRegex(ErrorRelease, "inmutable"):
                self._construir(fuente, destino)
            self.assertEqual(
                hashlib.sha256(primera.zip.read_bytes()).hexdigest(), sha_antes
            )

    def test_rechaza_travesia_y_nombres_reservados_de_windows(self):
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            fuente = self._crear_fuente(raiz)
            for ruta in ("../manage.py", "CON"):
                with self.subTest(ruta=ruta), self.assertRaises(ErrorRelease):
                    crear_release(
                        origen=fuente,
                        destino=raiz / "salida",
                        version="1.2.3-prueba.1",
                        commit=COMMIT_PRUEBA,
                        source_date_epoch=EPOCH_PRUEBA,
                        rutas_incluidas=(ruta,),
                    )

    def test_rechaza_rutas_no_representables_en_windows(self):
        invalidas = (
            "carpeta/invalido?.txt",
            "carpeta/control\t.txt",
            "CONIN$.txt",
            "LPT¹.log",
            "a" * 256,
        )
        for ruta in invalidas:
            with self.subTest(ruta=ruta), self.assertRaisesRegex(
                ErrorRelease, "Ruta relativa no segura"
            ):
                release_servidor._validar_ruta_relativa(ruta)

    def test_rechaza_enlace_en_un_componente_de_la_ruta_incluida(self):
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            fuente = self._crear_fuente(raiz)
            externa = raiz / "externa"
            externa.mkdir()
            (externa / "fuera.py").write_text("SECRETO = True\n", encoding="utf-8")
            enlace = fuente / "aplicacion" / "puente"
            try:
                if os.name == "nt":
                    creado = subprocess.run(
                        ["cmd", "/c", "mklink", "/J", str(enlace), str(externa)],
                        check=False,
                        capture_output=True,
                    )
                    if creado.returncode:
                        self.skipTest("Windows no permitió crear una junction de prueba")
                else:
                    enlace.symlink_to(externa, target_is_directory=True)
            except OSError:
                self.skipTest("El sistema no permitió crear un enlace de prueba")

            with self.assertRaisesRegex(ErrorRelease, "enlaces|uniones|escapa"):
                crear_release(
                    origen=fuente,
                    destino=raiz / "salida",
                    version="1.2.3-prueba.1",
                    commit=COMMIT_PRUEBA,
                    source_date_epoch=EPOCH_PRUEBA,
                    rutas_incluidas=("aplicacion/puente/fuera.py",),
                )

    def test_rechaza_inclusion_de_otro_archivo_del_directorio_datos(self):
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            fuente = self._crear_fuente(raiz)
            (fuente / "datos" / "otro.txt").write_text(
                "estado local\n", encoding="utf-8"
            )

            with self.assertRaisesRegex(ErrorRelease, "ruta reservada"):
                crear_release(
                    origen=fuente,
                    destino=raiz / "salida",
                    version="1.2.3-prueba.1",
                    commit=COMMIT_PRUEBA,
                    source_date_epoch=EPOCH_PRUEBA,
                    rutas_incluidas=(
                        *self._rutas_contrato(),
                        "datos/otro.txt",
                    ),
                )

    def test_rechaza_clave_privada_xml_aunque_no_se_incluya_en_rutas(self):
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            fuente = self._crear_fuente(raiz)
            (fuente / "publisher-private.xml").write_text(
                "<RSAKeyValue><D>secreto</D></RSAKeyValue>", encoding="ascii"
            )
            with self.assertRaisesRegex(ErrorRelease, "clave privada XML"):
                crear_release(
                    origen=fuente,
                    destino=raiz / "salida",
                    version="1.2.3-prueba.1",
                    commit=COMMIT_PRUEBA,
                    source_date_epoch=EPOCH_PRUEBA,
                    rutas_incluidas=self._rutas_contrato(),
                )

    def test_exige_version_autoritativa_y_coincidente(self):
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            fuente = self._crear_fuente(raiz)
            (fuente / "VERSION").write_text("9.9.9\n", encoding="ascii")

            with self.assertRaisesRegex(ErrorRelease, "no coincide con VERSION"):
                self._construir(fuente, raiz / "salida")

            (fuente / "VERSION").unlink()
            with self.assertRaisesRegex(ErrorRelease, "VERSION autoritativo"):
                self._construir(fuente, raiz / "salida")

    def test_incluye_solo_wheels_de_un_wheelhouse_plano(self):
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            fuente = self._crear_fuente(raiz)
            wheelhouse = raiz / "paquetes"
            wheelhouse.mkdir()
            self._crear_wheel_demo(wheelhouse / "demo-1.0-py3-none-any.whl")
            self._agregar_pywin32_fixture(wheelhouse)
            (fuente / "requirements-lock.txt").write_text(
                'demo==1.0\npywin32==311; sys_platform == "win32"\n', encoding="ascii"
            )

            artefactos = crear_release(
                origen=fuente,
                destino=raiz / "salida",
                version="1.2.3-prueba.1",
                commit=COMMIT_PRUEBA,
                source_date_epoch=EPOCH_PRUEBA,
                rutas_incluidas=self._rutas_contrato(),
                wheelhouse=wheelhouse,
            )
            manifiesto = verificar_release(archivo_zip=artefactos.zip)

            self.assertEqual(manifiesto["dependency_bundle"], "wheelhouse")
            self.assertEqual(
                manifiesto["target"],
                {
                    "implementation": "cp", "python": "3.13", "abi": "cp313",
                    "platform": "win_amd64", "bits": 64,
                },
            )
            self.assertIn(
                "wheelhouse/demo-1.0-py3-none-any.whl",
                [item["path"] for item in manifiesto["files"]],
            )

    def test_rechaza_wheel_adicional_aunque_sea_valido(self):
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            fuente = self._crear_fuente(raiz)
            (fuente / "requirements-lock.txt").write_text(
                'demo==1.0\npywin32==311; sys_platform == "win32"\n', encoding="ascii"
            )
            wheelhouse = raiz / "paquetes"
            wheelhouse.mkdir()
            self._crear_wheel_demo(wheelhouse / "demo-1.0-py3-none-any.whl")
            self._agregar_pywin32_fixture(wheelhouse)
            self._crear_wheel_demo(
                wheelhouse / "extra-1.0-py3-none-any.whl", nombre="extra"
            )

            with self.assertRaisesRegex(ErrorRelease, "sin paquetes adicionales"):
                crear_release(
                    origen=fuente,
                    destino=raiz / "salida",
                    version="1.2.3-prueba.1",
                    commit=COMMIT_PRUEBA,
                    source_date_epoch=EPOCH_PRUEBA,
                    rutas_incluidas=self._rutas_contrato(),
                    wheelhouse=wheelhouse,
                )

    def test_verificador_rechaza_wheel_sustituido_con_hashes_actualizados(self):
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            fuente = self._crear_fuente(raiz)
            (fuente / "requirements-lock.txt").write_text(
                'demo==1.0\npywin32==311; sys_platform == "win32"\n', encoding="ascii"
            )
            wheelhouse = raiz / "paquetes"
            wheelhouse.mkdir()
            self._crear_wheel_demo(wheelhouse / "demo-1.0-py3-none-any.whl")
            self._agregar_pywin32_fixture(wheelhouse)
            artefactos = crear_release(
                origen=fuente,
                destino=raiz / "salida",
                version="1.2.3-prueba.1",
                commit=COMMIT_PRUEBA,
                source_date_epoch=EPOCH_PRUEBA,
                rutas_incluidas=self._rutas_contrato(),
                wheelhouse=wheelhouse,
            )
            self._reemplazar_entrada_en_release(
                artefactos,
                "wheelhouse/demo-1.0-py3-none-any.whl",
                b"no-es-un-wheel",
            )

            with self.assertRaisesRegex(ErrorRelease, "wheel"):
                verificar_release(archivo_zip=artefactos.zip)

    def test_validador_de_wheel_aplica_limite_antes_de_descomprimir(self):
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            fuente = self._crear_fuente(raiz)
            (fuente / "requirements-lock.txt").write_text(
                'demo==1.0\npywin32==311; sys_platform == "win32"\n', encoding="ascii"
            )
            wheelhouse = raiz / "paquetes"
            wheelhouse.mkdir()
            self._crear_wheel_demo(wheelhouse / "demo-1.0-py3-none-any.whl")
            self._agregar_pywin32_fixture(wheelhouse)

            with mock.patch.object(release_servidor, "ENTRADAS_MAXIMAS_WHEEL", 2), \
                    self.assertRaisesRegex(ErrorRelease, "acotado"):
                crear_release(
                    origen=fuente,
                    destino=raiz / "salida",
                    version="1.2.3-prueba.1",
                    commit=COMMIT_PRUEBA,
                    source_date_epoch=EPOCH_PRUEBA,
                    rutas_incluidas=self._rutas_contrato(),
                    wheelhouse=wheelhouse,
                )

    def test_rechaza_archivos_ajenos_en_wheelhouse(self):
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            fuente = self._crear_fuente(raiz)
            wheelhouse = raiz / "paquetes"
            wheelhouse.mkdir()
            (wheelhouse / "instalador.exe").write_bytes(b"no")

            with self.assertRaisesRegex(ErrorRelease, "sólo admite wheels"):
                crear_release(
                    origen=fuente,
                    destino=raiz / "salida",
                    version="1.2.3-prueba.1",
                    commit=COMMIT_PRUEBA,
                    source_date_epoch=EPOCH_PRUEBA,
                    rutas_incluidas=self._rutas_contrato(),
                    wheelhouse=wheelhouse,
                )

    def test_rechaza_archivo_renombrado_como_wheel(self):
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            fuente = self._crear_fuente(raiz)
            wheelhouse = raiz / "paquetes"
            wheelhouse.mkdir()
            (wheelhouse / "demo-1.0-py3-none-any.whl").write_bytes(b"no es zip")

            with self.assertRaisesRegex(ErrorRelease, "wheel ZIP válido"):
                crear_release(
                    origen=fuente,
                    destino=raiz / "salida",
                    version="1.2.3-prueba.1",
                    commit=COMMIT_PRUEBA,
                    source_date_epoch=EPOCH_PRUEBA,
                    rutas_incluidas=self._rutas_contrato(),
                    wheelhouse=wheelhouse,
                )

    def test_rechaza_wheelhouse_integro_pero_incompleto_para_el_lock(self):
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            fuente = self._crear_fuente(raiz)
            (fuente / "requirements-lock.txt").write_text(
                'paquete-ausente-tocayos==987.654.321\npywin32==311; sys_platform == "win32"\n', encoding="ascii"
            )
            wheelhouse = raiz / "paquetes"
            wheelhouse.mkdir()
            self._crear_wheel_demo(wheelhouse / "demo-1.0-py3-none-any.whl")
            self._agregar_pywin32_fixture(wheelhouse)

            with self.assertRaisesRegex(ErrorRelease, "no resuelve completamente"):
                crear_release(
                    origen=fuente,
                    destino=raiz / "salida",
                    version="1.2.3-prueba.1",
                    commit=COMMIT_PRUEBA,
                    source_date_epoch=EPOCH_PRUEBA,
                    rutas_incluidas=self._rutas_contrato(),
                    wheelhouse=wheelhouse,
                )

    def test_rechaza_lock_con_rango_o_url(self):
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            fuente = self._crear_fuente(raiz)
            wheelhouse = raiz / "paquetes"
            wheelhouse.mkdir()
            wheel = wheelhouse / "demo-1.0-py3-none-any.whl"
            self._crear_wheel_demo(wheel)

            for contenido in ("demo>=1.0\n", f"demo @ {wheel.as_uri()}\n"):
                (fuente / "requirements-lock.txt").write_text(
                    contenido, encoding="ascii"
                )
                with self.subTest(contenido=contenido), self.assertRaisesRegex(
                    ErrorRelease, "sólo admite un pin"
                ):
                    crear_release(
                        origen=fuente,
                        destino=raiz / ("salida-" + hashlib.sha256(contenido.encode()).hexdigest()[:8]),
                        version="1.2.3-prueba.1",
                        commit=COMMIT_PRUEBA,
                        source_date_epoch=EPOCH_PRUEBA,
                        rutas_incluidas=self._rutas_contrato(),
                        wheelhouse=wheelhouse,
                    )

    def test_rechaza_url_file_fuera_del_wheelhouse(self):
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            wheelhouse = raiz / "paquetes"
            wheelhouse.mkdir()
            exterior = raiz / "demo-1.0-py3-none-any.whl"
            self._crear_wheel_demo(exterior)
            self._crear_wheel_demo(wheelhouse / exterior.name)

            with self.assertRaisesRegex(ErrorRelease, "no pertenece al wheelhouse"):
                release_servidor._ruta_wheel_desde_url(exterior.as_uri(), wheelhouse)

    def test_rechaza_dependencia_transitiva_sin_pin_exacto(self):
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            fuente = self._crear_fuente(raiz)
            (fuente / "requirements-lock.txt").write_text(
                'principal==1.0\npywin32==311; sys_platform == "win32"\n',
                encoding="ascii",
            )
            wheelhouse = raiz / "paquetes"
            wheelhouse.mkdir()
            self._crear_wheel_demo(
                wheelhouse / "principal-1.0-py3-none-any.whl",
                nombre="principal",
                requires_dist=("transitiva>=1",),
            )
            self._crear_wheel_demo(
                wheelhouse / "transitiva-1.0-py3-none-any.whl",
                nombre="transitiva",
            )
            self._agregar_pywin32_fixture(wheelhouse)

            with self.assertRaisesRegex(ErrorRelease, "pin exacto y activo"):
                crear_release(
                    origen=fuente,
                    destino=raiz / "salida",
                    version="1.2.3-prueba.1",
                    commit=COMMIT_PRUEBA,
                    source_date_epoch=EPOCH_PRUEBA,
                    rutas_incluidas=self._rutas_contrato(),
                    wheelhouse=wheelhouse,
                )

    def test_rechaza_record_sin_hashes(self):
        with tempfile.TemporaryDirectory() as temporal:
            wheel = Path(temporal) / "demo-1.0-py3-none-any.whl"
            self._crear_wheel_demo(wheel)
            reescrito = wheel.with_suffix(".tmp.whl")
            with zipfile.ZipFile(wheel, "r") as origen, zipfile.ZipFile(
                reescrito, "w", compression=zipfile.ZIP_DEFLATED
            ) as destino:
                for info in origen.infolist():
                    contenido = origen.read(info)
                    if info.filename.endswith(".dist-info/RECORD"):
                        contenido = contenido.replace(b",sha256=", b",,")
                    destino.writestr(info.filename, contenido)
            os.replace(reescrito, wheel)

            with self.assertRaisesRegex(ErrorRelease, "RECORD"):
                release_servidor._validar_wheel(wheel, "El wheel de prueba")

    def test_rechaza_metadata_ambiguo_en_wheel(self):
        with tempfile.TemporaryDirectory() as temporal:
            wheel = Path(temporal) / "demo-1.0-py3-none-any.whl"
            self._crear_wheel_demo(wheel)
            reescrito = wheel.with_suffix(".tmp.whl")
            with zipfile.ZipFile(wheel, "r") as origen, zipfile.ZipFile(
                reescrito, "w", compression=zipfile.ZIP_DEFLATED
            ) as destino:
                for info in origen.infolist():
                    contenido = origen.read(info)
                    if info.filename.endswith(".dist-info/METADATA"):
                        contenido = contenido.replace(
                            b"Name: demo\n", b"Name: demo\nName: impostor\n"
                        )
                    destino.writestr(info.filename, contenido)
            os.replace(reescrito, wheel)

            with self.assertRaisesRegex(ErrorRelease, "metadatos"):
                release_servidor._validar_wheel(wheel, "El wheel de prueba")

    def test_rechaza_payload_sin_contrato_minimo(self):
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            fuente = self._crear_fuente(raiz)

            with self.assertRaisesRegex(ErrorRelease, "contrato mínimo"):
                crear_release(
                    origen=fuente,
                    destino=raiz / "salida",
                    version="1.2.3-prueba.1",
                    commit=COMMIT_PRUEBA,
                    source_date_epoch=EPOCH_PRUEBA,
                    rutas_incluidas=("VERSION", "manage.py"),
                )

    def test_cli_exige_elegir_dependencias_exactamente_una_vez(self):
        parser = _parser()
        base = ["build", "--version", "1.2.3"]
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(base)
            with self.assertRaises(SystemExit):
                parser.parse_args(base + ["--source-only", "--wheelhouse", "paquetes"])

    def test_rechaza_marcas_git_que_ocultan_el_estado_del_payload(self):
        for opcion, modifica in (
            ("--assume-unchanged", True),
            ("--skip-worktree", False),
        ):
            with self.subTest(opcion=opcion), tempfile.TemporaryDirectory() as temporal:
                raiz = Path(temporal)
                fuente = self._crear_fuente(raiz)
                commit = self._inicializar_git(fuente)
                objetivo = "aplicacion/codigo.py"
                subprocess.run(
                    ["git", "-C", str(fuente), "update-index", opcion, objetivo],
                    check=True,
                    capture_output=True,
                )
                if modifica:
                    (fuente / objetivo).write_text(
                        "VALOR = 'oculto'\n", encoding="utf-8"
                    )

                with self.assertRaisesRegex(
                    ErrorRelease, "assume-unchanged, skip-worktree"
                ):
                    self._construir_git(fuente, raiz / "salida", commit)

    def test_allow_dirty_sigue_empaquetando_un_arbol_git_modificado(self):
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            fuente = self._crear_fuente(raiz)
            commit = self._inicializar_git(fuente)
            (fuente / "aplicacion" / "codigo.py").write_text(
                "VALOR = 'cambio deliberado'\n", encoding="utf-8"
            )

            artefactos = crear_release(
                origen=fuente,
                destino=raiz / "salida",
                version="1.2.3-prueba.1",
                commit=commit,
                source_date_epoch=EPOCH_PRUEBA,
                permitir_sucio=True,
                rutas_incluidas=self._rutas_contrato(),
            )
            manifiesto = verificar_release(archivo_zip=artefactos.zip)

            self.assertTrue(manifiesto["source_dirty"])
            with zipfile.ZipFile(artefactos.zip, "r") as paquete:
                self.assertEqual(
                    paquete.read("aplicacion/codigo.py").decode("utf-8").splitlines(),
                    ["VALOR = 'cambio deliberado'"],
                )

    def test_comparacion_git_respeta_filtros_de_gitattributes(self):
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            fuente = self._crear_fuente(raiz)
            (fuente / ".gitattributes").write_bytes(b"*.py text eol=lf\n")
            commit = self._inicializar_git(fuente)
            # La copia de trabajo puede conservar CRLF en Windows, pero el hash
            # limpio debe compararse con el blob tras aplicar el filtro Git.
            estado = subprocess.run(
                [
                    "git", "-C", str(fuente), "status", "--porcelain=v1",
                    "--", "aplicacion/codigo.py",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(estado.stdout, "")

            artefactos = self._construir_git(fuente, raiz / "salida", commit)
            manifiesto = verificar_release(archivo_zip=artefactos.zip)

            self.assertFalse(manifiesto["source_dirty"])

    def test_rechaza_filtro_git_que_oculta_bytes_del_payload(self):
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            fuente = self._crear_fuente(raiz)
            self._inicializar_git(fuente)
            (fuente / ".gitattributes").write_text(
                "aplicacion/codigo.py filter=oculto\n", encoding="ascii"
            )
            subprocess.run(
                [
                    "git", "-C", str(fuente), "config", "filter.oculto.clean",
                    "cat",
                ],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(fuente), "config", "filter.oculto.smudge", "cat"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(fuente), "add", ".gitattributes"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(fuente), "commit", "-m", "atributo oculto"],
                check=True,
                capture_output=True,
            )
            commit = subprocess.run(
                ["git", "-C", str(fuente), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            estado = subprocess.run(
                ["git", "-C", str(fuente), "status", "--porcelain=v1"],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(estado.stdout, "")

            with self.assertRaisesRegex(ErrorRelease, "transformación Git"):
                self._construir_git(fuente, raiz / "salida", commit)

    def test_git_replace_no_sustituye_el_commit_declarado(self):
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            fuente = self._crear_fuente(raiz)
            commit_original = self._inicializar_git(fuente)
            (fuente / "aplicacion" / "codigo.py").write_text(
                "VALOR = 'otro commit'\n", encoding="utf-8"
            )
            subprocess.run(
                ["git", "-C", str(fuente), "add", "aplicacion/codigo.py"], check=True
            )
            subprocess.run(
                ["git", "-C", str(fuente), "commit", "-m", "sustituto"],
                check=True,
                capture_output=True,
            )
            commit_sustituto = subprocess.run(
                ["git", "-C", str(fuente), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            subprocess.run(
                ["git", "-C", str(fuente), "replace", commit_original, commit_sustituto],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(fuente), "checkout", "--detach", commit_original],
                check=True,
                capture_output=True,
            )

            with self.assertRaisesRegex(
                ErrorRelease, "(?:cambios sin commit|no coincide con HEAD)"
            ):
                self._construir_git(
                    fuente, raiz / "salida", commit_original
                )

    def test_detecta_cambio_toctou_despues_de_escribir_un_archivo(self):
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            fuente = self._crear_fuente(raiz)
            commit = self._inicializar_git(fuente)
            escribir_original = release_servidor._escribir_entrada
            alterado = False

            def escribir_y_alterar(paquete, item, fecha):
                nonlocal alterado
                escribir_original(paquete, item, fecha)
                if item.ruta == "aplicacion/codigo.py" and not alterado:
                    item.origen.write_text(
                        "VALOR = 'cambio posterior'\n", encoding="utf-8"
                    )
                    alterado = True

            with mock.patch.object(
                release_servidor, "_escribir_entrada", escribir_y_alterar
            ), self.assertRaisesRegex(ErrorRelease, "no coincide con HEAD"):
                self._construir_git(fuente, raiz / "salida", commit)

    def test_rechaza_archivo_ignorado_por_git_dentro_del_payload(self):
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            fuente = self._crear_fuente(raiz)
            subprocess.run(["git", "init", str(fuente)], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", str(fuente), "config", "user.email", "tests@example.invalid"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(fuente), "config", "user.name", "Pruebas"],
                check=True,
            )
            (fuente / ".gitignore").write_text(
                "aplicacion/configuracion-local.txt\n", encoding="ascii"
            )
            subprocess.run(
                ["git", "-C", str(fuente), "add", "."], check=True, capture_output=True
            )
            subprocess.run(
                ["git", "-C", str(fuente), "commit", "-m", "fixture"],
                check=True,
                capture_output=True,
            )
            commit = subprocess.run(
                ["git", "-C", str(fuente), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            (fuente / "aplicacion" / "configuracion-local.txt").write_text(
                "no empaquetar\n", encoding="ascii"
            )

            with self.assertRaisesRegex(ErrorRelease, "ignorados por Git"):
                crear_release(
                    origen=fuente,
                    destino=raiz / "salida",
                    version="1.2.3-prueba.1",
                    commit=commit,
                    source_date_epoch=EPOCH_PRUEBA,
                    rutas_incluidas=self._rutas_contrato(),
                )

if __name__ == "__main__":
    unittest.main()
