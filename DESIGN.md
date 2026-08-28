---
name: "Los Tocayos · Punto de venta"
description: "Pase de cocina digital, rápido y táctil, con identidad Los Tocayos y comandas fieles al papel térmico."
colors:
  brand-yellow: "#ffed00"
  brand-yellow-soft: "#fff9b8"
  state-open: "#f2c500"
  state-open-surface: "#fff7c8"
  state-open-ink: "#795a00"
  state-free: "#4aa736"
  state-free-ink: "#2f7a26"
  state-free-surface: "#e9f5e6"
  state-processed: "#e42522"
  state-processed-surface: "#fde9e8"
  charcoal: "#353436"
  charcoal-soft: "#535154"
  rail: "#242629"
  muted: "#625f62"
  paper: "#f8f7f5"
  canvas: "#e9e7e1"
  focus: "#0066cc"
typography:
  display:
    fontFamily: '"Bebas Neue Tocayos", "Arial Narrow", sans-serif'
    fontSize: "clamp(2.8rem, 5vw, 3.8rem)"
    fontWeight: 400
    lineHeight: 0.82
    letterSpacing: "0.01em"
  title:
    fontFamily: '"Bebas Neue Tocayos", "Arial Narrow", sans-serif'
    fontSize: "1.25rem"
    fontWeight: 400
    lineHeight: 1
    letterSpacing: "0.02em"
  body:
    fontFamily: '"Montserrat Tocayos", Montserrat, system-ui, sans-serif'
    fontSize: "0.875rem"
    fontWeight: 500
    lineHeight: 1.35
    letterSpacing: "normal"
  label:
    fontFamily: '"Montserrat Tocayos", Montserrat, system-ui, sans-serif'
    fontSize: "0.75rem"
    fontWeight: 800
    lineHeight: 1.15
    letterSpacing: "0.08em"
rounded:
  none: "0"
  crisp: "2px"
  restrained: "6px"
spacing:
  xxs: "4px"
  xs: "8px"
  sm: "10px"
  md: "12px"
  lg: "16px"
  xl: "24px"
components:
  compact-header:
    backgroundColor: "{colors.brand-yellow}"
    textColor: "{colors.charcoal}"
    height: "62px"
    padding: "5px clamp(14px, 2.4vw, 34px)"
    rounded: "{rounded.none}"
  channel-ticket:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.charcoal}"
    typography: "{typography.title}"
    height: "70px"
    padding: "10px clamp(24px, 2.5vw, 34px) 10px clamp(15px, 2vw, 24px)"
    rounded: "{rounded.none}"
  position-free:
    backgroundColor: "#ffffff"
    textColor: "{colors.state-free-ink}"
    height: "118px"
    padding: "10px 12px"
    rounded: "{rounded.crisp}"
  position-open:
    backgroundColor: "{colors.state-open-surface}"
    textColor: "{colors.state-open-ink}"
    height: "118px"
    padding: "10px 12px"
    rounded: "{rounded.crisp}"
  position-processed:
    backgroundColor: "#ffffff"
    textColor: "{colors.state-processed}"
    height: "118px"
    padding: "10px 12px"
    rounded: "{rounded.crisp}"
  product-card:
    backgroundColor: "#ffffff"
    textColor: "{colors.charcoal}"
    height: "82px"
    padding: "0"
    rounded: "{rounded.crisp}"
  action-primary:
    backgroundColor: "{colors.brand-yellow}"
    textColor: "{colors.charcoal}"
    typography: "{typography.label}"
    height: "44px"
    padding: "8px 14px"
    rounded: "{rounded.crisp}"
---

# Design System: Los Tocayos · Punto de venta

## Overview

**Creative North Star: “El pase de cocina digital”**

La interfaz traduce el trabajo físico del restaurante —pase, tickets, clips, papel térmico y marcadores de estado— a una superficie operativa de alta densidad. Debe sentirse propia de Los Tocayos: enérgica, franca y táctil; la marca vive en la estructura amarilla, la tipografía condensada, las reglas de carbón y las decisiones de color, no en efectos decorativos.

La referencia de composición aprobada es `.impeccable/mocks/pos-pase-cocina-b-hibrida.png`. El contenido y las capacidades siempre provienen del producto real. La comanda virtual conserva la topología y la sobriedad de la comanda impresa; el resto de la pantalla puede evolucionar dentro de este sistema.

**Key Characteristics:**

- Pase de cocina, no dashboard genérico.
- Cabecera amarilla compacta y riel carbón con etiquetas de canal tipo ticket/clipboard.
- Densidad táctil, lectura inmediata y objetivos principales de al menos 44 CSS px.
- Estado siempre expresado con color, texto y marca visual.
- Recursos, fuentes y miniaturas disponibles sin internet.

## Colors

La paleta combina identidad cálida con papel y carbón; los colores de estado son semánticos y no se intercambian por preferencias decorativas.

### Primary

- **Amarillo de marca:** barra de identidad, selección primaria y acentos de clip.
- **Carbón de trabajo:** texto, reglas, estructura y controles oscuros.

### Secondary

- **Verde libre:** disponibilidad y confirmación; el tono oscuro se usa para texto pequeño.
- **Amarillo orden abierta:** superficie, marca superior y número de toda orden aún abierta; siempre lleva tinta oscura legible.
- **Rojo procesado:** pedido ya procesado, cancelación o urgencia explícita. No identifica órdenes abiertas.

### Neutral

- **Papel térmico:** superficies operativas y comanda.
- **Riel oscuro:** fondo de navegación y barra de estado.
- **Lienzo cálido:** fondo detrás de las superficies de trabajo.
- **Tinta secundaria:** ayuda y metadatos que conservan contraste AA.

**The Three-State Rule.** Libre es verde, Orden abierta es amarilla y Procesada es roja. Cada estado conserva etiqueta visible y nombre accesible; el color nunca actúa solo.

**The Red Means Resolved-or-Urgent Rule.** El rojo queda reservado para procesado, cancelación, error o acción destructiva; no se usa como decoración abundante.

## Typography

**Display Font:** Bebas Neue Tocayos (con Arial Narrow y sans-serif como respaldo)

**Body Font:** Montserrat Tocayos (con Montserrat, system-ui y sans-serif como respaldo)

**Character:** Bebas Neue aporta la voz frontal de letrero y ticket; Montserrat sostiene lectura funcional rápida. Ambas se sirven localmente y no dependen de CDN.

### Hierarchy

- **Display:** números de mesa o posición; cifras tabulares, gran contraste y línea compacta.
- **Title:** nombres breves de canal, zona y panel; preferentemente en mayúsculas y nunca para instrucciones largas.
- **Body:** nombres de producto, formularios, mensajes y datos de cliente; longitud controlada y lectura natural.
- **Label:** estados, abreviaturas y metadatos; peso alto y espaciado amplio sólo en frases breves.

**The Two-Voices Rule.** Bebas Neue nombra y numera; Montserrat explica y permite operar. No se introducen nuevas familias tipográficas sin actualizar este sistema.

## Layout

La aplicación sigue una secuencia vertical: cabecera de marca compacta, riel de canales, estación de posiciones y franja de estado. La cabecera no aloja telemetría pasiva de conexión o impresora. En la vista de posiciones reserva las acciones de ventana y `Salir`; al entrar a captura, `Salir` desaparece y la navegación vuelve a depender de la acción contextual `Volver`.

Las etiquetas Comedor, Domicilio y Sucursales son tickets/clipboards horizontales orientados a la derecha, con icono SVG a la izquierda, texto breve y clip superior. La estación usa una rejilla de siete columnas en escritorio: la zona principal ocupa seis y Recoger/Llevar una. Ambas heredan el mismo `subgrid`, por lo que toda tarjeta principal y auxiliar tiene exactamente el mismo ancho, alto, separación y padding. Bajo 1180 px el reparto pasa a cuatro más una; bajo 700 px ambas zonas ocupan dos columnas y se apilan sin deformar las tarjetas.

Las sucursales se organizan como columnas desplazables; sus encabezados y textos internos usan una escala compacta para evitar saturación. El flujo de captura mantiene tres zonas funcionales —comensal, catálogo y comanda— y reorganiza o desplaza contenido en pantallas estrechas sin ocultarlo con recortes.

El catálogo usa tarjetas horizontales de dos mitades exactas. La izquierda muestra un WebP local con `object-fit: cover`; la derecha contiene abreviatura y nombre. La imagen administrada en `Producto.imagen` tiene prioridad. Si no existe, se consulta el mapa estático de 25 WebP en `ventas/static/ventas/menu/`; un código sin asignación usa `mainlogo.webp`. Los 25 recursos forman parte del precache del service worker para operación offline.

## Elevation & Depth

El sistema es plano por defecto. La separación proviene de campos tonales, reglas de uno o dos píxeles, cortes de ticket y contraste entre papel y riel. Las sombras sólo aparecen en capas que realmente se elevan —diálogos, avisos persistentes y la hoja de comanda sobre su bandeja— y nunca convierten cada elemento en una tarjeta flotante.

### Shadow Vocabulary

- **Capa modal:** sombra ambiental amplia para separar un diálogo del flujo bloqueado.
- **Aviso persistente:** sombra media para que un error accionable permanezca encontrable.
- **Hoja térmica:** sombra corta y suave, equivalente a papel colocado sobre una mesa.

**The Flat-by-Default Rule.** Una superficie en reposo no usa sombra si una regla, un campo tonal o la propia geometría ya comunica su límite.

## Shapes

Predominan esquinas rectas o apenas suavizadas. Campos y tarjetas operativas usan radios discretos; la comanda permanece rectangular. Los canales incorporan un corte triangular hacia la derecha y un clip superior construido con CSS. Reglas de carbón, subrayados y bordes discontinuos evocan impresión y papelería sin usar texturas raster.

El logotipo se muestra completo, proporcional y sin recoloración, rotación, contornos, relieve ni sombras. Los iconos son SVG de línea, con trazo uniforme, remates redondos y texto visible cuando funcionan como navegación.

## Components

### Buttons

- **Shape:** rectos y táctiles, con radio mínimo y altura operativa no menor a 44 CSS px.
- **Primary:** amarillo de marca con tinta carbón; la selección activa puede usar rojo sólo cuando no represente estado de orden.
- **Hover / Focus:** cambio tonal breve, sin salto; foco azul de tres píxeles con separación exterior.
- **Loading / Disabled:** texto de espera explícito, `aria-busy` y contraste suficiente; nunca aparenta estar congelado.

### Cards / Containers

- **Position cards:** misma geometría para Comedor/Domicilio y Recoger/Llevar. Libre usa número verde; Orden abierta, amarillo con tinta oscura; Procesada, rojo con marca de verificación.
- **Branch cards:** conservan la misma gramática pero reducen título, estado, número y metadatos para que nombres largos no saturen la columna.
- **Product cards:** 50 % fotografía y 50 % texto, altura compacta y nombre real de hasta tres líneas; no inventan precio ni descriptor.
- **Containers:** papel plano, borde carbón y sin elevación salvo las excepciones definidas arriba.

### Inputs / Fields

- **Style:** fondo blanco o papel, borde visible y radio mínimo.
- **Focus:** contorno azul exterior de tres píxeles, también en radios y switches cuyo input nativo está visualmente oculto.
- **Error / Disabled:** el error accionable persiste hasta descartar o reintentar; el estado deshabilitado sigue siendo legible y no simula disponibilidad.

### Navigation

El riel de canales usa tres tickets tipo clipboard orientados a la derecha. El seleccionado expone `aria-pressed`, papel blanco, clip amarillo y una única regla roja inferior. `Salir` vive en la cabecera sólo mientras se muestran posiciones; dentro de una orden se oculta para evitar abandonar accidentalmente la captura.

### Virtual Comanda

La comanda virtual conserva deliberadamente el aspecto y el orden de la impresión térmica: encabezado, folio/contexto, comensales, preparación, productos, contacto, comentarios, bebidas, salsas y terminal. Sus celdas siguen siendo controles accesibles, pero no se rediseñan como tarjetas de aplicación.

### Product Image Resolver

La resolución sigue una sola prioridad: `Producto.imagen` mediante endpoint autenticado; después el mapa por código; finalmente `mainlogo.webp`. Lonches sin queso comparten la foto de su variante normal, todas las aguas frescas comparten `aguasfrescas.webp` y todos los refrescos comparten `refresco.webp`. La UI no depende de imágenes remotas.

## Do's and Don'ts

### Do:

- **Do** revisar `PRODUCT.md`, este archivo y el brief de superficie antes de crear o modificar una pantalla.
- **Do** preservar la igualdad geométrica entre posiciones principales y auxiliares mediante la misma retícula.
- **Do** mantener color, etiqueta y símbolo en todos los estados operativos.
- **Do** conservar la comanda virtual alineada con `impresion/render.py::render_comanda` y con una impresión real.
- **Do** servir tipografías, iconos e imágenes desde el proyecto y registrar la procedencia de todo raster nuevo.
- **Do** aplicar este lenguaje al futuro Administrador, pero con navegación separada y funciones sólo después de confirmar alcance y permisos.

### Don't:

- **Don't** devolver Orden abierta al rojo; amarillo es su semántica aprobada.
- **Don't** mostrar `Salir` durante la captura ni reintroducir estados pasivos de conexión o impresora en la cabecera.
- **Don't** crear tarjetas flotantes genéricas, glassmorphism, gradientes decorativos, halos, texturas de papel o sombras duras.
- **Don't** deformar el logotipo ni usar Bebas Neue para instrucciones, datos de cliente o errores extensos.
- **Don't** sustituir la comanda por una maqueta raster o reorganizar su contenido como dashboard.
- **Don't** cargar fotografías de terceros o depender de URLs remotas para productos.
