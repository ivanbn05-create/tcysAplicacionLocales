# Los Tocayos POS

Primera versión local del punto de venta de Los Tocayos. Permite operar pedidos de
comedor, domicilio y sucursales, capturar partidas por comensal, procesar la orden,
cobrarla y generar comandas/cuentas térmicas en modo ráster.

## Inicio rápido con Docker

1. Copia `.env.example` como `.env`. El valor predeterminado `PRINT_BACKEND=tcp` envía
   directamente a la impresora térmica configurada.
2. Ejecuta `docker compose up --build`.
3. Abre `http://localhost:8000`.

La base se migra y el menú se carga automáticamente. En modo archivo, las impresiones
de prueba quedan en `media/impresiones/` sin gastar papel.

## Inicio local sin Docker

En PowerShell, ejecuta `./iniciar-local.ps1`. La primera vez crea el entorno e instala
dependencias. Los pasos equivalentes son:

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python manage.py migrate
.\.venv\Scripts\python manage.py cargar_datos_iniciales
.\.venv\Scripts\python manage.py runserver 0.0.0.0:8000
```

La configuración local usa SQLite para facilitar la prueba. Docker usa PostgreSQL 16.

### Inicio rápido para pruebas en la LAN

En la computadora principal, haz doble clic en `iniciar-prueba-lan.bat`. El iniciador
comprueba dependencias, aplica migraciones y catálogos pendientes, y publica la
aplicación exactamente en `http://192.168.0.30:8000`. También activa impresión TCP
síncrona, por lo que no requiere abrir por separado el consumidor de impresión.

La ventana debe permanecer abierta durante la prueba; `Ctrl+C` detiene el servidor. La
computadora necesita conservar la dirección `192.168.0.30`, preferentemente mediante
una reserva DHCP en el módem o router. Si Windows recibe otra IP, debe corregirse la
reserva antes de usar este iniciador.

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

El diagnóstico técnico de la fase local y los riesgos pendientes antes de un piloto
multiusuario están en `diagnostico_calidad_pos_local.md`.

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

La pantalla de acceso muestra `Impresora lista`, `Impresora sin conexión` o `Sólo vista
previa`. Un trabajo en modo archivo queda como `Vista previa generada`; sólo se marca
`Impreso` después de completar el envío TCP.

## Identidad de marca

La interfaz usa el logotipo actual y los lineamientos del manual de identidad:
amarillo `#FFED00`, verde `#4AA736`, rojo `#E42522`, carbón `#353436` y Montserrat.
Los recursos están empaquetados localmente para que la PWA no dependa de internet.

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
confirmados del día. Se asignan al primer espacio libre de la sucursal correspondiente
y quedan abiertos para revisión antes de imprimir. La conexión compartida se configura
en `.env`; la contraseña nunca se guarda en Git:

```env
PEDIDOS_SUCURSALES_AUTO_SYNC=true
PEDIDOS_SUCURSALES_SYNC_SECONDS=300
PEDIDOS_SUCURSALES_HORA_INICIO=06:00
PEDIDOS_SUCURSALES_HORA_FIN=17:35
PEDIDOS_SUCURSALES_DB_HOST=aws-1-us-east-2.pooler.supabase.com
PEDIDOS_SUCURSALES_DB_PORT=5432
PEDIDOS_SUCURSALES_DB_NAME=postgres
PEDIDOS_SUCURSALES_DB_USER=postgres.uxcuejhzueagtscdxwlo
PEDIDOS_SUCURSALES_DB_PASSWORD=secreto-local
PEDIDOS_SUCURSALES_DB_SSLMODE=require
```

También se acepta una URI completa en `PEDIDOS_SUCURSALES_DATABASE_URL`. Si no hay
credenciales de Supabase, `PEDIDOS_SUCURSALES_DB` puede apuntar a una SQLite local para
desarrollo sin conexión.

La sincronización también puede ejecutarse manualmente:

```powershell
.\.venv\Scripts\python manage.py sincronizar_pedidos_sucursales
```

La consulta remota no modifica estados ni datos en Supabase. El control idempotente se
guarda únicamente en la base local del POS. El intervalo predeterminado de cinco
minutos reduce la carga remota a un máximo aproximado de 139 consultas diarias; fuera
del horario configurado no se abre ninguna conexión. Los cinco minutos posteriores a
las 17:30 funcionan como margen para recoger el último pedido permitido del día. Al
entrar a la pestaña **Sucursales** se solicita una revisión inmediata; si ya hubo una en
los últimos cinco minutos se reutiliza ese estado. La revisión periódica sólo permanece
activa mientras el usuario está en esa pestaña.

## Pruebas

```powershell
python manage.py test
```

Cubren apertura y cancelación, captura hasta 24 comensales, promociones y sus
componentes, preparación global/individual excluyente, entrega programada, salsas,
terminal, orden de impresión, acceso para tabletas, bebidas acumuladas, clientes,
procesamiento, cobro opcional, captura decimal de sucursales, importación idempotente y
generación de los formatos térmicos.
