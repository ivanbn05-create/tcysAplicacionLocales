---
version: 1
slug: "ventas-templates-ventas-inicio-html"
primary_target: "ventas/templates/ventas/inicio.html"
related_targets: ["ventas/static/ventas/app.css","ventas/static/ventas/brand-pos.css","ventas/static/ventas/app.js","catalogo/models.py","ventas/menu_imagenes.py"]
---

# Brief de superficie: inicio del POS

## Aprobación

- Composición aprobada: `.impeccable/mocks/pos-pase-cocina-b-hibrida.png`.
- Elección explícita del usuario: opción 2, iconos de canal de la opción 3 y revisión posterior de estados: verde libre, amarillo abierto y rojo procesado.
- Semilla de dirección: `102e4b50`; candidato asentado 3, “pase de cocina”.

## Compromisos de composición

- Barra amarilla compacta con logotipo completo y sucursal.
- Riel carbón horizontal con tres etiquetas-tíquet orientadas a la derecha, clip superior, iconos SVG de línea y marcador rojo único.
- Rejilla de posiciones dominante, con lectura de 24 mesas a 1440×900 cuando el canal lo permita.
- La captura real no implementa “Nuevo ticket” ni nombres inventados de la maqueta; la cabecera omite telemetría pasiva y reserva los avisos persistentes para fallos accionables.
- En el flujo de ticket se conserva la topología funcional de tres pasos y se aplica la misma gramática material.
- La estación horizontal usa siete pistas: cinco posiciones principales y dos auxiliares; no incluye footer.
- En captura horizontal el riel de comensales conserva su ancho y catálogo/comanda reparten aproximadamente 60/40. PC operativo hasta 1180 px y `/tabletas` muestran dos productos por fila.
- El menú completo ofrece siete atajos numerados que siguen el orden real de categorías y desplazan sólo el catálogo.

## Inventario y medio

| Elemento | Compromiso visible | Medio de implementación |
|---|---|---|
| Logotipo | marca principal sin deformación ni efectos; maestro vectorial todavía pendiente | recorte CSS determinista del lienzo compuesto de `logoactual.jpeg`, sólo como fallback |
| Barra de marca | campo amarillo plano y regla carbón | HTML/CSS semántico |
| Tickets de canal | papel con corte, regla de 2px, marcador rojo | HTML/CSS |
| Iconos de canal | comedor, ruta/domicilio, sucursal; trazo uniforme | SVG en línea, autoría local |
| Números/estado | Bebas Neue, verde libre, amarillo abierto y rojo procesado; etiqueta e icono siempre | HTML/CSS + datos reales de JS |
| Fondo/papel | `#F8F7F5`; sin textura raster | CSS plano |
| Riel | `#242629` muestreado de la composición | CSS plano |
| Display | Bebas Neue local, silueta condensada | fuente local con licencia OFL |
| Texto funcional | Montserrat local variable | `Montserrat-Variable.woff2` oficial + OFL |
| Acción principal | texto literal del flujo real, campo amarillo y estado busy | botón semántico HTML/JS |
| Foto de producto | miniatura real local en 50 % de la tarjeta; nombre y abreviatura contiguos | 25 WebP optimizados de `C:\tcysWeb\img\menu`, mapa por código y `mainlogo.webp` como respaldo; una carga privada desde Django conserva prioridad; procedencia en `.impeccable/assets/pos-pase-cocina-manifest.md` |

## Interacción y estados

- `Libre`: número verde + texto.
- `Orden abierta`: campo y marcador amarillo + número mostaza accesible, texto y marca de ticket.
- `Procesada`: número rojo + texto y marca de verificación.
- Canales publican estado seleccionado semántico y foco visible.
- Controles principales miden al menos 44px; la interfaz respeta teclado y movimiento reducido.
- Conexión e impresora no ocupan la cabecera; los fallos que requieran actuar se anuncian mediante aviso persistente.
- `Salir` vive en la cabecera sólo mientras se muestran posiciones; desaparece al entrar a captura para evitar abandonar accidentalmente la orden.
- Comedor/Llevar y Domicilio/Recoger comparten las mismas pistas de rejilla, por lo que sus tarjetas tienen dimensiones idénticas en cada breakpoint.
- Las promociones fuera de su día se ven en escala de grises y permanecen deshabilitadas desde el primer render.

## Límites

El acceso Administrador continúa deshabilitado y explícitamente separado. La dirección visual sí se documenta para ese módulo, pero sus capacidades no se inventan. No se importan acciones, métricas, nombres de personas ni datos que sólo aparecen como demostración en la maqueta.
