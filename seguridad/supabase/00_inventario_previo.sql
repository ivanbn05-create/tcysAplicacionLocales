-- Inventario de sólo lectura. Ejecutar y exportar ANTES de aplicar cambios.
-- No consulta filas de negocio ni modifica objetos persistentes.

SELECT current_database() AS base,
       current_user AS ejecutor,
       now() AS capturado_en,
       current_setting('server_version') AS version_postgresql;

-- Este manifiesto debe registrar exactamente 20 tablas antes de ejecutar 01.
-- La huella permite demostrar que se revisó el mismo conjunto de objetos.
WITH tablas AS (
    SELECT clase.relname
      FROM pg_class clase
      JOIN pg_namespace esquema ON esquema.oid = clase.relnamespace
     WHERE esquema.nspname = 'public'
       AND clase.relkind IN ('r', 'p')
)
SELECT count(*) AS total_tablas_public,
       string_agg(relname, ', ' ORDER BY relname) AS manifiesto_tablas,
       md5(string_agg(relname, E'\n' ORDER BY relname)) AS huella_manifiesto
  FROM tablas;

WITH obligatorias(tabla) AS (
    VALUES
      ('auth_user'),
      ('django_session'),
      ('pedidos_sesionactiva'),
      ('pedidos_pedido'),
      ('pedidos_sucursalcliente'),
      ('pedidos_itempedido'),
      ('pedidos_producto')
)
SELECT obligatorias.tabla,
       (clase.oid IS NOT NULL) AS existe,
       COALESCE(clase.relrowsecurity, FALSE) AS rls_habilitado,
       COALESCE(propietario.rolname, '<ausente>') AS propietario
  FROM obligatorias
  LEFT JOIN pg_namespace esquema ON esquema.nspname = 'public'
  LEFT JOIN pg_class clase
    ON clase.relnamespace = esquema.oid
   AND clase.relname = obligatorias.tabla
   AND clase.relkind IN ('r', 'p')
  LEFT JOIN pg_roles propietario ON propietario.oid = clase.relowner
 ORDER BY obligatorias.tabla;

SELECT esquema.nspname AS esquema,
       objeto.relname AS objeto,
       objeto.relkind,
       propietario.rolname AS propietario,
       objeto.relrowsecurity,
       objeto.relforcerowsecurity
  FROM pg_class objeto
  JOIN pg_namespace esquema ON esquema.oid = objeto.relnamespace
  JOIN pg_roles propietario ON propietario.oid = objeto.relowner
 WHERE esquema.nspname = 'public'
 ORDER BY objeto.relkind, objeto.relname;

SELECT table_schema, table_name, grantee, privilege_type
  FROM information_schema.table_privileges
 WHERE table_schema = 'public'
 ORDER BY table_name, grantee, privilege_type;

SELECT table_schema, table_name, column_name, grantee, privilege_type
  FROM information_schema.column_privileges
 WHERE table_schema = 'public'
 ORDER BY table_name, column_name, grantee, privilege_type;

-- Las secuencias no aparecen en information_schema.table_privileges. Esta
-- matriz registra tanto concesiones directas como las heredadas de PUBLIC.
SELECT secuencia.relname AS secuencia,
       identidad.rolname,
       has_sequence_privilege(identidad.oid, secuencia.oid, 'SELECT') AS puede_select,
       has_sequence_privilege(identidad.oid, secuencia.oid, 'USAGE') AS puede_usage,
       has_sequence_privilege(identidad.oid, secuencia.oid, 'UPDATE') AS puede_update
  FROM pg_class secuencia
  JOIN pg_namespace esquema ON esquema.oid = secuencia.relnamespace
 CROSS JOIN pg_roles identidad
 WHERE esquema.nspname = 'public'
   AND secuencia.relkind = 'S'
   AND identidad.rolname IN ('anon', 'authenticated', 'service_role', 'pos_local_reader')
 ORDER BY secuencia.relname, identidad.rolname;

SELECT schemaname, tablename, policyname, permissive, roles, cmd, qual, with_check
  FROM pg_policies
 WHERE schemaname = 'public'
 ORDER BY tablename, policyname;

SELECT procedimiento.oid::regprocedure AS rutina,
       procedimiento.prokind,
       procedimiento.prosecdef AS security_definer,
       procedimiento.proconfig,
       has_function_privilege('anon', procedimiento.oid, 'EXECUTE') AS anon_execute,
       has_function_privilege('authenticated', procedimiento.oid, 'EXECUTE') AS authenticated_execute,
       has_function_privilege('service_role', procedimiento.oid, 'EXECUTE') AS service_role_execute
  FROM pg_proc procedimiento
  JOIN pg_namespace esquema ON esquema.oid = procedimiento.pronamespace
 WHERE esquema.nspname = 'public'
 ORDER BY rutina;

SELECT propietario.rolname AS creador,
       esquema.nspname AS esquema,
       defaults.defaclobjtype AS tipo_objeto,
       COALESCE(receptor.rolname, 'PUBLIC') AS receptor,
       permiso.privilege_type
  FROM pg_default_acl defaults
  JOIN pg_roles propietario ON propietario.oid = defaults.defaclrole
  JOIN pg_namespace esquema ON esquema.oid = defaults.defaclnamespace
 CROSS JOIN LATERAL aclexplode(defaults.defaclacl) permiso
  LEFT JOIN pg_roles receptor ON receptor.oid = permiso.grantee
 WHERE esquema.nspname = 'public'
 ORDER BY creador, tipo_objeto, receptor, permiso.privilege_type;

SELECT rol.rolname,
       has_database_privilege(rol.oid, current_database(), 'CONNECT') AS puede_conectar,
       has_database_privilege(rol.oid, current_database(), 'CREATE') AS puede_crear_esquemas,
       has_database_privilege(rol.oid, current_database(), 'TEMP') AS puede_crear_temporales,
       has_schema_privilege(rol.oid, 'public', 'USAGE') AS usa_esquema_public,
       has_schema_privilege(rol.oid, 'public', 'CREATE') AS crea_en_esquema_public
  FROM pg_roles rol
 WHERE rol.rolname IN ('anon', 'authenticated', 'service_role', 'pos_local_reader')
 ORDER BY rol.rolname;

SELECT padre.rolname AS rol_concedido,
       miembro.rolname AS miembro,
       membresia.admin_option
  FROM pg_auth_members membresia
  JOIN pg_roles padre ON padre.oid = membresia.roleid
  JOIN pg_roles miembro ON miembro.oid = membresia.member
 WHERE padre.rolname IN ('anon', 'authenticated', 'service_role', 'pos_local_reader')
    OR miembro.rolname IN ('anon', 'authenticated', 'service_role', 'pos_local_reader')
 ORDER BY padre.rolname, miembro.rolname;

SELECT current_setting('pgrst.db_schemas', TRUE) AS esquemas_data_api;

SELECT ssl, version, cipher, bits
  FROM pg_stat_ssl
 WHERE pid = pg_backend_pid();
