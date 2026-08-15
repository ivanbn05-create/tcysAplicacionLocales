from django.contrib import admin

from .models import Cliente, EventoOutbox, Mesa, ModificadorTicket, Partida, Ticket


class PartidaInline(admin.TabularInline):
    model = Partida
    extra = 0
    readonly_fields = ("precio_unitario", "nombre_producto", "nombre_corto", "creada_en")


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
admin.site.register(Cliente)
admin.site.register(ModificadorTicket)
