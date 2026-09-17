# Despliegue Windows por sucursal

## Alcance y límites actuales

Guía del servidor Edge Windows actual, revisada el 2026-09-15 después de una
instalación limpia A y una actualización real A→B en laboratorio. En el equipo de desarrollo se
verificaron Windows PowerShell 5.1, Python 3.13.14 de 64 bits instalado para
todos los usuarios y HTTP LAN en el puerto 8000. Esta línea de releases fija
exactamente la versión mayor/menor Python 3.13: se admite una revisión `3.13.x`
compatible, pero no Python 3.12 ni 3.14. El wheelhouse y la `.venv` deben usar
ese mismo contrato.

Los flujos operativos ya están separados físicamente:

| Operación | Punto de entrada admitido | Efecto |
| --- | --- | --- |
| Instalación inicial | `instalar-servidor.ps1` | Exige identidad y red; crea servicio, configuración, migraciones, respaldo y ACL |
| Adopción de una instalación antigua | `aprovisionar-sucursal.ps1` | Con el servicio detenido, crea o valida únicamente la identidad y respalda `.env` |
| Actualización | `actualizar-servidor.ps1` | Conserva `.env`, identidad, catálogo, cuentas, firewall y tarea de respaldo |
| Reparación de acceso | `reparar-permisos-servidor.ps1` | Repara sólo acceso de Administradores/SYSTEM y termina |

`instalar-servicio-lan.ps1` es el motor interno y no es una interfaz para el
operador. No se debe invocar directamente. Ya no existe un flujo `-PrepareOnly`.

El servicio Windows usa los archivos locales, `.venv` y `runtime\db.sqlite3`. Esta
fase del instalador admite sólo SQLite: un `.env` que seleccione PostgreSQL se
rechaza antes de detener o registrar servicios porque aún no existe respaldo
`pg_dump`/restauración equivalente. Docker y el futuro VPS tienen un ciclo de
despliegue distinto. El servicio no hace
`git pull` ni descarga código desde GitHub. Un commit o push no actualiza un
servidor instalado. Una release productiva para Windows lleva obligatoriamente un
`wheelhouse` y permite que `pip` instale las versiones exactas de
`requirements-lock.txt` sin consultar Internet;
la excepción de red sólo se habilita mediante un switch explícito de desarrollo.
La instalación y actualización aún modifican la misma `.venv`. Hay que distribuir
la release completa: nunca copiar solamente un script `.ps1`. El preflight
rechaza releases construidas desde un árbol sucio/no verificable y archivos no
declarados dentro del código, incluidas migraciones antiguas. La extracción debe
hacerse en staging limpio, no superponiendo archivos a mano.

Si una instalación inicial falla después de registrar infraestructura, el bloque
de recuperación detiene y retira el servicio parcial, la tarea y la regla de
firewall, y restaura o elimina el `.env` creado por esa ejecución. Conserva la
base/runtime para diagnóstico y permite repetir `instalar-servidor.ps1` con la
misma identidad. No confundirlo con rollback de actualización: éste sigue
pendiente porque una migración ya iniciada no se revierte automáticamente.

La arquitectura futura y las decisiones vigentes de distribución están en
[ARQUITECTURA_DESPLIEGUE_Y_SINCRONIZACION_MULTISUCURSAL.md](ARQUITECTURA_DESPLIEGUE_Y_SINCRONIZACION_MULTISUCURSAL.md).
Este archivo sigue siendo exclusivamente el runbook del **servidor Edge Windows
actual** y tiene precedencia para toda operación, diagnóstico o recuperación de
esa instalación. No describe ni instala el backend central de Hostinger KVM 2.
El flujo de desarrollo, soporte por sucursal y clientes instalables se documenta
en [FLUJO_DESARROLLO_MANTENIMIENTO_Y_CLIENTES.md](FLUJO_DESARROLLO_MANTENIMIENTO_Y_CLIENTES.md).

## Estado consolidado del instalador al 2026-09-14

| Área | Ya implementado/probado | Pendiente antes de otras sucursales |
| --- | --- | --- |
| Servicio Windows | Host pywin32 dentro de la venv, DLL locales, `LocalService`, inicio retardado y registro de eventos | Validarlo desde una release completa en Windows limpio |
| Permisos | ACL diferenciadas para código, secretos, runtime, media y logs; ACL exacta pre/post para respaldos nuevos, antiguos y huérfanos; reparación limitada para Administradores/SYSTEM | Probar matriz de instalación, actualización y recuperación en equipos ajenos al desarrollo |
| Migración | Preflight de identidad/configuración antes de detener; mutex global de mantenimiento y mutex SQLite; bloqueo de lectura de `.env` durante toda la actualización, con revalidaciones antes de fases críticas; respaldo de `.env` y SQLite antes de migrar; comprobación de salud al arrancar | Dependencias y validación pesada aún ocurren con el servicio detenido; falta staging, cambio atómico, diario reanudable y rollback |
| Respaldo | Copia SQLite consistente; publicación atómica por archivo; triplete validado y retenido como unidad; hash, integridad, claves foráneas, restauración de prueba; mutex compartido y tarea diaria SYSTEM | Copia externa cifrada, alerta y respaldo de configuración/media |
| Validación | Suite aislada, incluida ejecución elevada real del wrapper; verificador de `.env`, servicio, tarea, DB, salud, listener, firewall y ACL exactas; `-RunBackup` exige un triplete fresco | Salud profunda con versión, esquema, worker, sincronización y prueba funcional; comprobación externa del proxy HTTPS |
| Red y seguridad | Firewall privado, hosts explícitos, opción HTTPS y rechazo de configuraciones inseguras implícitas | Firma del publicador y canal de actualización |
| Identidad | Clave/nombre obligatorios en instalación; adopción idempotente de instalaciones antiguas; sin fallback de producción | Enrolamiento autorizado por el VPS mediante código de un solo uso o archivo firmado |
| Catálogo | La actualización nunca carga semilla; Arboledas sólo puede solicitarla explícitamente en instalación | Paquete inicial versionado, validado y específico por sucursal |
| Módulos | Candidata B con `Modulo`/`ModuloSucursal`, núcleo/opcionales, dependencias y defensa en UI/API/tareas | Aceptar lista definitiva, probar instalación limpia B y enrolamiento futuro desde VPS |
| Release | ZIP reproducible; manifiesto v2 para `cp313/win_amd64`; pins exactos; wheelhouse offline resuelto sin fuentes externas; WHEEL/RECORD/CRC validados; CA pública y puente histórico de Arboledas temporal; excluye secretos, estado, árboles dirty y cualquier módulo no declarado | Firma, construcción atestada en CI, bundle de catálogo por sucursal e integración del paquete con el actualizador |

### Backlog obligatorio del instalador y actualizador

1. Firmar la release del servidor y automatizar/atestar en CI la preparación del
   único target admitido actualmente: Windows x64 con CPython 3.13. El formato v2
   ya lo declara, transporta y valida sin índice remoto.
2. Integrar verificación y extracción segura del paquete con el actualizador.
3. Autorizar el enrolamiento de sucursal mediante código de un solo uso o archivo
   firmado por el VPS; la identidad local explícita actual evita el fallback, pero
   todavía no acredita quién autorizó la sucursal.
4. Convertir la carga inicial de catálogo en un paquete explícito, versionado y
   específico por sucursal. La semilla histórica de Arboledas no es genérica.
5. Terminar el aprovisionamiento de módulos y credenciales. La candidata B ya
   modela módulos mínimos/opcionales y solicita un primer operador; faltan
   aceptación funcional, enrolamiento autorizado y validación de dispositivos.
   La selección pertenece al servidor Edge, no al instalador del cliente ligero.
6. Preparar la release en staging antes de detener el servicio, verificar su firma
   y manifiesto, hacer un cambio atómico y conservar rollback compatible. Los
   respaldos previos ya existen, pero no equivalen a rollback automático.
7. Registrar versión de aplicación, esquema, protocolo, catálogo, configuración
   y módulos; añadir una comprobación local protegida que valide el estado real.
8. Producir un informe de instalación/actualización sin secretos y desplegar
   primero por canal piloto.
9. Firmar el cliente Windows, comprobar la firma y los recursos embebidos dentro
   del bootstrap y hacer reproducible su paquete. El bootstrap ya comparte la
   versión 0.3.0.0 del cliente y propaga el código de salida de la instalación.

### Límite frente al VPS central

El futuro despliegue KVM 2 tendrá configuración propia, preferentemente bajo un
directorio como `deploy/vps/`. No reutilizará este instalador, no ejecutará el
worker de impresión de sucursal y jamás cargará la semilla Arboledas. Alojará
PostgreSQL, la API/panel central, HTTPS, respaldos externos y monitoreo; los Edge
seguirán operando y cobrando localmente cuando el VPS no esté disponible.

## Requisitos

Ejecutar desde **Windows PowerShell como administrador**. Python 3.13
debe estar instalado para todos los usuarios; comprobar la ruta con py -0p.
No usar un Python ubicado en C:\Users. Mantener fija la ruta del proyecto
despues de instalar el servicio.

Comprobar la elevacion real y el interprete sin mostrar configuracion privada:

~~~powershell
$identidad = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identidad)
$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
py -0p
& "C:\Program Files\Python313\python.exe" -I --version
~~~

La primera comprobacion debe devolver True. La ruta mostrada es la usada en este
servidor; en otro equipo verificar la ruta real. No basta que `python` funcione en
la consola del usuario. No ejecutar desde enlaces/junctions ni mover el proyecto
despues de registrar el servicio. No se necesita relajar la politica de ejecucion
global: el comando siguiente limita Bypass a ese proceso de PowerShell.

## Construcción y verificación de una release

Desde un commit limpio y aprobado, la herramienta de release genera un ZIP, un
manifiesto externo y un archivo de sumas SHA-256. La versión es obligatoria, el
resultado no se sobrescribe por defecto y `build` exige elegir exactamente una
fuente de dependencias. Para una release instalable sin red, preparar primero un
directorio plano, no vacío y compuesto sólo por wheels compatibles con el único
destino actual, Windows x64 con CPython 3.13, y pasarlo con `--wheelhouse DIR`:

~~~powershell
Set-Location -LiteralPath "C:\tcysAplicacionLocales"
$version = (Get-Content -LiteralPath ".\VERSION" -Raw).Trim()
$wheelhouse = "C:\ruta\wheelhouse-win-py313"
.\.venv\Scripts\python.exe .\herramientas\release_servidor.py build `
    --version $version `
    --output ".\release\servidor" `
    --wheelhouse $wheelhouse
~~~

El empaquetador sólo admite en `requirements-lock.txt` pins canónicos
`nombre==versión` (con marcador opcional), sin rangos, extras, URLs ni opciones
de `pip`; para este producto `pywin32` debe quedar activo en Windows. Comprueba
estructura, metadatos `WHEEL`, etiquetas, todos los hashes/tamaños de `RECORD`,
CRC y límites de entradas/descompresión antes de procesar cada wheel. Después usa
un reporte de `pip --dry-run --ignore-installed --no-index --only-binary=:all:`
fijado a `--platform win_amd64 --implementation cp --python-version 3.13
--abi cp313`, exige que cada URL `file:` resuelta permanezca dentro del
wheelhouse plano y que los archivos seleccionados sean exactamente los
entregados. La verificación del ZIP repite esas comprobaciones y acredita en el
manifiesto v2 el target `cp313/win_amd64`. El instalador valida de nuevo esquema,
procedencia, target, hashes, política de archivos y cobertura; ignora cualquier
`pip.ini`, instala con `--no-index` si hace falta y compara el conjunto instalado
completo con el lock. Si hay
versiones faltantes, distintas o paquetes extra, conserva la `.venv` anterior y
crea una limpia durante la ventana de mantenimiento. Un wheelhouse incompleto,
adicional, dañado o incompatible se rechaza antes de detener el servicio.

Para auditoría de código o pruebas también se puede crear deliberadamente un
paquete sin dependencias:

~~~powershell
.\.venv\Scripts\python.exe .\herramientas\release_servidor.py build `
    --version $version `
    --output ".\release\source-only" `
    --source-only
~~~

El manifiesto de esa variante declara `dependency_bundle: none`. Una release
source-only **no sirve por sí sola para instalar un equipo limpio sin red**. Sólo
puede continuar si la `.venv` ya satisface exactamente el lock o, durante
desarrollo supervisado y con red disponible, se autoriza la descarga explícita.

Antes de usar el paquete, comprobar juntos el ZIP, el manifiesto y las sumas:

~~~powershell
.\.venv\Scripts\python.exe .\herramientas\release_servidor.py verify `
    --archive ".\release\servidor\LosTocayosPOS-Servidor-$version.zip" `
    --manifest ".\release\servidor\LosTocayosPOS-Servidor-$version.manifest.json" `
    --checksum ".\release\servidor\LosTocayosPOS-Servidor-$version.sha256"
~~~

La lista permitida excluye `.env`, bases, `.venv`, respaldos, logs, media,
certificados privados y otros datos locales. Incluye expresamente la CA pública
`certs/prod-ca-2021.crt`, necesaria para verificar TLS de la integración
existente. También rechaza archivos locales ignorados por Git dentro de las rutas
permitidas. El instalador recorre toda la raíz extraída y rechaza también un
módulo o archivo ejecutable no declarado, aunque viva en una carpeta desconocida;
por eso la extracción se hace en staging limpio. `--allow-dirty` existe
únicamente para pruebas de desarrollo y deja
esa condición registrada en el manifiesto; no se admite para una entrega. Los
hashes y el manifiesto detectan diferencias o corrupción, pero **no son una firma
digital** y no demuestran quién publicó el paquete.

Esta primera fase tampoco extrae ni conmuta releases: `actualizar-servidor.ps1`
trabaja sobre código que ya fue colocado en su directorio. No presentar el ZIP
como un actualizador automático hasta implementar staging, firma y cambio atómico.

### Excepciones exclusivas de desarrollo

Los wrappers fallan de forma predeterminada si el árbol no contiene el manifiesto
embebido de una release o si faltan dependencias disponibles sin red. Sólo durante
trabajo supervisado se pueden habilitar estas excepciones independientes:

- `-AllowUnverifiedDevelopmentTree` admite un checkout al que le falta
  `_release\manifest.json`. No omite la validación si el manifiesto existe pero es
  inválido, ni acepta archivos cuyo tamaño o SHA-256 no coincidan.
- `-AllowOnlineDependencies` permite que `pip` descargue las versiones fijadas en
  `requirements-lock.txt` cuando la `.venv` no las satisface y no hay wheelhouse.
  No elimina el lock, `pip check` ni la comprobación final del entorno.

No usar esos switches para convertir un source-only en una entrega de producción:
el primero no es necesario porque esa release sí lleva manifiesto y el segundo
introduce dependencia de red durante la ventana de mantenimiento.

## Instalación inicial

Usar `instalar-servidor.ps1` solamente cuando el servicio `LosTocayosPOS` aún no
existe. Clave, nombre y hosts son obligatorios. Para el perfil HTTP LAN temporal
de Arboledas, suponiendo que se ejecuta desde una release extraída con manifiesto
y wheelhouse:

~~~powershell
Set-Location -LiteralPath "C:\tcysAplicacionLocales"
.\instalar-servidor.ps1 `
    -SucursalClave "ARBOLEDAS" `
    -SucursalNombre "Arboledas" `
    -AllowedHosts "localhost,127.0.0.1,192.168.0.30" `
    -ListenAddress "0.0.0.0" `
    -AllowInsecureHttpLan `
    -Port 8000
~~~

La tarea diaria se registra por defecto. Si existe otro mecanismo de respaldo
operativo aprobado, `-SkipBackupTask` permite omitirla o retirarla durante la
instalación; este switch **no omite** el respaldo SQLite verificable que se
ejecuta antes de una migración. `-BackupTime` y `-BackupRetentionDays` sólo
configuran la tarea inicial y su retención.

Para el perfil recomendado detrás de un proxy HTTPS local:

~~~powershell
.\instalar-servidor.ps1 `
    -SucursalClave "ARBOLEDAS" `
    -SucursalNombre "Arboledas" `
    -AllowedHosts "localhost,127.0.0.1,192.168.0.30" `
    -ListenAddress "127.0.0.1" `
    -TrustedProxy "127.0.0.1" `
    -Https `
    -Port 8000
~~~

`-SucursalId` acepta opcionalmente el UUID central que se haya asignado. Si se
omite, Django genera un UUID local estable. La instalación rechaza una clave que
no coincida con una identidad ya guardada y nunca reutiliza una instalación para
otra sucursal.

La salida de impresión predeterminada es `archivo`, sin direcciones heredadas de
otra sucursal. Para habilitar papel durante el alta se debe agregar
`-PrintBackend tcp` junto con `-PrinterCajaHost`, `-PrinterCocinaHost` y
`-PrinterBarraHost`; los tres son obligatorios y validados en ese modo. La
actualización no permite cambiar estos valores y conserva `.env` byte por byte.

`iniciar-local.ps1` es exclusivamente un arranque de diagnóstico en consola.
Después de cargar `.env` fuerza `PRINT_BACKEND=archivo`, incluso si el servicio
está configurado con `tcp`; las acciones de prueba pueden generar archivos de
ticket, pero nunca abren una conexión a las impresoras físicas. Antes de usarlo,
el servicio `LosTocayosPOS` debe estar detenido. El servidor usa la base local
real: la preparación no instala ni migra, pero las acciones realizadas en la UI sí
crean o modifican clientes, pedidos y ventas. Conserva el mutex de mantenimiento
hasta cerrar Waitress o pulsar `Ctrl+C`.

Por defecto sólo se crea o valida `Sucursal`: no se crean catálogo, posiciones,
roles, perfiles ni PIN. La semilla histórica se habilita únicamente agregando
`-InicializarDatosArboledas` a una instalación cuya clave sea `ARBOLEDAS`. Debe
revisarse antes porque puede crear o actualizar catálogo/precios y no es apta para
ninguna otra sucursal. La operación es transaccional: un XLSX ausente se omite,
pero uno presente e inválido hace fallar y revertir la carga completa. Ninguna
actualización ejecuta esa semilla.

`datos/Listado-Productos.xlsx` permanece dentro de la release como puente
temporal para el traspaso inicial reproducible de Arboledas; no representa un
catálogo genérico ni administrado. Debe retirarse del bundle común cuando la
publicación inicial se distribuya como paquete versionado y específico de cada
sucursal.

No copiar `.env`, credenciales, bases ni certificados privados entre sucursales.
La red de Windows debe ser privada: en HTTP, la regla sólo permite el puerto
elegido desde `LocalSubnet` en ese perfil y apunta al Python de máquina que
realmente escucha. HTTP no cifra contraseñas ni pedidos;
`-AllowInsecureHttpLan` confirma expresamente ese riesgo.

Para ejecutar el mismo flujo desde el checkout actual, agregar
`-AllowUnverifiedDevelopmentTree`. Agregar también `-AllowOnlineDependencies`
sólo si no hay wheelhouse, la `.venv` aún no satisface el lock y se acepta usar
Internet durante esa prueba. Esos switches no deben aparecer en el procedimiento
de una sucursal instalada.

## Adopción de una instalación antigua

Si una instalación anterior funciona pero su `.env` no contiene
`SUCURSAL_CLAVE`, detener el servicio y adoptar la identidad antes de actualizar:

~~~powershell
Stop-Service -Name LosTocayosPOS
.\aprovisionar-sucursal.ps1 `
    -SucursalClave "ARBOLEDAS" `
    -SucursalNombre "Arboledas"
# Después de colocar la release completa que corresponda:
.\actualizar-servidor.ps1
~~~

El script requiere `.env`, una `.venv` sana y el servicio detenido. Primero
respalda `.env`, escribe la clave necesaria para abrir Django y ejecuta el
aprovisionamiento dentro de una transacción contra la base indicada por ese mismo
archivo, sin heredar otra base o configuración de la consola. Crea la única
sucursal si la base está vacía o valida clave, nombre y UUID de la existente.
Rechaza otra sucursal, identidad inactiva o datos en conflicto; ante cualquier
fallo restaura `.env` byte por byte. No instala dependencias, no migra y no crea
menú, posiciones, roles, perfiles ni usuarios.

No se debe cerrar una adopción con un simple arranque si también hay código o
migraciones pendientes. El wrapper de actualización vuelve a validar identidad,
crea el respaldo obligatorio, migra, registra el host vigente, inicia el servicio
y comprueba su salud. En un checkout de desarrollo sin manifiesto se necesita la
excepción explícita descrita antes; una release productiva no usa esa excepción.

## Actualización supervisada

La actualización no recibe parámetros de identidad, red o configuración; en una
release completa no necesita switches:

~~~powershell
.\actualizar-servidor.ps1
~~~

Requiere que exista el servicio, `.env`, una `.venv` sana y exactamente la sucursal
activa indicada por `SUCURSAL_CLAVE`. Valida esos datos antes de detener el servicio.
Después instala dependencias sobre la misma `.venv`, valida el despliegue, respalda
`.env` y SQLite, ejecuta migraciones y `collectstatic`, actualiza el servicio,
reaplica ACL y comprueba `/salud/`. Verifica que `.env` permanezca byte por byte
igual; no cambia identidad, red, secretos, catálogo, cuentas, firewall ni la tarea
programada de respaldo.

Instalación, adopción, actualización, reparación y diagnóstico usan el mismo mutex
global de mantenimiento; un segundo flujo administrativo concurrente falla antes
de tocar el sistema. El mutex independiente
`Global\LosTocayosPOS-RespaldoSQLite-v1` serializa la tarea, la ejecución manual y
toda fase que pueda respaldar o mutar SQLite. El orden es mantenimiento y después
respaldo. Al invocar el wrapper hijo, el padre cede el segundo mutex y lo recupera
obligatoriamente antes de continuar o revertir; el hijo siempre adquiere el mutex
por sí mismo y no existe un parámetro público para omitirlo.

La actualización mantiene además un descriptor de lectura sobre `.env` desde el
preflight hasta la salud final: permite que la aplicación lo lea, pero impide
escritura, sustitución o borrado concurrentes. Su hash se revalida antes de detener
el servicio, antes de crear el respaldo y antes de ejecutar migraciones.

Sólo en desarrollo supervisado, una release source-only conserva un manifiesto
válido, pero carece de wheelhouse. Si la `.venv` ya coincide con
`requirements-lock.txt`, la actualización puede seguir sin red; en caso contrario
se detiene antes de tocar el servicio, salvo que se haya solicitado conscientemente
`-AllowOnlineDependencies`. No es una ruta productiva aunque la `.venv` ya
coincida. Un checkout sin manifiesto necesita además
`-AllowUnverifiedDevelopmentTree`, por ejemplo:

~~~powershell
.\actualizar-servidor.ps1 `
    -AllowUnverifiedDevelopmentTree `
    -AllowOnlineDependencies
~~~

El código completo debe estar ya colocado antes de ejecutar el wrapper. Mientras
no exista extracción y conmutación atómica, hacerlo sólo en una ventana supervisada
y con el servicio detenido si se van a reemplazar archivos. No mezclar archivos de
dos versiones y no copiar encima los directorios de estado local.

No hay rollback automático. Si falla antes de modificar runtime, el script intenta
reanudar el servicio que detuvo. Si ya cambió `.venv` o empezó una migración, lo
deja detenido para evitar un arranque ciego. Un respaldo de la base no garantiza
que código anterior sea compatible con el esquema nuevo.

## Recuperar permisos de un intento incompleto

Si un intento dejó archivos inaccesibles, ejecutar el wrapper dedicado:

~~~powershell
.\reparar-permisos-servidor.ps1
~~~

Agrega control efectivo a Administradores y SYSTEM sin conceder acceso adicional
a `LocalService` ni a otros usuarios, y termina sin instalar, actualizar, migrar o
detener el servicio. No usar `icacls /reset /T` ni borrar `.venv` para recuperarse.

Si ni siquiera se pueden leer el wrapper o su motor, desde una consola realmente
elevada recuperar sólo esos dos archivos, sin recursión ni permisos para terceros:

~~~powershell
takeown.exe /F "C:\tcysAplicacionLocales\reparar-permisos-servidor.ps1" /A
icacls.exe "C:\tcysAplicacionLocales\reparar-permisos-servidor.ps1" /grant:r "*S-1-5-32-544:F" "*S-1-5-18:F"
takeown.exe /F "C:\tcysAplicacionLocales\instalar-servicio-lan.ps1" /A
icacls.exe "C:\tcysAplicacionLocales\instalar-servicio-lan.ps1" /grant:r "*S-1-5-32-544:F" "*S-1-5-18:F"
~~~

Después ejecutar de nuevo `reparar-permisos-servidor.ps1` y, cuando termine, el
wrapper de instalación o actualización que corresponda. Si hay denegaciones
explícitas o no se puede leer la ACL, detenerse y diagnosticar la ruta; no aplicar
`/reset /T` ni conceder control total a Everyone/Users.

Una .venv incompleta, no ejecutable o dependiente de Python por usuario se
conserva como .venv-roto-FECHA-ID y se crea otra con Python de maquina. No se
eliminan esos directorios automaticamente y Git los ignora.

### Cuentas de acceso

Las cuentas sólo se revisan durante la instalación inicial. Si no hay
superusuario, se solicita crearlo para mantener Django en `/admin/`. En la
candidata B, si no existe ningún perfil operativo, el instalador crea de forma
interactiva el rol Encargado, el primer perfil POS, un PIN de cuatro dígitos y la
cuenta no administrativa asociada. No se crean credenciales predeterminadas. La
actualización nunca crea ni cambia cuentas. En la instalación de laboratorio se
confirmaron después un perfil POS activo y una cuenta operativa activa; falta la
aceptación manual del ingreso y el PIN.

La contraseña web de una cuenta operativa es distinta del PIN de cuatro números
del personal. Elegir contraseñas diferentes y largas (al menos 12 caracteres
recomendado); escribirlas sólo en prompts ocultos, nunca en comandos, tickets o
mensajes. La actualización no crea ni cambia cuentas.

En el modelo de usuario actual, el correo de createsuperuser es opcional:
se puede dejar en blanco pulsando Enter. No es una cuenta de GitHub ni un requisito
de conexion a internet. LocalService y la tarea SYSTEM no usan esas contrasenas.
Si las cuentas ya se crearon y fallo Start-Service, no hay que recrearlas.

## Orden y permisos

Los flujos validan primero elevación y, cuando corresponde, Python de máquina.
Las operaciones administrativas adquieren el mutex global de mantenimiento y las
fases sensibles de SQLite adquieren después el mutex de respaldo; ambos se liberan
en orden inverso incluso si hay un fallo. La actualización
comprueba además, sin modificar estado, `.env`, la `.venv` y la identidad de la
base antes de detener el servicio. Después de detenerlo trabaja sobre la misma
`.venv`: instala dependencias fijadas desde el wheelhouse sin índice remoto (o
desde Internet sólo con autorización de desarrollo), ejecuta la suite en una
copia temporal sin `.env` ni datos reales, valida configuraciones efímeras
HTTPS/HTTP LAN y prueba el respaldo. Esta parte todavía no está preparada en
staging y amplía la ventana de indisponibilidad.

El host pywin32 queda en `.venv\Scripts\pythonservice.exe`. Esa ubicación permite
que Python 3.13 encuentre correctamente el entorno virtual; no usar el host
antiguo en la raíz de `.venv`. Recibe junto a su ejecutable las DLL de Python,
pywintypes y Visual C++ del runtime instalado. Se comprueba con un `PATH` mínimo
de Windows: no depende del heredado por `services.exe`. El origen de eventos se
registra durante install/update con elevación.

En instalación se escribe la configuración de producción; en actualización se
conserva su hash y se guarda una copia antes de migrar. Ambos modos respaldan
SQLite si ya existe, migran y recopilan estáticos. Sólo la instalación revisa
cuentas y configura firewall/tarea de respaldo.

El endurecimiento general se reaplica al final; `.env`, certificados y respaldos
se protegen antes. Además, el wrapper SQLite aplica y verifica antes y después una
ACL exacta en `backups` y todos sus archivos físicos directos: herencia desactivada,
propietario Administradores y sólo SYSTEM/Administradores con control total, sin
acceso para `LocalService`. Esto incluye archivos antiguos, nuevos, temporales o
huérfanos. Los subdirectorios, enlaces y junctions se rechazan antes de
modificarlos.

| Ruta | LocalService |
| --- | --- |
| Codigo, .venv, estaticos, certificados | Lectura/ejecucion |
| .env | Lectura |
| runtime, logs, media | Modificacion |
| backups, .git, tmp, .venv-roto-*, base antigua en la raiz | Sin acceso |

El mantenimiento requiere una consola elevada; un usuario administrador sin
elevar no recibe permisos directos sobre el proyecto. Si falla una fase, resolver
el mensaje antes de repetir el wrapper correspondiente. El servicio puede quedar
detenido después de modificar runtime o iniciar migraciones, que no se revierten
automáticamente. La actualización conserva el contenido de `.env` y los respaldos
anteriores; eso no constituye un rollback de código o esquema.

## Diagnóstico del arranque

| Sintoma | Comprobacion y correccion incorporada |
| --- | --- |
| Acceso denegado a requirements.txt/manage.py | Elevación real; ejecutar `reparar-permisos-servidor.ps1`. La ACL de archivos debe tener permisos efectivos, no sólo heredables. |
| .venv incompleta o ligada a Python del usuario | Se valida Python/pip y se conserva como .venv-roto-* antes de recrearla con Python de maquina. |
| Servicio instalado pero Start-Service falla | Consultar estado y eventos. En este servidor el host terminaba con 0xC0000135 por DLL no localizables; el timeout 1053 por si solo no demuestra esa causa. |
| Python/servicemanager no se encuentra en el servicio | Host en .venv/Scripts y DLL locales; el preflight comprueba el cargador nativo con un entorno minimo. |
| Funciona en el servidor, pero no desde otro equipo | Comprobar IP, perfil privado y firewall. En CPython 3.13 la venv puede lanzar un redirector: la regla debe apuntar al `sys._base_executable` resuelto y validado por esa venv, que es la imagen real de Waitress. |

Consultar sin imprimir .env ni registros completos:

~~~powershell
Get-CimInstance Win32_Service -Filter "Name='LosTocayosPOS'" |
    Select-Object Name, State, StartName, StartMode, PathName
Get-NetConnectionProfile | Select-Object Name, NetworkCategory
Get-NetFirewallRule -DisplayName "Los Tocayos POS - LAN privada" |
    Get-NetFirewallApplicationFilter | Select-Object Program
~~~

El preflight `servicio_windows.py --check-host` prepara/copia el host y sus DLL;
no es una consulta de solo lectura. El instalador lo ejecuta con el servicio
detenido. No lanzarlo sobre una instancia en ejecucion ni sustituir DLL a mano.
No aumentar el timeout de Windows ni ampliar el firewall para ocultar un fallo
del cargador. Revisar logs/eventos localmente y compartir solo errores depurados.

## Comprobación

Desde una consola elevada en C:\tcysAplicacionLocales, para este perfil HTTP LAN:

~~~powershell
Set-Location -LiteralPath "C:\tcysAplicacionLocales"
.\.venv\Scripts\python.exe herramientas\validar_despliegue.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_release_servidor.py"
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_contratos_despliegue.py"
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_servicio_windows.py"
powershell -NoProfile -ExecutionPolicy Bypass -File tests\test_instalador_windows.ps1
Get-Service LosTocayosPOS
powershell -NoProfile -ExecutionPolicy Bypass -File verificar-servicio-lan.ps1
# Sólo si esta instalación SQLite tiene la tarea programada:
powershell -NoProfile -ExecutionPolicy Bypass -File verificar-servicio-lan.ps1 `
    -RunBackup -BackupTimeoutSeconds 600
~~~

La validacion efimera HTTPS debe terminar sin avisos. En HTTP LAN son esperados
los avisos Django de HSTS, redireccion HTTPS y cookies seguras; no se silencian
en produccion.
La suite usa una copia temporal sin `.env` ni datos reales. La prueba ACL elevada
ejecuta el wrapper real dos veces y también su ruta de fallo: acredita idempotencia,
publicación y reparación de artefactos heredados o huérfanos. La auditoría completa
puede tardar varios minutos. `-RunBackup` inventaría antes y después, ejecuta la
tarea real, exige resultado 0 dentro de `-BackupTimeoutSeconds` (600 segundos por
defecto) y requiere exactamente una base nueva con sus sidecars `.sha256` y
`.json`; informa su nombre como `BackupCreated` y después audita sus ACL.

`verificar-servicio-lan.ps1` exige elevación y lee la configuración sin mostrar
secretos. Comprueba presencia y unicidad de claves obligatorias, secreto no
trivial, `DEBUG=false`, hosts, parámetros Waitress, impresión y los contratos
HTTP/HTTPS. Del servicio acredita `LocalService`, inicio automático retardado,
`PathName`, `PythonClass` y las acciones de recuperación exactas. Del proceso
acredita que cada listener sea Waitress, use el host/puerto/WSGI esperados y
descienda del servicio a través del Python de esta `.venv`. En Windows admite
un único redirector intermedio hacia el CPython base resuelto por la propia
`.venv`, pero no procesos ajenos. Para SQLite valida ubicación, integridad y
pertenencia a la sucursal. Si existe la tarea diaria, exige exactamente un
disparador diario, cuenta SYSTEM con privilegios máximos, acción/argumentos y
un argumento de retención presente dentro del rango permitido, `StartWhenAvailable`
e `IgnoreNew`. También compara ACL
protegidas y derechos exactos para Administradores, SYSTEM y LocalService.
En las ACE positivas, el comparador incluye explícitamente `Synchronize`, que
.NET agrega al materializar `Read`, `ReadAndExecute` y `Modify`.

La tarea o la regla de firewall pueden aparecer como `NotConfigured` cuando se
omitieron expresamente. Si existe la regla nominal del POS, el verificador exige
entrada TCP, perfil Private, `LocalSubnet`, puerto y CPython base exactos; en HTTPS
exige que esa regla HTTP LAN no permanezca. Su alcance no hace inventario de otras
reglas de Windows con nombres distintos que pudieran abrir el mismo puerto. En modo
HTTPS valida el contrato interno entre Django, Waitress y el proxy loopback, pero
no puede acreditar desde este script que el proxy externo esté instalado, que su
certificado sea confiable ni que TLS funcione desde otro equipo: esas dos
comprobaciones forman parte de la aceptación de red externa.

En el perfil HTTP/SQLite predeterminado, el resultado esperado es: servicio
`Running` como `NT AUTHORITY\LocalService`, inicio automático retardado; tarea
`LosTocayosPOS-RespaldoSQLite` como SYSTEM; base runtime presente; `/salud/` con
estado `ok`; firewall Private/LocalSubnet/TCP 8000 y ninguna desviación de ACL.
La tarea o regla no existen si se omitieron expresamente o si el perfil no las
usa; en ese caso aparecen como `NotConfigured`, no como un falso fallo. Probar
`http://192.168.0.30:8000/salud/` también desde otro equipo: una
prueba desde el servidor no demuestra conectividad entre dispositivos.

Cuando está configurada, la tarea de respaldo se ejecuta como SYSTEM, diariamente
a las 03:15 y con retención de 30 días por defecto. Cada copia se crea con la API
de SQLite; la base y sus sidecars se publican atómicamente por archivo y sólo
después se retienen tripletes completos como unidad. El wrapper verifica integridad,
claves foráneas, restauración, SHA-256 y ACL exactas, y comparte mutex con los
flujos administrativos. Para una prueba de aceptación que espere la finalización y
acredite un triplete fresco, usar:

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File verificar-servicio-lan.ps1 `
    -RunBackup -BackupTimeoutSeconds 600
~~~

`Start-ScheduledTask LosTocayosPOS-RespaldoSQLite` sólo dispara la tarea; por sí
solo no acredita que haya terminado ni que el triplete sea nuevo.

No publicar .env, archivos de respaldo, certificados privados ni logs completos
en tickets o commits. Para diagnosticar, compartir solo nombres de fase,
codigos de error y metadatos de permisos.

## Cambios y siguientes sucursales

No editar el codigo de produccion mientras se esta cobrando. Probar cambios en
una copia de desarrollo sin secretos ni datos operativos. Los cambios de Python
y .env requieren reiniciar el servicio; los de estaticos requieren collectstatic
y recarga del navegador, y los de estructura de datos requieren migraciones.
Cambiar productos/precios desde la aplicacion modifica la base local, no Git.

La estrategia vigente es un repositorio común, releases versionadas y datos y
configuración propios por sucursal. Menús distintos no requieren ramas
permanentes. Ya existen operaciones separadas y la actualización omite toda carga
de catálogo. La candidata B selecciona módulos localmente, pero todavía no
descarga/verifica/activa por sí sola el ZIP ni recibe entitlements del VPS. La
consolidación futura tampoco forma parte de estos scripts.

Antes de distribuir a otra sucursal: generar y verificar el paquete completo,
definir su identidad y preparar un catálogo inicial sin sobrescrituras. Antes de
actualizar una sucursal operativa: esperar al actualizador transaccional o, durante
esta fase de desarrollo, usar ventana supervisada, respaldo verificado, migraciones
revisadas y comprobación posterior. Volver al código anterior no revierte una
migración ni recupera ventas; la recuperación debe considerar compatibilidad de
esquema y datos nuevos.
