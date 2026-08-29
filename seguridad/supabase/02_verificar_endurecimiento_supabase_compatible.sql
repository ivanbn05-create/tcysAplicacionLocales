-- Verificador de sólo lectura y fail-closed para el estado posterior a 01.
-- Ejecutar como `postgres` DESPUÉS de asignar una contraseña nueva y habilitar
-- LOGIN a pos_local_reader. No consulta filas de negocio ni cambia permisos.
-- Una excepción significa que el estado NO es apto para producción.

BEGIN;
SET TRANSACTION READ ONLY;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

DO $verificar_endurecimiento$
DECLARE
    total_tablas integer;
    rol_pos oid;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon')
       OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated')
       OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        RAISE EXCEPTION
            'Verificación fallida: faltan roles estándar anon/authenticated/service_role';
    END IF;

    SELECT oid
      INTO rol_pos
      FROM pg_roles
     WHERE rolname = 'pos_local_reader';

    IF rol_pos IS NULL THEN
        RAISE EXCEPTION
            'Verificación fallida: no existe pos_local_reader';
    END IF;

    SELECT count(*)
      INTO total_tablas
      FROM pg_class clase
      JOIN pg_namespace esquema ON esquema.oid = clase.relnamespace
     WHERE esquema.nspname = 'public'
       AND clase.relkind IN ('r', 'p');

    IF total_tablas <> 20 OR EXISTS (
        SELECT 1
          FROM pg_class clase
          JOIN pg_namespace esquema ON esquema.oid = clase.relnamespace
         WHERE esquema.nspname = 'public'
           AND clase.relkind IN ('r', 'p')
           AND NOT clase.relrowsecurity
    ) THEN
        RAISE EXCEPTION
            'Verificación fallida: se requieren exactamente 20 tablas public y RLS habilitado en todas';
    END IF;

    -- Deben existir únicamente cuatro políticas SELECT, una en cada tabla
    -- autorizada, aplicables exclusivamente al lector.
    IF (SELECT count(*)
          FROM pg_policy politica
          JOIN pg_class clase ON clase.oid = politica.polrelid
          JOIN pg_namespace esquema ON esquema.oid = clase.relnamespace
         WHERE esquema.nspname = 'public') <> 4
       OR EXISTS (
           SELECT 1
             FROM pg_policy politica
             JOIN pg_class clase ON clase.oid = politica.polrelid
             JOIN pg_namespace esquema ON esquema.oid = clase.relnamespace
            WHERE esquema.nspname = 'public'
              AND (
                  clase.relname NOT IN (
                      'pedidos_pedido',
                      'pedidos_sucursalcliente',
                      'pedidos_itempedido',
                      'pedidos_producto'
                  )
                  OR politica.polname <> 'pos_local_reader_select'
                  OR politica.polcmd <> 'r'
                  OR NOT politica.polpermissive
                  OR politica.polroles <> ARRAY[rol_pos]
                  OR politica.polqual IS NULL
                  OR politica.polwithcheck IS NOT NULL
              )
       )
       OR EXISTS (
           WITH esperadas(tabla) AS (
               VALUES
                 ('pedidos_pedido'),
                 ('pedidos_sucursalcliente'),
                 ('pedidos_itempedido'),
                 ('pedidos_producto')
           )
           SELECT 1
             FROM esperadas
            WHERE NOT EXISTS (
                SELECT 1
                  FROM pg_policy politica
                  JOIN pg_class clase ON clase.oid = politica.polrelid
                  JOIN pg_namespace esquema ON esquema.oid = clase.relnamespace
                 WHERE esquema.nspname = 'public'
                   AND clase.relname = esperadas.tabla
                   AND politica.polname = 'pos_local_reader_select'
            )
       ) THEN
        RAISE EXCEPTION
            'Verificación fallida: el conjunto de políticas RLS no es exactamente el esperado';
    END IF;

    -- Defensa contra una política accidentalmente ampliada a USING (TRUE). Las
    -- comprobaciones se basan en tokens semánticos y no en formato de pg_get_expr.
    IF EXISTS (
        SELECT 1
          FROM pg_policy politica
          JOIN pg_class clase ON clase.oid = politica.polrelid
          JOIN pg_namespace esquema ON esquema.oid = clase.relnamespace
         CROSS JOIN LATERAL (
             SELECT lower(pg_get_expr(politica.polqual, politica.polrelid)) AS expresion
         ) definicion
         WHERE esquema.nspname = 'public'
           AND politica.polname = 'pos_local_reader_select'
           AND CASE clase.relname
               WHEN 'pedidos_pedido' THEN
                   strpos(definicion.expresion, 'estado') = 0
                   OR strpos(definicion.expresion, 'confirmado') = 0
                   OR strpos(definicion.expresion, 'eliminado') = 0
                   OR strpos(definicion.expresion, 'sucursal_cliente_id') = 0
                   OR strpos(definicion.expresion, 'fecha_confirmacion') = 0
                   OR strpos(definicion.expresion, '2 days') = 0
                   OR strpos(
                       regexp_replace(definicion.expresion, '[[:space:]]', '', 'g'),
                       'array[1,2,3,4,5,6,7,8,9,10]'
                   ) = 0
               WHEN 'pedidos_sucursalcliente' THEN
                   strpos(definicion.expresion, 'id') = 0
                   OR strpos(
                       regexp_replace(definicion.expresion, '[[:space:]]', '', 'g'),
                       'array[1,2,3,4,5,6,7,8,9,10]'
                   ) = 0
               WHEN 'pedidos_itempedido' THEN
                   strpos(definicion.expresion, 'exists') = 0
                   OR strpos(definicion.expresion, 'pedidos_pedido') = 0
                   OR strpos(definicion.expresion, 'pedido_id') = 0
               WHEN 'pedidos_producto' THEN
                   regexp_replace(definicion.expresion, '[()[:space:]]', '', 'g') <> 'true'
               ELSE TRUE
           END
    ) THEN
        RAISE EXCEPTION
            'Verificación fallida: una política RLS no conserva el filtro autorizado';
    END IF;

    -- anon/authenticated no deben alcanzar relaciones, columnas, secuencias,
    -- vistas, tablas foráneas ni rutinas de public, incluso por herencia/PUBLIC.
    IF EXISTS (
        SELECT 1
          FROM (VALUES ('anon'), ('authenticated')) identidades(rolname)
          JOIN pg_roles rol ON rol.rolname = identidades.rolname
         CROSS JOIN pg_class clase
          JOIN pg_namespace esquema ON esquema.oid = clase.relnamespace
         WHERE esquema.nspname = 'public'
           AND clase.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
           AND CASE WHEN clase.relkind = 'S' THEN
               has_sequence_privilege(rol.oid, clase.oid, 'SELECT')
               OR has_sequence_privilege(rol.oid, clase.oid, 'USAGE')
               OR has_sequence_privilege(rol.oid, clase.oid, 'UPDATE')
           ELSE
               has_table_privilege(rol.oid, clase.oid, 'SELECT')
               OR has_any_column_privilege(rol.oid, clase.oid, 'SELECT')
               OR has_table_privilege(rol.oid, clase.oid, 'INSERT')
               OR has_any_column_privilege(rol.oid, clase.oid, 'INSERT')
               OR has_table_privilege(rol.oid, clase.oid, 'UPDATE')
               OR has_any_column_privilege(rol.oid, clase.oid, 'UPDATE')
               OR has_table_privilege(rol.oid, clase.oid, 'DELETE')
               OR has_table_privilege(rol.oid, clase.oid, 'TRUNCATE')
               OR has_table_privilege(rol.oid, clase.oid, 'REFERENCES')
               OR has_any_column_privilege(rol.oid, clase.oid, 'REFERENCES')
               OR has_table_privilege(rol.oid, clase.oid, 'TRIGGER')
           END
    ) OR EXISTS (
        SELECT 1
          FROM (VALUES ('anon'), ('authenticated')) identidades(rolname)
          JOIN pg_roles rol ON rol.rolname = identidades.rolname
         CROSS JOIN pg_proc rutina
          JOIN pg_namespace esquema ON esquema.oid = rutina.pronamespace
         WHERE esquema.nspname = 'public'
           AND has_function_privilege(rol.oid, rutina.oid, 'EXECUTE')
    ) OR EXISTS (
        -- Detecta además privilegios de relación incorporados por versiones
        -- nuevas de PostgreSQL sin depender de enumerarlos por nombre.
        SELECT 1
          FROM pg_class clase
          JOIN pg_namespace esquema ON esquema.oid = clase.relnamespace
         CROSS JOIN LATERAL aclexplode(
             COALESCE(
                 clase.relacl,
                 acldefault(
                     CASE WHEN clase.relkind = 'S' THEN 'S'::"char" ELSE 'r'::"char" END,
                     clase.relowner
                 )
             )
         ) permiso
          JOIN pg_roles receptor ON receptor.oid = permiso.grantee
         WHERE esquema.nspname = 'public'
           AND clase.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
           AND receptor.rolname IN ('anon', 'authenticated')
    ) THEN
        RAISE EXCEPTION
            'Verificación fallida: anon/authenticated conservan privilegios en objetos public';
    END IF;

    -- PUBLIC tampoco debe conservar permisos directos. Se inspeccionan las ACL
    -- crudas porque PUBLIC no es un rol consultable con has_*_privilege.
    IF EXISTS (
        SELECT 1
          FROM pg_class clase
          JOIN pg_namespace esquema ON esquema.oid = clase.relnamespace
         CROSS JOIN LATERAL aclexplode(
             COALESCE(
                 clase.relacl,
                 acldefault(
                     CASE WHEN clase.relkind = 'S' THEN 'S'::"char" ELSE 'r'::"char" END,
                     clase.relowner
                 )
             )
         ) permiso
         WHERE esquema.nspname = 'public'
           AND clase.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
           AND permiso.grantee = 0
    ) OR EXISTS (
        SELECT 1
          FROM pg_attribute atributo
          JOIN pg_class clase ON clase.oid = atributo.attrelid
          JOIN pg_namespace esquema ON esquema.oid = clase.relnamespace
         CROSS JOIN LATERAL aclexplode(atributo.attacl) permiso
         WHERE esquema.nspname = 'public'
           AND atributo.attnum > 0
           AND NOT atributo.attisdropped
           AND permiso.grantee = 0
    ) OR EXISTS (
        SELECT 1
          FROM pg_proc rutina
          JOIN pg_namespace esquema ON esquema.oid = rutina.pronamespace
         CROSS JOIN LATERAL aclexplode(
             COALESCE(rutina.proacl, acldefault('f'::"char", rutina.proowner))
         ) permiso
         WHERE esquema.nspname = 'public'
           AND permiso.grantee = 0
    ) OR EXISTS (
        SELECT 1
          FROM pg_namespace esquema
         CROSS JOIN LATERAL aclexplode(
             COALESCE(esquema.nspacl, acldefault('n'::"char", esquema.nspowner))
         ) permiso
         WHERE esquema.nspname = 'public'
           AND permiso.grantee = 0
    ) THEN
        RAISE EXCEPTION
            'Verificación fallida: PUBLIC conserva privilegios en el esquema u objetos public';
    END IF;

    IF has_schema_privilege('anon', 'public', 'USAGE')
       OR has_schema_privilege('anon', 'public', 'CREATE')
       OR has_schema_privilege('authenticated', 'public', 'USAGE')
       OR has_schema_privilege('authenticated', 'public', 'CREATE')
       OR NOT has_schema_privilege(rol_pos, 'public', 'USAGE')
       OR has_schema_privilege(rol_pos, 'public', 'CREATE') THEN
        RAISE EXCEPTION
            'Verificación fallida: privilegios del esquema public inesperados';
    END IF;

    -- CONNECT permanece disponible para las identidades estándar de Supabase,
    -- pero sin USAGE/objetos no pueden acceder a public. CREATE/TEMP se cierran.
    IF NOT has_database_privilege(rol_pos, current_database(), 'CONNECT')
       OR has_database_privilege(rol_pos, current_database(), 'CREATE')
       OR has_database_privilege(rol_pos, current_database(), 'TEMP')
       OR has_database_privilege('anon', current_database(), 'CREATE')
       OR has_database_privilege('anon', current_database(), 'TEMP')
       OR has_database_privilege('authenticated', current_database(), 'CREATE')
       OR has_database_privilege('authenticated', current_database(), 'TEMP') THEN
        RAISE EXCEPTION
            'Verificación fallida: CONNECT/CREATE/TEMP de base no están restringidos como se espera';
    END IF;

    -- En esta fase el rol ya debe estar activado y sin capacidades
    -- administrativas, herencia o bypass de RLS. La existencia/calidad del
    -- secreto no se infiere de pg_roles, que enmascara contraseñas.
    IF NOT EXISTS (
        SELECT 1
         FROM pg_roles rol
         WHERE rol.oid = rol_pos
           AND rol.rolcanlogin
           AND NOT rol.rolinherit
           AND NOT rol.rolsuper
           AND NOT rol.rolcreaterole
           AND NOT rol.rolcreatedb
           AND NOT rol.rolreplication
           AND NOT rol.rolbypassrls
           AND rol.rolconnlimit BETWEEN 1 AND 5
           AND 'default_transaction_read_only=on' = ANY(COALESCE(rol.rolconfig, ARRAY[]::text[]))
           AND 'statement_timeout=10s' = ANY(COALESCE(rol.rolconfig, ARRAY[]::text[]))
           AND 'lock_timeout=3s' = ANY(COALESCE(rol.rolconfig, ARRAY[]::text[]))
           AND 'idle_in_transaction_session_timeout=10s' = ANY(COALESCE(rol.rolconfig, ARRAY[]::text[]))
           AND EXISTS (
               SELECT 1
                 FROM unnest(COALESCE(rol.rolconfig, ARRAY[]::text[])) configuracion
                WHERE replace(configuracion, ' ', '') = 'search_path=pg_catalog,public'
           )
    ) OR EXISTS (
        SELECT 1
          FROM pg_auth_members membresia
          JOIN pg_roles padre ON padre.oid = membresia.roleid
          JOIN pg_roles miembro ON miembro.oid = membresia.member
          LEFT JOIN pg_roles otorgante ON otorgante.oid = membresia.grantor
         WHERE (membresia.roleid = rol_pos OR membresia.member = rol_pos)
           AND NOT (
               padre.rolname = 'pos_local_reader'
               AND miembro.rolname = 'postgres'
               AND membresia.admin_option
               AND COALESCE(otorgante.rolname, '') = 'supabase_admin'
           )
    ) THEN
        RAISE EXCEPTION
            'Verificación fallida: atributos o membresías de pos_local_reader no son mínimos';
    END IF;

    -- Compara privilegios SELECT efectivos columna por columna. Esto también
    -- detecta un GRANT SELECT de tabla completa, porque habilitaría más columnas.
    IF EXISTS (
        WITH esperadas(tabla, columna) AS (
            VALUES
              ('pedidos_pedido', 'id'),
              ('pedidos_pedido', 'sucursal_cliente_id'),
              ('pedidos_pedido', 'codigo_publico'),
              ('pedidos_pedido', 'estado'),
              ('pedidos_pedido', 'fecha_confirmacion'),
              ('pedidos_pedido', 'eliminado'),
              ('pedidos_sucursalcliente', 'id'),
              ('pedidos_sucursalcliente', 'nombre'),
              ('pedidos_itempedido', 'id'),
              ('pedidos_itempedido', 'pedido_id'),
              ('pedidos_itempedido', 'producto_id'),
              ('pedidos_itempedido', 'cantidad'),
              ('pedidos_itempedido', 'precio_unitario'),
              ('pedidos_producto', 'id'),
              ('pedidos_producto', 'nombre'),
              ('pedidos_producto', 'nombre_ticket')
        ), actuales AS (
            SELECT clase.relname AS tabla, atributo.attname AS columna
              FROM pg_attribute atributo
              JOIN pg_class clase ON clase.oid = atributo.attrelid
              JOIN pg_namespace esquema ON esquema.oid = clase.relnamespace
             WHERE esquema.nspname = 'public'
               AND clase.relkind IN ('r', 'p', 'v', 'm', 'f')
               AND atributo.attnum > 0
               AND NOT atributo.attisdropped
               AND has_column_privilege(rol_pos, clase.oid, atributo.attnum, 'SELECT')
        ), diferencias AS (
            (SELECT * FROM esperadas EXCEPT SELECT * FROM actuales)
            UNION ALL
            (SELECT * FROM actuales EXCEPT SELECT * FROM esperadas)
        )
        SELECT 1 FROM diferencias
    ) THEN
        RAISE EXCEPTION
            'Verificación fallida: las columnas SELECT efectivas del lector no son exactas';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_class clase
          JOIN pg_namespace esquema ON esquema.oid = clase.relnamespace
         WHERE esquema.nspname = 'public'
           AND clase.relkind IN ('r', 'p', 'v', 'm', 'f')
           AND (
               has_table_privilege(rol_pos, clase.oid, 'SELECT')
               OR has_table_privilege(rol_pos, clase.oid, 'INSERT')
               OR has_any_column_privilege(rol_pos, clase.oid, 'INSERT')
               OR has_table_privilege(rol_pos, clase.oid, 'UPDATE')
               OR has_any_column_privilege(rol_pos, clase.oid, 'UPDATE')
               OR has_table_privilege(rol_pos, clase.oid, 'DELETE')
               OR has_table_privilege(rol_pos, clase.oid, 'TRUNCATE')
               OR has_table_privilege(rol_pos, clase.oid, 'REFERENCES')
               OR has_any_column_privilege(rol_pos, clase.oid, 'REFERENCES')
               OR has_table_privilege(rol_pos, clase.oid, 'TRIGGER')
           )
    ) OR EXISTS (
        -- El lector sólo recibe ACL de columna; cualquier ACL a nivel de
        -- relación, incluso un privilegio futuro, es una ampliación indebida.
        SELECT 1
          FROM pg_class clase
          JOIN pg_namespace esquema ON esquema.oid = clase.relnamespace
         CROSS JOIN LATERAL aclexplode(
             COALESCE(
                 clase.relacl,
                 acldefault(
                     CASE WHEN clase.relkind = 'S' THEN 'S'::"char" ELSE 'r'::"char" END,
                     clase.relowner
                 )
             )
         ) permiso
         WHERE esquema.nspname = 'public'
           AND clase.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
           AND permiso.grantee = rol_pos
    ) OR EXISTS (
        SELECT 1
          FROM pg_class clase
          JOIN pg_namespace esquema ON esquema.oid = clase.relnamespace
         WHERE esquema.nspname = 'public'
           AND clase.relkind = 'S'
           AND (
               has_sequence_privilege(rol_pos, clase.oid, 'SELECT')
               OR has_sequence_privilege(rol_pos, clase.oid, 'USAGE')
               OR has_sequence_privilege(rol_pos, clase.oid, 'UPDATE')
           )
    ) OR EXISTS (
        SELECT 1
          FROM pg_proc rutina
          JOIN pg_namespace esquema ON esquema.oid = rutina.pronamespace
         WHERE esquema.nspname = 'public'
           AND has_function_privilege(rol_pos, rutina.oid, 'EXECUTE')
    ) THEN
        RAISE EXCEPTION
            'Verificación fallida: el lector conserva permisos fuera de sus columnas SELECT autorizadas';
    END IF;

    -- Los defaults de cada propietario actual deben impedir que un objeto nuevo
    -- vuelva a quedar visible a PUBLIC, anon, authenticated o al lector.
    IF EXISTS (
        WITH propietarios(oid) AS (
            SELECT clase.relowner
              FROM pg_class clase
              JOIN pg_namespace esquema ON esquema.oid = clase.relnamespace
             WHERE esquema.nspname = 'public'
               AND clase.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
            UNION
            SELECT rutina.proowner
              FROM pg_proc rutina
              JOIN pg_namespace esquema ON esquema.oid = rutina.pronamespace
             WHERE esquema.nspname = 'public'
            UNION
            SELECT esquema.nspowner
              FROM pg_namespace esquema
             WHERE esquema.nspname = 'public'
        ), tipos(tipo) AS (
            VALUES ('r'::"char"), ('S'::"char"), ('f'::"char")
        ), defaults AS (
            SELECT propietarios.oid,
                   tipos.tipo,
                   COALESCE(defaults_global.defaclacl, acldefault(tipos.tipo, propietarios.oid)) AS acl_base,
                   defaults_schema.defaclacl AS acl_schema
              FROM propietarios
             CROSS JOIN tipos
              LEFT JOIN pg_default_acl defaults_global
                ON defaults_global.defaclrole = propietarios.oid
               AND defaults_global.defaclnamespace = 0
               AND defaults_global.defaclobjtype = tipos.tipo
              LEFT JOIN pg_default_acl defaults_schema
                ON defaults_schema.defaclrole = propietarios.oid
               AND defaults_schema.defaclnamespace = 'public'::regnamespace
               AND defaults_schema.defaclobjtype = tipos.tipo
        ), permisos AS (
            SELECT permiso.grantee
              FROM defaults
             CROSS JOIN LATERAL aclexplode(defaults.acl_base) permiso
            UNION ALL
            SELECT permiso.grantee
              FROM defaults
             CROSS JOIN LATERAL aclexplode(defaults.acl_schema) permiso
             WHERE defaults.acl_schema IS NOT NULL
        )
        SELECT 1
          FROM permisos
          LEFT JOIN pg_roles receptor ON receptor.oid = permisos.grantee
         WHERE permisos.grantee = 0
            OR receptor.rolname IN ('anon', 'authenticated', 'pos_local_reader')
    ) THEN
        RAISE EXCEPTION
            'Verificación fallida: privilegios predeterminados podrían reabrir public';
    END IF;
END
$verificar_endurecimiento$;

-- Este resumen sólo se emite si todas las postcondiciones anteriores pasaron.
SELECT TRUE AS cumple,
       (SELECT count(*)
          FROM pg_class clase
          JOIN pg_namespace esquema ON esquema.oid = clase.relnamespace
         WHERE esquema.nspname = 'public'
           AND clase.relkind IN ('r', 'p')) AS tablas_public,
       (SELECT count(*)
          FROM pg_class clase
          JOIN pg_namespace esquema ON esquema.oid = clase.relnamespace
         WHERE esquema.nspname = 'public'
           AND clase.relkind IN ('r', 'p')
           AND clase.relrowsecurity) AS tablas_con_rls,
       (SELECT count(*)
          FROM pg_policies
         WHERE schemaname = 'public') AS politicas_public,
       (SELECT rolcanlogin
          FROM pg_roles
         WHERE rolname = 'pos_local_reader') AS lector_login;

SELECT tablename,
       policyname,
       roles,
       cmd,
       qual
  FROM pg_policies
 WHERE schemaname = 'public'
 ORDER BY tablename, policyname;

COMMIT;
