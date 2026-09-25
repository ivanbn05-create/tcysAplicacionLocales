from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

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


class Command(BaseCommand):
    help = "Descarga y aplica una publicacion candidata de catalogo cuando el flag esta activo."

    def handle(self, *args, **options):
        if not settings.CENTRAL_ENABLE_CATALOG_DISTRIBUTION_V2:
            self.stdout.write(
                self.style.WARNING(
                    "Distribucion Central de catalogo apagada; no se realizo ninguna solicitud."
                )
            )
            return
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
                ruta="/api/v2/edge/catalogo/publicaciones/actual/",
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
        try:
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
            encolar_ack_catalogo_rechazado(sucursal, respuesta.datos, exc)
            raise CommandError(
                f"Publicacion rechazada de forma segura: {exc.codigo}."
            ) from exc
        estado = "aplicada" if creada else "ya aplicada"
        self.stdout.write(
            self.style.SUCCESS(
                f"Publicacion {publicacion.publicacion_id} version {publicacion.version} {estado}."
            )
        )
