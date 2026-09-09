# Diagnóstico de calidad del POS local

Fecha de revisión: 23 de agosto de 2026.

> **Documento histórico.** Refleja el estado observado en esa fecha. Varias
> brechas aquí descritas se corrigieron posteriormente, por lo que no debe usarse
> como lista vigente de pendientes. Consultar `DESPLIEGUE_WINDOWS.md` y
> `ARQUITECTURA_DESPLIEGUE_Y_SINCRONIZACION_MULTISUCURSAL.md` para el estado
> actual.

Este diagnóstico complementa `propuesta_arquitectura_pos_multisucursal.md`. La prioridad actual sigue siendo estabilizar la operación local; no introduce todavía VPS, sincronización ni infraestructura distribuida.

## Mejoras aplicadas en esta entrega

- La apertura de una posición se serializa y la base de datos impide que existan dos tickets activos para la misma posición.
- El cambio Mesa/Llevar y Domicilio/Recoger mueve atómicamente el mismo ticket al primer lugar libre; no copia partidas ni crea un segundo folio.
- Recoger y Llevar tienen validaciones de identidad propias. Recoger no genera ticket total de información.
- La captura por nombres limita a cuatro productos principales, conserva cuatro columnas impresas y coloca consomés/bebidas al final asociados a la persona.
- Los datos del cliente se guardan como fotografía histórica del ticket y se limpian al convertir Recoger a Domicilio, evitando arrastrar contactos.
- Las promociones se asignan internamente por capacidad y su fila auxiliar no se imprime.
- La impresión está centralizada en `impresion/services.py`; la comanda, el ticket de cuenta y el ticket de domicilio comparten reglas verificables.
- La migración local se ejecutó después de comprobar que no había posiciones activas duplicadas y se creó una copia previa de SQLite en `backups/`.

## Riesgos prioritarios antes de operar varias terminales

### P0 — Autenticación y permisos

Las API operativas no exigen iniciar sesión. Cualquier equipo con acceso a la LAN y a la dirección del servidor puede abrir, modificar, cancelar, cobrar o reimprimir pedidos. Antes de una prueba real con usuarios se requieren sesiones, identidad de dispositivo y permisos separados para mesero, caja y supervisor.

### P0 — Servidor de desarrollo

El ejecutable local inicia `manage.py runserver`, con `DEBUG=true` y hosts abiertos. Es adecuado para esta fase de prueba, pero no para una sucursal en producción. La siguiente fase debe usar un servicio de Windows con Waitress u otro servidor WSGI soportado, `DEBUG=false`, secreto propio y lista de hosts de la LAN.

### P0 — Edición simultánea del mismo ticket

Ya no pueden abrirse dos tickets en la misma posición, pero dos dispositivos todavía pueden editar el mismo ticket activo al mismo tiempo. La solución objetivo es un lock con lease, `device_id`, heartbeat, token y versión de entidad, como define la propuesta arquitectónica.

### P1 — Base de datos y concurrencia

SQLite es suficiente para una sola máquina durante desarrollo. Varias tabletas escribiendo simultáneamente pueden producir esperas o `database is locked`. Antes del piloto multiusuario conviene mover el Edge local a PostgreSQL y añadir pruebas reales de concurrencia.

### P1 — Idempotencia

Los botones se bloquean durante una solicitud, pero la API no recibe una clave idempotente. Un reintento de red o un cliente distinto podría repetir ciertas operaciones. Procesar, cobrar, convertir e imprimir deben aceptar `operation_id` único y responder con el resultado ya registrado.

### P1 — Auditoría incompleta

Existe `EventoOutbox`, pero los eventos aún no registran usuario, dispositivo, motivo ni instantáneas antes/después. Cancelaciones, cobros, reimpresiones y conversiones deben poder atribuirse a una persona y terminal.

### P1 — Cola de impresión

Los trabajos quedan registrados y conservan el PNG cuando fallan, lo cual es una buena base. Falta un worker persistente con reintentos controlados, estado visible de cola y recuperación automática después de reiniciar el Edge.

### P2 — Respaldo y recuperación

La copia previa de esta migración fue manual. Debe automatizarse un respaldo local diario, retención, verificación de restauración y alerta por espacio en disco. Un respaldo no probado no debe considerarse recuperable.

### P2 — Salud y observabilidad

El indicador actual comprueba la impresora, pero faltan endpoints separados para aplicación, base, cola y disco; logs con `request_id`; y una pantalla sencilla de diagnósticos para soporte.

## Calidad de vida recomendada

- Mostrar qué usuario y dispositivo tienen tomada una mesa y cuánto falta para expirar el lock.
- Permitir filtrar posiciones ocupadas y saltar directamente a un folio.
- Mostrar una alerta persistente cuando una comanda no se imprimió, no sólo un mensaje temporal.
- Incorporar motivos obligatorios para cancelar y reimprimir.
- Añadir una vista de trabajos de impresión fallidos con reintento controlado.
- Mantener atajos de teclado para captura por nombres y avanzar automáticamente a la siguiente persona, como ya hace el campo con Enter.

## Puerta de salida para el piloto

No se recomienda un piloto de varias tabletas hasta completar, como mínimo: autenticación/permisos, servidor Windows de producción, PostgreSQL local, lock de edición, cola de impresión recuperable, backup automático y una prueba de caída de Internet/impresora/reinicio sin pérdida ni duplicados.
