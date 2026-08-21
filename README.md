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

## Pruebas

```powershell
python manage.py test
```

Cubren apertura y cancelación, captura hasta 24 comensales, promociones y sus
componentes, preparación global/individual excluyente, entrega programada, salsas,
terminal, orden de impresión, acceso para tabletas, bebidas acumuladas, clientes,
procesamiento, cobro opcional y generación de los formatos térmicos.
