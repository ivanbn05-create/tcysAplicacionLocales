# Arquitectura de despliegue, actualización y sincronización multisucursal

## 1. Propósito y estado del documento

Este documento conserva el contexto y las decisiones acordadas para convertir el
POS local de Los Tocayos en un producto instalable, actualizable y administrable
en varias sucursales. Está pensado como material de continuidad para otro agente
o para retomar el trabajo sin depender de conversaciones anteriores.

**Estado a 2026-09-15:** dirección arquitectónica aceptada. Se eligió Hostinger
KVM 2 como infraestructura central inicial prevista; el backend central, su
aprovisionamiento y la sincronización general todavía no están implementados.
La candidata A `0.4.0-dev.1` ya acreditó instalación limpia y la candidata
provisional B `0.4.0-dev.2` acreditó una actualización real en el equipo de
laboratorio. B no es todavía la base funcional aceptada ni se ha integrado al
repositorio principal.

Este documento complementa a:

- `propuesta_arquitectura_pos_multisucursal.md`, que contiene la visión técnica
  amplia de Edge Server, VPS, concurrencia, impresión y operación offline.
- `DESPLIEGUE_WINDOWS.md`, que describe el despliegue Windows actualmente
  probado en Arboledas y sus limitaciones.
- `README.md`, que contiene la operación actual del proyecto.
- `FLUJO_DESARROLLO_MANTENIMIENTO_Y_CLIENTES.md`, que define el trabajo entre
  desarrollador y Codex, el despliegue por sucursal y la ruta de `.exe`/`.apk`.

La precedencia depende del tema. Para ejecutar, diagnosticar o recuperar el
servidor Edge Windows **manda `DESPLIEGUE_WINDOWS.md`**, porque describe el código
vigente y sus límites comprobados; `README.md` sólo lo resume. Este documento
manda sobre la **dirección futura** multisucursal —VPS, catálogo central,
módulos, enrolamiento y actualizador transaccional—, pero una propuesta futura
no autoriza a saltarse el runbook ni afirmar que una capacidad ya está
implementada.

## 2. Decisión principal

Se mantendrá **una sola línea de código y un solo artefacto de aplicación para
todas las sucursales**.

Cada sucursal tendrá una instalación local independiente que conservará:

- identidad propia;
- base operativa local;
- configuración de red e impresoras;
- credenciales y secretos propios;
- catálogo, precios y módulos habilitados para esa sucursal;
- última configuración válida recibida del VPS;
- capacidad de operar temporalmente sin Internet.

No se crearán ramas de Git, ejecutables o paquetes distintos únicamente porque
una sucursal tenga otro menú o módulos deshabilitados. Esa variación debe vivir
en datos y configuración. Sólo se reconsideraría esta decisión si en el futuro
existieran productos verdaderamente incompatibles por hardware, regulación o
dependencias de gran tamaño; un menú diferente no constituye esa situación.

### 2.1 Infraestructura central acordada

Para la primera producción multisucursal se eligió un **VPS Hostinger KVM 2
autoadministrado** como destino central. Esta decisión sustituye el
dimensionamiento preliminar KVM 4 de
`propuesta_arquitectura_pos_multisucursal.md` y la alternativa de usar Supabase
como backend central principal.

La ficha comercial consultada el 2026-09-09 indicaba:

| Recurso | KVM 2 |
| --- | ---: |
| vCPU | 2 |
| RAM | 8 GB |
| Disco NVMe | 100 GB |
| Transferencia | 8 TB |
| Precio promocional observado | MXN 154.99/mes equivalente |
| Renovación observada | MXN 273.99/mes por 2 años |

Los importes anunciados son equivalentes mensuales de periodos pagados por
adelantado, pueden cambiar y deben comprobarse en el carrito antes de contratar.
Referencias consultadas: [planes VPS de Hostinger
México](https://www.hostinger.com/mx/vps-hosting) y [límites oficiales de los
planes](https://www.hostinger.com/support/6976044-parameters-and-limits-of-hosting-plans-in-hostinger/).

KVM 1 sería técnicamente suficiente para construcción o piloto según la carga
estimada, pero su único vCPU deja poco margen cuando coincidan API, PostgreSQL,
respaldo, migración o reporte. KVM 2 se adopta como base prudente para producción
por su segundo vCPU y memoria adicional. Esta estimación debe validarse con una
prueba de carga y métricas reales antes de abrir todas las sucursales.

### 2.2 Alternativas evaluadas y portabilidad

Supabase fue evaluado como sustituto del VPS. Su plan gratuito sigue siendo útil
para prototipos y pruebas; Pro reduce trabajo operativo al incluir servicios
administrados, pero su costo base de USD 25 mensuales resultó menos atractivo que
KVM 2 para esta carga. El ahorro del VPS se obtiene a cambio de asumir instalación,
parches, seguridad, respaldos, monitoreo y recuperación. Referencia de comparación:
[precios de Supabase](https://supabase.com/pricing).

Esta decisión no elimina la integración actual que consulta pedidos confirmados
de `tcysPedidosSucursales` en Supabase. Esa fuente externa seguirá siendo un flujo
separado mientras exista; no es la futura base central de Los Tocayos y no deberá
retirarse sin un plan de migración acordado.

La API Edge-central debe conservar contratos propios y versionados. La lógica de
negocio no dependerá de primitivas exclusivas de Hostinger o Supabase, de modo que
el backend pueda migrarse en el futuro sin reescribir el POS de cada sucursal.

## 3. Contexto operativo conocido

### 3.1 Negocio y despliegue previsto

La propuesta multisucursal anterior documenta el siguiente escenario, que debe
revalidarse antes del despliegue definitivo:

- 4 sucursales activas y al menos 2 adicionales previstas;
- aproximadamente 2 computadoras y 3 tabletas por sucursal;
- alrededor de 30 dispositivos cliente al alcanzar 6 sucursales;
- carga pico estimada de unas 150 órdenes por hora entre todas las sucursales.

La dificultad principal no es la capacidad de cómputo, sino la continuidad
operativa, sincronización, impresión, seguridad, soporte y recuperación.

### 3.2 Estado actual de producción

La aplicación entró a un entorno denominado producción, pero el negocio aún no
la utiliza de manera activa. Las operaciones realizadas han sido simulaciones.
Mientras continúe esta condición, es aceptable detener o levantar el servicio y,
con autorización expresa, reiniciar los datos para validar una instalación
limpia. Esta tolerancia es temporal: una vez iniciada la operación real, ningún
instalador, actualizador o sincronizador podrá borrar o reiniciar datos como parte
de un proceso normal.

### 3.3 Topología local actual

- Django sirve el POS dentro de la sucursal.
- Waitress se ejecuta como servicio Windows `LosTocayosPOS`.
- El servicio usa `NT AUTHORITY\LocalService`.
- SQLite se encuentra en `runtime\db.sqlite3` en el despliegue actual.
- Computadoras y tabletas consumen la misma interfaz por la red local.
- El cliente Windows es un contenedor ligero que abre el servidor local; no
  contiene la base ni una segunda copia del frontend.
- La impresión y la operación inmediata deben seguir siendo locales.

Por esta razón, actualizar el frontend del servidor de una sucursal actualiza a
sus clientes sin reinstalar cada tableta o computadora. El ejecutable cliente
sólo necesita otra versión cuando cambie su lanzador, configuración o integración
con Windows.

## 4. Lecciones del despliegue y arranque del servicio

Durante la preparación de producción simulada aparecieron problemas que
requirieron comandos elevados y cambios en el instalador:

- permisos NTFS que impedían al servicio escribir `logs\waitress.log`;
- necesidad de conservar acceso efectivo para Administradores y SYSTEM;
- resolución correcta del host `pythonservice.exe` y las DLL requeridas por
  Python/pywin32 al iniciar desde `services.exe`;
- necesidad de validar el intérprete de máquina en lugar de depender del Python
  instalado para un usuario;
- necesidad de detener el servicio durante operaciones que sustituyen su host;
- respaldo verificable de SQLite antes de migrar;
- comprobación posterior del servicio, firewall y endpoint de salud.

La corrección actual ya concede a `LocalService`:

- lectura/ejecución sobre código, entorno virtual, estáticos y certificados;
- lectura sobre `.env`;
- modificación sobre `runtime`, `logs` y `media`;
- ningún acceso a `backups`, `.git`, temporales protegidos y bases legadas.

Las funciones relevantes están en `instalar-servicio-lan.ps1`, y las auditorías
en `tests/test_instalador_windows.ps1`, `herramientas/validar_despliegue.py` y
`verificar-servicio-lan.ps1`.

### 4.1 Interpretación correcta de los privilegios administrativos

Es esperado que la instalación y una actualización del servidor soliciten
elevación para:

- registrar o modificar el servicio de Windows;
- escribir código protegido;
- configurar ACL;
- crear reglas de firewall;
- registrar tareas de respaldo;
- cambiar el runtime que ejecutará el servicio.

Esto no significa que el POS deba ejecutarse como administrador. La operación
diaria, la sincronización de catálogo y los cambios realizados desde el
Administrador General no deben recibir privilegios del sistema operativo.

También deben distinguirse dos conceptos:

- **Administrador General del negocio:** usuario funcional que gobierna catálogo,
  precios, sucursales y módulos desde la aplicación central.
- **Administrador de Windows:** identidad técnica usada únicamente para instalar,
  reparar o actualizar componentes protegidos del equipo.

Nunca debe otorgarse al Administrador General capacidad de ejecutar comandos
arbitrarios del sistema operativo en las sucursales.

## 5. Estado técnico reutilizable del proyecto

La base actual ya contiene piezas compatibles con el diseño objetivo:

- `personas.models.Sucursal` usa UUID y una clave única.
- `Categoria`, `Producto` y `Precio` pertenecen a una sucursal.
- productos y categorías tienen indicadores de actividad.
- `Precio` tiene vigencia y permite preservar historial de precios.
- `Partida` conserva nombre y precio unitario como instantánea; un cambio futuro
  de catálogo no debe reescribir ventas históricas.
- `EventoOutbox` ofrece una base para publicar eventos locales al VPS, aunque aún
  no implementa el protocolo central definitivo.
- `PedidoSucursalImportado` demuestra una estrategia idempotente mediante una
  restricción única de sucursal, origen e identificador remoto.
- existe sincronización de pedidos confirmados desde `tcysPedidosSucursales`, con
  bloqueo local y control de intervalo. Esta integración no debe confundirse con
  la futura sincronización general de catálogo y ventas con el VPS.
- el respaldo SQLite actual usa la API de respaldo de SQLite, calcula SHA-256,
  revisa integridad y claves foráneas, y ensaya restauración.

La selección local de sucursal se realiza mediante `SUCURSAL_CLAVE`. Ya no
existe un valor predeterminado en producción: una instalación sin identidad
explícita y válida falla antes de aceptar pedidos.

También debe distinguirse `Sucursal`, que identifica el local operativo, de
`SucursalPedido`, que representa a las sucursales/clientes atendidos dentro del
módulo de pedidos mayoristas. No son identidades intercambiables.

### 5.1 Estado real frente al objetivo

| Área | Estado comprobado | Trabajo pendiente |
| --- | --- | --- |
| Servicio Windows local | Host pywin32, DLL, cuenta `LocalService`, arranque, ACL y comprobaciones implementados | Validar la release final en Windows limpio y en una segunda sucursal |
| Respaldo del Edge | Respaldo SQLite con integridad, hash y ensayo de restauración | Copia externa cifrada, alertas y política definitiva de retención |
| Identidad y catálogo | `SUCURSAL_CLAVE` obligatoria, aprovisionamiento explícito e idempotente, modelos por sucursal y precios con vigencia reutilizables | Enrolamiento autorizado por VPS y publicación de catálogos versionados |
| Pedidos desde `tcysPedidosSucursales` | Integración Supabase de sólo lectura e importación idempotente existente | Mantenerla operativamente separada del protocolo Edge-central |
| Sincronización de ventas | `EventoOutbox` proporciona una base local | Definir contrato, reintentos, acuses y construir el inbox central |
| Instalación y soporte | Instalación, adopción, actualización y reparación separadas; dependencias fijadas; `.env` preservado; respaldo, migración, servicio, firewall y salud disponibles | Validar en Windows limpio y añadir staging, cambio atómico, reanudación y rollback |
| Release del Edge | `VERSION` autoritativa, ZIP reproducible, manifiesto v2 para `cp313/win_amd64`, pins exactos y wheelhouse offline validados; actualización A→B acreditada en laboratorio | Firma digital, CI atestada e integración del ZIP con staging/actualizador |
| Módulos | Primera implementación de `Modulo`/`ModuloSucursal`, dependencias y defensa en UI/API/tareas dentro de la candidata B | Aceptar la lista funcional real, probar una segunda sucursal e integrar la candidata |
| Clientes | Launcher `.exe` que abre Edge en modo aplicación y PWA básica con manifiesto/service worker | `.exe` firmado/aceptado, WebView2 opcional y proyecto Android/APK inexistente |
| Backend central | Modelado y responsabilidades documentados | Implementar API, panel general, PostgreSQL central, autenticación y auditoría |
| Despliegue KVM 2 | Proveedor y tamaño inicial elegidos | Contratar, aprovisionar, endurecer, monitorear y probar recuperación/carga |

El `docker-compose.yml` y el `Dockerfile` actuales **no constituyen el
despliegue del VPS central**. El compose existente sirve como base local/de
desarrollo: incluye PostgreSQL, aplicación y un proceso de impresión, publica el
puerto 8000 y usa `entrypoint.sh`. Ese arranque ya exige clave y nombre de
sucursal, aprovisiona sólo esa identidad y nunca ejecuta la semilla histórica.
La carga manual de Arboledas exige que esa sea la única identidad activa y ya
aprovisionada, y tampoco crea usuarios ni PIN predeterminados.
El contexto de construcción excluye secretos y estado local, usa el lock exacto
y el worker de impresión espera la salud de la aplicación. Su configuración
vive en `.env.docker`, separada del `.env` del servicio Windows. Aun así, carece de
proxy HTTPS, respaldo externo y separación de responsabilidades central/Edge.

El futuro despliegue central deberá vivir en una configuración independiente
(por ejemplo, `deploy/vps/`), no incluir impresión local y no ejecutar ninguna
semilla fijada a Arboledas. Este límite evita que un agente futuro despliegue el
compose actual en KVM 2 suponiendo que el backend central ya existe.

## 6. Hallazgos de línea base y límites restantes

Este bloque nació de la auditoría previa a la separación del instalador. Los
apartados 6.1 a 6.4 se actualizaron para distinguir lo ya corregido de lo que
todavía impide considerar el despliegue maduro.

### 6.1 Operaciones separadas, actualización aún in-place

`instalar-servidor.ps1`, `aprovisionar-sucursal.ps1`,
`actualizar-servidor.ps1` y `reparar-permisos-servidor.ps1` ya exponen
responsabilidades distintas. El motor compartido conserva dos recorridos
explícitos: instalación y actualización. La actualización no cambia identidad,
red, secretos, catálogo, cuentas, firewall ni tareas programadas.

Sigue siendo una actualización supervisada sobre el mismo árbol y la misma
`.venv`: aún no extrae a staging, conmuta releases ni ejecuta rollback. Un
fallo después de modificar runtime o iniciar migraciones deja deliberadamente
el servicio detenido para revisión.

### 6.2 Inicialización fijada a Arboledas

`catalogo/management/commands/cargar_datos_iniciales.py`:

- exige que `ARBOLEDAS` sea la única sucursal activa y que ya esté aprovisionada;
- contiene el menú y precios dentro de código Python;
- incorpora temporalmente `datos/Listado-Productos.xlsx` para reproducir el
  traspaso inicial de nombres de Arboledas;
- actualiza o reactiva productos incluidos;
- puede desactivar categorías o productos ajenos a la lista;
- prepara posiciones y el catálogo de pedidos de sucursales;
- no crea usuarios ni PIN; los perfiles operativos se dan de alta por separado.

El instalador ya no llama incondicionalmente a este comando. Sólo puede invocarlo
durante una instalación de `ARBOLEDAS` mediante
`-InicializarDatosArboledas`; nunca forma parte de una actualización. El
comando sigue siendo una semilla histórica con efectos amplios y debe sustituirse
por publicaciones de catálogo antes de desplegar otras sucursales. Su ejecución
es ahora atómica: un fallo al abrir o importar el XLSX revierte también todo lo
creado previamente por el comando. Esto evita una base parcial, pero no convierte
el archivo histórico en una fuente de catálogo vigente.

La inclusión de ese XLSX en la release común es una medida transitoria de
migración, no el diseño objetivo. La fase de catálogo versionado debe retirarlo
del bundle de aplicación y entregar un paquete de datos independiente, validado y
autorizado para la sucursal destinataria.

### 6.3 Release reproducible, autenticidad pendiente

`requirements-lock.txt` admite únicamente pins exactos `nombre==versión` y debe
mantener activo `pywin32` para Windows. Una release productiva del Edge Windows
lleva obligatoriamente un wheelhouse offline plano cuya cobertura se prueba al
construir y al instalar para CPython 3.13 x64 (`cp313/win_amd64`). El formato de
manifiesto v2 declara ese target y valida metadatos `WHEEL`, hashes/tamaños de
`RECORD`, contenido y SHA-256. Un paquete `source-only` sirve para auditoría o
desarrollo controlado, no como entrega productiva a una sucursal.

El ZIP ya es reproducible y verificable, pero aún falta firma digital: sustituir
conjuntamente ZIP, manifiesto y sumas podría suplantar al publicador. La
resolución en línea sólo existe como excepción explícita de desarrollo y no es
el flujo de una entrega.

El paquete de `desktop/build-package.ps1` corresponde únicamente al cliente
ligero de Windows. Sus sumas SHA-256 detectan corrupción si el archivo de sumas
es confiable, pero no autentican por sí solas el origen; los ejecutables aún no
tienen firma digital.

### 6.4 Versionado del servidor iniciado, contrato operativo incompleto

`VERSION` ya es la fuente autoritativa de la release del servidor. El cliente
Windows y su bootstrap comparten actualmente la versión `0.3.0.0`; además, el
instalador del cliente registra la versión obtenida del ejecutable instalado y el
bootstrap devuelve el código de salida real del instalador. Esto evita una
inconsistencia puntual, pero todavía no constituye una versión coordinada de
todo el ecosistema, que debe abarcar:

- actualizador;
- cliente Windows;
- protocolo Edge-VPS;
- esquema de base;
- catálogo;
- configuración;
- módulos habilitados.

`/salud/` responde deliberadamente sólo `{"estado": "ok"}` y no permite al
actualizador confirmar versión, esquema, sucursal o preparación operativa. Debe
conservarse una sonda pública mínima y agregarse una comprobación local protegida
con información no secreta para mantenimiento.

### 6.5 Modelo de módulos provisional

La candidata B incorpora `Modulo` y `ModuloSucursal`, un catálogo de cuatro
módulos núcleo y cuatro opcionales, dependencias y un comando de configuración.
La interfaz oculta o desactiva capacidades no disponibles; las API vuelven a
validarlas y las tareas de fondo relacionadas no se ejecutan. Una migración desde
A habilita todos los módulos para conservar el comportamiento existente.

Esta pieza está implementada y probada en el commit candidato `67b43b3`, pero aún
no define por sí sola la base funcional real. Faltan aceptación con los módulos
definitivos, prueba limpia de B y futura autoridad del VPS sobre entitlements.

## 7. Componentes de la arquitectura objetivo

```text
                         HTTPS saliente
                +-----------------------------+
                |                             |
        +-------v-----------------------------+------+
        |             VPS CENTRAL                    |
        | Administrador General                      |
        | catálogo, precios, módulos y configuración |
        | consolidación de ventas y estado de Edge   |
        +----+--------------------+-------------------+
             |                    |
     cambios versionados   acuses/eventos/ventas
             |                    |
   +---------v---------+  +-------v-----------+       ...
   | Edge Sucursal A   |  | Edge Sucursal B   |
   | operación local   |  | operación local   |
   | DB/config/cache   |  | DB/config/cache   |
   +----+---------+----+  +----+----------+----+
        |         |            |          |
       PC       tablet        PC        tablet
                 LAN local; los clientes no hablan con el VPS
```

### 7.1 VPS central

Será autoridad de control para:

- registro e identidad de sucursales;
- catálogo maestro;
- asignación, orden y visibilidad de artículos por sucursal;
- precios y fechas de vigencia;
- módulos/capacidades habilitados;
- configuración distribuible no secreta;
- publicaciones y versiones;
- recepción idempotente de ventas consolidadas;
- inventario de versiones y salud de cada Edge;
- auditoría de quién publicó cada cambio.

El propietario del negocio, operando desde la sucursal matriz con rol de
Administrador General, será quien habilite, agregue o modifique los artículos de
los menús de cada sucursal. La ubicación física del usuario no cambia la
autoridad: las decisiones se guardan en el VPS y se distribuyen a los Edge.

#### 7.1.1 Plataforma inicial prevista

KVM 2 alojará un backend central independiente del POS local. La línea base
recomendada es:

- sistema operativo Linux LTS aún por seleccionar;
- proxy inverso con TLS y exposición pública exclusiva por HTTPS;
- Django y Gunicorn para panel y API versionada;
- PostgreSQL accesible sólo desde el propio servidor o una red privada;
- programador o worker sencillo para sincronización y tareas operativas;
- registros, métricas, alertas y respaldos externos.

Redis y Celery se introducirán únicamente si las métricas o una necesidad real de
colas distribuidas lo justifican. No forman parte obligatoria del primer
despliegue. También sigue pendiente decidir entre contenedores y servicios
`systemd`; cualquiera de los dos enfoques deberá quedar reproducible, versionado
y sin configuración manual oculta.

El VPS sólo ofrecerá el panel general y la API. Ningún Edge conectará directamente
a PostgreSQL y ninguna terminal de la sucursal se comunicará con el VPS para una
operación de venta normal.

#### 7.1.2 Acceso y mantenimiento

Hostinger KVM es un VPS autoadministrado: contratarlo no delega la operación del
sistema, PostgreSQL ni la aplicación. El acceso técnico seguirá este modelo:

- la cuenta Hostinger, MFA, códigos de recuperación y acceso de emergencia
  permanecerán bajo control del propietario del proyecto;
- SSH usará una llave dedicada cuya parte privada quedará fuera del repositorio;
- el aprovisionamiento se hará con un usuario no root y `sudo` temporal;
- el usuario habitual de despliegue tendrá sólo permisos para releases,
  migraciones, registros y reinicios necesarios;
- se verificará la huella del host antes del primer acceso;
- no se deshabilitarán root/contraseña hasta probar una segunda sesión por llave
  y conservar acceso de rescate desde hPanel;
- el firewall expondrá sólo 80/443 y restringirá SSH por IP o VPN cuando sea
  viable; PostgreSQL nunca se publicará a Internet;
- llaves y tokens se podrán revocar o rotar sin reinstalar el servidor.

Un agente Codex puede configurar, diagnosticar y mantener el VPS por SSH dentro
de tareas expresamente autorizadas, pero esto no equivale a vigilancia humana
permanente ni a soporte 24/7. Respaldos, renovación de certificados, health
checks, parches controlados y alertas deben ejecutarse automáticamente en el
servidor o en un monitor independiente. Toda intervención relevante deberá quedar
en un runbook y, cuando sea posible, en scripts declarativos sin secretos.

### 7.2 Edge o servidor local por sucursal

Será autoridad operativa para:

- pedidos abiertos y su concurrencia;
- cobros locales;
- impresión;
- clientes y posiciones locales según la política definitiva;
- continuidad durante una caída de Internet;
- cola de eventos y ventas pendientes de subir;
- aplicación transaccional del último catálogo/configuración válidos.

El Edge será el único componente local que se comunica con el VPS. Las
terminales no deben consultar el VPS para servir una mesa, cobrar o imprimir.

### 7.3 Clientes Windows y Android

Consumirán el Edge por LAN. No contienen la base central ni deben conocer las
credenciales Edge-VPS. Los cambios de menú y la mayoría de cambios de frontend
se reciben desde el Edge sin generar paquetes diferentes por dispositivo.

El cliente Windows actual es un launcher `.exe` que ejecuta Microsoft Edge con
`--app`; oculta la interfaz del navegador, pero todavía depende de `msedge.exe`.
Django ya expone una PWA básica para terminal/tableta mediante manifiesto y
service worker. No existe aún un proyecto Android ni un APK firmado. La ruta
recomendada y las implicaciones de WebView2/Capacitor están documentadas en
`FLUJO_DESARROLLO_MANTENIMIENTO_Y_CLIENTES.md`.

### 7.4 Matriz de autoridad

| Dominio | Autoridad | Réplica/consumidor |
| --- | --- | --- |
| Catálogo, precio, orden, imagen y activación | VPS/Administrador General | Edge de la sucursal |
| Módulos autorizados | VPS; selección inicial durante enrolamiento | Edge aplica y valida |
| Pedidos abiertos, cobros e impresión | Edge local | VPS recibe eventos consolidados |
| Ventas históricas consolidadas | Edge origina; VPS recibe idempotentemente | Ambos conservan instantáneas |
| Impresoras, IP y parámetros LAN | Configuración local | VPS puede mostrar estado, no imponer secretos |
| Código y compatibilidad | Release firmada | Actualizador local elevado |
| Secretos de instalación | Edge local | VPS conserva sólo la contraparte necesaria |

No deben existir dos autoridades que editen el mismo campo con una regla de
«última escritura gana». El VPS puede mantener plantillas globales, por grupos y
por sucursal, pero debe entregar al Edge un resultado final determinista; el Edge
no debería resolver una jerarquía compleja de excepciones.

## 8. Separación entre código, módulos y catálogo

| Cambio | Naturaleza | Mecanismo esperado |
| --- | --- | --- |
| Nombre, imagen, precio, orden o visibilidad de producto | Dato | Publicación de catálogo |
| Producto disponible sólo en ciertas sucursales | Dato por sucursal | Asignación central |
| Domicilios, programados, reparto o pedidos entre sucursales | Capacidad | `ModuloSucursal` |
| Corrección, lógica nueva o cambio de esquema | Código | Release del servidor |
| Cambio del lanzador o integración Windows | Código cliente | Release del cliente |
| IP de impresora o red local | Configuración local | Aprovisionamiento/mantenimiento |
| Secretos y credenciales | Configuración sensible | Almacén local protegido |

El paquete común puede instalar todas las tablas y lógica soportadas. Activar un
módulo significa permitir funcionalidad y configurarla, no copiar código distinto.
Esto mantiene migraciones homogéneas y simplifica pruebas y soporte.

Una capacidad deshabilitada debe:

- ocultarse o marcarse como no disponible en la interfaz;
- rechazarse también en el servidor si se llama directamente a su API;
- no ejecutar tareas de fondo relacionadas;
- conservar sus datos existentes sin borrarlos;
- poder reactivarse sin reinstalar, si la versión local la soporta.

El VPS no podrá habilitar una capacidad si el Edge no anuncia la versión mínima
requerida o si faltan parámetros obligatorios. La respuesta deberá indicar
`actualizacion_requerida` o `configuracion_incompleta`, sin aplicar un estado
parcial.

## 9. Modelo conceptual recomendado

Los nombres exactos podrán cambiar durante la implementación, pero conviene
modelar explícitamente:

### 9.1 En el VPS

- `Sucursal`: identidad de negocio y zona horaria.
- `InstalacionEdge`: identificador de instalación, clave pública/credencial,
  versión, canal y última comunicación.
- `Modulo`: capacidad conocida, dependencias y versión mínima.
- `ModuloSucursal`: estado deseado y configuración por sucursal.
- `ProductoMaestro`: definición común del artículo.
- `ProductoSucursal`: asignación, alias, orden, visibilidad e impresión.
- `PrecioSucursal`: importe, vigencia y moneda.
- `PublicacionCatalogo`: versión inmutable, fecha efectiva, autor y estado.
- `CambioCatalogo`: operaciones incluidas o instantánea canónica.
- `AcuseEdge`: versión descargada, validada y aplicada por instalación.
- `EventoInbox` o `VentaInbox`: recepción idempotente desde sucursales.
- `CanalDespliegue`: desarrollo, piloto, estable o corrección urgente.

### 9.2 En el Edge

- identidad inmutable de instalación y sucursal;
- `catalog_version`, `config_version` y `modules_version` aplicados;
- instantánea local activa y, opcionalmente, última instantánea anterior;
- cursor de sincronización y fecha/hora del último éxito;
- cola outbox de ventas/eventos pendientes;
- registro de intentos y errores depurados;
- capacidades efectivas ya validadas contra la versión local;
- estado de mantenimiento y actualización.

No debe usarse únicamente el nombre visible de la sucursal como identidad. El
modelo ya dispone de UUID; el aprovisionamiento debe vincular un UUID central con
un identificador único de instalación.

## 10. Flujo presencial de instalación por sucursal

La instalación inicial será realizada presencialmente por una persona autorizada.
El instalador debe ser firmado y ejecutarse como administrador de Windows.

### 10.1 Principio sobre los módulos

El instalador podrá preguntar qué módulos se desean **habilitar inicialmente**,
pero no debe producir una instalación física diferente por módulo. El código
común se instala completo y la selección genera la configuración inicial de
`ModuloSucursal`.

Cuando se defina el conjunto mínimo:

- los módulos núcleo aparecerán seleccionados y no podrán desmarcarse;
- las dependencias se seleccionarán automáticamente;
- los módulos opcionales exigirán su configuración mínima;
- el VPS conservará la decisión autorizada y podrá modificarla después;
- una selección no compatible con la versión instalada será rechazada antes de
  activar el POS.

### 10.2 Secuencia propuesta

1. Verificar firma, integridad, versión de Windows, espacio y requisitos.
2. Solicitar elevación una sola vez para la fase protegida.
3. Identificar la sucursal:
   - preferentemente mediante código de enrolamiento de un solo uso generado por
     el Administrador General;
   - alternativamente mediante un archivo de aprovisionamiento firmado para una
     instalación sin Internet;
   - nunca aceptar silenciosamente `ARBOLEDAS` como valor predeterminado.
4. Confirmar nombre, UUID y ubicación antes de escribir datos.
5. Registrar una `InstalacionEdge` única y generar sus credenciales localmente.
6. Elegir los módulos iniciales y validar dependencias.
7. Capturar configuración local:
   - dirección/host del Edge;
   - HTTP temporal o HTTPS;
   - puerto y perfil de red;
   - impresoras de caja, cocina y barra;
   - horario y retención de respaldos;
   - zona horaria y fecha del equipo;
   - fuente de pedidos de sucursales, cuando corresponda.
8. Crear secretos y cuentas iniciales sin valores predecibles.
9. Instalar código, runtime y dependencias fijadas.
10. Crear una base vacía, ejecutar migraciones y aprovisionar únicamente los
    datos de la sucursal seleccionada.
11. Descargar o importar la primera publicación de catálogo correspondiente.
12. Crear y verificar el respaldo pre-migración cuando ya exista SQLite;
    registrar servicio, firewall y ACL, y registrar la tarea diaria salvo que se
    haya omitido expresamente.
13. Iniciar y validar salud, versión, base, catálogo, impresión no física y
    conectividad LAN.
14. Realizar una prueba física local de cada impresora necesaria.
15. Generar un informe de instalación sin secretos, con versiones, sucursal,
    módulos, respaldos y resultados.

El respaldo verificable anterior a una migración es obligatorio. La tarea
diaria es recomendable y predeterminada, pero operativamente opcional: omitirla
no debe omitir el respaldo de la ventana de mantenimiento ni ocultar esa decisión
en el informe.

### 10.3 Protección contra errores de identidad

- Un código de enrolamiento debe caducar y consumirse una sola vez.
- El VPS debe impedir dos instalaciones activas con la misma identidad salvo un
  flujo explícito de reemplazo/recuperación.
- Un paquete de catálogo debe declarar el UUID de destino o el grupo autorizado.
- El Edge debe rechazar una respuesta destinada a otra sucursal.
- Cambiar de sucursal una instalación existente no será una edición ordinaria;
  requerirá desaprovisionar o reinstalar de manera controlada.

## 11. Sincronización al iniciar cada día

### 11.1 Requisito acordado

Al arrancar el programa cada día, el sistema local consultará al VPS para conocer
y aplicar cambios decididos por el Administrador General, especialmente:

- altas, bajas y modificaciones de artículos;
- precios y fechas de vigencia;
- orden y visibilidad del menú por sucursal;
- módulos habilitados;
- configuración distribuible;
- versión mínima o recomendada del servidor local.

La consulta diaria es un requisito mínimo. Técnicamente debe ejecutarla el Edge,
no cada terminal. Debe protegerse con un bloqueo para que varias computadoras que
abran el POS a la vez no inicien sincronizaciones duplicadas.

### 11.2 Momento de ejecución

Se propone disparar la comprobación cuando ocurra el primero de estos eventos en
cada fecha operativa local:

- arranque del servicio Edge;
- tarea programada poco antes del horario normal de apertura;
- primer acceso al POS del día;
- acción manual «Buscar cambios» de un administrador autorizado.

El Edge registrará que ya completó la comprobación para esa fecha. Si falló por
conectividad, reintentará con espera creciente. Como mejora posterior puede hacer
comprobaciones periódicas mientras esté abierto; esta frecuencia y la política de
cambios a mitad de turno quedan pendientes de decisión.

### 11.3 Regla esencial de disponibilidad

La imposibilidad de contactar al VPS **no debe impedir abrir el negocio**. El Edge
continuará con la última configuración válida, mostrará un aviso administrativo
no bloqueante, registrará el fallo y reintentará. Nunca se vaciará el menú por una
respuesta incompleta, un timeout o un error de autenticación.

Se podrá definir más adelante una política explícita para instalaciones revocadas,
pero una falla ordinaria de Internet no debe interpretarse como revocación.

### 11.4 Intercambio propuesto

El Edge enviará, sin secretos en logs:

```json
{
  "installation_id": "uuid",
  "branch_id": "uuid",
  "server_version": "1.4.2",
  "updater_version": "1.1.0",
  "protocol_version": 1,
  "catalog_version": 175,
  "config_version": 31,
  "modules_version": 8,
  "last_success_at": "2026-09-08T12:00:00Z"
}
```

El VPS responderá con el estado deseado y con cambios incrementales o una
instantánea completa cuando el cursor sea demasiado antiguo:

```json
{
  "branch_id": "uuid",
  "protocol_version": 1,
  "server_time": "2026-09-08T12:00:01Z",
  "catalog": {
    "from": 175,
    "to": 176,
    "effective_at": "2026-09-09T06:00:00-06:00",
    "sha256": "...",
    "changes": []
  },
  "config_version": 31,
  "modules_version": 8,
  "minimum_server_version": "1.4.0"
}
```

Los endpoints y nombres son ilustrativos; deberán vivir bajo una API versionada,
por ejemplo `/api/v1/edge/sync`.

### 11.5 Aplicación local de cambios

1. Autenticar el VPS y validar TLS.
2. Comprobar que `branch_id` coincide con la instalación.
3. Validar versión de protocolo, versión mínima, esquema, firma/hash y estructura.
4. Descargar todos los datos antes de alterar el catálogo activo.
5. Aplicar los cambios dentro de una transacción de base de datos.
6. Verificar referencias, productos, categorías, precios, módulos y restricciones.
7. Activar la nueva versión sólo si la transacción completa fue válida.
8. Conservar la versión anterior o información suficiente para revertir la
   publicación de catálogo.
9. Enviar un acuse con versión aplicada, checksum y resultado.
10. Actualizar `last_success_at` sólo después del acuse local duradero.

Repetir la misma publicación debe producir el mismo estado. Una versión antigua
o fuera de orden debe rechazarse o provocar la descarga de una instantánea
canónica; nunca debe deshacer silenciosamente una publicación más nueva.

### 11.6 Precios y pedidos existentes

Las partidas ya guardan nombre y precio como instantánea. La regla recomendada es:

- una partida existente conserva el precio con el que fue agregada;
- una publicación nueva afecta productos agregados después de su fecha efectiva;
- las ventas históricas nunca se recalculan por un cambio de catálogo;
- preferentemente los precios se publican con vigencia al inicio del siguiente
  día operativo para reducir ambigüedad.

El Edge podrá descargar por anticipado una publicación con vigencia futura y
activarla localmente al llegar la fecha operativa, aunque ese día no tenga
Internet. Si nunca pudo descargarla, conservará el precio anterior y reportará
claramente el atraso; no inventará ni vaciará precios.

Queda por confirmar si una cuenta todavía abierta debe usar el precio anterior
también para partidas nuevas del mismo producto. Hasta definirlo, el diseño no
debe asumir que todas las cuentas abiertas pueden recalcularse.

## 12. Administración central del catálogo

El Administrador General debe poder:

- crear un producto maestro;
- asignarlo a una, varias o todas las sucursales;
- establecer nombre visible y nombre de ticket;
- asignar categoría, orden, imagen y destino de impresión;
- definir precio y vigencia por sucursal;
- activar o desactivar un producto por sucursal;
- preparar cambios sin publicarlos inmediatamente;
- revisar una vista previa/diferencias;
- programar la fecha efectiva;
- publicar una versión inmutable;
- consultar qué Edge ya la aplicó;
- revertir mediante una nueva publicación, nunca reescribiendo auditoría.

La autoridad central evita que menús distintos deriven por ediciones manuales no
controladas. De manera predeterminada, los campos gobernados por el VPS no deben
ser editables permanentemente en el administrador local.

Queda pendiente definir si un encargado local podrá marcar temporalmente un
artículo como agotado. Si se permite, deberá ser un estado local temporal,
separado de la activación central, con autor, vencimiento y reglas claras de
sincronización.

## 13. Subida y consolidación de ventas

El VPS futuro consolidará las ventas de todas las sucursales. La operación local
seguirá siendo autoritativa durante el turno.

No se recomienda depender exclusivamente de una carga masiva al finalizar el día.
La estrategia más resistente es:

- registrar cada venta/evento confirmado en un outbox local dentro de la misma
  transacción de negocio;
- enviarlo al VPS cuando haya Internet;
- reintentar hasta recibir acuse;
- deduplicar en el VPS por identificador global e instalación de origen;
- ejecutar al cierre una conciliación del día;
- al arranque siguiente, subir cualquier evento pendiente del día anterior.

La descarga de catálogo y la subida de ventas son flujos independientes. Si uno
falla, el otro podrá continuar. Un fallo del VPS nunca debe revertir un cobro local
ya confirmado ni crear ventas duplicadas.

El VPS debe conservar un inbox idempotente. El Edge no marcará un evento como
publicado hasta recibir confirmación duradera. Los importes, nombres, impuestos y
otros datos históricos se enviarán como instantáneas de la venta, no se
reconstruirán desde el catálogo actual.

La conciliación diaria debería incluir como mínimo fecha operativa, corte local,
cantidad de tickets, primer y último folio, totales por forma de pago,
cancelaciones, devoluciones, total general y cantidad/hash de eventos. Una
discrepancia no debe sobrescribir silenciosamente ventas: se marca para revisión
y, si procede, se corrige mediante un evento compensatorio auditable.

## 14. Módulos por sucursal

### 14.1 Ejemplos iniciales

La lista mínima se definirá posteriormente. Como inventario preliminar pueden
existir capacidades como:

- núcleo POS y comedor;
- recoger;
- llevar;
- domicilios y repartidores;
- pedidos programados;
- pedidos entre sucursales;
- movimientos de caja;
- reportes y corte;
- administración de personal;
- integración con pedidos externos;
- sincronización central.

Esta lista no constituye todavía una decisión comercial ni funcional.

### 14.2 Dependencias y estado

Cada módulo debe declarar:

- clave estable;
- nombre visible;
- versión mínima del servidor;
- módulos requeridos;
- configuración obligatoria;
- tareas de fondo asociadas;
- endpoints y permisos que protege.

Estados recomendados:

- `disabled`: no disponible;
- `pending_configuration`: elegido pero incompleto;
- `enabled`: configurado y autorizado;
- `update_required`: el VPS lo permite, pero el Edge es demasiado antiguo;
- `suspended`: desactivado centralmente conservando datos.

El módulo núcleo, identidad de sucursal, autenticación, respaldo y salud no deben
ser opcionales.

## 15. Instalador, aprovisionador y actualizador separados

### 15.1 `Instalar-Servidor`

Responsabilidad exclusiva de primera instalación o recuperación total:

- instalar runtime y código;
- crear directorios protegidos;
- registrar servicio, firewall, ACL y respaldo pre-migración obligatorio;
- registrar por defecto la tarea diaria de respaldo, permitiendo su omisión
  explícita cuando exista otro mecanismo operativo aprobado;
- enrolar la sucursal;
- preparar una base nueva;
- crear cuentas iniciales;
- aplicar el primer catálogo explícito.

### 15.2 `Aprovisionar-Sucursal`

Responsabilidad de identidad y configuración de negocio:

- vincular instalación con sucursal;
- establecer selección inicial de módulos;
- aplicar configuración de impresoras y horarios;
- importar o descargar la publicación inicial;
- validar preparación operativa.

Debe poder existir como fase guiada del instalador inicial, pero su lógica no debe
estar mezclada con la copia del código ni con futuras migraciones.

### 15.3 `Actualizar-Servidor`

Responsabilidad exclusiva de cambiar una versión ya instalada:

1. consultar o recibir un manifiesto firmado;
2. verificar firma, hash, tamaño, origen, compatibilidad y espacio;
3. preparar la versión nueva en `staging` mientras la anterior funciona;
4. ejecutar pruebas aisladas y revisar el plan de migraciones;
5. entrar en mantenimiento y detener el servicio sólo en la ventana final;
6. crear y verificar un respaldo `pre-update` de base y configuración;
7. ejecutar únicamente las migraciones de la release;
8. recopilar estáticos dentro de la versión nueva;
9. aplicar y verificar ACL;
10. cambiar atómicamente el servicio a la nueva versión;
11. arrancar y validar versión, DB, migraciones, sucursal, worker de impresión y
    recursos web;
12. confirmar la versión o regresar a la anterior.

Este proceso **nunca** ejecutará `cargar_datos_iniciales` ni modificará catálogo
como efecto secundario.

### 15.4 Rollback

- Si la migración fue aditiva y compatible: volver al código anterior.
- Si el esquema dejó de ser compatible: detener y restaurar código, base y
  configuración desde el punto `pre-update`.
- Nunca restaurar automáticamente una base después de reabrir ventas, porque se
  perderían operaciones nuevas.
- Las migraciones futuras deben preferir la secuencia expandir, desplegar código
  compatible, transformar y contraer en una versión posterior.

## 16. Distribución reproducible del servidor

### 16.1 Estructura objetivo en Windows

```text
C:\Program Files\LosTocayosPOS\
  updater\
  releases\1.4.0\
  releases\1.4.1\

C:\ProgramData\LosTocayosPOS\
  config\
  data\
  media\
  logs\
  backups\
  staging\
  update-state.json
```

- `Program Files`: código/runtime inmutables, modificables sólo por el proceso
  elevado de instalación o actualización.
- `ProgramData`: estado operativo persistente y respaldable.
- `LocalService`: lectura del código y modificación sólo de datos operativos.
- el servicio POS no podrá sustituir su propio ejecutable;
- un actualizador separado verificará paquetes antes de elevar/aplicar.

### 16.2 Contenido de una release

- versión única y commit de origen;
- código aprobado;
- migraciones;
- estáticos generados con nombres versionados/hash;
- runtime certificado o requisito exacto de Python 3.13 (mayor/menor);
- dependencias con pins exactos `nombre==versión` y wheelhouse offline obligatorio
  para una release productiva del Edge Windows;
- manifiesto v2 que declare `implementation=cp`, Python 3.13, ABI `cp313`,
  plataforma `win_amd64` y arquitectura de 64 bits;
- SHA-256;
- firma digital/Authenticode;
- compatibilidad de protocolo y esquema;
- acciones cerradas de migración y verificaciones posteriores.

No incluir:

- `.env` de ninguna sucursal;
- bases o respaldos;
- certificados privados;
- tokens de enrolamiento;
- logs;
- imágenes privadas o datos operativos.

### 16.3 Versiones mínimas a registrar

- `server_version`;
- `desktop_client_version`;
- `updater_version`;
- `protocol_version`;
- `database_schema_version`;
- `catalog_version`;
- `config_version`;
- `modules_version`.

El endpoint protegido de preparación operativa y el informe de soporte deben
mostrar estas versiones sin revelar secretos.

## 17. Seguridad del canal Edge-VPS

- Todo tráfico deberá usar HTTPS con validación de certificado.
- Cada instalación tendrá una credencial distinta y revocable.
- Es preferible generar una pareja de claves localmente y registrar sólo la clave
  pública; mTLS puede adoptarse cuando la operación lo justifique.
- Un token de enrolamiento no será una credencial permanente.
- No se copiarán `.env`, bases ni credenciales entre sucursales.
- El Edge iniciará conexiones salientes; no será necesario exponer cada sucursal
  públicamente ni abrir acceso administrativo entrante.
- Los manifiestos de código y publicaciones de catálogo se autenticarán; un hash
  descargado junto al archivo no sustituye una firma.
- El Administrador General necesitará roles, MFA cuando esté disponible y
  bitácora de altas, cambios, publicaciones y reversión.
- Los logs enviados a soporte no contendrán secretos, contraseñas, tokens,
  certificados privados ni registros completos de clientes.
- La consolidación minimizará los datos personales de clientes a domicilio y
  sólo subirá al VPS los campos necesarios para el propósito autorizado.

## 18. Respaldo y recuperación

El respaldo SQLite actual es una buena base para recuperación rápida, pero antes
del uso real debe ampliarse con:

- copia cifrada fuera del equipo o disco local;
- alerta central si el último respaldo es antiguo o falló;
- respaldo separado de configuración, certificados públicos necesarios y media;
- simulacros periódicos de restauración;
- asociación del respaldo `pre-update` con la versión de código y esquema;
- política específica si se adopta PostgreSQL en el Edge.

El VPS también necesitará respaldo, retención y restauración propios. Consolidar
ventas en el VPS no elimina la necesidad de respaldar la sucursal, ni el respaldo
local sustituye al central.

Hostinger incluye respaldo automático semanal y ofrece respaldo diario como
complemento. Sus snapshots son puntos de recuperación temporales y no pueden ser
la única copia de la base. La política central deberá incluir, como mínimo:

- `pg_dump` diario consistente, cifrado y almacenado fuera del mismo VPS y de
  la misma cuenta de fallo;
- respaldo de configuración reproducible y de los archivos no regenerables;
- snapshot inmediatamente antes de un despliegue o cambio de infraestructura;
- verificación automatizada de que cada copia terminó y puede leerse;
- prueba real de restauración al menos trimestral;
- procedimiento para levantar otro VPS y reasignar DNS;
- versión anterior disponible para rollback de aplicación.

Como punto de partida sujeto a aprobación se propone conservar 7 copias diarias,
4 semanales y 3 mensuales. Aún deben definirse RPO, RTO, proveedor de almacenamiento,
ubicación geográfica, responsable, cifrado y presupuesto. La guía del proveedor
consultada está en [respaldos y snapshots de Hostinger
VPS](https://www.hostinger.com/support/1583232-how-to-back-up-or-restore-a-vps-at-hostinger/).

## 19. Manejo de fallos esperado

| Situación | Comportamiento seguro |
| --- | --- |
| VPS no disponible al abrir | Continuar con última configuración válida y reintentar |
| Respuesta incompleta o checksum inválido | No aplicar nada; conservar versión anterior |
| Publicación repetida | Resultado idempotente, sin duplicados |
| Publicación antigua/fuera de orden | Rechazar o solicitar instantánea actual |
| Respuesta para otra sucursal | Rechazar y generar alerta |
| Módulo requiere versión más nueva | No activar; reportar actualización requerida |
| Varias terminales abren simultáneamente | Un solo Edge sincroniza mediante lock |
| Cambio de precio con venta histórica | Conservar instantánea de la partida |
| Fallo al subir una venta | Mantener en outbox y reintentar |
| Acuse perdido | Reenvío idempotente, sin duplicar en VPS |
| Fallo durante actualización de código | Mantener mantenimiento y ejecutar rollback probado |
| Internet vuelve después de varios días | Reconciliar cursores o descargar instantánea completa |

## 20. Despliegue progresivo y soporte periódico

Canales recomendados:

- `development`: desarrollo y pruebas internas;
- `pilot`: una instalación controlada;
- `stable`: despliegue progresivo al resto;
- `hotfix`: corrección urgente derivada de la versión estable.

Debe promoverse exactamente el mismo artefacto entre canales, no recompilarlo.
Cada Edge reportará versión, canal, último respaldo, último catálogo aplicado y
última comunicación. Una actualización de código podrá descargarse con
anticipación, pero su instalación se hará en una ventana de mantenimiento. El
flujo de trabajo, la política de versiones y el ejemplo de una función exclusiva
de una sucursal están en `FLUJO_DESARROLLO_MANTENIMIENTO_Y_CLIENTES.md`.

Al principio se recomienda actualización presencial o remota supervisada. No se
automatizará una instalación desatendida hasta haber probado repetidamente:

- instalación limpia;
- actualización N a N+1;
- interrupción intencional;
- reanudación o rollback;
- restauración;
- compatibilidad offline;
- despliegue piloto y promoción.

## 21. Plan de implementación propuesto

### Fase 0. Consolidar el estado actual

- terminar y verificar los cambios de producción simulada;
- revisar el árbol de trabajo y no perder modificaciones existentes;
- incluir todos los nuevos archivos de servicio, validación, migraciones y pruebas;
- ejecutar suites completas;
- crear una versión autoritativa y una release reproducible;
- documentar que el catálogo histórico sigue siendo exclusivo de Arboledas y no
  forma parte del aprovisionamiento genérico.

### Fase 1. Separar instalación y actualización

- hacer `SUCURSAL_CLAVE` obligatoria en producción;
- reemplazar el valor inicial de Arboledas por aprovisionamiento explícito;
- retirar `cargar_datos_iniciales` de la actualización;
- dividir instalación, aprovisionamiento, configuración y actualización;
- eliminar PIN inicial predecible;
- fijar dependencias y preparar paquete completo del servidor;
- agregar versión/estado de instalación y verificación operativa.

**Estado al 15 de septiembre de 2026:** la fase local separada quedó probada
mediante instalación limpia A y actualización A→B en este equipo. La actualización
conservó `.env`, identidad, catálogo y cuentas; aplicó la migración, respondió
saludable y terminó sin desviaciones ACL. Una auditoría posterior confirmó un
perfil POS y una cuenta operativa activos; la aceptación manual de ingreso/PIN
sigue pendiente. El ZIP B fue reproducible, pero el staging se orquestó externamente: firma, extracción integrada, conmutación,
diario reanudable y rollback siguen deliberadamente en la fase 5. También faltan
Windows limpio, segunda sucursal y aceptación funcional para declarar una base.

### Fase 2. Catálogo local versionado

- definir publicación, versión, checksum y fecha efectiva;
- convertir la carga inicial a un paquete de datos explícito por sucursal;
- implementar vista previa y modo ensayo;
- aplicar publicaciones de forma transaccional e idempotente;
- conservar instantáneas históricas de ventas;
- probar actualización y reversión del menú sin tocar código.

Esta fase puede construirse inicialmente con archivos firmados, antes de que
exista el VPS. Así se valida el modelo de datos y el aplicador local.

### Fase 3. Módulos y aprovisionamiento

- definir módulos núcleo y opcionales;
- crear `Modulo`/`ModuloSucursal` y dependencias;
- proteger interfaz, API y tareas de fondo;
- implementar enrolamiento de instalación;
- crear asistente presencial de impresoras, red, respaldo y módulos;
- generar informe de instalación.

**Estado provisional:** la candidata B cubre el catálogo local de módulos,
dependencias, selección inicial y defensa en UI/API/tareas. Faltan aceptación de
la lista definitiva, enrolamiento autorizado, prueba en otra sucursal e informe
canónico de instalación.

### Fase 4. VPS y Administrador General

- contratar y aprovisionar Hostinger KVM 2 cuando las fases locales previas estén
  listas para una prueba integral;
- crear un despliegue central separado del `docker-compose.yml` local;
- configurar dominio, DNS, TLS, firewall, acceso SSH limitado y PostgreSQL interno;
- implementar identidad de Edge y API versionada;
- construir catálogo maestro y asignaciones por sucursal;
- preparar, revisar, programar y publicar cambios;
- implementar consulta diaria, acuses y estado por sucursal;
- implementar inbox idempotente y consolidación de ventas;
- agregar auditoría y roles del Administrador General;
- activar respaldos externos, alertas y procedimiento de restauración;
- ejecutar pruebas de carga, caída, recuperación y operación offline antes de
  enrolar todas las sucursales.

### Fase 5. Actualizador transaccional

- adoptar `Program Files`/`ProgramData` y releases por versión;
- firmar artefactos y manifiestos;
- preparar en staging antes de detener;
- evolucionar el mutex local ya implementado hacia un estado de mantenimiento
  persistente y un diario reanudable;
- implementar cambio atómico y rollback;
- desplegar por anillos piloto/estable.

### Fase 6. Operación y recuperación maduras

- copias externas cifradas;
- alertas de salud, respaldo y sincronización;
- simulacros de restauración;
- rotación de secretos y certificados;
- procedimiento de reemplazo de Edge;
- métricas de despliegue y soporte remoto controlado.

## 22. Pruebas de aceptación necesarias

### 22.1 Instalación

- Windows limpio sin sesión previa del desarrollador.
- Python/runtime ausente, presente y versión incorrecta.
- instalación de Arboledas y de una segunda sucursal distinta.
- código de enrolamiento correcto, vencido, usado y perteneciente a otra sucursal.
- selección de módulos y validación de dependencias.
- impresora disponible y no disponible.
- HTTP LAN temporal y HTTPS.
- reinicio completo de Windows y arranque automático.
- ACL reales de archivos y carpetas.

### 22.2 Catálogo y sincronización

- sin cambios desde la última versión;
- un cambio de precio con vigencia futura;
- alta, baja, reordenamiento e imagen;
- publicación repetida;
- versión omitida o fuera de orden;
- conexión interrumpida durante descarga;
- respuesta dañada, no firmada o para otra sucursal;
- VPS sin conexión durante varios días;
- dos terminales iniciando simultáneamente;
- producto cambiado mientras existe una cuenta abierta;
- Edge demasiado antiguo para el cambio;
- módulo deshabilitado llamado directamente por API.

### 22.3 Ventas y consolidación

- venta enviada una vez;
- reenvío después de perder el acuse;
- varias ventas offline y posterior reconexión;
- cierre con eventos pendientes;
- conciliación del día;
- restauración local sin duplicar ventas ya recibidas en VPS;
- desfase de reloj y zona horaria.

### 22.4 Actualización

- actualización que no cambia esquema;
- migración aditiva;
- fallo antes y después del respaldo;
- fallo al arrancar la nueva versión;
- rollback compatible;
- restauración pre-update cuando sea imprescindible;
- conservación de `.env`, sucursal, usuarios, catálogo, imágenes y ventas;
- navegador sin recursos estáticos mezclados;
- servicio, worker de impresión, firewall, respaldo previo, tarea diaria cuando
  esté configurada y salud finales.

## 23. Decisiones explícitamente pendientes

Antes de implementar deberán confirmarse:

1. Lista definitiva y UUID de sucursales.
2. Qué módulos forman el mínimo obligatorio.
3. Qué módulos son opcionales y sus dependencias.
4. Si la selección presencial necesita aprobación posterior del Administrador
   General o queda aprobada por el propio enrolamiento.
5. Si un encargado local puede marcar temporalmente productos agotados.
6. Política exacta de precio para partidas nuevas dentro de una cuenta ya abierta.
7. Hora que define el inicio del día operativo por sucursal.
8. Frecuencia de sincronización adicional después de la consulta inicial diaria.
9. Si los cambios urgentes pueden entrar durante el turno o sólo al día siguiente.
10. Dominio, DNS, región, sistema operativo, contenedores frente a `systemd` y
    autenticación Edge-VPS. El proveedor y tamaño inicial ya están decididos:
    Hostinger KVM 2.
11. Disponibilidad y calidad real de Internet en cada sucursal.
12. Si SQLite seguirá siendo la base del Edge durante la primera expansión o se
    migrará a PostgreSQL local.
13. Política de actualización: presencial, remota supervisada o automática.
14. Ventanas de mantenimiento y responsables de autorización.
15. Retención local/central y ubicación de copias externas.
16. Política de sustitución de un equipo Edge averiado.
17. Roles y permisos exactos del Administrador General.
18. RPO y RTO aceptables para Edge y servidor central.
19. Proveedor, cifrado, credenciales y presupuesto del respaldo fuera del VPS.
20. Canal de alertas y responsables ante fallo de respaldo, disco, TLS o
    sincronización.
21. Alcance mínimo de datos personales de clientes que se consolidará.
22. Fecha de compra y aprovisionamiento de KVM 2; no contratarlo antes de que una
    fase del plan pueda aprovecharlo.
23. Umbrales de CPU, RAM, disco, latencia y cola que justificarán subir de plan.
24. Cuenta de emergencia, rotación de llaves SSH y política de revocación al
    terminar cada intervención.

## 24. Reglas que un agente futuro no debe infringir

- No ejecutar el instalador actual sobre otra sucursal asumiendo que basta cambiar
  IP o `SUCURSAL_CLAVE`.
- No usar `cargar_datos_iniciales` como actualización de catálogo.
- No invocar ni distribuir sólo `instalar-servicio-lan.ps1`: es el motor interno.
  El operador debe usar los wrappers y entregar el resto de código,
  dependencias, herramientas, migraciones y pruebas compatibles.
- No desplegar `docker-compose.yml`/`entrypoint.sh` actuales como backend
  central: contienen responsabilidades Edge locales, incluido el proceso de
  impresión; la semilla de Arboledas es opt-in, pero tampoco pertenece al VPS.
- No copiar `.env`, bases, respaldos ni certificados privados entre sucursales.
- No publicar PostgreSQL en Internet ni permitir que un Edge se conecte
  directamente a la base central; todo intercambio pasa por la API HTTPS.
- No usar `git pull` como mecanismo de actualización de producción.
- No ampliar `LocalService` a administrador ni conceder control total a Users o
  Everyone para ocultar un error de permisos.
- No restaurar una base automáticamente después de reabrir ventas.
- No habilitar módulos únicamente ocultando navegación; validar también servidor.
- No bloquear la apertura de la sucursal por una caída ordinaria del VPS.
- No borrar información de un módulo al deshabilitarlo.
- No aplicar publicaciones parciales ni sin versión.
- No modificar ventas históricas por cambios de catálogo.
- No desplegar una release simultáneamente a todas las sucursales sin piloto.
- No considerar los snapshots o respaldos semanales de Hostinger como única
  recuperación; mantener copias consistentes y cifradas fuera del VPS.
- No pegar llaves privadas SSH, contraseñas, tokens ni códigos de recuperación en
  el repositorio, documentos o conversaciones.
- No asumir que el árbol de trabajo actual está limpio; preservar los cambios del
  usuario y revisar su estado antes de editar.
- No ejecutar comandos destructivos ni reiniciar datos una vez iniciada la
  operación real sin autorización específica y respaldo verificado.

## 25. Archivos relevantes para retomar el trabajo

- `instalar-servidor.ps1`, `aprovisionar-sucursal.ps1`,
  `actualizar-servidor.ps1` y `reparar-permisos-servidor.ps1`: puntos de entrada
  admitidos para el operador.
- `instalar-servicio-lan.ps1`: motor interno compartido de instalación y
  actualización; no se invoca directamente.
- `servicio_windows.py`: host del servicio, Waitress y worker de impresión.
- `herramientas/host_servicio_windows.py`: preparación/validación del host nativo.
- `herramientas/validar_despliegue.py`: suite aislada previa al despliegue.
- `verificar-servicio-lan.ps1`: auditoría de la instalación real.
- `herramientas/respaldo_sqlite.py`: respaldo verificable y restauración de prueba.
- `tests/test_instalador_windows.ps1`: pruebas de preflight y ACL.
- `DESPLIEGUE_WINDOWS.md`: procedimiento y límites del despliegue vigente.
- `catalogo/management/commands/cargar_datos_iniciales.py`: semilla fija de
  Arboledas que debe separarse del actualizador.
- `catalogo/models.py`: catálogo/precios por sucursal.
- `personas/models.py`: identidad de sucursal, roles y usuarios POS.
- `ventas/models.py`: operación, instantáneas, outbox e importación idempotente.
- `ventas/integracion_sucursales.py`: integración existente con pedidos externos.
- `pos/settings.py` y `.env.example`: identidad, impresión y conectividad actuales.
- `desktop/README.md` y `desktop/build-package.ps1`: cliente ligero Windows.
- `propuesta_arquitectura_pos_multisucursal.md`: diseño distribuido general.
- `docker-compose.yml`, `Dockerfile` y `entrypoint.sh`: base Docker local que
  no debe confundirse con el despliegue central.
- `seguridad/supabase/REMEDIACION_SUPABASE_2026-08-29.md`: estado de seguridad
  confirmado de la integración externa de pedidos.

## 26. Próximo paso recomendado

Antes de construir el VPS, el usuario debe definir la matriz funcional que
formará la base real. Después se implementará esa matriz sobre la línea común, se
repetirán instalación limpia y actualización, y se probarán el cliente Windows y
un primer APK piloto en dispositivos distintos del Edge. En paralelo se deben
crear publicaciones locales de catálogo versionadas para validar la semántica que
después usará la consulta diaria al VPS.

La primera prueba integral debe instalar la release dorada en un Windows limpio,
aprovisionar Arboledas y una sucursal distinta, aplicar dos versiones de catálogo,
simular ausencia del VPS y comprobar que ventas, impresión, respaldo y rollback
continúan siendo correctos.

Después de superar esas pruebas, el siguiente hito será crear el perfil central
independiente, aprovisionarlo primero en entorno de piloto sobre KVM 2, restaurar
un respaldo de ensayo y medir carga/recursos antes de enrolar sucursales reales.
