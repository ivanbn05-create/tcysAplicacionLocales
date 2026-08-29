-- Cierre transaccional y fail-closed del esquema public de Supabase.
-- Ejecutar como `postgres`, después de exportar 00_inventario_previo.sql.
-- No asigna LOGIN ni contraseña: el lector permanece inutilizable hasta que
-- una persona revise el COMMIT y lo active por un canal seguro.

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '90s';
SELECT pg_advisory_xact_lock(hashtext('tocayos_supabase_endurecimiento_v2'));

-- Cualquier deriva respecto de la auditoría detiene toda la transacción.
DO $precondiciones$
DECLARE
    total_tablas integer;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon')
       OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated')
       OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        RAISE EXCEPTION
            'Precondición fallida: faltan roles estándar anon/authenticated/service_role';
    END IF;

    SELECT count(*)
      INTO total_tablas
      FROM pg_class clase
      JOIN pg_namespace esquema ON esquema.oid = clase.relnamespace
     WHERE esquema.nspname = 'public'
       AND clase.relkind IN ('r', 'p');

    IF total_tablas <> 20 THEN
        RAISE EXCEPTION
            'Deriva de esquema: se esperaban exactamente 20 tablas en public y se encontraron %',
            total_tablas;
    END IF;

    IF EXISTS (
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
        SELECT 1
          FROM obligatorias
         WHERE NOT EXISTS (
             SELECT 1
               FROM pg_class clase
               JOIN pg_namespace esquema ON esquema.oid = clase.relnamespace
              WHERE esquema.nspname = 'public'
                AND clase.relname = obligatorias.tabla
                AND clase.relkind IN ('r', 'p')
         )
    ) THEN
        RAISE EXCEPTION
            'Deriva de esquema: falta al menos una tabla obligatoria o no es tabla base/particionada';
    END IF;

    IF EXISTS (
        WITH columnas(tabla, columna) AS (
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
        )
        SELECT 1
          FROM columnas
         WHERE NOT EXISTS (
             SELECT 1
               FROM pg_attribute atributo
               JOIN pg_class clase ON clase.oid = atributo.attrelid
               JOIN pg_namespace esquema ON esquema.oid = clase.relnamespace
              WHERE esquema.nspname = 'public'
                AND clase.relname = columnas.tabla
                AND atributo.attname = columnas.columna
                AND atributo.attnum > 0
                AND NOT atributo.attisdropped
         )
    ) THEN
        RAISE EXCEPTION
            'Deriva de esquema: faltan columnas requeridas por el lector del POS';
    END IF;

    -- La auditoría inicial encontró cero políticas. En una repetición sólo se
    -- toleran las cuatro políticas creadas por este mismo archivo.
    IF EXISTS (
        SELECT 1
          FROM pg_policies
         WHERE schemaname = 'public'
           AND NOT (
               policyname = 'pos_local_reader_select'
               AND tablename IN (
                   'pedidos_pedido',
                   'pedidos_sucursalcliente',
                   'pedidos_itempedido',
                   'pedidos_producto'
               )
           )
    ) THEN
        RAISE EXCEPTION
            'Precondición fallida: existen políticas public no inventariadas; revisar antes de continuar';
    END IF;

    -- ALTER TABLE y ALTER DEFAULT PRIVILEGES requieren ser propietario, miembro
    -- del rol propietario o superusuario. Se comprueba antes de modificar nada.
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
        )
        SELECT 1
          FROM propietarios
         WHERE NOT pg_has_role(current_user, propietarios.oid, 'MEMBER')
           AND NOT EXISTS (
               SELECT 1 FROM pg_roles WHERE rolname = current_user AND rolsuper
           )
    ) THEN
        RAISE EXCEPTION
            'Precondición fallida: el ejecutor no puede administrar todos los objetos de public';
    END IF;
END
$precondiciones$;

DO $crear_rol$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pos_local_reader') THEN
        CREATE ROLE pos_local_reader
            NOLOGIN
            NOSUPERUSER
            NOCREATEDB
            NOCREATEROLE
            NOINHERIT
            NOREPLICATION
            NOBYPASSRLS
            CONNECTION LIMIT 5;
    END IF;
END
$crear_rol$;

-- Supabase administra el rol postgres sin SUPERUSER real. El CREATE ROLE
-- anterior ya fija NOSUPERUSER/NOBYPASSRLS/NOCREATEDB/NOCREATEROLE/
-- NOINHERIT/NOREPLICATION/NOLOGIN/CONNECTION LIMIT 5; repetir esos
-- atributos con ALTER ROLE falla en Supabase. Si el rol ya existiera con
-- atributos inseguros, la postcondicion inferior abortaria el COMMIT.

ALTER ROLE pos_local_reader SET default_transaction_read_only = on;
ALTER ROLE pos_local_reader SET statement_timeout = '10s';
ALTER ROLE pos_local_reader SET lock_timeout = '3s';
ALTER ROLE pos_local_reader SET idle_in_transaction_session_timeout = '10s';
ALTER ROLE pos_local_reader SET search_path = pg_catalog, public;

-- El lector no hereda otros roles y ningún rol hereda accidentalmente al lector.
DO $revocar_membresias$
DECLARE
    rol name;
BEGIN
    FOR rol IN
        SELECT padre.rolname
          FROM pg_auth_members membresia
          JOIN pg_roles miembro ON miembro.oid = membresia.member
          JOIN pg_roles padre ON padre.oid = membresia.roleid
         WHERE miembro.rolname = 'pos_local_reader'
    LOOP
        EXECUTE format('REVOKE %I FROM pos_local_reader', rol);
    END LOOP;

    FOR rol IN
        SELECT miembro.rolname
          FROM pg_auth_members membresia
          JOIN pg_roles padre ON padre.oid = membresia.roleid
          JOIN pg_roles miembro ON miembro.oid = membresia.member
         WHERE padre.rolname = 'pos_local_reader'
    LOOP
        EXECUTE format('REVOKE pos_local_reader FROM %I', rol);
    END LOOP;
END
$revocar_membresias$;

DO $cerrar_base$
BEGIN
    EXECUTE format(
        'REVOKE ALL PRIVILEGES ON DATABASE %I FROM pos_local_reader',
        current_database()
    );
    -- CREATE/TEMP suelen heredarse de PUBLIC; retirarlos sólo al lector no
    -- sería efectivo si una concesión pública los vuelve a habilitar.
    EXECUTE format(
        'REVOKE CREATE, TEMPORARY ON DATABASE %I '
        'FROM PUBLIC, anon, authenticated, pos_local_reader',
        current_database()
    );
    EXECUTE format(
        'GRANT CONNECT ON DATABASE %I TO pos_local_reader',
        current_database()
    );
END
$cerrar_base$;

REVOKE ALL PRIVILEGES ON SCHEMA public
    FROM PUBLIC, anon, authenticated, pos_local_reader;
GRANT USAGE ON SCHEMA public TO service_role, pos_local_reader;

-- Se protege el conjunto completo de 20 tablas, no sólo las de nombre conocido.
DO $habilitar_rls$
DECLARE
    objeto record;
BEGIN
    FOR objeto IN
        SELECT esquema.nspname AS esquema, clase.relname AS tabla
          FROM pg_class clase
          JOIN pg_namespace esquema ON esquema.oid = clase.relnamespace
         WHERE esquema.nspname = 'public'
           AND clase.relkind IN ('r', 'p')
         ORDER BY clase.relname
    LOOP
        EXECUTE format(
            'ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY',
            objeto.esquema,
            objeto.tabla
        );
    END LOOP;
END
$habilitar_rls$;

-- Cierre de relaciones y columnas expuestas por PostgREST/Data API.
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public
    FROM PUBLIC, anon, authenticated, pos_local_reader;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public
    FROM PUBLIC, anon, authenticated, pos_local_reader;

DO $cerrar_columnas$
DECLARE
    objeto record;
    columnas text;
BEGIN
    FOR objeto IN
        SELECT esquema.nspname AS esquema, clase.relname AS relacion, clase.oid
          FROM pg_class clase
          JOIN pg_namespace esquema ON esquema.oid = clase.relnamespace
         WHERE esquema.nspname = 'public'
           AND clase.relkind IN ('r', 'p', 'v', 'm', 'f')
    LOOP
        SELECT string_agg(format('%I', atributo.attname), ', ' ORDER BY atributo.attnum)
          INTO columnas
          FROM pg_attribute atributo
         WHERE atributo.attrelid = objeto.oid
           AND atributo.attnum > 0
           AND NOT atributo.attisdropped;

        IF columnas IS NOT NULL THEN
            EXECUTE format(
                'REVOKE SELECT (%s), INSERT (%s), UPDATE (%s), REFERENCES (%s) '
                'ON TABLE %I.%I FROM PUBLIC, anon, authenticated, pos_local_reader',
                columnas,
                columnas,
                columnas,
                columnas,
                objeto.esquema,
                objeto.relacion
            );
        END IF;
    END LOOP;
END
$cerrar_columnas$;

-- Toda rutina public es alcanzable como RPC si conserva EXECUTE. El cierre se
-- aplica tanto a funciones como a procedimientos; service_role se conserva.
DO $cerrar_rutinas$
DECLARE
    rutina record;
BEGIN
    FOR rutina IN
        SELECT procedimiento.oid,
               procedimiento.prokind,
               esquema.nspname,
               procedimiento.proname,
               pg_get_function_identity_arguments(procedimiento.oid) AS argumentos
          FROM pg_proc procedimiento
          JOIN pg_namespace esquema ON esquema.oid = procedimiento.pronamespace
         WHERE esquema.nspname = 'public'
    LOOP
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON %s %I.%I(%s) '
            'FROM PUBLIC, anon, authenticated, pos_local_reader',
            CASE WHEN rutina.prokind = 'p' THEN 'PROCEDURE' ELSE 'FUNCTION' END,
            rutina.nspname,
            rutina.proname,
            rutina.argumentos
        );
    END LOOP;
END
$cerrar_rutinas$;

-- Previene que el propietario/migrador de los objetos actuales reabra la Data
-- API al crear tablas, secuencias o funciones nuevas.
DO $cerrar_privilegios_predeterminados$
DECLARE
    propietario name;
BEGIN
    FOR propietario IN
        WITH propietarios(oid) AS (
            SELECT clase.relowner
              FROM pg_class clase
              JOIN pg_namespace esquema ON esquema.oid = clase.relnamespace
             WHERE esquema.nspname = 'public'
               AND clase.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
            UNION
            SELECT procedimiento.proowner
              FROM pg_proc procedimiento
              JOIN pg_namespace esquema ON esquema.oid = procedimiento.pronamespace
             WHERE esquema.nspname = 'public'
            UNION
            SELECT esquema.nspowner
              FROM pg_namespace esquema
             WHERE esquema.nspname = 'public'
        )
        SELECT rol.rolname
          FROM propietarios
          JOIN pg_roles rol ON rol.oid = propietarios.oid
    LOOP
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
            'REVOKE ALL PRIVILEGES ON TABLES '
            'FROM PUBLIC, anon, authenticated, pos_local_reader',
            propietario
        );
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
            'REVOKE ALL PRIVILEGES ON SEQUENCES '
            'FROM PUBLIC, anon, authenticated, pos_local_reader',
            propietario
        );
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
            'REVOKE ALL PRIVILEGES ON FUNCTIONS '
            'FROM PUBLIC, anon, authenticated, pos_local_reader',
            propietario
        );
    END LOOP;
END
$cerrar_privilegios_predeterminados$;

-- En PostgreSQL, EXECUTE sobre funciones tiene una concesión implícita a
-- PUBLIC a nivel global. Un REVOKE limitado a IN SCHEMA public no elimina ese
-- default; por eso se cierra también globalmente para los propietarios actuales.
DO $cerrar_privilegios_predeterminados_globales$
DECLARE
    propietario name;
BEGIN
    FOR propietario IN
        WITH propietarios(oid) AS (
            SELECT clase.relowner
              FROM pg_class clase
              JOIN pg_namespace esquema ON esquema.oid = clase.relnamespace
             WHERE esquema.nspname = 'public'
               AND clase.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
            UNION
            SELECT procedimiento.proowner
              FROM pg_proc procedimiento
              JOIN pg_namespace esquema ON esquema.oid = procedimiento.pronamespace
             WHERE esquema.nspname = 'public'
            UNION
            SELECT esquema.nspowner
              FROM pg_namespace esquema
             WHERE esquema.nspname = 'public'
        )
        SELECT rol.rolname
          FROM propietarios
          JOIN pg_roles rol ON rol.oid = propietarios.oid
    LOOP
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES FOR ROLE %I '
            'REVOKE ALL PRIVILEGES ON FUNCTIONS '
            'FROM PUBLIC, anon, authenticated, pos_local_reader',
            propietario
        );
    END LOOP;
END
$cerrar_privilegios_predeterminados_globales$;

-- Concesiones mínimas: cuatro tablas y sólo las columnas que consume el POS.
GRANT SELECT (
    id,
    sucursal_cliente_id,
    codigo_publico,
    estado,
    fecha_confirmacion,
    eliminado
) ON TABLE public.pedidos_pedido TO pos_local_reader;

GRANT SELECT (id, nombre)
    ON TABLE public.pedidos_sucursalcliente TO pos_local_reader;

GRANT SELECT (id, pedido_id, producto_id, cantidad, precio_unitario)
    ON TABLE public.pedidos_itempedido TO pos_local_reader;

GRANT SELECT (id, nombre, nombre_ticket)
    ON TABLE public.pedidos_producto TO pos_local_reader;

DROP POLICY IF EXISTS pos_local_reader_select ON public.pedidos_pedido;
CREATE POLICY pos_local_reader_select
    ON public.pedidos_pedido
    FOR SELECT
    TO pos_local_reader
    USING (
        estado = 'confirmado'
        AND eliminado = FALSE
        AND sucursal_cliente_id = ANY (ARRAY[1,2,3,4,5,6,7,8,9,10])
        AND fecha_confirmacion >= CURRENT_TIMESTAMP - INTERVAL '2 days'
    );

DROP POLICY IF EXISTS pos_local_reader_select ON public.pedidos_sucursalcliente;
CREATE POLICY pos_local_reader_select
    ON public.pedidos_sucursalcliente
    FOR SELECT
    TO pos_local_reader
    USING (id = ANY (ARRAY[1,2,3,4,5,6,7,8,9,10]));

DROP POLICY IF EXISTS pos_local_reader_select ON public.pedidos_producto;
CREATE POLICY pos_local_reader_select
    ON public.pedidos_producto
    FOR SELECT
    TO pos_local_reader
    USING (TRUE);

DROP POLICY IF EXISTS pos_local_reader_select ON public.pedidos_itempedido;
CREATE POLICY pos_local_reader_select
    ON public.pedidos_itempedido
    FOR SELECT
    TO pos_local_reader
    USING (
        EXISTS (
            SELECT 1
              FROM public.pedidos_pedido pedido
             WHERE pedido.id = pedidos_itempedido.pedido_id
        )
    );

-- Verificación dentro de la misma transacción. Cualquier excepción provoca
-- ROLLBACK: nunca se confirma un cierre parcial.
DO $postcondiciones$
DECLARE
    total_tablas integer;
    rol_pos oid;
BEGIN
    SELECT oid INTO STRICT rol_pos
      FROM pg_roles
     WHERE rolname = 'pos_local_reader';

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
            'Postcondición fallida: no están exactamente las 20 tablas con RLS habilitado';
    END IF;

    IF (SELECT count(*)
          FROM pg_policy politica
          JOIN pg_class clase ON clase.oid = politica.polrelid
          JOIN pg_namespace esquema ON esquema.oid = clase.relnamespace
         WHERE esquema.nspname = 'public') <> 4
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
                   AND politica.polcmd = 'r'
                   AND politica.polpermissive
                   AND politica.polroles = ARRAY[rol_pos]
                   AND politica.polqual IS NOT NULL
                   AND politica.polwithcheck IS NULL
            )
       ) THEN
        RAISE EXCEPTION
            'Postcondición fallida: el conjunto de políticas RLS no es exactamente el esperado';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM (VALUES ('anon'), ('authenticated')) identidades(rolname)
          JOIN pg_roles rol ON rol.rolname = identidades.rolname
         CROSS JOIN pg_class clase
          JOIN pg_namespace esquema ON esquema.oid = clase.relnamespace
         WHERE esquema.nspname = 'public'
           AND clase.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
           AND (
               CASE WHEN clase.relkind = 'S' THEN
                   has_sequence_privilege(rol.oid, clase.oid, 'SELECT')
                   OR has_sequence_privilege(rol.oid, clase.oid, 'USAGE')
                   OR has_sequence_privilege(rol.oid, clase.oid, 'UPDATE')
               ELSE
                   has_table_privilege(rol.oid, clase.oid, 'SELECT')
                   OR has_any_column_privilege(rol.oid, clase.oid, 'SELECT')
                   OR has_table_privilege(rol.oid, clase.oid, 'INSERT')
                   OR has_table_privilege(rol.oid, clase.oid, 'UPDATE')
                   OR has_table_privilege(rol.oid, clase.oid, 'DELETE')
                   OR has_table_privilege(rol.oid, clase.oid, 'TRUNCATE')
                   OR has_table_privilege(rol.oid, clase.oid, 'REFERENCES')
                   OR has_table_privilege(rol.oid, clase.oid, 'TRIGGER')
               END
           )
    ) THEN
        RAISE EXCEPTION
            'Postcondición fallida: anon/authenticated aún tienen privilegios sobre relaciones public';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM (VALUES ('anon'), ('authenticated')) identidades(rolname)
          JOIN pg_roles rol ON rol.rolname = identidades.rolname
         CROSS JOIN pg_proc rutina
          JOIN pg_namespace esquema ON esquema.oid = rutina.pronamespace
         WHERE esquema.nspname = 'public'
           AND has_function_privilege(rol.oid, rutina.oid, 'EXECUTE')
    ) THEN
        RAISE EXCEPTION
            'Postcondición fallida: anon/authenticated aún pueden ejecutar rutinas public';
    END IF;

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
    ) THEN
        RAISE EXCEPTION
            'Postcondición fallida: PUBLIC conserva privilegios en objetos public';
    END IF;

    IF has_schema_privilege('anon', 'public', 'USAGE')
       OR has_schema_privilege('anon', 'public', 'CREATE')
       OR has_schema_privilege('authenticated', 'public', 'USAGE')
       OR has_schema_privilege('authenticated', 'public', 'CREATE')
       OR NOT has_schema_privilege(rol_pos, 'public', 'USAGE')
       OR has_schema_privilege(rol_pos, 'public', 'CREATE') THEN
        RAISE EXCEPTION
            'Postcondición fallida: privilegios de esquema public inesperados';
    END IF;

    IF NOT has_database_privilege(rol_pos, current_database(), 'CONNECT')
       OR has_database_privilege(rol_pos, current_database(), 'CREATE')
       OR has_database_privilege(rol_pos, current_database(), 'TEMP')
       OR has_database_privilege('anon', current_database(), 'CREATE')
       OR has_database_privilege('anon', current_database(), 'TEMP')
       OR has_database_privilege('authenticated', current_database(), 'CREATE')
       OR has_database_privilege('authenticated', current_database(), 'TEMP') THEN
        RAISE EXCEPTION
            'Postcondición fallida: CONNECT/CREATE/TEMP de base no quedaron restringidos';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_roles rol
         WHERE rol.oid = rol_pos
           AND NOT rol.rolcanlogin
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
            'Postcondición fallida: atributos o membresías del lector no son mínimos';
    END IF;

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
            SELECT table_name AS tabla, column_name AS columna
              FROM information_schema.column_privileges
             WHERE table_schema = 'public'
               AND grantee = 'pos_local_reader'
               AND privilege_type = 'SELECT'
        ), diferencias AS (
            (SELECT * FROM esperadas EXCEPT SELECT * FROM actuales)
            UNION ALL
            (SELECT * FROM actuales EXCEPT SELECT * FROM esperadas)
        )
        SELECT 1 FROM diferencias
    ) THEN
        RAISE EXCEPTION
            'Postcondición fallida: las columnas SELECT del lector no son exactas';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_class clase
          JOIN pg_namespace esquema ON esquema.oid = clase.relnamespace
         WHERE esquema.nspname = 'public'
           AND clase.relkind IN ('r', 'p', 'v', 'm', 'f')
           AND (
               has_table_privilege(rol_pos, clase.oid, 'INSERT')
               OR has_table_privilege(rol_pos, clase.oid, 'UPDATE')
               OR has_table_privilege(rol_pos, clase.oid, 'DELETE')
               OR has_table_privilege(rol_pos, clase.oid, 'TRUNCATE')
               OR has_table_privilege(rol_pos, clase.oid, 'REFERENCES')
               OR has_table_privilege(rol_pos, clase.oid, 'TRIGGER')
               OR (
                   clase.relname NOT IN (
                       'pedidos_pedido',
                       'pedidos_sucursalcliente',
                       'pedidos_itempedido',
                       'pedidos_producto'
                   )
                   AND (
                       has_table_privilege(rol_pos, clase.oid, 'SELECT')
                       OR has_any_column_privilege(rol_pos, clase.oid, 'SELECT')
                   )
               )
           )
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
            'Postcondición fallida: el lector conserva privilegios fuera de su lista mínima';
    END IF;

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
            'Postcondición fallida: privilegios predeterminados podrían reabrir public';
    END IF;
END
$postcondiciones$;

SELECT count(*) AS tablas_public_con_rls
  FROM pg_class clase
  JOIN pg_namespace esquema ON esquema.oid = clase.relnamespace
 WHERE esquema.nspname = 'public'
   AND clase.relkind IN ('r', 'p')
   AND clase.relrowsecurity;

COMMIT;

-- Estado esperado al terminar: 20 tablas protegidas y pos_local_reader NOLOGIN.
-- Activación deliberadamente separada:
--   1. SET password_encryption = 'scram-sha-256';
--   2. En psql: \password pos_local_reader
--   3. ALTER ROLE pos_local_reader LOGIN;
--   4. Ejecutar 02_verificar_endurecimiento.sql; debe terminar con COMMIT.
