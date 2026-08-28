# Endurecimiento de Supabase

El POS sólo necesita leer cuatro tablas del sistema de pedidos. No debe usar el
rol `postgres`, una secret/service-role key ni las identidades `anon` o
`authenticated`. La cuenta histórica `postgres.<project-ref>` puede crear roles
y bases y omite RLS; `SET TRANSACTION READ ONLY` sólo protege una transacción del
proceso, no la credencial si se copia o roba.

## Aplicación en el proyecto remoto

1. Ejecuta `00_inventario_previo.sql` y exporta sus resultados. Confirma qué
   aplicaciones usan `anon`, `authenticated`, `service_role`, vistas o RPC del
   esquema `public`; prueba primero en staging. El inventario es el insumo para
   una reversa específica. No restaures grants públicos genéricos.
2. En **Database Settings > SSL Configuration**, activa **Enforce SSL** y descarga
   el certificado CA del proyecto. Instálalo en una ruta legible sólo por la
   cuenta del servicio, por ejemplo
   `certs\prod-ca-2021.crt` dentro del árbol protegido de la instalación.
3. Revisa en `01_endurecer_rol_pos.sql` que los identificadores autorizados
   `1..10` correspondan al catálogo real de sucursales de esta instalación.
   Después ejecútalo como `postgres`. El cambio conserva al
   aplicativo web, que actualmente conecta como propietario, pero bloquea la
   Data API para `anon` y `authenticated` sobre las tablas Django y crea
   `pos_local_reader` sin capacidad de inicio de sesión. Conserva los grants de
   `service_role` para no romper un backend confiable sin inventario; esa clave
   debe permanecer exclusivamente del lado servidor. También revoca la
   ejecución pública de funciones `SECURITY DEFINER`; esto puede afectar
   consumidores existentes y es la razón de exigir inventario y staging.
4. Genera una contraseña aleatoria larga por un canal que no guarde el secreto
   en Git. Mientras el rol sigue en `NOLOGIN`, con `psql` ejecuta
   `SET password_encryption = 'scram-sha-256';`, después
   `\password pos_local_reader` y sólo al final
   `ALTER ROLE pos_local_reader LOGIN;`. El prompt evita incluir la contraseña
   en el historial y el orden evita una ventana con el rol activo sin el secreto
   nuevo.
5. Configura el servicio Windows con `PEDIDOS_SUCURSALES_FUENTE=supabase`,
   `PEDIDOS_SUCURSALES_AUTO_SYNC=true` y `PEDIDOS_SUCURSALES_DB_USER` igual a
   `pos_local_reader.<project-ref>`, la contraseña nueva,
   `PEDIDOS_SUCURSALES_DB_SSLMODE=verify-full` y
   `PEDIDOS_SUCURSALES_DB_SSLROOTCERT` apuntando al certificado descargado.
   No uses `SB_PASSWORD` ni una URI con `postgres`, `service_role` o
   `supabase_admin`.
6. Ejecuta `02_verificar_endurecimiento.sql` como `postgres`, después de activar
   el lector. Es una transacción de sólo lectura y falla con una excepción ante
   cualquier deriva: exige 20/20 tablas con RLS, exactamente cuatro políticas
   del lector, cero acceso de `PUBLIC`/`anon`/`authenticated` a objetos de
   `public`, cero acceso del lector a secuencias/vistas/rutinas y sólo las 16
   columnas autorizadas. El resultado válido termina con `cumple = true`,
   `tablas_public = tablas_con_rls = 20`, `politicas_public = 4` y `COMMIT`.
7. Prueba la sincronización y después rota la contraseña histórica de `postgres`
   en Supabase. Coordina esa rotación con la aplicación web de pedidos, que usa
   su propia `DATABASE_URL`.

Si aparece una regresión o se sospecha exposición de la credencial, ejecuta
`03_desactivar_lector_emergencia.sql`: deshabilita nuevos inicios de sesión,
termina conexiones vigentes y retira los grants del lector. La aplicación fuente
propietaria no recibe permisos públicos como parte de esa acción.

## Controles del Dashboard que el SQL no puede comprobar

- Restringe Postgres y el pooler a las IP públicas de las ubicaciones que lo
  consumen en **Network Restrictions**. Si las IP cambian, documenta el proceso
  de actualización antes de cerrar la lista.
- Retira `public` de **Exposed schemas** o desactiva la Data API si ninguna otra
  aplicación la utiliza. Las revocaciones y RLS del script siguen siendo defensa
  en profundidad.
- Revisa que no existan API secret/service-role keys en navegadores, repositorios,
  instaladores, logs o gestores de soporte. Rota cualquier clave expuesta.
- Supabase Storage y Auth no aparecen en este repositorio. Si el proyecto remoto
  los utiliza por otros clientes, audita por separado políticas de
  `storage.objects`, buckets públicos, proveedores Auth, redirecciones y MFA.
- Confirma backups/PITR, retención, miembros del proyecto y MFA desde el
  Dashboard. La conexión SQL no expone toda esa configuración de plataforma.
- Repite los `ALTER DEFAULT PRIVILEGES` para cada rol migrador futuro que pueda
  crear objetos en `public`. El script cubre dinámicamente a los propietarios
  actuales del esquema, sus objetos y rutinas, pero los defaults son específicos
  de cada creador.

## Estado de aplicación

Estos archivos preparan y validan la remediación, pero no prueban que se haya
aplicado al proyecto remoto. La alerta continúa abierta hasta ejecutar el
procedimiento en una ventana de mantenimiento, guardar la salida satisfactoria
de `02_verificar_endurecimiento.sql` y volver a ejecutar Security Advisor.

## Política de integridad de pedidos

El importador valida el pedido completo antes de abrir un ticket. Cantidades
deben ser finitas, positivas, estar entre `0.001` y `999.999` y tener máximo tres
decimales. Los precios deben ser finitos, no negativos y tener máximo dos
decimales. El precio remoto nunca se copia como autoridad: debe coincidir con el
precio local vigente y el ticket usa el valor local. Un concepto inválido,
duplicado, desconocido o con precio divergente rechaza ese pedido completo, pero
la sincronización continúa con los demás. Una divergencia legítima requiere
actualizar primero el catálogo local y repetir la sincronización.

La lectura remota se ejecuta en transacción `READ ONLY`, con límites de tiempo,
máximo 500 pedidos por ciclo (configurable hasta 2000) y máximo 100 conceptos por
pedido. El `application_name` es `tocayos_pos_sync` para identificarla en
telemetría de PostgreSQL.
