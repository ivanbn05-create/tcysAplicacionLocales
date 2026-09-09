# Los Tocayos POS

Primera versión local del punto de venta de Los Tocayos. Permite operar pedidos de
comedor, domicilio y sucursales, capturar partidas por comensal, procesar la orden,
cobrarla y generar comandas/cuentas térmicas en modo ráster.

La dirección futura de paquete único, módulos por sucursal, actualización segura,
sincronización y servidor central Hostinger KVM 2 está documentada en
[ARQUITECTURA_DESPLIEGUE_Y_SINCRONIZACION_MULTISUCURSAL.md](ARQUITECTURA_DESPLIEGUE_Y_SINCRONIZACION_MULTISUCURSAL.md).
Esa arquitectura está aceptada, pero el backend central todavía no está
implementado.

## Producción local en Windows

La guia operativa esta en [DESPLIEGUE_WINDOWS.md](DESPLIEGUE_WINDOWS.md): incluye
la recuperacion de ACL, los requisitos de Python/pywin32, la comprobacion del
servicio y las limitaciones para otras sucursales.

**Antes de repetir el instalador:** todavia ejecuta una carga de datos ligada a
Arboledas que puede sobrescribir catalogos y precios. No es aun un actualizador
generico para sucursales personalizadas. `-PrepareOnly` tambien modifica la base.

Instala Python para **todos los usuarios** y abre PowerShell como administrador. El
instalador genera `DJANGO_SECRET_KEY` si falta, obliga `DEBUG=false`, valida una lista
concreta de hosts, instala Waitress y registra `LosTocayosPOS` como servicio con inicio
automático retardado:

El perfil recomendado escucha sólo en loopback detrás de un proxy HTTPS local:

```powershell
.\instalar-servicio-lan.ps1 `
  -AllowedHosts "localhost,127.0.0.1,192.168.0.30" `
  -ListenAddress "127.0.0.1" `
  -TrustedProxy "127.0.0.1" `
  -Https `
  -Port 8000
```

Si todavía no existe ese proxy y se acepta operar temporalmente sin cifrado, la
exposición LAN exige el consentimiento explícito `-AllowInsecureHttpLan`:

```powershell
.\instalar-servicio-lan.ps1 `
  -AllowedHosts "localhost,127.0.0.1,192.168.0.30" `
  -ListenAddress "0.0.0.0" `
  -AllowInsecureHttpLan `
  -Port 8000
```

El instalador migra la base a `runtime\db.sqlite3`, crea una cuenta administrativa y
otra operativa mediante prompts sin eco de contraseña, recopila estáticos, limita el
firewall a perfil privado/subred local y comprueba `/salud/`. El servicio corre como
`LocalService`; sólo puede modificar `runtime`, `logs` y `media`. El código, `.venv`,
`.env`, certificados y respaldos quedan bajo una ACL de SYSTEM/Administradores.

El mismo instalador deja activo un respaldo diario verificable de
`runtime\db.sqlite3`. De forma predeterminada registra la tarea programada
`LosTocayosPOS-RespaldoSQLite` a las 03:15, con 30 días de retención, y ejecuta un
primer respaldo antes de iniciar el servicio:

```powershell
.\instalar-servicio-lan.ps1 `
  -AllowedHosts "localhost,127.0.0.1,192.168.0.30" `
  -ListenAddress "127.0.0.1" `
  -TrustedProxy "127.0.0.1" `
  -Https `
  -Port 8000 `
  -BackupTime "03:15" `
  -BackupRetentionDays 30
```

La tarea corre como `SYSTEM`, no usa cuentas ni contraseñas guardadas y no otorga
acceso adicional a `LocalService`; `backups` queda reservado para SYSTEM y
Administradores. Cada ejecución usa la API de respaldo online de SQLite para obtener
una copia consistente aunque Waitress esté activo, guarda
`backups\db-YYYYMMDD-HHMMSS.sqlite3`, su `.sha256` y un `.json` con resultado, hash,
integridad y prueba de restauración. El registro operativo queda en
`logs\sqlite-backup.log` como JSON Lines. `-BackupTime` y `-BackupRetentionDays`
definen horario y retención al instalar. No vuelvas a ejecutar todo el instalador
solo para cambiar esos valores sin revisar la advertencia sobre catalogos.
`-SkipBackupTask` omite el respaldo programado y retira la tarea si ya existía.

Para lanzar una comprobación manual sin reconfigurar el servicio:

```powershell
.\respaldar-db-sqlite.ps1 -RetentionDays 30
Get-Content .\logs\sqlite-backup.log -Tail 1
```

Para restaurar, detén el servicio, conserva una copia de emergencia del archivo actual,
reemplaza `runtime\db.sqlite3` por el respaldo elegido y verifica la base antes de
arrancar de nuevo:

```powershell
Stop-Service LosTocayosPOS
Copy-Item .\runtime\db.sqlite3 .\runtime\db.sqlite3.pre-restauracion -Force
Copy-Item .\backups\db-YYYYMMDD-HHMMSS.sqlite3 .\runtime\db.sqlite3 -Force
.\.venv\Scripts\python -c "import sqlite3; c=sqlite3.connect(r'runtime\db.sqlite3'); print(c.execute('PRAGMA integrity_check').fetchone()[0])"
Start-Service LosTocayosPOS
```

Para iniciar o comprobar posteriormente el servicio:

```powershell
.\iniciar-servicio-lan.ps1
```

`iniciar-local.ps1` conserva un arranque de producción en consola con Waitress para
diagnóstico. Por defecto sólo escucha en `127.0.0.1`; para HTTP LAN exige el mismo
indicador `-AllowInsecureHttpLan`. No usa `runserver` y la ventana debe permanecer
abierta.

HTTP dentro de una LAN no cifra contraseñas, cookies ni datos de clientes. Para el
perfil recomendado coloca un proxy inverso con certificado delante del aplicativo y
ejecuta el instalador con `-Https`; en ese modo Waitress sólo acepta loopback y confía
en un proxy loopback concreto. La opción configura Django/Waitress, pero no instala el
proxy ni el certificado HTTPS.

### Modo de prueba explícito y aislado

`iniciar-prueba-lan.bat` es el único arranque con `runserver` y `DEBUG=true`. Publica
`http://192.168.0.30:8001`, usa exclusivamente
`runtime\prueba\db.sqlite3` y `runtime\prueba\media`, desactiva Supabase y guarda las
impresiones como archivos. No toca la base, los clientes ni las impresoras de
producción. Carece de autenticación y nunca debe dejarse activo para operación real.

La computadora necesita conservar la dirección `192.168.0.30`, preferentemente con
una reserva DHCP. `Ctrl+C` detiene este modo.

## Inicio con Docker

1. Copia `.env.example` como `.env`, genera valores aleatorios para
   `DJANGO_SECRET_KEY` y `POSTGRES_PASSWORD`, y restringe `DJANGO_ALLOWED_HOSTS`.
2. El `compose` publica Gunicorn en la LAN y falla cerrado: para ese despliegue HTTP
   directo debes aceptar conscientemente el riesgo con `ALLOW_INSECURE_HTTP_LAN=true`
   en `.env`; no reutilices esa opción si después colocas un proxy HTTPS.
3. Ejecuta `docker compose up --build`.
4. Crea las cuentas con `docker compose exec web python manage.py createsuperuser` y
   `docker compose exec web python manage.py crear_operador_pos`.
5. Abre `http://localhost:8000`.

Docker usa PostgreSQL 16 y Gunicorn; Waitress es el servidor de producción de Windows.

## Aplicación de escritorio para Windows

La carpeta `desktop/` contiene un ejecutable ligero que abre el punto de venta como una
aplicación independiente, sin pestañas ni barra de direcciones. Reutiliza el frontend y
el servidor local existentes; no requiere Docker ni conexión a un VPS.

```powershell
.\desktop\build.ps1
.\desktop\dist\TocayosPOS.exe
```

El modo táctil se abre con `TocayosPOS.exe --tableta`. Consulta
`desktop/README.md` para configurar un servidor de red o iniciar sólo el servicio.

`diagnostico_calidad_pos_local.md` conserva el diagnóstico histórico del
2026-08-23. Varias brechas que enumera —autenticación, Waitress, locks, worker y
respaldo— se corrigieron después; no debe utilizarse como matriz vigente. El
estado actual de despliegue está en `DESPLIEGUE_WINDOWS.md` y en la arquitectura
canónica enlazada al inicio.

## Captura en tabletas

Abre `http://localhost:8000/tabletas/` para mostrar únicamente las 24 mesas de comedor
con controles táctiles ampliados. La captura distribuye la pantalla entre la tira de
24 comensales, el menú completo con scroll y una comanda interactiva en tiempo real.
Cada bloque agrupa seis comensales; las bebidas se acumulan debajo de la matriz y el
comentario general sustituye la fila de preparación individual. La interfaz bloquea el
scroll encadenado y el gesto de recarga de Android, inicia en modo `fullscreen` cuando
se abre como PWA y ofrece un botón **Salir** para abandonar la pantalla completa.

### Instalación en tabletas Android

La opción recomendada para operación diaria es publicar `/tabletas/` por HTTPS dentro
de la red local y usar **Instalar aplicación** desde Chrome. El manifiesto ya define el
inicio de tableta y solicita `fullscreen`; HTTPS es importante para que el navegador
mantenga habilitados el service worker y la instalación de la PWA. Consulta los
[requisitos de instalación de Chrome](https://developer.chrome.com/docs/lighthouse/pwa/installable-manifest).

Para una prueba temporal por USB-C se puede usar ADB, con depuración USB habilitada:

```powershell
adb reverse tcp:8000 tcp:8000
```

Después se abre `http://localhost:8000/tabletas/` en la tableta. Android documenta este
[reenvío inverso por USB](https://developer.android.com/develop/ui/views/layout/webapps/access-local-server?hl=es-419),
pero depende de conservar la sesión ADB y no sustituye el acceso de red para producción.
Una conexión física puerto-a-puerto por sí sola no instala ni mantiene comunicada la
PWA. Si no se puede servir HTTPS en la red local, las alternativas estables son envolver
la interfaz en una aplicación Android (WebView/TWA) o conservar el acceso por Escritorio
Remoto.

Por restricciones de Android, una página web no puede cerrar por fuerza la aplicación
instalada. **Salir** abandona el Fullscreen API, vuelve al selector de mesas y permite al
operador cambiar o cerrar la aplicación desde el sistema.

## Bloqueo de edición

Cada navegador/tableta genera un `device_id` local y toma un lock temporal al abrir una
comanda activa. El lease dura 15 segundos por defecto (`POS_TICKET_LOCK_LEASE_SECONDS`)
y la PWA lo renueva cada 10 segundos mientras el ticket sigue en pantalla.

Si otra tableta intenta editar una mesa tomada recibe `423 Locked` con el operador que la
tiene; si llega una petición atrasada con `version_entidad` vieja recibe `409 Conflict`.
Al volver, cerrar o completar la orden se libera el lock explícitamente; si la tableta se
apaga, la siguiente puede recuperarlo cuando expira el lease.

## Impresora térmica

Para validar sólo el diseño, sin gastar papel:

```env
PRINT_BACKEND=archivo
PRINT_SYNC=true
```

Para la POS-80C del local:

```env
PRINT_BACKEND=tcp
PRINT_SYNC=false
PRINTER_CAJA_HOST=192.168.0.33
PRINTER_COCINA_HOST=192.168.0.33
PRINTER_BARRA_HOST=192.168.0.33
PRINTER_PORT=9100
```

El servicio `impresion` consume la cola de trabajos, renderiza PNG monocromático de
576 píxeles (80 mm) y envía comandos ESC/POS ráster por TCP. Conserva el PNG como
evidencia incluso cuando imprime por red.

La cabecera no ocupa espacio con telemetría pasiva. Un trabajo en modo archivo queda
como `Vista previa generada`; sólo se marca `Impreso` después de completar el envío TCP.
Los fallos de impresión aparecen como avisos persistentes cuando requieren una acción.

## Identidad de marca

La interfaz usa el logotipo actual y los lineamientos del manual de identidad:
amarillo `#FFED00`, verde `#4AA736`, rojo `#E42522`, carbón `#353436` y Montserrat.
Los recursos están empaquetados localmente para que la PWA no dependa de internet.
La dirección de producto, los tokens, los componentes y las reglas para extender el
futuro módulo Administrador están documentados en `DESIGN.md` y `PRODUCT.md`.

El selector de operación conserva una lectura de pase de cocina: mesas libres en verde,
órdenes abiertas en amarillo y procesadas en rojo, siempre con etiqueta e ícono para no
depender sólo del color. La comanda virtual mantiene la estructura del ticket térmico;
no debe convertirse en una tarjeta genérica ni en una imagen.

## Catálogo

`cargar_datos_iniciales` crea los 47 productos activos confirmados, incluidas las
promociones por día, cinco postres, bebidas y variantes de quesadilla/lonche. También importa los nombres de
`datos/Listado-Productos.xlsx`
como inactivos para revisión: el traspaso advierte que 189 precios son desconocidos y no
es seguro habilitarlos para cobro. Para repetir la importación desde otro archivo:

```powershell
python manage.py importar_catalogo_legado "C:\ruta\Listado-Productos.xlsx"
```

Los precios y productos se editan desde `/admin/` después de crear un superusuario con
`python manage.py createsuperuser`.

Cada producto admite opcionalmente una fotografía JPG, PNG o WebP de hasta 3 MB desde
`/admin/`. El archivo original no se publica directamente: el POS entrega una miniatura
WebP privada, sin metadatos y recortada a `320×240`. Si no hay una carga administrada,
el POS usa el mapa local de 25 WebP de `ventas/static/ventas/menu/`; un código sin foto
asignada usa `mainlogo.webp`. Lonches sin queso comparten la foto de su variante normal,
todas las aguas frescas comparten `aguasfrescas.webp` y todos los refrescos comparten
`refresco.webp`. Las tarjetas compactas conservan la proporción 50/50 entre imagen y
nombre/abreviatura, y los recursos quedan disponibles sin conexión.

## Clientes a domicilio

El directorio permite buscar por nombre, clave corta, teléfono, domicilio, número
exterior y referencia. Para importar el traspaso legado de forma repetible:

```powershell
python manage.py importar_clientes_legado "C:\ruta\Clientes-Domicilio-Completo.xlsx"
```

El importador omite filas sin nombre/domicilio/contacto y agrupa domicilios del mismo
nombre. Cuando una empresa no tiene teléfono fijo pero la referencia contiene el
contacto rotativo, se marca para solicitar nombre y celular en cada pedido.

## Pedidos de sucursales

La pestaña **Sucursales** contiene 11 posiciones para cada sucursal o cliente
mayorista. Al seleccionar un producto se abre la calculadora integrada para capturar o
editar cantidades enteras o decimales; al procesar se imprime un ticket total exclusivo
con cantidad, precio e importe por concepto. El catálogo y sus precios vigentes se
cargan con `cargar_datos_iniciales` y permanecen separados del menú de comedor.

El POS consulta Supabase en modo de sólo lectura e importa una sola vez los pedidos
confirmados de hoy o del día anterior. Se asignan al primer espacio libre de la
sucursal correspondiente y quedan abiertos para revisión antes de imprimir. La conexión
compartida se configura en `.env`; la contraseña nunca se guarda en Git:

```env
PEDIDOS_SUCURSALES_FUENTE=supabase
PEDIDOS_SUCURSALES_AUTO_SYNC=true
PEDIDOS_SUCURSALES_SYNC_SECONDS=300
PEDIDOS_SUCURSALES_MAX_PEDIDOS=500
PEDIDOS_SUCURSALES_HORA_INICIO=06:00
PEDIDOS_SUCURSALES_HORA_FIN=17:35
PEDIDOS_SUCURSALES_DB_HOST=<pooler-del-proyecto>.supabase.com
PEDIDOS_SUCURSALES_DB_PORT=5432
PEDIDOS_SUCURSALES_DB_NAME=postgres
PEDIDOS_SUCURSALES_DB_USER=pos_local_reader.<project-ref>
PEDIDOS_SUCURSALES_DB_PASSWORD=<secreto-local-aleatorio>
PEDIDOS_SUCURSALES_DB_SSLMODE=verify-full
PEDIDOS_SUCURSALES_DB_SSLROOTCERT=certs/prod-ca-2021.crt
```

También se acepta una URI completa en `PEDIDOS_SUCURSALES_DATABASE_URL`. Producción no
hace fallback silencioso: `PEDIDOS_SUCURSALES_FUENTE=sqlite` sólo funciona con el
módulo de prueba. Antes de activar Supabase aplica y verifica, en orden, los archivos de
`seguridad\supabase\`; el aplicativo rechaza roles distintos de `pos_local_reader`,
TLS sin validación de host/CA, permisos extra, RLS ausente y funciones públicas
`SECURITY DEFINER` accesibles.

La auditoría inicial y su alcance están en
`seguridad\supabase\AUDITORIA_2026-08-26.md`. La aplicación controlada de la
remediación y la verificación final —20/20 tablas con RLS, retiro de `public` de
Data API y lector dedicado de sólo lectura— quedaron registradas en
`seguridad\supabase\REMEDIACION_SUPABASE_2026-08-29.md`. Esta integración sigue
siendo únicamente la fuente externa de pedidos confirmados; no es el backend
central multisucursal previsto.

La sincronización también puede ejecutarse manualmente:

```powershell
.\.venv\Scripts\python manage.py sincronizar_pedidos_sucursales
```

La consulta remota no modifica estados ni datos en Supabase. Se ejecuta en una
transacción `READ ONLY`, limita tablas/columnas/pedidos/partidas y vuelve a validar
identidades, cantidades y precios contra el catálogo local. El control idempotente se
guarda únicamente en la base local del POS. El intervalo predeterminado de cinco
minutos reduce la carga remota a un máximo aproximado de 139 consultas diarias; fuera
del horario configurado no se abre ninguna conexión. Los cinco minutos posteriores a
las 17:30 funcionan como margen para recoger el último pedido permitido del día. Al
entrar a la pestaña **Sucursales** se solicita una revisión inmediata; si ya hubo una en
los últimos cinco minutos se reutiliza ese estado. La revisión periódica sólo permanece
activa mientras el usuario está en esa pestaña.

## Pruebas

```powershell
.\.venv\Scripts\python manage.py test
.\.venv\Scripts\python manage.py check --deploy
```

Cubren apertura y cancelación, captura hasta 24 comensales, promociones y sus
componentes, preparación global/individual excluyente, entrega programada, salsas,
terminal, orden de impresión, acceso para tabletas, bebidas acumuladas, clientes,
procesamiento, cobro opcional, captura decimal de sucursales, importación idempotente y
generación de los formatos térmicos. También cubren autenticación/CSRF, rate limiting,
permisos por rol, pagos inválidos, privacidad de caché, rutas de vistas previas y
validación defensiva de la integración externa.

## Controles de seguridad operativa

- Usa la cuenta operativa para ventas y reserva el superusuario para `/admin/`.
  `UsuarioPOS` vincula cada cuenta Django con una sucursal y un rol; cobro,
  cancelación, reimpresión y sincronización se autorizan por separado.
- Las API y páginas con datos operativos envían `private, no-store`, exigen sesión y
  CSRF, y aplican CSP, `frame-ancestors 'none'`, `nosniff` y política restrictiva de
  capacidades del navegador.
- Las vistas previas de impresión sólo se entregan tras autenticación y se purgan a
  los siete días por defecto (`PRINT_PREVIEW_RETENTION_DAYS`, máximo 30).
- SQLite usa transacciones `IMMEDIATE` y espera de bloqueo para serializar mutaciones
  entre hilos de Waitress. Si la concurrencia supera una sucursal pequeña, cambia la
  base principal a PostgreSQL.
- Revisa `logs\django.log` y `logs\waitress.log`, prueba restauraciones de respaldo,
  aplica actualizaciones y ejecuta auditoría de dependencias antes de cada entrega.
