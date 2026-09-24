# Contrato ejecutable · Pedidos API POS v2

Este paquete fija el contrato que consume `ventas/pedidos_api_v2.py` para leer pedidos confirmados por HTTPS. Sus datos son sintéticos. No contiene secretos, no activa la integración y no autoriza un corte.

## Procedencia y estado

- Candidato remoto observado: `8fad56815f856b4286a2f60f488480960e34cde5`.
- Estado: **candidato sin merge ni despliegue**.
- Ruta candidata: `GET /api/v2/pos/pedidos/`.
- API v1 y transporte legacy Supabase permanecen disponibles como rollback hasta aprobar E2E v2 y un corte explícito.
- Producción de Pedidos permanece en 8002; este paquete no modifica servicios, puertos, DNS ni TLS.

## Autorización requerida

El contrato objetivo exige `orders:v2:read` y una credencial distinta por instalación Edge, ligada a los `SucursalCliente.id` aprobados para esa sucursal. `SucursalCliente` no tiene `codigo_publico`; `Pedido.codigo_publico` es el UUID durable de cada pedido. El candidato `8fad568…` aún comparte una allowlist global; por ello el scope documentado es un requisito para agente1/integrador, no una afirmación sobre el despliegue actual. El rate limit de `LocMemCache` por worker sólo es defensa adicional.

## Límites que aplica el cliente POS

| Elemento | Límite |
| --- | ---: |
| Ventana solicitada | > 0 y ≤ 31 días; el sincronizador Edge usa tramos consecutivos de ≤ 24 h |
| `limite` | 1–500; configuración Edge predeterminada 100 |
| Respuesta | configuración Edge predeterminada 1 MiB; rango permitido 4 KiB–4 MiB |
| Cursor | 1–2048 caracteres, opaco; el POS nunca lo interpreta |
| Páginas por ventana | 1000 |
| Conceptos por pedido aplicable | 1–100 |
| Cantidad / cantidad por precio | hasta 3 enteros y 3 decimales; > 0 |
| Precio unitario | hasta 8 enteros y 2 decimales; ≥ 0 |
| Subtotal / total | hasta 18 enteros y 2 decimales; ≥ 0 |
| Reintentos Edge configurables | 0–5; predeterminado 2 |
| Timeouts Edge | conexión 1–30 s; lectura 1–60 s |

La respuesta requiere JSON UTF-8, claves únicas, `Content-Type: application/json`, `X-Request-ID` válido y concordante con el cuerpo. Pedidos se ordenan por `(fecha_confirmacion, id)`. `id` y `codigo_publico` no se repiten en una página.

## Cursores, continuidad y 410

El cursor es firmado y opaco. Sólo avanza después de persistir atómicamente la página y los pedidos. Un cursor legado de época numérica y cualquier cursor fuera de retención reciben `410 retention_gap`.

`410 retention_gap` significa que el servidor ya no puede demostrar continuidad. El POS conserva el cursor, el high-water mark y lo ya importado; entra en conciliación y no informa “sin pedidos”. Salir de ese estado exige snapshot/exportación auditada y una decisión explícita.

## Archivos

| Ruta en el repositorio | Contenido |
| --- | --- |
| `contracts/pedidos-v2/openapi.json` | OpenAPI 3.1 de la ruta candidata y sus parámetros/respuestas |
| `contracts/pedidos-v2/schemas/pagina-pedidos-v2.schema.json` | Respuesta 200 estricta |
| `contracts/pedidos-v2/schemas/error-pedidos-v2.schema.json` | Error estable para 401/403/410 |
| `contracts/pedidos-v2/fixtures/index.json` | Índice HTTP ejecutable, scope, headers, query y acción Edge |
| `contracts/pedidos-v2/fixtures/*.json` | Página con datos, vacía y errores sintéticos |
| `contracts/pedidos-v2/fixtures/invalid/*.json` | Página 200 que debe rechazar el cliente |
| `contracts/pedidos-v2/test_contract.py` | Validación sólo con biblioteca estándar y comparación con el cliente POS |

Ejecutar desde la raíz del repositorio:

```powershell
python contracts/pedidos-v2/test_contract.py -v
```

La prueba no abre red ni requiere Django. Comprueba referencias, fixtures, límites, semántica de página y las excepciones reales del cliente POS para 401, 403, 410, cursor numérico y página inválida.
