from io import BytesIO
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse, HttpResponseNotModified
from django.utils.cache import patch_cache_control
from django.utils.http import quote_etag

from catalogo.models import validar_imagen_producto


TAMANO_MINIATURA = (320, 240)


def respuesta_miniatura_producto(request, producto):
    """Entrega una miniatura WebP segura sin publicar MEDIA_ROOT en producción."""

    if not producto.imagen:
        raise Http404("El producto no tiene imagen.")

    revision = int(producto.actualizado_en.timestamp())
    etag = quote_etag(f"producto-{producto.pk}-{revision}")
    clave_cache = f"producto-miniatura:{producto.pk}:{revision}"
    contenido = cache.get(clave_cache)
    try:
        with producto.imagen.open("rb") as archivo:
            validar_imagen_producto(archivo)
            if contenido is None:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", Image.DecompressionBombWarning)
                    archivo.seek(0)
                    with Image.open(archivo) as original:
                        imagen = ImageOps.exif_transpose(original)
                        if imagen.mode in {"RGBA", "LA"}:
                            fondo = Image.new("RGB", imagen.size, "white")
                            alfa = imagen.getchannel("A")
                            fondo.paste(imagen.convert("RGB"), mask=alfa)
                            imagen = fondo
                        else:
                            imagen = imagen.convert("RGB")
                        imagen = ImageOps.fit(
                            imagen,
                            TAMANO_MINIATURA,
                            method=Image.Resampling.LANCZOS,
                            centering=(0.5, 0.5),
                        )
                        salida = BytesIO()
                        imagen.save(salida, format="WEBP", quality=82, method=6)
                        contenido = salida.getvalue()
    except (
        FileNotFoundError,
        OSError,
        SyntaxError,
        TypeError,
        ValueError,
        ValidationError,
        UnidentifiedImageError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ) as exc:
        raise Http404("La imagen del producto no está disponible.") from exc

    if request.headers.get("If-None-Match") == etag:
        response = HttpResponseNotModified()
        response["ETag"] = etag
        return response

    if cache.get(clave_cache) is None:
        cache.set(clave_cache, contenido, 24 * 60 * 60)

    response = HttpResponse(contenido, content_type="image/webp")
    response["Content-Disposition"] = f'inline; filename="producto-{producto.pk}.webp"'
    response["ETag"] = etag
    response["X-Content-Type-Options"] = "nosniff"
    patch_cache_control(response, private=True, max_age=86400, immutable=True)
    return response
