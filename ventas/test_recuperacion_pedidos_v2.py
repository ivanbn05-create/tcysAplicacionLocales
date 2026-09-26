"""Prueba local del flujo r7 congelado: 410, ACK, recibo y cinco negativos."""
import base64
import io
import json
import os
import tempfile
import uuid
import zipfile
from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.test import TestCase, override_settings
from django.utils import timezone

from personas.models import Sucursal
from ventas.models import (
    ConfiguracionSucursal,
    EstadoSincronizacionPedidos,
    Mesa,
    PedidoSucursalImportado,
    PrecioProductoSucursal,
    ProductoSucursal,
    RecuperacionPedidosV2,
    SucursalPedido,
)
from ventas.pedidos_recovery_v2 import PinnedPedidosKey, RecoveryV2Error, canonical_json, sha256
from ventas.recuperacion_pedidos_v2 import (
    RecuperacionLocalError,
    acuse_persistido,
    aplicar_recibo,
    exportar_preestado,
    preparar_recuperacion,
)


EDGE_ID = "11111111-1111-4111-8111-111111111111"
BRANCH_ID = "93a42904-4d09-4a50-94cf-2edbf5aa0e51"
SENDERS = (77,)
KEY_ID = "22222222-2222-4222-8222-222222222222"
EDGE_KEY_ID = "33333333-3333-4333-8333-333333333333"
RECOVERY_ID = "44444444-4444-4444-8444-444444444444"
FREEZE_ID = "55555555-5555-4555-8555-555555555555"


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _signed(payload, key):
    return canonical_json({
        "payload": payload,
        "signature_b64": _b64(key.sign(canonical_json(payload))),
    }) + b"\n"


def _zip(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_STORED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return output.getvalue()


@override_settings(
    PEDIDOS_API_BOUND_IDENTITY=True,
    PEDIDOS_API_EDGE_ID=EDGE_ID,
    PEDIDOS_API_POS_BRANCH_ID=BRANCH_ID,
    PEDIDOS_API_SUCURSAL_IDS=SENDERS,
    PEDIDOS_API_MAX_RESPONSE_BYTES=1048576,
)
class RecoveryServiceE2ETests(TestCase):
    def setUp(self):
        self.private = tempfile.TemporaryDirectory()
        self.addCleanup(self.private.cleanup)
        self.runtime = Path(self.private.name)
        runtime_override = override_settings(RUNTIME_DIR=self.runtime)
        runtime_override.enable()
        self.addCleanup(runtime_override.disable)

        self.sucursal = Sucursal.objects.create(
            id=uuid.UUID(BRANCH_ID), clave="RECOVERY-LAB", nombre="Sucursal recovery"
        )
        ConfiguracionSucursal.objects.create(
            sucursal=self.sucursal, instalacion_id=uuid.UUID(EDGE_ID),
            clave_administrador="hash-prueba",
        )
        self.sender = SucursalPedido.objects.create(
            sucursal=self.sucursal, origen_id=77, nombre="Sucursal de prueba",
            tipo=SucursalPedido.Tipo.SUCURSAL,
            identidad_confirmada_en=timezone.now(),
        )
        self.product = ProductoSucursal.objects.create(
            sucursal=self.sucursal, origen_id=8, nombre="Producto de prueba",
            nombre_ticket="PROD PRUEBA", unidad="PZA",
            cantidad_por_precio=Decimal("1.000"),
        )
        PrecioProductoSucursal.objects.create(
            sucursal=self.sucursal, cliente_sucursal=self.sender,
            producto=self.product, importe=Decimal("122.75"),
            nombre_ticket="PROD PRUEBA", vigente_desde=date(2026, 1, 1),
        )
        Mesa.objects.create(
            sucursal=self.sucursal, canal=Mesa.Canal.SUCURSALES,
            clave="SUC-77", nombre="Mesa prueba", cliente_sucursal=self.sender,
        )
        self.start = datetime(2026, 9, 25, tzinfo=dt_timezone.utc)
        self.end = datetime(2026, 9, 26, tzinfo=dt_timezone.utc)
        self.state = EstadoSincronizacionPedidos.objects.create(
            sucursal=self.sucursal, version_api="v2",
            estado=EstadoSincronizacionPedidos.Estado.RECONCILIACION,
            ventana_desde=self.start, ventana_hasta=self.end,
            agua_alta_hasta=self.start, sucursales_origen=[77],
            cursor="cursor-dentro-ventana",
            ultimo_cursor_confirmado="cursor-dentro-ventana",
        )
        self.pedidos_private = Ed25519PrivateKey.generate()
        public = self.pedidos_private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.pins = {KEY_ID: PinnedPedidosKey(_b64(public))}
        self.edge_private = Ed25519PrivateKey.generate()
        self.edge_key_path = self.runtime / "edge-private.pem"
        self.edge_key_path.write_bytes(self.edge_private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ))
        os.chmod(self.edge_key_path, 0o600)

    def _order(self):
        return {
            "id": 12001,
            "codigo_publico": "00000000-0000-4000-8000-000000000001",
            "fecha_confirmacion": "2026-09-25T14:30:00.123456Z",
            "total": "245.50",
            "sucursal": {"id": 77, "nombre": "Sucursal de prueba", "tipo": "sucursal"},
            "items": [{
                "id": 45001, "pedido_id": 12001,
                "producto": {
                    "id": 8, "nombre": "Producto de prueba",
                    "nombre_ticket": "PROD PRUEBA",
                    "unidad_medida": "PIEZA (PZA)",
                    "unidad_abreviatura": "PZA",
                    "cantidad_por_precio": "1.000",
                },
                "cantidad": "2.000", "precio_unitario": "122.75",
                "subtotal": "245.50",
            }],
        }

    def _baseline(self, *, bad_signature=False, missing_entry=False):
        prestate = exportar_preestado(self.sucursal)
        orders = canonical_json({"sender_id": 77, "order": self._order()}) + b"\n"
        recovered = b""
        tombstones = canonical_json([]) + b"\n"
        payload = {
            "type": "pedidos.recovery_manifest.v2",
            "key_id": KEY_ID,
            "recovery_id": RECOVERY_ID,
            "edge_id": EDGE_ID,
            "pos_branch_id": BRANCH_ID,
            "sender_ids": [77],
            "pos_prestate": prestate,
            "pos_prestate_sha256": sha256(canonical_json(prestate)),
            "desde": prestate["ventana_desde"],
            "hasta": prestate["ventana_hasta"],
            "purge_epoch": "0" * 64,
            "freeze_id": FREEZE_ID,
            "orders_sha256": sha256(orders),
            "orders_count": 1,
            "recovered_orders_sha256": sha256(recovered),
            "recovered_orders_count": 0,
            "tombstones_sha256": sha256(tombstones),
            "tombstones_count": 0,
            "next_desde": prestate["ventana_hasta"],
            "next_cursor": None,
        }
        manifest = _signed(payload, self.pedidos_private)
        if bad_signature:
            envelope = json.loads(manifest)
            envelope["signature_b64"] = _b64(b"\0" * 64)
            manifest = canonical_json(envelope) + b"\n"
        entries = {
            "manifest.json": manifest,
            "orders.jsonl": orders,
            "recovered_orders.jsonl": recovered,
            "tombstones.json": tombstones,
        }
        if missing_entry:
            entries.pop("orders.jsonl")
        snapshot = _zip(entries)
        path = self.runtime / "incoming-baseline.zip"
        path.write_bytes(snapshot)
        return path, sha256(snapshot), payload

    def _prepare(self, path, digest, *, edge_id=EDGE_ID):
        return preparar_recuperacion(
            self.sucursal,
            snapshot_path=path, snapshot_sha256=digest,
            pedidos_keys=self.pins, edge_id=edge_id,
            pos_branch_id=BRANCH_ID, sender_ids=SENDERS,
            edge_key_id=EDGE_KEY_ID,
            edge_private_key_path=self.edge_key_path,
            archivos_custodia={},
        )

    def _receipt(self, acta, manifest, *, corrupt=False):
        payload = {
            "type": "pedidos.recovery_receipt.v2",
            "key_id": KEY_ID,
            "recovery_id": RECOVERY_ID,
            "edge_id": EDGE_ID,
            "pos_branch_id": BRANCH_ID,
            "sender_ids": [77],
            "pos_prestate_sha256": manifest["pos_prestate_sha256"],
            "snapshot_sha256": acta.snapshot_sha256,
            "manifest_sha256": acta.manifest_sha256,
            "edge_ack_sha256": acta.ack_sha256,
            "status": "completada",
            "next_desde": manifest["hasta"],
            "next_cursor": None,
            "issued_at": "2026-09-26T12:00:00.000000Z",
        }
        content = _signed(payload, self.pedidos_private)
        if corrupt:
            envelope = json.loads(content)
            envelope["signature_b64"] = _b64(b"\0" * 64)
            content = canonical_json(envelope) + b"\n"
        path = self.runtime / "incoming-receipt.json"
        path.write_bytes(content)
        return path

    def test_410_import_ack_lost_replay_receipt_restart_cas(self):
        path, digest, manifest = self._baseline()
        acta = self._prepare(path, digest)
        self.state.refresh_from_db()
        self.assertEqual(self.state.estado, "reconciliacion")
        self.assertEqual(self.state.cursor, "cursor-dentro-ventana")
        imported = PedidoSucursalImportado.objects.get(
            sender_id=77, codigo_publico=self._order()["codigo_publico"]
        )
        self.assertEqual(imported.order_sha256, sha256(canonical_json(self._order())))

        ack_path = acuse_persistido(acta)
        exact_ack = ack_path.read_bytes()
        ack_path.unlink()  # pérdida de archivo local; DB conserva bytes y nonce
        replay = self._prepare(path, digest)
        self.assertEqual(replay.ack_nonce, acta.ack_nonce)
        self.assertEqual(acuse_persistido(replay).read_bytes(), exact_ack)
        self.assertEqual(PedidoSucursalImportado.objects.count(), 1)
        receipt = self._receipt(acta, manifest)
        self.assertTrue(aplicar_recibo(acta, receipt_path=receipt, pedidos_keys=self.pins))
        self.state.refresh_from_db()
        self.assertEqual(self.state.estado, "listo")
        self.assertEqual(self.state.cursor, "")
        self.assertEqual(self.state.ventana_desde, self.end)
        self.assertEqual(self.state.agua_alta_hasta, self.end)
        self.assertIsNone(self.state.ventana_hasta)
        self.assertEqual(self.state.ultimo_recovery_id, acta.id)
        self.assertTrue(aplicar_recibo(acta, receipt_path=receipt, pedidos_keys=self.pins))
        self.state.refresh_from_db()
        self.assertEqual(self.state.ventana_desde, self.end)

    def test_firma_baseline_invalida_no_importa_ni_avanza(self):
        path, digest, _ = self._baseline(bad_signature=True)
        with self.assertRaises(RecoveryV2Error):
            self._prepare(path, digest)
        self.state.refresh_from_db()
        self.assertEqual(self.state.cursor, "cursor-dentro-ventana")
        self.assertFalse(PedidoSucursalImportado.objects.exists())

    def test_edge_ajeno_rechazado_antes_de_importar(self):
        path, digest, _ = self._baseline()
        with self.assertRaises(RecuperacionLocalError):
            self._prepare(path, digest, edge_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        self.assertFalse(PedidoSucursalImportado.objects.exists())

    def test_baseline_parcial_rechazada_sin_ack(self):
        path, digest, _ = self._baseline(missing_entry=True)
        with self.assertRaises(RecoveryV2Error):
            self._prepare(path, digest)
        self.assertFalse(RecuperacionPedidosV2.objects.exists())
        self.assertFalse(PedidoSucursalImportado.objects.exists())

    def test_recibo_firma_invalida_conserva_checkpoint(self):
        path, digest, manifest = self._baseline()
        acta = self._prepare(path, digest)
        receipt = self._receipt(acta, manifest, corrupt=True)
        with self.assertRaises(RecoveryV2Error):
            aplicar_recibo(acta, receipt_path=receipt, pedidos_keys=self.pins)
        self.state.refresh_from_db()
        self.assertEqual(self.state.cursor, "cursor-dentro-ventana")
        self.assertEqual(self.state.estado, "reconciliacion")
