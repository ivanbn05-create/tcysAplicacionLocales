# Especificación Production 1.0 — Los Tocayos POS

## 1. Objetivo

Production 1.0 no se plantea como un MVP limitado.

El objetivo es entregar una primera versión productiva completa del ecosistema Los Tocayos POS, preparada para operar inicialmente en Arboledas y Santa Anita y posteriormente desplegarse sobre la misma arquitectura y la misma línea de software en el resto de las sucursales.

La prioridad principal del proyecto es el POS local/Edge. `tcysPedidosSucursales` y el Backend Central complementan al POS, pero una falla de Internet, del VPS o de cualquiera de esos servicios externos nunca debe impedir:

* abrir ventas;
* modificar ventas abiertas según permisos;
* cobrar;
* imprimir;
* consultar el catálogo local vigente;
* operar durante una pérdida temporal de conectividad.

La instalación productiva debe ser mantenible, recuperable, segura, reproducible y desplegable en múltiples sucursales sin mantener variantes diferentes del código.

---

# 2. Sucursales

Las sucursales iniciales son:

| Nombre canónico    | Clave propuesta |
| ------------------ | --------------- |
| Arboledas — Matriz | `ARBOLEDAS`     |
| Águilas            | `AGUILAS`       |
| Estancia           | `ESTANCIA`      |
| Plaza del Sol      | `PLAZA_DEL_SOL` |
| Santa Anita        | `SANTA_ANITA`   |

Cada sucursal tendrá además un UUID técnico permanente generado por el sistema central.

La identidad técnica nunca debe depender únicamente del nombre visible.

## Primera validación productiva

La primera implantación conjunta se realizará en:

* Arboledas — Matriz;
* Santa Anita.

Esto no implica una versión reducida del software. Ambas sucursales deben utilizar la misma línea de Production 1.0 destinada posteriormente a las demás.

---

# 3. Una sola línea de producto

Debe existir:

* una sola línea de código del servidor Edge;
* una sola release de servidor compatible con todas las sucursales;
* un solo cliente Windows compatible;
* una sola aplicación Android/PWA compatible.

No deben crearse ejecutables, ramas o builds distintos únicamente por:

* sucursal;
* menú;
* precios;
* impresoras;
* módulos activados;
* número de terminales.

Las diferencias entre sucursales pertenecen a configuración y datos.

---

# 4. Arquitectura objetivo

```text
                         INTERNET
                             │
                             ▼
                    BACKEND CENTRAL VPS
             ┌───────────────┼────────────────┐
             │               │                │
        Clientes        Catálogo/precios     Recibos
        permanentes       publicaciones      / ventas
             │               │                │
             └───────────────┼────────────────┘
                             │ HTTPS
                   ┌─────────┴─────────┐
                   │                   │
                   ▼                   ▼
             EDGE ARBOLEDAS      EDGE SANTA ANITA
              SQLite local        SQLite local
                   │                   │
          ┌────────┼────────┐          │
          │        │        │          │
         PC       PC      Android     ...
         EXE      EXE     APK/PWA
```

Cada sucursal tiene exactamente un Edge operativo principal.

El Edge contiene la base local y gobierna la operación inmediata del restaurante.

Las PC adicionales y las tabletas son terminales del Edge, no servidores POS adicionales.

---

# 5. Módulos

## Núcleo obligatorio Production 1.0

Todas las sucursales tendrán habilitados:

* POS;
* Catálogo;
* Impresión;
* Respaldos;
* Domicilios;
* Pedidos programados;
* Reparto.

Estos módulos ya no deben considerarse opcionales en documentación futura de Production 1.0.

## Módulos adicionales

### Pedidos de sucursales

Habilitado inicialmente únicamente para:

* Arboledas — Matriz.

### Quesaking

Previsto posteriormente para:

* Plaza del Sol.

`Quesaking` no forma parte de la entrega inicial Production 1.0.

Sin embargo, la arquitectura de módulos debe permitir incorporarlo en una release común y habilitarlo exclusivamente para Plaza del Sol sin crear una variante del POS.

---

# 6. Enrolamiento de una sucursal

Las sucursales se crean primero desde el sistema central.

El flujo objetivo será:

1. crear sucursal;
2. asignar UUID;
3. asignar clave canónica;
4. configurar módulos;
5. preparar identidad;
6. generar código de enrolamiento de un solo uso;
7. instalar el servidor Edge;
8. introducir código de enrolamiento;
9. validarlo por HTTPS contra Central;
10. recibir la identidad autorizada;
11. generar/recibir credenciales correspondientes;
12. inicializar configuración y catálogo;
13. registrar el Edge.

El técnico no debe poder seleccionar arbitrariamente una identidad distinta a la autorizada por el enrolamiento.

El código:

* es de un solo uso;
* expira;
* no debe quedar registrado en logs;
* no constituye una credencial permanente.

Debe quedar preparada una futura alternativa mediante archivo de enrolamiento firmado para escenarios sin conectividad durante instalación.

---

# 7. Identidades

Deben distinguirse como mínimo:

### Sucursal

Negocio físico.

Ejemplo:

```text
SANTA_ANITA
UUID: ...
```

### Edge

Instalación servidor concreta de esa sucursal.

```text
EDGE-SA-01
UUID: ...
```

Una reinstalación o sustitución controlada debe definir explícitamente si recupera el Edge existente o enrola otro.

### Terminal

Dispositivo cliente.

Ejemplos:

```text
PC-SA-01
PC-SA-02
TAB-SA-01
TAB-SA-02
```

### Identidad de Pedidos

Debe existir correspondencia explícita entre:

```text
Sucursal POS
↔ Edge
↔ Branch Central
↔ SucursalCliente.id de tcysPedidosSucursales
```

Nunca se realizará esta unión por nombre.

---

# 8. Equipamiento de referencia

Configuración típica esperada por sucursal:

* 1 PC servidor Edge;
* 1 PC adicional como cliente;
* aproximadamente 2 tabletas Android;
* aproximadamente 2 impresoras.

La cantidad puede variar sin alterar arquitectura o release.

---

# 9. Cliente Windows

El cliente `.exe` es un cliente ligero.

No contiene:

* base SQLite del negocio;
* copia independiente del catálogo;
* credenciales de Central;
* sincronizador propio de ventas.

Se conecta al Edge local.

Debe poder enrolarse como terminal y conocer:

* identidad de terminal;
* Edge correspondiente;
* configuración necesaria de impresión;
* estado del servidor local.

Cambiar el frontend del Edge no debe requerir reinstalar el cliente salvo que cambie el contenedor o integración Windows.

---

# 10. Android

Production 1.0 debe ofrecer:

* PWA;
* APK Android firmado.

El APK es la distribución preferida para las tabletas internas.

La PWA se conserva como alternativa y fundamento web.

El APK:

* no contiene otra base POS;
* no sustituye al Edge;
* no necesita conectarse al VPS para vender;
* consume el Edge local.

Las tabletas deben probarse físicamente antes de promoción.

---

# 11. Catálogo y precios

El dueño global es la máxima autoridad de catálogo y precios.

La modificación normal de:

* productos;
* categorías;
* precios;
* excepciones de precio por sucursal;

se realiza desde el Backend Central.

## Flujo

```text
Editar borrador
     ↓
Guardar
     ↓
Publicar
     ↓
snapshot por sucursal
     ↓
POS descarga
     ↓
valida checksum/esquema
     ↓
aplica atómicamente
     ↓
ACK
```

Guardar un borrador no modifica las sucursales.

Publicar tampoco significa automáticamente “aplicado”.

Una publicación sólo aparece como aplicada cuando el Edge correspondiente confirma el ACK.

## Offline

Cada Edge conserva la última versión válida.

Una caída del VPS nunca deja el menú inutilizable.

## Históricos

Los cambios de catálogo o precio nunca modifican tickets cerrados.

El precio realmente cobrado permanece almacenado en la venta.

---

# 12. Roles humanos del POS

Los permisos se implementarán como capacidades explícitas, aunque exista una jerarquía funcional.

## Dueño global

Es la máxima autoridad de negocio.

Puede gestionar desde Central:

* catálogo;
* precios;
* publicaciones;
* sucursales;
* configuración central autorizada;
* operaciones centrales correspondientes.

El portal Central exige MFA.

## Dueño de sucursal

Máxima autoridad funcional dentro de su POS.

Puede:

* usar Ventas;
* cobrar;
* modificar;
* cancelar/eliminar conforme a reglas;
* acceder al panel administrativo;
* gestionar usuarios locales;
* cambiar claves/PIN autorizados;
* reiniciar folios;
* consultar funciones administrativas del negocio.

No obtiene privilegios de Windows/VPS.

No modifica el catálogo maestro Central.

## Usuario elevado

Hereda las capacidades operativas de niveles inferiores.

Puede:

* entrar a Ventas;
* cobrar;
* modificar;
* eliminar/cancelar según reglas;
* realizar operaciones administrativas cotidianas autorizadas.

No puede:

* crear nuevos usuarios;
* cambiar claves de otros usuarios;
* administrar identidades;
* reiniciar folios a cero.

## Mesero

Puede:

* entrar a Ventas;
* abrir y trabajar comandas;
* agregar/modificar partidas mientras la operación lo permita.

No puede:

* cobrar;
* eliminar/cancelar operaciones restringidas;
* entrar al panel Administrador.

## Repartidor

Por ahora actúa principalmente como identidad asignable a pedidos a domicilio.

No entra a Ventas.

No cobra.

No administra.

Una interfaz específica de repartidor puede añadirse en una versión futura.

---

# 13. Administrador del sistema

El Administrador del sistema constituye un plano técnico independiente de la jerarquía de negocio.

Su objetivo es soporte y mantenimiento.

Debe existir una interfaz técnica local para funciones como:

* visualizar/modificar terminales conocidas;
* configurar IP/hostname del Edge;
* registrar impresoras;
* modificar IP y puerto de impresoras;
* asignar terminal → impresora;
* modificar destinos de impresión;
* diagnóstico de conectividad;
* información de versión;
* estado de servicios;
* estado de sincronización;
* configuración operativa técnica;
* otras acciones seguras de soporte.

Esta interfaz:

* se limita a la LAN/local del Edge;
* exige cuenta técnica apropiada;
* no se expone públicamente por Internet;
* no proporciona consola de comandos arbitraria;
* no convierte al usuario en Administrador Windows.

---

# 14. Impresión

Las impresoras no tendrán una función fija codificada.

Se modelan como recursos configurables.

Ejemplo:

```text
IMPRESORA-A
IP / hostname
Puerto
Descripción

IMPRESORA-B
IP / hostname
Puerto
Descripción
```

Después las terminales pueden dirigirse a cualquiera:

```text
PC-01     → IMPRESORA-A
PC-02     → IMPRESORA-B
TABLET-01 → IMPRESORA-A
TABLET-02 → IMPRESORA-B
```

En Arboledas las dos impresoras pueden realizar el mismo tipo de trabajo para permitir que dos personas impriman simultáneamente.

En otras sucursales la asignación puede representar caja, cocina, barra u otra organización.

El comportamiento pertenece a configuración, no a código.

---

# 15. tcysPedidosSucursales

`tcysPedidosSucursales` es un sistema independiente del Backend Central POS.

Production 1.0 debe permitir que Arboledas consuma los pedidos mediante API HTTPS versionada.

No debe existir acceso directo del POS a PostgreSQL del VPS.

## API v2

Debe incluir:

* identificación pública de pedido;
* paginación;
* cursor durable;
* idempotencia;
* scopes por Edge/sucursal;
* TLS;
* credencial independiente.

## `410 retention_gap`

Un `410 retention_gap` significa:

> no puede garantizarse continuidad desde el cursor actual.

No significa:

> no existen pedidos.

El Edge:

* conserva el cursor;
* conserva pedidos existentes;
* no avanza;
* entra en estado de conciliación.

Production 1.0 necesita un procedimiento ejecutable y auditado de salida de ese estado.

La API v1/legacy sólo podrá retirarse una vez terminado y autorizado el corte.

---

# 16. Credenciales

Cada Edge tendrá credenciales separadas.

Como mínimo:

* lectura Pedidos;
* ingesta de ventas/clientes Central;
* catálogo Central.

Una credencial no debe sustituir a otra.

Cada credencial se limita por:

* instalación;
* sucursal;
* scopes;
* vigencia/revocación cuando corresponda.

La rotación de credenciales no debe modificar:

* UUID de eventos;
* cursores;
* lotes;
* payload;
* ACK;
* identidad del Edge.

Nunca se registran tokens en logs.

---

# 17. Ventas y sincronización

La sincronización nunca pertenece al camino crítico de una venta.

```text
Venta
 ↓
SQLite
 ↓
cobro
 ↓
impresión
 ↓
outbox
 ↓
sincronización posterior
```

Si Internet está caído:

* la venta se completa;
* se cobra;
* se imprime;
* los eventos esperan.

El outbox conserva:

* identidad;
* cuerpo/hash;
* intentos;
* estado;
* ACK.

Los reintentos usan la misma identidad idempotente.

---

# 18. Retención central

## Ventas

La información transaccional temporal del Backend Central permanece hasta que ocurra primero:

1. confirmación válida de exportación/descarga a un equipo autorizado; o
2. 30 días desde la primera recepción en el VPS.

Después debe purgarse físicamente dentro del alcance controlado.

No es suficiente:

* soft delete;
* archivado;
* `deleted=true`.

Puede permanecer un tombstone técnico mínimo para evitar rehidratación.

## Permanentes

No se purgan mediante esta política:

* clientes;
* catálogo;
* publicaciones;
* identidades;
* mappings;
* ACK;
* datos maestros necesarios.

---

# 19. Backups

## Edge

Debe existir:

* backup consistente de SQLite;
* hash;
* integrity check;
* foreign key check;
* restauración probada;
* backup de configuración relevante.

Debe definirse además el mecanismo de copia externa.

## Central

Los backups permanentes:

* incluyen permanentes;
* excluyen cuerpos transaccionales purgables;
* conservan tombstones/recibos necesarios.

Un restore nunca debe reintroducir ventas ya purgadas.

La política de snapshots físicos del proveedor debe quedar resuelta antes de activar de manera definitiva la política productiva de retención.

---

# 20. MFA y seguridad Central

El portal del dueño requiere:

* contraseña;
* TOTP compatible con Google Authenticator;
* recuperación controlada;
* códigos de un uso;
* rate limiting;
* sesión segura.

Acciones críticas requieren revalidación/step-up reciente.

Entre ellas:

* publicar catálogo;
* confirmar exportaciones;
* purgar;
* modificar seguridad;
* resetear MFA;
* modificar roles críticos.

---

# 21. Seguridad Edge

El Edge debe mantener:

* secretos fuera del repositorio;
* ACL adecuadas;
* credenciales separadas;
* TLS verificado para conexiones remotas;
* ningún `verify=False`;
* firewall limitado a LAN necesaria;
* servicio bajo cuenta no administrativa;
* configuración técnica restringida.

La operación diaria no requiere privilegios de Administrador Windows.

---

# 22. Releases y firma

Production 1.0 debe ser reproducible.

Cada release se identifica por:

* versión;
* commit fuente;
* manifiesto;
* SHA-256;
* artefacto exacto.

Dos builds del mismo source deben producir artefactos reproducibles cuando el protocolo lo exija.

## Android

El APK debe estar firmado mediante una clave Android propia y permanentemente custodiada.

No requiere comprar un certificado comercial.

La clave debe conservarse de forma segura porque futuras actualizaciones deben mantener la identidad de firma.

## Windows

No se considera obligatorio adquirir inicialmente un certificado Authenticode comercial.

Para distribución interna controlada se implementará como mínimo:

* manifiesto;
* SHA-256;
* firma criptográfica propia;
* verificación previa a actualización.

La firma debe demostrar que la release procede del proceso autorizado y no fue alterada.

El certificado comercial puede evaluarse posteriormente si la distribución se hace pública o los avisos de reputación de Windows se convierten en un problema operativo.

---

# 23. Instalador y actualización

El instalador del servidor Edge debe cubrir:

* prerequisitos;
* enrolamiento;
* identidad;
* servicio Windows;
* SQLite;
* configuración;
* ACL;
* firewall;
* módulos;
* catálogo inicial;
* secretos;
* tareas de backup;
* verificación de salud.

Una actualización debe:

1. validar release;
2. verificar firma/hash;
3. respaldar;
4. preparar staging;
5. detener sólo cuando sea necesario;
6. migrar;
7. conmutar;
8. validar salud;
9. permitir rollback documentado.

No se permite actualizar copiando archivos Python manualmente sobre una instalación productiva.

---

# 24. Monitoreo

El canal de alerta inicial será:

**correo electrónico.**

Se deben contemplar alertas para:

* VPS no disponible;
* error de aplicación;
* certificado próximo a vencer;
* backup fallido;
* falta de espacio;
* purga atrasada;
* Edge sin sincronización prolongada;
* fallos repetidos de integración;
* otros incidentes críticos definidos posteriormente.

---

# 25. Portal Central y soporte local

Deben mantenerse conceptualmente separados.

## Portal Central

Orientado al negocio.

Disponible por HTTPS.

Protegido con MFA.

Gestiona principalmente:

* catálogo;
* precios;
* publicaciones;
* sucursales;
* datos centrales;
* exportaciones;
* estado global.

## Administrador POS

Orientado a operación de cada sucursal.

## Panel técnico de soporte

Orientado a infraestructura local.

Accesible sólo desde entorno local/LAN autorizado.

No debe convertirse en un panel web público.

---

# 26. Alcance Productivo inicial

## Arboledas

Módulos:

* núcleo completo;
* Pedidos de sucursales.

Equipamiento esperado:

* aproximadamente 2 PC;
* aproximadamente 2 impresoras;
* tabletas según operación actual.

## Santa Anita

Módulos:

* núcleo completo.

Equipamiento esperado:

* aproximadamente 2 PC;
* aproximadamente 2 tabletas;
* aproximadamente 2 impresoras.

Ambas deben validar el mismo artefacto Production 1.0.

---

# 27. Fuera de Production 1.0 inicial

No forman parte del bloqueo de la primera producción:

* módulo Quesaking;
* aplicación específica para repartidores;
* certificado Authenticode comercial;
* publicación APK mediante Google Play;
* acceso remoto público al panel técnico;
* variantes de POS por sucursal.

La arquitectura debe permitir incorporarlos posteriormente sin una reescritura estructural.

---

# 28. Gates obligatorios antes de producción

Production 1.0 sólo puede promoverse cuando pasen todos los siguientes grupos.

## POS

* venta;
* modificación;
* cobro;
* cancelación según permisos;
* impresión;
* clientes;
* domicilios;
* programados;
* reparto;
* offline;
* reinicio;
* backup;
* restore.

## Roles

Pruebas positivas y negativas de:

* dueño de sucursal;
* elevado;
* mesero;
* repartidor;
* administrador técnico.

## Multisucursal

* Arboledas;
* Santa Anita;
* separación de identidad;
* separación de scopes;
* separación de catálogo/precios;
* separación de clientes/ventas donde corresponda.

## Central

* MFA;
* catálogo;
* precio global;
* excepción;
* publicación;
* ACK;
* clientes;
* ventas;
* exportación;
* retención;
* restore.

## Pedidos

* API v2;
* paginación;
* deduplicación;
* scopes;
* rotación;
* 401;
* timeout;
* `410 retention_gap`;
* procedimiento de recuperación.

## Impresión

* dos impresoras;
* ruteo por terminal;
* impresora inalcanzable;
* reintento controlado;
* hardware físico real.

## Windows

* instalación limpia;
* actualización;
* rollback;
* cliente `.exe`;
* PC real;
* firma/verificación.

## Android

* APK firmado;
* instalación;
* actualización;
* tableta real;
* reconexión;
* uso de Edge local.

## Infraestructura

* TLS productivo;
* Nginx;
* PostgreSQL;
* firewall;
* backups;
* restore;
* monitoreo;
* alertas por correo;
* snapshots resueltos.

---

# 29. Política de promoción

Una prueba automatizada aprobada no autoriza por sí sola producción.

La secuencia es:

```text
código
 ↓
pruebas automatizadas
 ↓
build reproducible
 ↓
laboratorio E2E
 ↓
hardware real
 ↓
Arboledas + Santa Anita
 ↓
aceptación operativa
 ↓
Production 1.0
 ↓
resto de sucursales
```

Toda promoción productiva requiere autorización humana explícita.

---

# 30. Principio rector

Toda decisión futura debe preservar estas propiedades:

1. una sola línea de producto;
2. variación mediante configuración/módulos;
3. operación local aun sin VPS;
4. no pérdida de ventas;
5. identidad inequívoca de sucursal/Edge;
6. seguridad por defecto;
7. actualizaciones recuperables;
8. Central como autoridad de catálogo;
9. soporte técnico sin acceso arbitrario al sistema operativo;
10. capacidad de extender nuevas sucursales y módulos sin mantener forks.
