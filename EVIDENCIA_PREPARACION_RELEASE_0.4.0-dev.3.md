# Preparación de release `0.4.0-dev.3`

Fecha de preparación: 2026-09-17.

## Alcance y estado operativo

Esta candidata se prepara exclusivamente para desarrollo y aceptación en
laboratorio. **No existe una sucursal en producción y este documento no autoriza
un despliegue productivo.** La instalación `C:\LosTocayosPOS` es un laboratorio y
no se modifica durante la preparación del código.

La publicación de catálogo incluida cambia solamente el producto `AM` de la
sucursal `ARBOLEDAS`: nombre corto `Topo` y precio vigente `$32.00`. La semilla
incluye esos valores para instalaciones limpias. Una actualización nunca debe
ejecutar `cargar_datos_iniciales`.

## Parche explícito de catálogo

El comando versionado exige que `SUCURSAL_CLAVE=ARBOLEDAS`, exige además el
alcance por argumento y valida los valores anteriores antes de escribir. Su
salida `PARCHE_CATALOGO={...}` no contiene secretos y registra estado,
antes/después y campos modificados.

Primero se valida sin cambios:

```powershell
& .\.venv\Scripts\python.exe manage.py `
  aplicar_parche_catalogo_0_4_0_dev_3_topo `
  --sucursal ARBOLEDAS --dry-run
```

Después del respaldo inmediato y de aplicar migraciones:

```powershell
& .\.venv\Scripts\python.exe manage.py `
  aplicar_parche_catalogo_0_4_0_dev_3_topo `
  --sucursal ARBOLEDAS
```

Repetir el comando es seguro: si el producto ya tiene `Topo` y `$32.00`, informa
`ya_aplicado`. Si encuentra otro nombre corto o precio, se detiene para no
sobrescribir una personalización. La vigencia del nuevo precio es
`2026-09-17`; el precio anterior se conserva y se cierra el `2026-09-16`.

## Validaciones previas a construir

```powershell
& .\.venv\Scripts\python.exe manage.py test catalogo.test_parche_topo

$env:DJANGO_SETTINGS_MODULE = "pos.settings_test"
$env:DJANGO_ALLOW_INSECURE_TEST_SETTINGS = "1"
& .\.venv\Scripts\python.exe -m django check --settings pos.settings_test
& .\.venv\Scripts\python.exe -m django makemigrations `
  --check --dry-run --settings pos.settings_test

& .\.venv\Scripts\python.exe -m unittest tests.test_release_servidor
& powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\tests\test_instalador_windows.ps1
```

La suite completa y las validaciones funcionales de los otros dominios deben
terminar antes de construir la candidata.

## Construcción reproducible pendiente de integración

La construcción final se realiza desde un commit limpio que contenga todos los
cambios aceptados. Debe usarse un wheelhouse plano para CPython 3.13 x64; un
paquete `--source-only` no sirve para instalación limpia. Se debe fijar el mismo
commit y `SOURCE_DATE_EPOCH` en ambos builds, utilizar dos directorios de salida
vacíos y comparar el SHA-256 de los ZIP.

```powershell
$commit = git rev-parse HEAD
$epoch = git show -s --format=%ct $commit

& .\.venv\Scripts\python.exe .\herramientas\release_servidor.py build `
  --version 0.4.0-dev.3 --source . --output .\release\build-1 `
  --commit $commit --source-date-epoch $epoch `
  --wheelhouse ..\wheelhouse-base-a

& .\.venv\Scripts\python.exe .\herramientas\release_servidor.py build `
  --version 0.4.0-dev.3 --source . --output .\release\build-2 `
  --commit $commit --source-date-epoch $epoch `
  --wheelhouse ..\wheelhouse-base-a
```

Cada ZIP debe pasar `release_servidor.py verify` con su manifiesto y archivo de
sumas. No debe usarse `--allow-dirty` para la candidata final.

El wheelhouse local inventariado está en `..\wheelhouse-base-a` y contiene los
wheels nativos `pillow`, `psycopg_binary` y `pywin32` para `cp313-win_amd64`.
El constructor volverá a comprobar que coincide exactamente con
`requirements-lock.txt`.

## Secuencia preparada para instalación y actualización

1. Registrar estado del laboratorio sin secretos: versión, salud, identidad,
   hash de `.env`, conteos y advertencias de impresora.
2. Crear y verificar un respaldo SQLite inmediatamente antes del cambio.
3. Validar la release y extraerla a un staging externo.
4. Probar primero una instalación limpia en otra ruta mediante
   `instalar-servidor.ps1` y su inicialización explícita de Arboledas. Como este
   host no dispone de Windows Sandbox ni Hyper-V, complementar esa prueba con
   extracción del ZIP en un directorio aislado, un venv creado desde el
   wheelhouse, migraciones, aprovisionamiento, semilla y `/salud/` en un puerto
   alterno. Esta comprobación del artefacto no sustituye la validación del
   servicio Windows, que ya se ejercitó en el recorrido A→B anterior.
5. Para `C:\LosTocayosPOS`, usar `actualizar-servidor.ps1`; no ejecutar
   directamente `instalar-servicio-lan.ps1`.
6. Tras las migraciones, ejecutar el dry-run del parche, revisar su JSON y
   aplicarlo una vez.
7. Verificar `/salud/`, integridad SQLite, servicio, ACL, tarea de respaldo,
   logs y conservación de `.env`, identidad, usuarios, módulos, ventas, medios,
   respaldos y todo producto distinto de `AM`.
8. Ejecutar la aceptación manual. Una advertencia de conectividad de impresora
   se registra como no bloqueante; no equivale a una prueba física.

## Resultados de preparación de esta subárea

- `catalogo.test_parche_topo`: 5 pruebas aprobadas.
- `tests.test_release_servidor`: 37 pruebas aprobadas.
- `django check`: sin hallazgos con `pos.settings_test`.
- `makemigrations --check --dry-run`: sin cambios pendientes.
- `tests/test_instalador_windows.ps1 -SkipAcl`: aprobado. La ejecución completa
  de ACL queda para la validación elevada de integración.
- `VERSION`: incrementada de forma monótona a `0.4.0-dev.3`.

No se construyó aún el ZIP porque el árbol compartido sigue recibiendo la
integración funcional y no constituye un commit limpio e inmutable.

## Evidencia aún pendiente

- SHA-256 idéntico de dos builds integrados.
- Resultado de instalación limpia separada.
- Pre/post y salida JSON del parche en `C:\LosTocayosPOS`.
- Resultado de la suite completa e inspección funcional.

Hasta completar esa evidencia, `0.4.0-dev.3` sigue siendo una candidata de
desarrollo y no una base estable.
