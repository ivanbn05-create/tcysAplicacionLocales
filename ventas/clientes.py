from difflib import SequenceMatcher
from uuid import UUID

from django.db import transaction
from django.db.models import Prefetch, Q
from django.utils import timezone

from personas.models import Sucursal

from .models import Cliente, ConsecutivoCliente, DomicilioCliente, TelefonoCliente
from .normalizacion import normalizar_texto, normalizar_telefono


class ErrorCliente(ValueError):
    pass


MAX_TELEFONOS_POR_CLIENTE = 10
MAX_DOMICILIOS_POR_CLIENTE = 10
MAX_NOTAS_CLIENTE = 2000
MAX_REFERENCIA_DOMICILIO = 1000


def _id_opcional(value):
    if value in (None, ""):
        return None
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ErrorCliente("El identificador de un teléfono o domicilio no es válido.") from exc


def _lista_registros(datos, campo, limite):
    registros = datos.get(campo, [])
    if not isinstance(registros, list) or any(not isinstance(item, dict) for item in registros):
        raise ErrorCliente(f"La lista de {campo} no es válida.")
    if len(registros) > limite:
        raise ErrorCliente(f"Se permiten como máximo {limite} {campo} por cliente.")
    return registros


def telefono_payload(telefono):
    return {
        "id": str(telefono.id),
        "numero": telefono.numero,
        "etiqueta": telefono.etiqueta,
        "principal": telefono.principal,
    }


def domicilio_payload(domicilio):
    return {
        "id": str(domicilio.id),
        "etiqueta": domicilio.etiqueta,
        "calle": domicilio.calle,
        "numero_exterior": domicilio.numero_exterior,
        "numero_interior": domicilio.numero_interior,
        "colonia": domicilio.colonia,
        "codigo_postal": domicilio.codigo_postal,
        "municipio": domicilio.municipio,
        "referencia": domicilio.referencia,
        "texto": domicilio.texto_completo,
        "principal": domicilio.principal,
    }


def cliente_payload(cliente):
    telefonos = list(cliente.telefonos.filter(activo=True).order_by("-principal", "creado_en"))
    domicilios = list(cliente.domicilios.filter(activo=True).order_by("-principal", "creado_en"))
    return {
        "id": str(cliente.id),
        "clave_corta": cliente.clave_corta,
        "nombre": cliente.nombre,
        "notas": cliente.notas,
        "comentarios_multiples": cliente.comentarios_multiples,
        "telefonos": [telefono_payload(telefono) for telefono in telefonos],
        "domicilios": [domicilio_payload(domicilio) for domicilio in domicilios],
    }


def _clientes_con_datos(sucursal):
    return (
        Cliente.objects.filter(sucursal=sucursal, activo=True)
        .prefetch_related(
            Prefetch("telefonos", queryset=TelefonoCliente.objects.filter(activo=True).order_by("-principal", "creado_en")),
            Prefetch("domicilios", queryset=DomicilioCliente.objects.filter(activo=True).order_by("-principal", "creado_en")),
        )
        .order_by("-actualizado_en")
    )


def _resultado(cliente, telefono, domicilio, motivo, puntuacion):
    return {
        "cliente_id": str(cliente.id),
        "clave_corta": cliente.clave_corta,
        "nombre": cliente.nombre,
        "comentarios_multiples": cliente.comentarios_multiples,
        "telefono": telefono_payload(telefono) if telefono else None,
        "domicilio": domicilio_payload(domicilio) if domicilio else None,
        "motivo": motivo,
        "puntuacion": puntuacion,
    }


def buscar_clientes(sucursal, consulta, limite=10):
    consulta = str(consulta or "").strip()
    texto = normalizar_texto(consulta)
    digitos = normalizar_telefono(consulta)
    consulta_clientes = _clientes_con_datos(sucursal)
    if not consulta:
        clientes = list(consulta_clientes[: max(1, min(int(limite), 20))])
    else:
        filtros = Q()
        if texto:
            filtros |= Q(nombre_normalizado__contains=texto)
            filtros |= Q(domicilios__normalizado__contains=texto)
        if consulta:
            filtros |= Q(clave_corta__icontains=consulta)
        if digitos:
            filtros |= Q(telefonos__normalizado__contains=digitos)
            filtros |= Q(domicilios__numero_exterior__icontains=digitos)
        clientes = list(consulta_clientes.filter(filtros).distinct()[:200])
        # Conserva la tolerancia a nombres aproximados cuando no hubo una
        # coincidencia textual directa. El directorio local es suficientemente
        # pequeño para que este segundo paso siga siendo inmediato.
        if not clientes and texto:
            clientes = list(consulta_clientes[:2000])
    resultados = []

    for cliente in clientes:
        telefonos = list(cliente.telefonos.all())
        domicilios = list(cliente.domicilios.all())
        telefono_principal = telefonos[0] if telefonos else None
        domicilio_principal = domicilios[0] if domicilios else None

        if not consulta:
            resultados.append(_resultado(cliente, telefono_principal, domicilio_principal, "Cliente reciente", 10))
            continue

        puntuacion_cliente = 0
        motivo_cliente = ""
        if consulta == cliente.clave_corta:
            puntuacion_cliente, motivo_cliente = 120, f"Clave exacta {cliente.clave_corta}"
        elif texto == cliente.nombre_normalizado:
            puntuacion_cliente, motivo_cliente = 100, "Nombre exacto"
        elif texto and cliente.nombre_normalizado.startswith(texto):
            puntuacion_cliente, motivo_cliente = 82, "El nombre comienza igual"
        elif texto and texto in cliente.nombre_normalizado:
            puntuacion_cliente, motivo_cliente = 72, "Coincidencia en el nombre"
        elif texto:
            similitud = SequenceMatcher(None, texto, cliente.nombre_normalizado).ratio()
            if similitud >= 0.58:
                puntuacion_cliente, motivo_cliente = int(45 + similitud * 20), "Nombre similar"

        telefonos_coincidentes = []
        if digitos:
            for telefono in telefonos:
                if telefono.normalizado == digitos:
                    telefonos_coincidentes.append((telefono, 110, "Teléfono exacto"))
                elif digitos in telefono.normalizado or telefono.normalizado.endswith(digitos):
                    telefonos_coincidentes.append((telefono, 88, "Coincidencia en teléfono"))

        domicilios_coincidentes = []
        for domicilio in domicilios:
            if digitos and domicilio.numero_exterior == digitos:
                domicilios_coincidentes.append((domicilio, 105, f"Número exterior exacto: {digitos}"))
            elif texto and texto in domicilio.normalizado:
                domicilios_coincidentes.append((domicilio, 78, "Coincidencia en domicilio o referencia"))

        if domicilios_coincidentes:
            telefono = telefonos_coincidentes[0][0] if telefonos_coincidentes else telefono_principal
            for domicilio, puntuacion, motivo in domicilios_coincidentes:
                if puntuacion_cliente > puntuacion:
                    puntuacion, motivo = puntuacion_cliente, motivo_cliente
                resultados.append(_resultado(cliente, telefono, domicilio, motivo, puntuacion))
        elif telefonos_coincidentes:
            telefono, puntuacion, motivo = max(telefonos_coincidentes, key=lambda valor: valor[1])
            if puntuacion_cliente > puntuacion:
                puntuacion, motivo = puntuacion_cliente, motivo_cliente
            resultados.append(_resultado(cliente, telefono, domicilio_principal, motivo, puntuacion))
        elif puntuacion_cliente:
            resultados.append(
                _resultado(cliente, telefono_principal, domicilio_principal, motivo_cliente, puntuacion_cliente)
            )

    resultados.sort(key=lambda item: (-item["puntuacion"], item["nombre"].casefold(), item["clave_corta"]))
    return resultados[: max(1, min(int(limite), 20))]


def buscar_clientes_directorio(sucursal, consulta="", pagina=1, limite=30):
    """Lista el directorio local con búsqueda amplia y paginación estable.

    Esta consulta está separada de buscar_clientes porque la selección para un
    domicilio prioriza coincidencias y devuelve pocos resultados, mientras que
    el directorio debe poder recorrer todos los clientes de la sucursal.
    """

    consulta = str(consulta or "").strip()
    texto = normalizar_texto(consulta)
    digitos = normalizar_telefono(consulta)
    try:
        pagina = max(1, int(pagina))
    except (TypeError, ValueError):
        pagina = 1
    try:
        limite = max(1, min(int(limite), 50))
    except (TypeError, ValueError):
        limite = 30

    clientes = _clientes_con_datos(sucursal).order_by(
        "nombre_normalizado", "clave_corta", "id"
    )
    if consulta:
        filtros = Q(clave_corta__icontains=consulta)
        if texto:
            filtros |= Q(nombre_normalizado__contains=texto)
            filtros |= Q(domicilios__normalizado__contains=texto)
        filtros |= Q(notas__icontains=consulta)
        filtros |= Q(telefonos__numero__icontains=consulta)
        filtros |= Q(telefonos__etiqueta__icontains=consulta)
        filtros |= Q(domicilios__etiqueta__icontains=consulta)
        filtros |= Q(domicilios__calle__icontains=consulta)
        filtros |= Q(domicilios__numero_exterior__icontains=consulta)
        filtros |= Q(domicilios__numero_interior__icontains=consulta)
        filtros |= Q(domicilios__colonia__icontains=consulta)
        filtros |= Q(domicilios__codigo_postal__icontains=consulta)
        filtros |= Q(domicilios__municipio__icontains=consulta)
        filtros |= Q(domicilios__referencia__icontains=consulta)
        if digitos:
            filtros |= Q(telefonos__normalizado__contains=digitos)
        clientes = clientes.filter(filtros).distinct()

    total = clientes.count()
    inicio = (pagina - 1) * limite
    clientes_pagina = list(clientes[inicio : inicio + limite])
    resultados = []
    for cliente in clientes_pagina:
        telefonos = list(cliente.telefonos.all())
        domicilios = list(cliente.domicilios.all())
        resultado = _resultado(
            cliente,
            telefonos[0] if telefonos else None,
            domicilios[0] if domicilios else None,
            "Registro del directorio",
            0,
        )
        resultado["notas"] = cliente.notas
        resultado["telefonos"] = [telefono_payload(item) for item in telefonos]
        resultado["domicilios"] = [domicilio_payload(item) for item in domicilios]
        resultados.append(resultado)

    return {
        "resultados": resultados,
        "pagina": pagina,
        "total": total,
        "hay_mas": inicio + len(resultados) < total,
    }


def duplicados_por_nombre(sucursal, nombre, excluir=None):
    consulta = Cliente.objects.filter(
        sucursal=sucursal,
        activo=True,
        nombre_normalizado=normalizar_texto(nombre),
    )
    if excluir:
        consulta = consulta.exclude(pk=excluir)
    return [cliente_payload(cliente) for cliente in consulta.prefetch_related("telefonos", "domicilios")[:5]]


def _limpiar_telefono(datos):
    numero = str(datos.get("numero", "")).strip()[:30]
    normalizado = normalizar_telefono(numero)
    if not numero or len(normalizado) < 7:
        raise ErrorCliente("Cada teléfono debe contener al menos 7 dígitos.")
    return {
        "id": _id_opcional(datos.get("id")),
        "numero": numero,
        "normalizado": normalizado,
        "etiqueta": str(datos.get("etiqueta", "Principal")).strip()[:30] or "Principal",
    }


def _limpiar_domicilio(datos):
    calle = str(datos.get("calle", "")).strip()[:180]
    exterior = str(datos.get("numero_exterior", "")).strip()[:30]
    if not calle:
        raise ErrorCliente("Cada domicilio requiere una calle o ubicación.")
    referencia = str(datos.get("referencia", "")).strip()
    if len(referencia) > MAX_REFERENCIA_DOMICILIO:
        raise ErrorCliente(f"La referencia no puede superar {MAX_REFERENCIA_DOMICILIO} caracteres.")
    return {
        "id": _id_opcional(datos.get("id")),
        "etiqueta": str(datos.get("etiqueta", "Principal")).strip()[:30] or "Principal",
        "calle": calle,
        "numero_exterior": exterior,
        "numero_interior": str(datos.get("numero_interior", "")).strip()[:30],
        "colonia": str(datos.get("colonia", "")).strip()[:120],
        "codigo_postal": str(datos.get("codigo_postal", "")).strip()[:10],
        "municipio": str(datos.get("municipio", "")).strip()[:120],
        "referencia": referencia,
    }


def _siguiente_clave(sucursal):
    Sucursal.objects.select_for_update().get(pk=sucursal.pk)
    consecutivo, _ = ConsecutivoCliente.objects.select_for_update().get_or_create(
        sucursal=sucursal, defaults={"ultimo": 100000}
    )
    siguiente = consecutivo.ultimo + 1
    if siguiente > 999999:
        raise ErrorCliente("Se agotaron las claves cortas de esta sucursal.")
    consecutivo.ultimo = siguiente
    consecutivo.save(update_fields=["ultimo"])
    return f"{siguiente:06d}"


@transaction.atomic
def guardar_cliente(sucursal, datos, cliente=None):
    if not isinstance(datos, dict):
        raise ErrorCliente("Los datos del cliente no son válidos.")
    nombre = str(datos.get("nombre", "")).strip()[:180]
    if not nombre:
        raise ErrorCliente("El nombre del cliente es obligatorio.")
    telefonos_datos = _lista_registros(datos, "telefonos", MAX_TELEFONOS_POR_CLIENTE)
    domicilios_datos = _lista_registros(datos, "domicilios", MAX_DOMICILIOS_POR_CLIENTE)
    telefonos = [_limpiar_telefono(item) for item in telefonos_datos if item.get("numero")]
    domicilios = [
        _limpiar_domicilio(item)
        for item in domicilios_datos
        if item.get("calle") or item.get("numero_exterior")
    ]
    comentarios_multiples = datos.get("comentarios_multiples", False)
    if not isinstance(comentarios_multiples, bool):
        raise ErrorCliente("La opción de contactos múltiples no es válida.")
    notas = str(datos.get("notas", "")).strip()
    if len(notas) > MAX_NOTAS_CLIENTE:
        raise ErrorCliente(f"Las notas no pueden superar {MAX_NOTAS_CLIENTE} caracteres.")
    if cliente:
        cliente = Cliente.objects.select_for_update().get(pk=cliente.pk, sucursal=sucursal, activo=True)
        cliente.nombre = nombre
        cliente.notas = notas
        cliente.comentarios_multiples = comentarios_multiples
        cliente.version_entidad += 1
        cliente.save()
    else:
        cliente = Cliente.objects.create(
            sucursal=sucursal,
            clave_corta=_siguiente_clave(sucursal),
            nombre=nombre,
            notas=notas,
            comentarios_multiples=comentarios_multiples,
        )

    cliente.telefonos.update(activo=False, principal=False, actualizado_en=timezone.now())
    for indice, item in enumerate(telefonos):
        telefono = None
        if item["id"]:
            telefono = cliente.telefonos.filter(pk=item["id"]).first()
        if not telefono:
            telefono = cliente.telefonos.filter(normalizado=item["normalizado"]).first()
        if not telefono:
            telefono = TelefonoCliente(sucursal=sucursal, cliente=cliente)
        telefono.numero = item["numero"]
        telefono.normalizado = item["normalizado"]
        telefono.etiqueta = item["etiqueta"]
        telefono.principal = indice == 0
        telefono.activo = True
        telefono.save()

    cliente.domicilios.update(activo=False, principal=False, actualizado_en=timezone.now())
    for indice, item in enumerate(domicilios):
        domicilio = cliente.domicilios.filter(pk=item["id"]).first() if item["id"] else None
        if not domicilio:
            normalizado = normalizar_texto(
                " ".join(
                    item[campo]
                    for campo in [
                        "calle",
                        "numero_exterior",
                        "numero_interior",
                        "colonia",
                        "codigo_postal",
                        "municipio",
                        "referencia",
                    ]
                )
            )
            domicilio = cliente.domicilios.filter(normalizado=normalizado).first()
        if not domicilio:
            domicilio = DomicilioCliente(sucursal=sucursal, cliente=cliente)
        for campo in [
            "etiqueta",
            "calle",
            "numero_exterior",
            "numero_interior",
            "colonia",
            "codigo_postal",
            "municipio",
            "referencia",
        ]:
            setattr(domicilio, campo, item[campo])
        domicilio.principal = indice == 0
        domicilio.activo = True
        domicilio.save()

    cliente.refresh_from_db()
    from .sincronizacion_central import encolar_cliente_central

    encolar_cliente_central(cliente)
    return cliente
