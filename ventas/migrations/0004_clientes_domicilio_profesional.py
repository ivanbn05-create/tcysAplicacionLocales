import re
import unicodedata
import uuid

import django.db.models.deletion
from django.db import migrations, models


def _normalizar(valor):
    texto = unicodedata.normalize("NFKD", str(valor or ""))
    texto = "".join(caracter for caracter in texto if not unicodedata.combining(caracter))
    texto = re.sub(r"[^a-z0-9]+", " ", texto.casefold())
    return " ".join(texto.split())


def _telefono(valor):
    digitos = re.sub(r"\D", "", str(valor or ""))
    if len(digitos) > 10 and digitos.startswith("52"):
        digitos = digitos[-10:]
    return digitos[-10:]


def migrar_clientes(apps, schema_editor):
    Cliente = apps.get_model("ventas", "Cliente")
    Telefono = apps.get_model("ventas", "TelefonoCliente")
    Domicilio = apps.get_model("ventas", "DomicilioCliente")
    Ticket = apps.get_model("ventas", "Ticket")

    consecutivos = {}
    for cliente in Cliente.objects.order_by("sucursal_id", "creado_en", "id"):
        consecutivo = consecutivos.get(cliente.sucursal_id, 100000) + 1
        consecutivos[cliente.sucursal_id] = consecutivo
        cliente.clave_corta = f"{consecutivo:06d}"
        cliente.nombre_normalizado = _normalizar(cliente.nombre)
        cliente.save(update_fields=["clave_corta", "nombre_normalizado"])

        telefono = None
        if cliente.telefono.strip():
            telefono = Telefono.objects.create(
                id=uuid.uuid4(),
                sucursal_id=cliente.sucursal_id,
                cliente_id=cliente.id,
                numero=cliente.telefono.strip(),
                normalizado=_telefono(cliente.telefono),
                etiqueta="Principal",
                principal=True,
            )

        domicilio = None
        domicilio_legado = cliente.domicilio.strip()
        if domicilio_legado:
            coincidencia = re.match(r"^(.*?)(?:\s+#?(\d+[A-Za-z-]*))$", domicilio_legado)
            calle = coincidencia.group(1).strip(" ,#") if coincidencia else domicilio_legado
            exterior = coincidencia.group(2) if coincidencia else ""
            texto_normalizado = _normalizar(
                " ".join([domicilio_legado, cliente.colonia, cliente.referencia])
            )
            domicilio = Domicilio.objects.create(
                id=uuid.uuid4(),
                sucursal_id=cliente.sucursal_id,
                cliente_id=cliente.id,
                etiqueta="Principal",
                calle=calle,
                numero_exterior=exterior,
                colonia=cliente.colonia,
                referencia=cliente.referencia,
                normalizado=texto_normalizado,
                principal=True,
            )

        actualizaciones = {
            "cliente_nombre": cliente.nombre,
            "cliente_telefono": telefono.numero if telefono else cliente.telefono,
            "cliente_domicilio": domicilio_legado,
            "cliente_referencia": cliente.referencia,
            "telefono_cliente_id": telefono.id if telefono else None,
            "domicilio_cliente_id": domicilio.id if domicilio else None,
        }
        Ticket.objects.filter(cliente_id=cliente.id).update(**actualizaciones)


def revertir_clientes(apps, schema_editor):
    Cliente = apps.get_model("ventas", "Cliente")
    for cliente in Cliente.objects.all():
        telefono = cliente.telefonos.filter(activo=True).order_by("-principal", "creado_en").first()
        domicilio = cliente.domicilios.filter(activo=True).order_by("-principal", "creado_en").first()
        cliente.telefono = telefono.numero if telefono else ""
        cliente.domicilio = (
            f"{domicilio.calle} {domicilio.numero_exterior}".strip() if domicilio else ""
        )
        cliente.colonia = domicilio.colonia if domicilio else ""
        cliente.referencia = domicilio.referencia if domicilio else ""
        cliente.save(update_fields=["telefono", "domicilio", "colonia", "referencia"])


class Migration(migrations.Migration):

    dependencies = [
        ("ventas", "0003_partida_termino"),
    ]

    operations = [
        migrations.AddField(
            model_name="cliente",
            name="actualizado_en",
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AddField(
            model_name="cliente",
            name="clave_corta",
            field=models.CharField(blank=True, max_length=6),
        ),
        migrations.AddField(
            model_name="cliente",
            name="nombre_normalizado",
            field=models.CharField(blank=True, db_index=True, max_length=180),
        ),
        migrations.AddField(
            model_name="cliente",
            name="notas",
            field=models.TextField(blank=True),
        ),
        migrations.AlterModelOptions(
            name="cliente",
            options={"ordering": ["nombre", "clave_corta"]},
        ),
        migrations.CreateModel(
            name="TelefonoCliente",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("numero", models.CharField(max_length=30)),
                ("normalizado", models.CharField(db_index=True, max_length=15)),
                ("etiqueta", models.CharField(default="Principal", max_length=30)),
                ("principal", models.BooleanField(default=False)),
                ("activo", models.BooleanField(default=True)),
                ("creado_en", models.DateTimeField(auto_now_add=True)),
                ("cliente", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="telefonos", to="ventas.cliente")),
                ("sucursal", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="telefonos_cliente", to="personas.sucursal")),
            ],
            options={"ordering": ["-principal", "creado_en"]},
        ),
        migrations.CreateModel(
            name="DomicilioCliente",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("etiqueta", models.CharField(default="Principal", max_length=30)),
                ("calle", models.CharField(max_length=180)),
                ("numero_exterior", models.CharField(max_length=30)),
                ("numero_interior", models.CharField(blank=True, max_length=30)),
                ("colonia", models.CharField(blank=True, max_length=120)),
                ("codigo_postal", models.CharField(blank=True, max_length=10)),
                ("municipio", models.CharField(blank=True, max_length=120)),
                ("referencia", models.TextField(blank=True)),
                ("normalizado", models.TextField(blank=True)),
                ("principal", models.BooleanField(default=False)),
                ("activo", models.BooleanField(default=True)),
                ("creado_en", models.DateTimeField(auto_now_add=True)),
                ("cliente", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="domicilios", to="ventas.cliente")),
                ("sucursal", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="domicilios_cliente", to="personas.sucursal")),
            ],
            options={"ordering": ["-principal", "creado_en"]},
        ),
        migrations.AddField(
            model_name="ticket",
            name="cliente_domicilio",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="ticket",
            name="cliente_nombre",
            field=models.CharField(blank=True, max_length=180),
        ),
        migrations.AddField(
            model_name="ticket",
            name="cliente_referencia",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="ticket",
            name="cliente_telefono",
            field=models.CharField(blank=True, max_length=30),
        ),
        migrations.AddField(
            model_name="ticket",
            name="domicilio_cliente",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="tickets", to="ventas.domiciliocliente"),
        ),
        migrations.AddField(
            model_name="ticket",
            name="telefono_cliente",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="tickets", to="ventas.telefonocliente"),
        ),
        migrations.AddConstraint(
            model_name="telefonocliente",
            constraint=models.UniqueConstraint(fields=("cliente", "normalizado"), name="cliente_telefono_unico"),
        ),
        migrations.AddIndex(
            model_name="telefonocliente",
            index=models.Index(fields=["sucursal", "normalizado"], name="ventas_tel_suc_norm_idx"),
        ),
        migrations.AddIndex(
            model_name="domiciliocliente",
            index=models.Index(fields=["sucursal", "numero_exterior"], name="ventas_dom_suc_num_idx"),
        ),
        migrations.RunPython(migrar_clientes, revertir_clientes),
        migrations.AlterField(
            model_name="cliente",
            name="clave_corta",
            field=models.CharField(max_length=6),
        ),
        migrations.AddConstraint(
            model_name="cliente",
            constraint=models.UniqueConstraint(fields=("sucursal", "clave_corta"), name="cliente_clave_sucursal"),
        ),
        migrations.RemoveField(model_name="cliente", name="telefono"),
        migrations.RemoveField(model_name="cliente", name="domicilio"),
        migrations.RemoveField(model_name="cliente", name="colonia"),
        migrations.RemoveField(model_name="cliente", name="referencia"),
    ]
