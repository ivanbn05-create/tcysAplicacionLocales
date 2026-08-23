# Propuesta de arquitectura técnica para POS multi-sucursal

## 1. Propósito de este documento

Este documento sirve como contexto técnico persistente para un agente de código que continúe el desarrollo de la aplicación aunque no tenga acceso a conversaciones anteriores.

La aplicación corresponde a un restaurante con múltiples sucursales. Actualmente el sistema está siendo desarrollado como una aplicación web con Django, originalmente pensada como PWA. La estrategia acordada es **no intentar distribuir ni desplegar toda la arquitectura desde el inicio**.

La prioridad inmediata es:

1. Hacer que la aplicación funcione localmente de forma correcta y estable.
2. Pulir el flujo de pedidos, mesas, usuarios, impresión, permisos, precios y lógica de negocio.
3. Mantener el código preparado para poder separar posteriormente responsabilidades.
4. Solo cuando el funcionamiento local esté maduro, evolucionar hacia:
   - aplicación de escritorio `.exe` para Windows;
   - aplicación `.apk` para Android;
   - Edge Server por sucursal;
   - VPS central;
   - sincronización offline/online;
   - administración centralizada multi-sucursal.

El objetivo final no es simplemente "subir Django a Internet", sino construir un sistema POS distribuido que pueda seguir operando dentro de cada sucursal aun cuando se pierda la conexión a Internet.

---

# 2. Contexto operativo

El negocio actualmente cuenta con:

- 4 sucursales activas.
- 2 computadoras por sucursal.
- 3 tablets Android por sucursal.
- Aproximadamente 5 terminales por sucursal.
- Se prevé abrir al menos 2 sucursales adicionales con la misma cantidad de equipos.

Escenario esperado:

- 6 sucursales.
- 12 computadoras.
- 18 tablets.
- 30 dispositivos cliente aproximadamente.

Carga estimada en hora pico:

- 3 sucursales: aproximadamente 30 pedidos por hora cada una.
- 3 sucursales: aproximadamente 20 pedidos por hora cada una.
- Pico total aproximado: 150 pedidos por hora.
- Equivalente promedio: 2.5 pedidos por minuto entre todas las sucursales.

La carga transaccional es baja para PostgreSQL/Django modernos. El reto principal no será la capacidad de CPU sino:

- disponibilidad;
- sincronización;
- prevención de duplicados;
- impresión confiable;
- concurrencia;
- tolerancia a fallos;
- operación sin Internet;
- seguridad de dispositivos;
- consistencia de datos.

---

# 3. Estado actual del negocio

Actualmente cada sucursal utiliza una computadora central que:

- ejecuta la aplicación;
- procesa las órdenes;
- genera comandas;
- envía trabajos a impresoras térmicas;
- sirve como punto central para tablets conectadas por escritorio remoto.

Las tablets Android actualmente dependen de la computadora central mediante escritorio remoto.

Esto genera un punto único de falla:

```text
PC central
   |
   +-- lógica POS
   +-- impresión
   +-- almacenamiento/procesamiento
   +-- escritorio remoto
   |
   +-- tablets dependientes

Si la PC falla, la operación completa puede detenerse.
```

Las impresoras actuales trabajan mediante:

- protocolo ESC/POS;
- conexión TCP/IP;
- red LAN.

Esto es favorable para la nueva arquitectura porque permite impresión directa en red sin necesitar USB ni redirección de impresora mediante RDP.

---

# 4. Decisión arquitectónica principal

No se recomienda basar el sistema final en VDI, Windows 365 o sesiones RDP remotas como arquitectura principal.

La arquitectura objetivo será:

```text
                   VPS CENTRAL
                 Hostinger / Cloud
              +----------------------+
              | Django API           |
              | PostgreSQL central   |
              | Redis opcional       |
              | Celery opcional      |
              | Panel administrador  |
              | Reportes globales    |
              | Configuración        |
              +----------+-----------+
                         |
                    HTTPS / WSS
                         |
       +-----------------+------------------+
       |                 |                  |
       v                 v                  v
   Sucursal 1        Sucursal 2         Sucursal N
       |                 |                  |
       v                 v                  v
   Edge Server        Edge Server        Edge Server
       |                 |                  |
   LAN local           LAN local          LAN local
       |                 |                  |
 +-----+------+      +----+-----+       +----+-----+
 |     |      |      |    |     |       |    |     |
 PC  Tablets Printer PC Tablets Printer  PC Tablets Printer
```

La regla conceptual importante es:

> Centralizar datos no significa centralizar toda la ejecución.

El VPS será la autoridad global para configuración y consolidación, pero cada sucursal debe poder operar localmente aun sin Internet.

---

# 5. Fases de desarrollo

## Fase 1 — Aplicación local estable

Esta es la fase actual.

Objetivo:

- perfeccionar toda la lógica de negocio;
- validar flujos;
- eliminar errores funcionales;
- establecer modelos de datos sólidos;
- diseñar servicios desacoplados;
- preparar el código para una futura separación cliente/servidor.

En esta etapa no es necesario:

- desplegar VPS;
- implementar sincronización distribuida completa;
- crear Edge Server definitivo;
- empaquetar `.exe`;
- generar `.apk`;
- resolver alta disponibilidad multi-sucursal.

Sí conviene desde ahora diseñar interfaces y servicios que no dependan directamente de que todo viva en el mismo proceso.

---

## Fase 2 — Separación API / cliente

La aplicación Django debe evolucionar hacia una arquitectura donde la lógica central pueda exponerse mediante API.

Objetivo conceptual:

```text
Frontend / Cliente
       |
       v
Django REST API
       |
       v
Servicios de dominio
       |
       v
Persistencia
```

Debe evitarse que la lógica de negocio importante quede exclusivamente incrustada en:

- templates;
- JavaScript de UI;
- vistas monolíticas;
- señales difíciles de rastrear;
- código directamente ligado al navegador.

La lógica crítica debe quedar en servicios reutilizables.

Ejemplos:

- crear pedido;
- agregar producto;
- bloquear mesa;
- liberar mesa;
- cancelar producto;
- cerrar cuenta;
- registrar pago;
- generar comanda;
- generar ticket;
- sincronizar catálogo;
- auditar cambios.

---

## Fase 3 — Aplicaciones cliente

Posteriormente se generarán:

### Windows

Aplicación `.exe` para terminales de escritorio.

Puede construirse con tecnologías como:

- Electron;
- Tauri;
- wrapper web;
- otra estrategia compatible con el frontend existente.

La elección debe evaluarse según:

- consumo de RAM;
- facilidad de actualización;
- integración con sistema operativo;
- persistencia local;
- compatibilidad con red.

### Android

Aplicación `.apk`.

Una estrategia razonable es reutilizar la PWA mediante Capacitor u otra capa híbrida, siempre que permita:

- almacenamiento local robusto;
- conectividad LAN;
- API nativa cuando sea necesario;
- control de ciclo de vida;
- actualizaciones;
- autenticación de dispositivo.

---

# 6. Edge Server por sucursal

Cada sucursal tendrá un Edge Server.

Inicialmente puede ser una computadora Windows ya existente.

No requiere hardware especializado.

Ejemplo:

```text
PC Caja 1
   |
   +-- Cliente POS
   |
   +-- Servicio Edge
       |
       +-- API local
       +-- base de datos local
       +-- print worker
       +-- sync worker
       +-- lock manager
       +-- health monitor
```

En una fase posterior puede migrarse a una mini PC dedicada con Linux.

Hardware sugerido mínimo razonable:

- 4 cores;
- 8 GB RAM;
- SSD de 128 GB o más;
- Ethernet preferentemente;
- UPS recomendado.

La carga real esperada es muy baja.

---

# 7. Rol del Edge Server

El Edge será la autoridad operativa de una sucursal.

Debe encargarse de:

- pedidos;
- mesas;
- cuentas abiertas;
- locks;
- impresión;
- almacenamiento temporal;
- operación offline;
- cola de eventos;
- sincronización con VPS;
- recepción de catálogo;
- configuración de impresoras;
- auditoría local;
- estado de dispositivos.

El cliente no debería necesitar hablar directamente con el VPS para realizar operaciones normales dentro del restaurante.

Flujo habitual:

```text
Tablet / PC
    |
    v
Edge local
    |
    +-- PostgreSQL local
    +-- impresoras ESC/POS
    +-- cola de sincronización
    |
    v
VPS central
```

---

# 8. Base de datos local del Edge

Se recomienda PostgreSQL para el Edge debido a:

- múltiples clientes concurrentes;
- transacciones;
- locking;
- consistencia;
- consultas relacionales;
- robustez ante concurrencia.

SQLite puede ser útil dentro de clientes para caché, pero no debe ser la base compartida principal de la sucursal cuando múltiples dispositivos escriben simultáneamente.

Cada Edge puede mantener información como:

- sucursal;
- productos;
- precios;
- usuarios permitidos;
- mesas;
- pedidos;
- cuentas;
- pagos;
- eventos pendientes;
- trabajos de impresión;
- locks;
- configuraciones;
- versiones de catálogo.

---

# 9. Identidad de sucursal y dispositivo

Toda operación distribuida debe poder identificar:

```text
branch_id
device_id
terminal_id
user_id
```

Ejemplo:

```text
branch_id = "ZAPOPAN_01"
device_id = "TABLET_03"
terminal_id = "MESEROS"
user_id = 42
```

Nunca confiar únicamente en IP o nombre del dispositivo.

Debe existir una identidad persistente por terminal.

---

# 10. Seguridad de dispositivos

Cada dispositivo debe autenticarse contra el Edge.

No basta con asumir que "si está en la LAN es confiable".

Opciones:

- token de dispositivo;
- secreto provisionado;
- certificado cliente;
- JWT emitido tras enrolamiento;
- combinación de device_id + credential.

Ejemplo conceptual:

```http
Authorization: Bearer <device-token>
X-Device-ID: 81f2...
X-Branch-ID: ZAPOPAN_01
```

El Edge debe validar:

- que el dispositivo existe;
- que pertenece a la sucursal correcta;
- que no está revocado;
- que posee permisos válidos.

---

# 11. Segmentación de red

Idealmente la red POS debe estar separada de la red de clientes.

Ejemplo:

```text
VLAN / LAN POS
|
+-- Edge
+-- PCs
+-- Tablets
+-- impresoras

WiFi clientes
|
+-- teléfonos de clientes
+-- dispositivos no confiables
```

Las impresoras ESC/POS no deberían estar expuestas a la red de invitados.

Idealmente:

- IPs fijas o reservas DHCP;
- firewall local;
- únicamente puertos necesarios;
- sin acceso directo desde Internet.

---

# 12. Descubrimiento del Edge

Cada cliente necesita saber dónde está el Edge.

Opciones:

1. IP fija/reserva DHCP.
2. DNS local.
3. mDNS.
4. archivo de configuración provisionado.
5. descubrimiento mediante broadcast controlado.

Para un restaurante se recomienda inicialmente una solución simple y predecible.

Ejemplo:

```text
Edge Sucursal 1:
192.168.10.10
```

Posteriormente puede existir:

```text
pos-edge.local
```

No depender únicamente de asignaciones dinámicas impredecibles.

---

# 13. Operación sin Internet

La pérdida de Internet no debe detener:

- apertura de mesas;
- modificación de pedidos;
- impresión;
- cierre de cuentas;
- pagos locales;
- consulta de productos;
- uso de precios vigentes conocidos;
- interacción entre tablets y PCs dentro de la sucursal.

Cuando el VPS no esté accesible:

```text
Internet = OFF

Tablet
   |
   v
Edge local
   |
   +-- DB local
   |
   +-- impresoras
```

El Edge debe almacenar cambios pendientes de sincronización.

Cuando vuelva Internet:

```text
Edge
 |
 +-- sync worker
 |
 +-- envía eventos pendientes
 |
 v
VPS
 |
 +-- valida
 +-- persiste
 +-- responde ACK
```

---

# 14. Sincronización mediante eventos

No se recomienda sincronizar copiando tablas completas de forma ingenua.

Es preferible un modelo de eventos o cambios versionados.

Ejemplo:

```text
EVENT 10001 ORDER_CREATED
EVENT 10002 ITEM_ADDED
EVENT 10003 ITEM_ADDED
EVENT 10004 ORDER_SENT
EVENT 10005 PAYMENT_REGISTERED
```

Cada evento debe tener un identificador global único.

Ejemplo:

```text
event_id = UUID
```

Campos recomendados:

```text
event_id
branch_id
device_id
user_id
event_type
entity_type
entity_id
payload
created_at
sequence_number
sync_status
retry_count
```

---

# 15. Idempotencia

La idempotencia es crítica.

Un evento nunca debe producir dos operaciones por un simple retry.

Ejemplo problemático:

```text
Edge -> VPS
POST venta

timeout

Edge no sabe si llegó

Edge reintenta

VPS crea venta duplicada
```

Solución:

Cada operación relevante debe tener:

```text
operation_id / event_id
```

El VPS conserva identificadores ya procesados.

Si recibe el mismo evento otra vez:

```text
event_id ya existe
```

debe responder como éxito idempotente y no volver a procesarlo.

---

# 16. Orden de eventos

Por sucursal puede ser conveniente mantener un número de secuencia monotónico.

Ejemplo:

```text
branch_sequence = 10451
```

Esto permite detectar:

- huecos;
- eventos fuera de orden;
- repeticiones;
- pérdida de eventos.

No sustituye al UUID, sino que lo complementa.

---

# 17. Sincronización de catálogo y precios

El VPS será autoridad global para:

- productos;
- precios;
- categorías;
- configuraciones;
- usuarios;
- impresoras;
- promociones;
- reglas;
- sucursales.

Debe existir versionado.

Ejemplo:

```text
catalog_version = 175
```

Edge:

```text
local_version = 174
```

Consulta:

```text
GET /sync/catalog?since=174
```

Respuesta:

```text
version = 175
changes = [...]
```

El Edge actualiza localmente.

Esto permite operar offline con la última versión válida conocida.

---

# 18. Política ante cambios de precio

Un pedido abierto no debería cambiar de precio de forma retroactiva sin una regla explícita.

Recomendación:

Al agregar un producto a una orden, almacenar:

```text
product_id
product_name_snapshot
unit_price_snapshot
tax_snapshot
discount_snapshot
```

El catálogo puede cambiar después sin alterar históricamente una venta ya capturada.

---

# 19. Locks de mesa/cuenta

Una mesa o cuenta no debe poder ser modificada por más de un usuario a la vez.

Se utilizará un lock temporal con lease.

Ejemplo:

```text
table_id = 8
locked_by_user = 42
locked_by_device = TABLET_02
lock_token = UUID
expires_at = ...
```

La petición inicial:

```text
POST /tables/8/lock
```

Si está libre:

```text
LOCK_GRANTED
```

Si otro usuario la posee:

```text
LOCK_DENIED
```

---

# 20. Heartbeat para locks

Un lock no debe ser permanente.

Si la tablet se apaga, pierde red o la app se cierra, la mesa no puede quedar bloqueada indefinidamente.

Se recomienda un heartbeat.

Ejemplo:

```text
lease_duration = 30 segundos
heartbeat_interval = 10 segundos
```

Mientras el usuario está editando:

```text
Tablet
  |
  +---- heartbeat cada 10 s ----> Edge
```

Cada heartbeat renueva:

```text
expires_at = now + lease_duration
```

Si pasan más de 30 segundos sin heartbeat:

```text
lock expirado
```

y otro usuario puede reclamarlo.

Valores exactos pueden ajustarse después.

---

# 21. Token de lock

No confiar solo en `user_id`.

Al obtener un lock el servidor debe emitir un token:

```text
lock_token = UUID
```

Cada operación que modifique la mesa debe presentar ese token.

Ejemplo:

```text
PATCH /orders/123
X-Lock-Token: ...
```

Esto evita que una sesión antigua o solicitud atrasada siga escribiendo después de perder el lock.

---

# 22. Liberación explícita y expiración

El lock puede terminar por:

1. liberación explícita;
2. cierre de cuenta;
3. timeout;
4. desconexión prolongada;
5. acción administrativa.

Endpoint conceptual:

```text
DELETE /tables/8/lock
```

Debe existir también una función administrativa para desbloqueo forzado con auditoría.

---

# 23. Prevención de split-brain local

La autoridad para locks dentro de una sucursal debe ser el Edge.

Las tablets no deben decidir localmente si una mesa está libre.

Regla:

```text
Edge = autoridad de concurrencia de sucursal
```

Sin Edge disponible, el sistema debe entrar en modo degradado y no permitir silenciosamente edición simultánea de la misma cuenta.

---

# 24. Impresión ESC/POS

Las impresoras ya utilizan ESC/POS por TCP/IP.

Aunque una tablet podría imprimir directamente, la arquitectura recomendada es:

```text
Cliente
   |
   v
Edge
   |
   v
Print Queue
   |
   v
Printer ESC/POS
```

Esto centraliza impresión y evita duplicados.

---

# 25. Cola de impresión

Nunca imprimir directamente desde el request principal sin registrar estado.

Crear una entidad `print_job`.

Campos sugeridos:

```text
print_job_id
branch_id
order_id
printer_id
document_type
payload_snapshot
status
retry_count
created_at
started_at
printed_at
last_error
```

Estados sugeridos:

```text
PENDING
PRINTING
PRINTED
FAILED
CANCELLED
```

---

# 26. Impresión exactamente una vez a nivel de intención

TCP por sí mismo no puede garantizar "exactly once" físicamente.

Por ello se debe aproximar mediante:

- `print_job_id` único;
- registro previo a impresión;
- retries controlados;
- auditoría;
- protección contra doble enqueue.

Ejemplo:

```text
order_id + printer_id + print_type
```

puede formar una restricción única cuando aplique.

---

# 27. Manejo de error de impresión

Escenario:

```text
print_job = PRINTING
socket enviado
respuesta incierta
conexión cae
```

El sistema puede no saber si la impresora realmente imprimió.

Por ello:

- registrar incertidumbre;
- evitar retry inmediato infinito;
- permitir reimpresión manual auditada;
- marcar reimpresiones.

Una copia reimpresa debería indicar visualmente:

```text
REIMPRESIÓN
```

cuando el formato lo permita.

---

# 28. Ruteo por impresora

El Edge debe conocer la configuración de impresoras por sucursal.

Ejemplo:

```text
COCINA
192.168.10.20:9100

BARRA
192.168.10.21:9100

CAJA
192.168.10.22:9100
```

Los productos pueden tener destino.

Ejemplo:

```text
Hamburguesa -> COCINA
Cerveza -> BARRA
Postre -> COCINA
Ticket final -> CAJA
```

Una orden puede generar varios print jobs.

---

# 29. Transacciones

Crear pedido y generar intención de impresión deben estar coordinados.

Patrón recomendado:

```text
BEGIN TRANSACTION

guardar pedido
guardar items
crear print_job PENDING

COMMIT
```

Después un worker procesa los print jobs.

Evitar:

```text
guardar pedido
imprimir
fallo
guardar print_job
```

porque puede dejar estados inconsistentes.

---

# 30. Outbox Pattern

Para sincronización cloud se recomienda considerar el Outbox Pattern.

Dentro de la misma transacción que modifica negocio:

```text
BEGIN

UPDATE order
INSERT event_outbox

COMMIT
```

Luego un worker sincroniza el outbox.

Esto evita el problema:

```text
se guarda venta local
pero proceso muere antes de crear evento sync
```

---

# 31. Inbox Pattern en VPS

El VPS puede tener una tabla de eventos recibidos.

Ejemplo:

```text
event_inbox
```

Antes de aplicar el evento:

1. verificar `event_id`;
2. si ya fue procesado, devolver ACK;
3. si es nuevo, procesarlo dentro de transacción;
4. registrar como aplicado.

Esto mejora idempotencia.

---

# 32. Auditoría

Toda operación sensible debe registrar:

```text
actor_user_id
device_id
branch_id
action
entity_type
entity_id
timestamp
before_snapshot opcional
after_snapshot opcional
reason opcional
```

Eventos importantes:

- cancelar producto;
- cancelar cuenta;
- cambiar precio manualmente;
- descuento;
- reimpresión;
- desbloqueo forzado;
- modificación de pago;
- cierre de caja;
- devolución.

---

# 33. Fechas y tiempo

No depender ciegamente del reloj de la tablet.

Recomendación:

- guardar timestamps en UTC;
- Edge mantiene hora sincronizada;
- VPS mantiene UTC;
- mostrar hora local según sucursal.

Campos:

```text
created_at_utc
updated_at_utc
```

Para eventos offline puede guardarse adicionalmente:

```text
device_created_at
edge_received_at
server_received_at
```

---

# 34. Versionado de entidades

Para detectar actualizaciones obsoletas puede utilizarse:

```text
version
```

Ejemplo:

```text
order.version = 12
```

El cliente manda:

```text
expected_version = 12
```

Si el servidor ya está en 13:

```text
409 Conflict
```

Aunque exista lock, el versionado agrega defensa contra solicitudes retrasadas.

---

# 35. Estado de conexión

La UI debe mostrar claramente:

```text
ONLINE CLOUD
LOCAL ONLY
SYNC PENDING
EDGE DISCONNECTED
```

No esconder el estado real.

Ejemplo:

```text
Internet sin conexión
Operación local disponible
27 eventos pendientes de sincronización
```

---

# 36. Health checks

El Edge debería exponer health endpoints.

Ejemplo:

```text
GET /health
GET /health/db
GET /health/printers
GET /health/cloud
```

Respuesta conceptual:

```json
{
  "edge": "ok",
  "database": "ok",
  "cloud": "offline",
  "pending_events": 14,
  "printers": {
    "kitchen": "ok",
    "bar": "offline"
  }
}
```

---

# 37. Heartbeat de dispositivos

Además del heartbeat de locks, puede existir un heartbeat de dispositivo.

Ejemplo cada 30-60 segundos:

```text
device_id
app_version
battery opcional
last_seen
network_state
```

Esto permite al Edge conocer terminales activas.

No debe generar carga significativa.

---

# 38. Watchdog del Edge

Si el Edge corre en Windows debe instalarse preferentemente como servicio.

Debe:

- iniciar con el sistema;
- reiniciarse ante fallo;
- no requerir login;
- registrar logs;
- tener recuperación automática.

Evitar depender de que un empleado abra manualmente una ventana de terminal.

En Linux puede utilizarse:

- systemd;
- Docker restart policy;
- supervisión equivalente.

---

# 39. Actualizaciones

Las actualizaciones futuras deben distinguir:

```text
Cloud backend
Edge service
Windows app
Android app
```

Cada componente debe tener `version`.

Ejemplo:

```text
edge_version = 1.3.2
client_version = 1.8.0
protocol_version = 4
```

Debe existir compatibilidad de protocolo o un mecanismo claro de migración.

---

# 40. API versioning

Recomendación:

```text
/api/v1/
```

Evitar romper clientes desplegados.

Ejemplo:

```text
/api/v1/orders
/api/v1/tables
/api/v1/sync
```

---

# 41. Migraciones de base de datos

Django migrations deben mantenerse ordenadas.

Para Edge y VPS se debe definir estrategia de actualización:

1. backup;
2. migración;
3. validación;
4. rollback cuando sea posible.

No asumir que todos los Edge se actualizarán exactamente al mismo instante.

---

# 42. Backups

## VPS

Debe tener:

- backup automático;
- backup PostgreSQL;
- copia externa o segunda ubicación;
- política de retención.

## Edge

Aunque el VPS consolide ventas, el Edge también debería tener backup local básico.

No confiar en un único SSD.

---

# 43. UPS

Para el Edge y networking se recomienda UPS.

Idealmente conectados:

- Edge;
- router;
- switch;
- access point;
- impresoras críticas según capacidad.

Esto aumenta disponibilidad más que implementar clustering prematuramente.

---

# 44. Logs

Todos los componentes deben generar logs estructurados.

Campos útiles:

```text
timestamp
level
service
branch_id
device_id
user_id
request_id
event_id
order_id
message
```

Nunca registrar:

- contraseñas;
- secretos;
- tokens completos;
- datos sensibles innecesarios.

---

# 45. Request ID y correlación

Cada request puede tener:

```text
request_id = UUID
```

Esto permite seguir:

```text
Tablet -> Edge -> Sync -> VPS
```

en logs.

Para procesos distribuidos es muy valioso.

---

# 46. Métricas recomendadas

En una fase posterior:

- eventos pendientes de sync;
- tiempo de sync;
- errores de sync;
- jobs de impresión pendientes;
- printers offline;
- latencia Edge;
- locks activos;
- locks expirados;
- errores por dispositivo;
- versión de clientes;
- pedidos/hora;
- uptime;
- tamaño DB local;
- uso de disco.

---

# 47. VPS central

La arquitectura inicialmente propuesta para el VPS:

```text
Hostinger KVM 4

4 vCPU
16 GB RAM
200 GB NVMe
```

Stack posible:

```text
Nginx
Django
Django REST Framework
Gunicorn/Uvicorn
PostgreSQL
Redis
Celery
Docker
```

No todos los componentes deben introducirse desde el día uno.

Evitar complejidad sin necesidad.

---

# 48. Rol del VPS

El VPS central se encargará de:

- autenticación global;
- configuración de sucursales;
- catálogo;
- precios;
- usuarios;
- permisos;
- promociones;
- consolidación de ventas;
- reportes;
- administración;
- backups;
- recepción de eventos;
- distribución de configuración.

No será responsable directo de imprimir en las sucursales.

---

# 49. Fuente de verdad

Debe definirse autoridad por dominio.

Ejemplo:

```text
Catálogo global -> VPS
Precio global -> VPS
Configuración -> VPS

Mesa activa -> Edge
Pedido activo -> Edge
Print jobs -> Edge

Venta consolidada -> VPS
Historial local reciente -> Edge + VPS
```

Esto debe documentarse explícitamente para evitar conflictos.

---

# 50. Pago y consistencia

Los pagos merecen reglas más estrictas.

Un pago debe tener:

```text
payment_id
order_id
amount
method
created_at
user_id
device_id
status
```

Usar idempotencia obligatoria.

Nunca deducir un pago únicamente de que una pantalla cambió de estado.

---

# 51. Efectivo vs terminal bancaria

Si existen terminales bancarias externas, no asumir integración automática.

El sistema POS puede registrar:

```text
payment_method = CARD
```

pero la confirmación real depende de la integración disponible.

Mantener separado:

```text
POS payment record
```

de:

```text
external processor confirmation
```

si en el futuro se integran.

---

# 52. Cierre de cuenta

Cerrar una cuenta debería ser una operación transaccional.

Ejemplo:

```text
validar lock
validar saldo
registrar pago
marcar cuenta cerrada
crear impresión ticket
crear evento sync
liberar lock
```

Todo con manejo explícito de fallo.

---

# 53. Estados de una orden

Conviene modelar estados claros.

Ejemplo:

```text
OPEN
SENT_TO_KITCHEN
PARTIALLY_SERVED
CLOSED
CANCELLED
```

No depender de múltiples booleanos ambiguos.

---

# 54. Soft delete

Para información transaccional usar soft delete o estados.

Evitar borrar físicamente:

- ventas;
- pagos;
- cancelaciones;
- órdenes;
- auditorías.

Ejemplo:

```text
status = CANCELLED
cancelled_at
cancelled_by
cancel_reason
```

---

# 55. Constraints en base de datos

No confiar únicamente en validaciones del frontend.

Usar:

- unique constraints;
- foreign keys;
- check constraints;
- índices;
- not null;
- transacciones.

Ejemplos:

```text
UNIQUE(event_id)
UNIQUE(print_job_id)
UNIQUE(device_id)
```

---

# 56. Índices

Índices importantes probablemente incluyan:

```text
order(branch_id, status)
event_outbox(sync_status, created_at)
print_job(status, created_at)
lock(entity_id, expires_at)
sale(branch_id, created_at)
```

Optimizar posteriormente con métricas reales.

---

# 57. API local

La API local debe estar diseñada como interfaz estable.

Ejemplos:

```text
POST /api/v1/tables/{id}/lock
POST /api/v1/tables/{id}/heartbeat
DELETE /api/v1/tables/{id}/lock

POST /api/v1/orders
PATCH /api/v1/orders/{id}
POST /api/v1/orders/{id}/send
POST /api/v1/orders/{id}/close

GET /api/v1/catalog
GET /api/v1/sync/status
```

---

# 58. Autorización

Distinguir autenticación de autorización.

Un usuario autenticado no necesariamente puede:

- cancelar;
- reimprimir;
- cambiar precios;
- cerrar caja;
- desbloquear mesas;
- modificar usuarios.

Roles posibles:

```text
WAITER
CASHIER
SUPERVISOR
MANAGER
ADMIN
```

Evitar hardcode excesivo de roles si se requieren permisos granulares.

---

# 59. Protección del panel administrador

El panel central debe utilizar:

- HTTPS;
- contraseñas robustas;
- MFA idealmente;
- rate limiting;
- sesiones seguras;
- registro de acciones;
- control de acceso.

Nunca exponer directamente PostgreSQL a Internet.

---

# 60. Secretos

No guardar secretos en repositorio.

Usar variables de entorno o secret manager.

Ejemplos:

```text
DATABASE_URL
DJANGO_SECRET_KEY
JWT_PRIVATE_KEY
SYNC_SECRET
```

Agregar `.env` al `.gitignore`.

---

# 61. HTTPS

Todo tráfico Edge <-> VPS debe usar HTTPS.

Nunca enviar:

- credenciales;
- tokens;
- pedidos;
- datos administrativos;

por HTTP abierto en Internet.

En LAN local puede evaluarse TLS también, especialmente si se usan credenciales persistentes.

---

# 62. Certificados

A futuro puede considerarse mTLS entre Edge y VPS.

Cada Edge podría tener:

```text
edge_id
certificate
private_key
```

Esto permite revocar una sucursal comprometida.

No es obligatorio para la primera implementación.

---

# 63. Rate limiting

Endpoints expuestos en VPS deben tener límites razonables.

Especial cuidado con:

- login;
- recuperación;
- sync;
- endpoints administrativos.

---

# 64. CSRF / CORS

Si Django sirve API y frontend:

- configurar CSRF correctamente;
- evitar `CORS_ALLOW_ALL_ORIGINS=True` en producción;
- limitar origins conocidos;
- considerar token auth para clientes empaquetados.

---

# 65. Integridad local

El Edge debe rechazar datos inválidos aun si un cliente fue manipulado.

Nunca confiar en:

```text
total enviado por cliente
```

El Edge debería recalcular:

```text
subtotal
impuestos
descuentos
total
```

según reglas autorizadas.

---

# 66. Prueba crítica de aceptación offline

Una prueba futura obligatoria debe ser:

1. Abrir sistema normalmente.
2. Confirmar catálogo sincronizado.
3. Cortar físicamente Internet WAN.
4. Mantener LAN funcionando.
5. Abrir mesas.
6. Crear pedidos.
7. Imprimir comandas.
8. Cerrar cuentas.
9. Registrar pagos.
10. Confirmar que otras tablets ven estados.
11. Reconectar Internet.
12. Esperar sync.
13. Verificar VPS.
14. Confirmar cero duplicados.
15. Confirmar cero pérdida de eventos.
16. Confirmar orden correcto.
17. Validar auditoría.

Si esta prueba pasa repetidamente, la arquitectura offline está funcionando correctamente.

---

# 67. Pruebas de fallo recomendadas

También probar:

### Tablet apagada durante lock

Esperado:

```text
lock expira
```

### Edge reiniciado

Esperado:

- DB consistente;
- servicios reinician;
- print jobs pendientes recuperables;
- sync continúa.

### Internet intermitente

Esperado:

- retries con backoff;
- sin duplicados.

### Impresora desconectada

Esperado:

- job FAILED/PENDING;
- alerta;
- reimpresión controlada.

### Evento enviado dos veces

Esperado:

- una sola operación.

### Petición atrasada con lock viejo

Esperado:

- rechazada.

### Catálogo actualizado mientras sucursal offline

Esperado:

- sucursal mantiene versión anterior;
- actualiza al reconectar.

---

# 68. Retry y backoff

No reintentar agresivamente cada milisegundo.

Usar exponential backoff.

Ejemplo:

```text
1 s
2 s
5 s
10 s
30 s
60 s
```

con jitter cuando corresponda.

Debe existir límite y estado visible.

---

# 69. Circuit breaker

En una fase madura puede implementarse un circuit breaker para cloud.

Si VPS está claramente offline:

```text
no seguir saturando requests
```

El Edge puede marcar:

```text
cloud_state = OFFLINE
```

y probar periódicamente.

---

# 70. Compatibilidad temporal

Un Edge debe poder seguir operando si el VPS se actualiza brevemente.

Evitar acoplamiento tan fuerte que una versión nueva de servidor rompa clientes anteriores instantáneamente.

---

# 71. Migración futura desde la aplicación local

Durante la fase actual, intentar organizar el código en capas:

```text
UI
|
Application Services
|
Domain Logic
|
Repositories
|
Database
```

Esto facilitará mover después `Application Services` a Edge/API.

Evitar que modelos Django concentren absolutamente toda la lógica.

---

# 72. Interfaces recomendadas desde ahora

Crear servicios conceptuales como:

```text
OrderService
TableService
PaymentService
PrintService
CatalogService
LockService
AuditService
```

Aunque inicialmente sean implementaciones locales.

Después podrán intercambiar backend sin reescribir toda la UI.

---

# 73. Abstracción de impresión

Desde ahora conviene tener algo como:

```python
printer_service.print_kitchen_ticket(order)
```

No dispersar sockets ESC/POS por múltiples vistas.

Posteriormente `printer_service` puede convertirse en cliente del Edge.

---

# 74. Abstracción de almacenamiento

No es necesario crear una arquitectura excesivamente abstracta, pero sí evitar acoplamientos innecesarios.

Ejemplo:

```text
OrderRepository
```

puede facilitar pruebas.

---

# 75. Testing

Priorizar pruebas para:

- cálculo de total;
- descuentos;
- impuestos;
- locks;
- expiración de locks;
- pagos;
- cancelaciones;
- generación de comanda;
- idempotencia;
- sincronización;
- ruteo de impresoras;
- permisos.

---

# 76. Tests de concurrencia

Crear pruebas donde:

```text
Tablet A intenta lock mesa 8
Tablet B intenta lock mesa 8
```

Solo una debe ganar.

También:

```text
Tablet A tiene lock
lock expira
Tablet B obtiene lock
request tardío de A llega
```

La petición de A debe fallar.

---

# 77. Request tardío

Para evitar que una acción atrasada se aplique fuera de contexto, combinar:

```text
lock_token
entity_version
operation_id
```

---

# 78. Resumen de arquitectura final

Arquitectura objetivo:

```text
                 +----------------------+
                 |      VPS CENTRAL     |
                 |----------------------|
                 | Django API           |
                 | PostgreSQL           |
                 | Admin                |
                 | Reporting            |
                 | Catalog              |
                 | Sync endpoint        |
                 +----------+-----------+
                            |
                         HTTPS
                            |
        +-------------------+-------------------+
        |                   |                   |
        v                   v                   v
+---------------+    +---------------+    +---------------+
| EDGE BRANCH 1 |    | EDGE BRANCH 2 |    | EDGE BRANCH N |
|---------------|    |---------------|    |---------------|
| API local     |    | API local     |    | API local     |
| PostgreSQL    |    | PostgreSQL    |    | PostgreSQL    |
| Sync worker   |    | Sync worker   |    | Sync worker   |
| Print worker  |    | Print worker  |    | Print worker  |
| Lock manager  |    | Lock manager  |    | Lock manager  |
+-------+-------+    +-------+-------+    +-------+-------+
        |                    |                    |
   LAN local            LAN local            LAN local
        |                    |                    |
 +------+-------+       +----+------+        +----+------+
 |      |       |       |    |     |        |    |     |
 PC   Tablets Printers  PC Tablets Printers PC Tablets Printers
```

---

# 79. Prioridades actuales para el agente de código

Mientras la aplicación siga en fase local, priorizar:

1. Corregir y estabilizar lógica de negocio.
2. Mantener modelos consistentes.
3. Crear servicios separados de la UI.
4. Centralizar impresión en un servicio.
5. Centralizar permisos.
6. Diseñar estados explícitos.
7. Utilizar UUIDs en entidades que luego deban sincronizarse.
8. Agregar auditoría.
9. Evitar borrados destructivos.
10. Crear constraints.
11. Diseñar APIs internas limpias.
12. Preparar tablas para branch/device cuando sea oportuno.
13. Escribir tests.
14. No implementar aún infraestructura distribuida si obstaculiza estabilizar el producto.

---

# 80. Decisiones que NO deben tomarse prematuramente

No es necesario aún:

- desplegar seis Edge;
- comprar mini PCs;
- implementar Kubernetes;
- usar microservicios;
- montar RabbitMQ si Redis/Celery o un worker simple basta;
- crear clustering PostgreSQL;
- configurar alta disponibilidad compleja;
- usar Azure VDI;
- hacer multi-region;
- implementar service mesh.

La escala del negocio no lo requiere.

---

# 81. Principios de implementación

El agente debe respetar estos principios:

### Local-first

La sucursal debe seguir operando aunque Internet falle.

### Edge as authority

El Edge es la autoridad operativa local.

### Cloud as global authority

El VPS gobierna catálogo, configuración y consolidación.

### Idempotency everywhere

Toda operación crítica distribuida debe tolerar reintentos.

### Explicit state

Evitar estados implícitos o booleanos confusos.

### Auditability

Las operaciones importantes deben ser rastreables.

### Fail safely

Si no se puede garantizar consistencia, rechazar o degradar de forma explícita.

### Minimal complexity

No introducir infraestructura empresarial innecesaria.

### Test failure, not only success

La calidad se define también por qué ocurre cuando algo se cae.

---

# 82. Objetivo final

El sistema final debe permitir que:

- cualquier PC o tablet autorizada tome pedidos;
- las tablets no dependan de escritorio remoto;
- una sola computadora de usuario no sea la aplicación completa;
- la sucursal siga funcionando sin Internet;
- las impresoras funcionen por ESC/POS TCP/IP;
- las mesas no sean editadas simultáneamente;
- los locks expiren mediante lease + heartbeat;
- no se dupliquen ventas;
- no se dupliquen eventos;
- la impresión sea auditable;
- los datos se consoliden automáticamente;
- precios y configuración se administren centralmente;
- el VPS pueda escalar a más sucursales;
- el sistema pueda evolucionar sin rehacer toda la aplicación.

---

# 83. Recomendación inmediata

Durante el desarrollo actual:

```text
NO priorizar despliegue distribuido todavía.
```

Primero conseguir:

```text
Aplicación local estable
          |
          v
Dominio y servicios bien diseñados
          |
          v
API clara
          |
          v
Cliente Windows / Android
          |
          v
Edge Server
          |
          v
VPS y sincronización
```

La arquitectura distribuida debe ser la evolución del sistema estable, no una forma de compensar errores funcionales todavía abiertos.

---

# 84. Nota para futuras sesiones de desarrollo

Si un agente recibe este documento sin contexto adicional, debe asumir lo siguiente:

- La aplicación ya existe en Django.
- Originalmente fue pensada como PWA.
- Primero debe estabilizarse localmente.
- Posteriormente habrá clientes `.exe` y `.apk`.
- Cada sucursal tendrá un Edge Server.
- El Edge podrá inicialmente correr en una PC Windows existente.
- A futuro puede migrarse a mini PC Linux.
- Las impresoras son ESC/POS TCP/IP.
- La operación offline es deseable y forma parte del diseño objetivo.
- Solo un usuario puede modificar una mesa/cuenta al mismo tiempo.
- Se usarán locks con lease y heartbeat.
- El VPS centralizará configuración y ventas.
- El Edge manejará pedidos activos, locks e impresión local.
- La sincronización debe ser idempotente.
- La cola de eventos y print jobs debe sobrevivir reinicios.
- No se debe depender de RDP/VDI.
- El volumen de carga esperado es bajo.
- La robustez operativa importa más que la potencia del servidor.

Este documento debe utilizarse como referencia arquitectónica principal durante las fases posteriores del proyecto.
