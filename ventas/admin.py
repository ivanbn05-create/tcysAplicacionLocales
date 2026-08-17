from django.contrib import admin

from .models import Cliente, DomicilioCliente, EventoOutbox, Mesa, ModificadorTicket, Partida, TelefonoCliente, Ticket


class PartidaInline(admin.TabularInline):
    model = Partida
    extra = 0
    readonly_fields = ("precio_unitario", "nombre_producto", "nombre_corto", "creada_en")


class TelefonoClienteInline(admin.TabularInline):
    model = TelefonoCliente
    extra = 0


class DomicilioClienteInline(admin.StackedInline):
    model = DomicilioCliente
    extra = 0


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ("clave_corta", "nombre", "comentarios_multiples", "activo", "actualizado_en")
    search_fields = ("clave_corta", "nombre", "telefonos__normalizado", "domicilios__normalizado")
    list_filter = ("sucursal", "comentarios_multiples", "activo")
    inlines = [TelefonoClienteInline, DomicilioClienteInline]


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ("folio", "canal", "mesa", "estado", "creado_en", "pagado_en")
    list_filter = ("sucursal", "canal", "estado", "forma_pago")
    search_fields = ("folio", "mesa__nombre", "cliente__nombre")
    inlines = [PartidaInline]


@admin.register(EventoOutbox)
class EventoOutboxAdmin(admin.ModelAdmin):
    list_display = ("tipo", "agregado_id", "creado_en", "publicado_en", "intentos")
    list_filter = ("tipo", "publicado_en", "sucursal")
    readonly_fields = ("id", "creado_en")


admin.site.register(Mesa)
admin.site.register(ModificadorTicket)
