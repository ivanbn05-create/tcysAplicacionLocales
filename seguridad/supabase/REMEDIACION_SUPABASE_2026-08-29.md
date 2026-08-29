# Remediacion Supabase RLS - 2026-08-29

Este documento deja contexto operativo para agentes de codigo futuros que trabajen
con `tcysAplicacionLocales` y la integracion del POS con la app remota
`tcysPedidosSucursales`.

## Resumen ejecutivo

- La app web de pedidos en Render/Django usa conexion directa PostgreSQL via
  pooler de Supabase, no Supabase Data API.
- El POS local debe leer pedidos remotos usando conexion directa PostgreSQL con
  el rol dedicado `pos_local_reader.<project-ref>`.
- El esquema `public` fue retirado de Supabase Data API desde el Dashboard.
- SSL fue habilitado desde Supabase Dashboard.
- El certificado CA fue guardado en:
  `C:\tcysAplicacionLocales\certs\prod-ca-2021.crt`
- Se aplico endurecimiento RLS a la base remota de Supabase.
- Se activo `pos_local_reader` con una contrasena aleatoria nueva y se guardo en
  `C:\tcysAplicacionLocales\.env`.

## Estado confirmado

Verificacion final, 2026-08-29:

- `public` tiene 20 tablas.
- 20/20 tablas tienen RLS habilitado.
- Existen 4 politicas RLS, todas llamadas `pos_local_reader_select`, en:
  - `pedidos_pedido`
  - `pedidos_sucursalcliente`
  - `pedidos_itempedido`
  - `pedidos_producto`
- `anon` y `authenticated` no conservan grants de tablas en `public`.
- `anon` y `authenticated` no conservan grants de secuencias en `public`.
- `pos_local_reader` no tiene grants de secuencias.
- `pos_local_reader` esta en `default_transaction_read_only=on`.
- `pos_local_reader` puede leer productos y columnas permitidas de pedidos.
- `pos_local_reader` no puede leer `auth_user.password`.
- `pos_local_reader` no puede leer `pedidos_precio`.
- La prueba HTTP a `/rest/v1/pedidos_producto?select=id&limit=1` con anon key
  regreso `404` y `data_api_public_table_accessible=False`.
- La conexion tipo app web como `postgres.<project-ref>` pudo leer
  `django_migrations` y escribir en `django_session` dentro de una transaccion
  con `ROLLBACK`.
- La URL publica de Render `https://tcyspedidossucursales.onrender.com/`
  regreso `200` y mostro la pantalla `Login | Los Tocayos`.
- El endpoint publico de Render
  `https://tcyspedidossucursales.onrender.com/api/horarios/` regreso `200`
  con JSON valido. En la prueba reporto pedidos cerrados por horario:
  `hora_inicio=06:00`, `hora_fin=17:30`, `hora_actual=17:38`.

## Evidencia local

Archivos generados en:
`C:\Users\Srv1\Downloads\tcysPedidosSucursales-main\exports`

- Diagnostico inicial con problema vigente:
  `supabase_rls_diagnostico_20260829_163239.txt`
- Diagnostico posterior al endurecimiento:
  `supabase_rls_diagnostico_20260829_172740.txt`
- Diagnostico final tras activar lector:
  `supabase_rls_diagnostico_20260829_173034.txt`
- Aplicacion de endurecimiento:
  `supabase_rls_endurecimiento_01_compatible_20260829_172724.txt`
- Verificacion final:
  `supabase_rls_verificacion_02_compatible_20260829_172858.txt`
- SQL compatible aplicado:
  `01_endurecer_rol_pos_supabase_compatible.sql`
- SQL verificador compatible:
  `02_verificar_endurecimiento_supabase_compatible.sql`

Los reportes no guardan contrasenas ni JWTs. Contienen nombres de roles,
metadatos de permisos y nombres de objetos SQL.

## Configuracion actual del POS

`C:\tcysAplicacionLocales\.env` fue actualizado con estas variables:

```env
PEDIDOS_SUCURSALES_DATABASE_URL=
PEDIDOS_SUCURSALES_FUENTE=supabase
PEDIDOS_SUCURSALES_AUTO_SYNC=true
PEDIDOS_SUCURSALES_DB_HOST=aws-1-us-east-2.pooler.supabase.com
PEDIDOS_SUCURSALES_DB_PORT=5432
PEDIDOS_SUCURSALES_DB_NAME=postgres
PEDIDOS_SUCURSALES_DB_USER=pos_local_reader.<project-ref>
PEDIDOS_SUCURSALES_DB_PASSWORD=<secreto local aleatorio>
PEDIDOS_SUCURSALES_DB_SSLMODE=verify-full
PEDIDOS_SUCURSALES_DB_SSLROOTCERT=C:\tcysAplicacionLocales\certs\prod-ca-2021.crt
```

No usar `SB_PASSWORD`, `SERVICE_ROLE`, `ANON_PUBLIC`, `postgres.<project-ref>` ni
`supabase_admin` para la integracion del POS.

## Ajustes necesarios a los SQL originales

Los SQL originales en `seguridad\supabase` eran correctos en intencion, pero en
este proyecto Supabase administrado el rol `postgres` no es superusuario real.
Por eso el script original fallo y PostgreSQL hizo `ROLLBACK` sin cambios.

La version compatible aplicada hizo tres ajustes:

1. No repitio `ALTER ROLE pos_local_reader NOSUPERUSER ... NOBYPASSRLS`.
   El `CREATE ROLE` ya fija esos atributos. Repetirlos falla en Supabase porque
   solo un superusuario real puede alterar atributos de superusuario/bypass RLS.
2. Acepto exactamente la membresia automatica inevitable:
   `pos_local_reader -> postgres`, `admin_option=true`, otorgada por
   `supabase_admin`.
   Cualquier otra membresia del lector debe seguir considerandose fallo.
3. Cerra tambien defaults globales de funciones para los propietarios actuales.
   En PostgreSQL, funciones nuevas tienen `EXECUTE` para `PUBLIC` por default.
   El `REVOKE ... IN SCHEMA public` no basta para eliminar ese default global.

Los cambios no alteran la politica de negocio:

- `pedidos_pedido`: solo confirmado, no eliminado, sucursales `1..10`, ultimos
  2 dias.
- `pedidos_sucursalcliente`: solo IDs `1..10`.
- `pedidos_producto`: lectura completa permitida al lector.
- `pedidos_itempedido`: solo items cuyo pedido padre sea visible por RLS.

## Instrucciones para agentes futuros

- Tratar `SERVICE_ROLE` como secreto critico. No imprimirlo, no guardarlo en
  reportes, no usarlo en navegador ni en el POS.
- No volver a exponer `public` en Supabase Data API salvo que se disene una API
  deliberada con RLS y grants minimos.
- No desactivar SSL enforcement.
- No cambiar el POS a Supabase API. Debe usar PostgreSQL directo con
  `pos_local_reader`.
- No ampliar `pos_local_reader` a tablas sensibles como `auth_user`,
  `django_session`, `pedidos_sesionactiva`, `pedidos_precio` o secuencias.
- No cambiar el whitelist `1..10` sin confirmar primero el catalogo real de
  sucursales/clientes.
- Si se crean tablas, funciones o migraciones nuevas en `public`, revisar que
  no reabran grants a `PUBLIC`, `anon`, `authenticated` o `pos_local_reader`.
- Si la sincronizacion del POS falla, revisar primero:
  - ruta del certificado CA,
  - `sslmode=verify-full`,
  - usuario `pos_local_reader.<project-ref>`,
  - password del lector,
  - host `aws-1-us-east-2.pooler.supabase.com`,
  - que `PEDIDOS_SUCURSALES_DATABASE_URL` este vacia.

## Pendientes recomendados

1. Rotar `SERVICE_ROLE` en Supabase Dashboard. Durante esta sesion su valor
   aparecio accidentalmente en salida de terminal, por lo que debe tratarse como
   expuesto.
2. Ejecutar Supabase Security Advisor y guardar evidencia de que las alertas de
   RLS/Data API quedaron cerradas.
3. Probar login real de sucursal/admin en la URL publica de Render.
4. Antes de activar Network Restrictions, confirmar IPs salientes de Render y de
   los locales. En Render Free pueden cambiar, asi que hacerlo sin esa lista
   puede romper la app.
5. Rotar la password historica de `postgres.<project-ref>` solo despues de
   actualizar `DATABASE_URL` en Render y confirmar deploy saludable.

## Comandos utiles ya usados

```powershell
& 'C:\tcysAplicacionLocales\.venv\Scripts\python.exe' 'exports\supabase_rls_diag.py' --admin-from-sb --pooler-host aws-1-us-east-2.pooler.supabase.com --pooler-port 5432
& 'C:\tcysAplicacionLocales\.venv\Scripts\python.exe' 'exports\supabase_test_reader.py'
& 'C:\tcysAplicacionLocales\.venv\Scripts\python.exe' 'exports\supabase_test_data_api_closed.py'
& 'C:\tcysAplicacionLocales\.venv\Scripts\python.exe' 'exports\supabase_test_app_owner.py'
```
