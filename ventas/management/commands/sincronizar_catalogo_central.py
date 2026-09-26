from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from catalogo.models import PublicacionCatalogoCentral
from personas.models import Sucursal
from ventas.aprovisionamiento import marcar_esperando_catalogo
from ventas.catalogo_central import (
    ErrorCatalogoCentral,
    aplicar_publicacion_catalogo,
    encolar_ack_catalogo_rechazado,
    validar_publicacion_catalogo,
)
from ventas.central_api import (
    ClienteCentral,
    ErrorCentral,
)


MAX_CADENA_CATCHUP_V3 = 64


class Command(BaseCommand):
    help = "Descarga y aplica una publicacion candidata de catalogo cuando el flag esta activo."

    @staticmethod
    def _rechazar_v3(sucursal, datos, error):
        encolar_ack_catalogo_rechazado(
            sucursal, datos, error, version_contrato=3
        )
        raise CommandError(
            f"Publicacion rechazada de forma segura: {error.codigo}."
        ) from error

    @staticmethod
    def _descargar_cadena_v3(sucursal, cliente, actual, ultima):
        """Prevalida una cadena v3 acotada antes de escribir en SQLite."""

        base_version = ultima.version if ultima is not None else 0
        cadena = [actual]
        vistos = {actual["publicacion_id"]}
        while cadena[-1]["version_sucursal"] > base_version + 1:
            if len(cadena) >= MAX_CADENA_CATCHUP_V3:
                raise CommandError(
                    "La recuperacion de catalogo supera el limite de publicaciones; "
                    "requiere conciliacion asistida."
                )
            siguiente = cadena[-1]
            anterior_id = siguiente["publicacion_anterior_id"]
            if anterior_id is None or anterior_id in vistos:
                raise CommandError(
                    "La cadena historica de catalogo v3 contiene un ciclo o predecesor ausente."
                )
            try:
                respuesta = cliente.solicitar(
                    metodo="GET",
                    ruta=f"/api/v3/edge/catalogo/publicaciones/{anterior_id}/",
                )
            except ErrorCentral as exc:
                raise CommandError(
                    "No fue posible descargar el historial de catalogo v3; "
                    "se conserva la ultima version valida."
                ) from exc
            if respuesta.status != 200:
                raise CommandError(
                    f"Historial de catalogo v3 no disponible (HTTP {respuesta.status}); "
                    "se conserva la ultima version valida."
                )
            try:
                if (
                    type(respuesta.datos) is not dict
                    or respuesta.datos.get("version_contrato") != 3
                ):
                    raise ErrorCatalogoCentral(
                        "El historial no corresponde al contrato v3.",
                        codigo="schema_no_soportado",
                    )
                anterior, *_ = validar_publicacion_catalogo(respuesta.datos, sucursal)
            except ErrorCatalogoCentral as exc:
                raise CommandError(
                    f"Historial de catalogo v3 invalido: {exc.codigo}; "
                    "se conserva la ultima version valida."
                ) from exc
            if (
                anterior["publicacion_id"] != anterior_id
                or anterior["version_sucursal"] != siguiente["version_sucursal"] - 1
            ):
                raise CommandError(
                    "El historial de catalogo v3 no coincide con la cadena publicada."
                )
            vistos.add(anterior_id)
            cadena.append(anterior)

        primera = cadena[-1]
        if ultima is not None:
            if primera["publicacion_anterior_id"] != str(ultima.publicacion_id):
                raise CommandError(
                    "El historial de catalogo v3 no enlaza con la ultima version local."
                )
        elif primera["version_sucursal"] != 1 or primera["publicacion_anterior_id"] is not None:
            raise CommandError(
                "El historial de catalogo v3 no llega a la primera publicacion."
            )
        return list(reversed(cadena))

    def _sincronizar_v3(self, sucursal, cliente, datos):
        try:
            if type(datos) is not dict or datos.get("version_contrato") != 3:
                raise ErrorCatalogoCentral(
                    "La respuesta no coincide con el contrato solicitado.",
                    codigo="schema_no_soportado",
                )
            actual, *_ = validar_publicacion_catalogo(datos, sucursal)
        except ErrorCatalogoCentral as exc:
            self._rechazar_v3(sucursal, datos, exc)

        ultima = (
            PublicacionCatalogoCentral.objects.filter(
                sucursal=sucursal,
                estado=PublicacionCatalogoCentral.Estado.APLICADA,
                version_contrato=3,
            )
            .order_by("-version")
            .first()
        )
        if ultima is not None and actual["version_sucursal"] < ultima.version:
            raise CommandError(
                "El Central presento una publicacion v3 obsoleta; "
                "se conserva la ultima version local sin emitir otro ACK."
            )
        if ultima is not None and actual["version_sucursal"] == ultima.version:
            try:
                publicacion, creada = aplicar_publicacion_catalogo(sucursal, actual)
            except ErrorCatalogoCentral as exc:
                self._rechazar_v3(sucursal, actual, exc)
            estado = "aplicada" if creada else "ya aplicada"
            self.stdout.write(self.style.SUCCESS(
                f"Publicacion {publicacion.publicacion_id} version {publicacion.version} {estado}."
            ))
            return

        cadena = self._descargar_cadena_v3(sucursal, cliente, actual, ultima)
        hoy = timezone.localdate()
        vigentes = []
        for publicacion in cadena:
            if date.fromisoformat(publicacion["aplicar_desde"]) > hoy:
                break
            vigentes.append(publicacion)
        if not vigentes:
            self.stdout.write(self.style.WARNING(
                "La publicacion aun no entra en vigor; se conserva la ultima version valida."
            ))
            return

        en_aplicacion = None
        try:
            # Las descargas ocurren antes del lock; todo el catch-up se confirma o revierte.
            with transaction.atomic():
                for en_aplicacion in vigentes:
                    aplicada, _creada = aplicar_publicacion_catalogo(
                        sucursal, en_aplicacion
                    )
        except ErrorCatalogoCentral as exc:
            self._rechazar_v3(sucursal, en_aplicacion, exc)
        self.stdout.write(self.style.SUCCESS(
            f"Catalogo v3 recuperado hasta publicacion {aplicada.publicacion_id} "
            f"version {aplicada.version} ({len(vigentes)} publicaciones aplicadas)."
        ))
        if len(vigentes) < len(cadena):
            self.stdout.write(self.style.WARNING(
                "Hay una publicacion posterior pendiente de vigencia; "
                "se conserva el ultimo menu valido."
            ))

    def handle(self, *args, **options):
        if not (settings.CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V2
                or settings.CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V3):
            self.stdout.write(
                self.style.WARNING(
                    "Distribucion Central de catalogo apagada; no se realizo ninguna solicitud."
                )
            )
            return
        contrato = 3 if settings.CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V3 else 2
        try:
            sucursal = Sucursal.objects.get(
                clave=settings.SUCURSAL_CLAVE,
                activa=True,
            )
        except Sucursal.DoesNotExist as exc:
            raise CommandError("La sucursal local no esta aprovisionada.") from exc
        if (
            str(sucursal.id) != settings.CENTRAL_BRANCH_ID
            or sucursal.clave != settings.CENTRAL_BRANCH_CODE
        ):
            raise CommandError("La identidad local no coincide con CENTRAL_BRANCH_ID/CODE.")
        # 204, 304 y fallos de red no equivalen a un catálogo inicial aplicado.
        # Una instalación nueva conserva esta espera en SQLite tras reiniciar.
        marcar_esperando_catalogo(sucursal)
        cliente = ClienteCentral(
            base_url=settings.CENTRAL_API_BASE_URL,
            token=settings.CENTRAL_CATALOG_TOKEN,
            ca_bundle=settings.CENTRAL_API_CA_BUNDLE or None,
            timeout=max(
                settings.CENTRAL_CONNECT_TIMEOUT_SECONDS,
                settings.CENTRAL_READ_TIMEOUT_SECONDS,
            ),
            max_response_bytes=settings.CENTRAL_MAX_RESPONSE_BYTES,
        )
        try:
            respuesta = cliente.solicitar(
                metodo="GET",
                ruta=f"/api/v{contrato}/edge/catalogo/publicaciones/actual/",
            )
        except ErrorCentral as exc:
            raise CommandError(str(exc)) from exc
        if respuesta.status in {204, 304}:
            self.stdout.write(
                self.style.WARNING(
                    "No hay una publicacion de catalogo aplicable; se conserva la ultima version valida."
                )
            )
            return
        if respuesta.status == 404:
            raise CommandError(
                "La ruta candidata de catalogo no esta activa en el Central."
            )
        if respuesta.status != 200:
            raise CommandError(
                f"El Central rechazo la descarga de catalogo (HTTP {respuesta.status})."
            )
        if contrato == 3:
            return self._sincronizar_v3(sucursal, cliente, respuesta.datos)
        try:
            if respuesta.datos.get("version_contrato") != contrato:
                raise ErrorCatalogoCentral(
                    "La respuesta no coincide con el contrato solicitado.",
                    codigo="schema_no_soportado",
                )
            raiz, *_identidades = validar_publicacion_catalogo(
                respuesta.datos,
                sucursal,
            )
            if date.fromisoformat(raiz["aplicar_desde"]) > timezone.localdate():
                self.stdout.write(
                    self.style.WARNING(
                        "La publicacion aun no entra en vigor; se conserva la ultima version valida."
                    )
                )
                return
            publicacion, creada = aplicar_publicacion_catalogo(
                sucursal,
                respuesta.datos,
            )
        except ErrorCatalogoCentral as exc:
            encolar_ack_catalogo_rechazado(
                sucursal, respuesta.datos, exc, version_contrato=contrato
            )
            raise CommandError(
                f"Publicacion rechazada de forma segura: {exc.codigo}."
            ) from exc
        estado = "aplicada" if creada else "ya aplicada"
        self.stdout.write(
            self.style.SUCCESS(
                f"Publicacion {publicacion.publicacion_id} version {publicacion.version} {estado}."
            )
        )
