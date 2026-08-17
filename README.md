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
comentario general se imprime al final.

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

`cargar_datos_iniciales` crea los 39 productos activos confirmados, incluidas las
bebidas y las variantes de quesadilla/lonche. También importa los nombres de
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

Cubren apertura y cancelación, captura hasta 24 comensales, preparación global, acceso
para tabletas, matriz por comensal, bebidas acumuladas, clientes, procesamiento, cobro
opcional con o sin ticket y generación de los dos formatos térmicos.
