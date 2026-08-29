---
version: 1
slug: "ventas-templates-ventas-administrador-html"
primary_target: "ventas/templates/ventas/administrador.html"
related_targets: ["ventas/static/ventas/admin.css","ventas/static/ventas/admin.js","ventas/views.py","ventas/models.py","personas/models.py","impresion/render.py","ventas/templates/ventas/inicio.html","ventas/static/ventas/app.js","ventas/static/ventas/brand-pos.css"]
---

# Brief de superficie: Administrador local

## Alcance y modo

- **Modo:** Operate.
- **Superficie:** administrador de una sola sucursal, separado del futuro administrador central/VPS.
- **Dispositivos:** PC local de caja; la ruta `/tabletas/` no expone acceso administrativo.

## Trabajo principal

El encargado entra con una clave de cuatro cifras y resuelve pendientes del turno: personal, domicilios sin repartidor, pedidos programados, movimientos, reportes, cortes y sucursales. Cada mutación administrativa vuelve a solicitar la clave; entrar a la superficie no concede autorización permanente para actuar.

## Dirección aprobada

- **Estructura:** “Bandeja de pendientes”, candidato 5 de 7, semilla `63e84054`.
- **Comp aprobado:** `.impeccable/mocks/decision/admin-bandeja-pendientes.png`.
- **Momento memorable:** la primera vista convierte los domicilios sin repartidor y los cierres bloqueados en tickets físicos priorizados; las herramientas no urgentes permanecen en una pista inferior.
- **Movimiento:** un solo gesto de cambio de hoja desplaza la bandeja en dirección horizontal; con movimiento reducido, el cambio es inmediato.

## Contrato de composición

- Cabecera amarilla compacta con marca, título Administrador, sucursal y salida.
- Riel carbón estable de 244–260 px con Inicio, Personal, Domicilios, Programados, Movimientos, Reportes y Sucursales.
- La vista inicial usa una bandeja 2:1: pendientes de domicilio/programados a la izquierda y bloqueos de cierre a la derecha.
- Lista-detalle en las herramientas de edición; no se apilan modales para tareas persistentes.
- Objetivos principales de 44 px, foco azul exterior, teclado completo y adaptación a 200 % de zoom.

## Inventario de fidelidad

| Ingrediente | Compromiso | Medio |
|---|---|---|
| Cabecera | Campo continuo de amarillo corporativo; 62 px; regla carbón | HTML/CSS, `#FFED00` vinculante por manual de marca; el raster renderiza aprox. `#FCDF08` |
| Riel | Carbón casi negro, controles grandes separados por reglas | HTML/CSS, muestra del comp `#212426` |
| Lienzo | Papel cálido casi blanco, sin textura | HTML/CSS, muestra `#F7F6F4` |
| Tickets pendientes | Rectángulos planos, borde 1–2 px, clip centrado y jerarquía de folio | HTML/CSS/SVG semántico |
| Tipografía | Bebas Neue condensada para títulos/folios; Montserrat para operación | fuentes locales existentes |
| Iconos | Trazos lineales uniformes; nunca emoji ni glifos Unicode | SVG en línea de autoría local |
| Acción principal | Amarillo, tinta carbón, altura ≥44 px, sin sombra | botón semántico HTML/CSS |
| Estados | Verde confirmado, amarillo pendiente, rojo sólo urgente/destructivo | texto + icono + color |
| Comp | Referencia de composición, no recurso publicado | sólo revisión; no se sirve en producción |

## Reglas funcionales confirmadas

- Clave administrativa inicial `1212`, modificable; clave exigida por cada acción protegida.
- Meseros se identifican con código de cuatro cifras antes de tomar comandas; repartidores se registran igual, aunque su código todavía no autoriza acciones.
- Domicilio se procesa e imprime, pero no se cobra desde el POS; después sólo puede asignarse a repartidor o cancelarse con clave administrativa.
- Los programados se muestran al final de Domicilio y se activan por fecha —no por hora— en la primera posición libre al iniciar o refrescar el sistema.
- El reporte parcial suma Comedor, Llevar, Domicilio y Recoger sin separar estados.
- Sucursales se eligen primero como tarjeta y luego muestran una rejilla ampliada de posiciones.

## Límite de producto

Esta superficie no administra precios, artículos ni métricas consolidadas y no introduce capacidades del futuro servicio VPS.
