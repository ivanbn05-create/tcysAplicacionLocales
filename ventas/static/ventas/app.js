(() => {
  "use strict";

  const productos = JSON.parse(document.getElementById("datos-productos").textContent);
  const posiciones = JSON.parse(document.getElementById("datos-posiciones").textContent);
  const nombresCanal = { comedor: "Comedor", domicilio: "Domicilio", sucursales: "Sucursales" };
  const modificadores = [
    { codigo: "C/T", nombre: "Con todo" },
    { codigo: "S/N", nombre: "Sin nada" },
    { codigo: "Ceb", nombre: "Cebolla" },
    { codigo: "DOR", nombre: "Dorado" },
    { codigo: "MED", nombre: "Medio" },
    { codigo: "BLA", nombre: "Blando" },
    { codigo: "C/Q", nombre: "Con queso" },
  ];
  const estado = {
    canal: "comedor",
    persona: 1,
    modoMenu: "productos",
    objetivoModificador: { tipo: "persona", persona: 1 },
    partidaSeleccionada: null,
    tickets: {},
    ticket: null,
    operando: false,
  };

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];
  const csrf = () => document.cookie.split("; ").find(v => v.startsWith("csrftoken="))?.split("=")[1] || "";
  const dinero = (valor) => new Intl.NumberFormat("es-MX", { style: "currency", currency: "MXN" }).format(Number(valor || 0));
  const cantidad = (valor) => Number(valor).toLocaleString("es-MX", { maximumFractionDigits: 3 });
  const escapar = (valor) => String(valor ?? "").replace(/[&<>'"]/g, caracter => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[caracter]);

  async function api(url, opciones = {}) {
    const respuesta = await fetch(url, {
      ...opciones,
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrf(),
        ...(opciones.headers || {}),
      },
    });
    let datos;
    try { datos = await respuesta.json(); } catch { datos = {}; }
    if (!respuesta.ok) throw new Error(datos.error || "No fue posible completar la operación.");
    return datos;
  }

  function toast(mensaje, error = false) {
    const nodo = $("#toast");
    nodo.textContent = mensaje;
    nodo.classList.toggle("error", error);
    nodo.classList.add("visible");
    clearTimeout(toast.temporizador);
    toast.temporizador = setTimeout(() => nodo.classList.remove("visible"), 3300);
  }

  function bloquear(valor) {
    estado.operando = valor;
    $$("button").forEach(boton => boton.disabled = valor);
    if (!valor && estado.ticket) {
      renderMenu();
      renderComanda();
      renderAcciones();
    }
  }

  async function cargarEstado() {
    try {
      const datos = await api("/api/estado/");
      estado.tickets = datos.tickets;
      $("#conexion").classList.remove("error");
      renderPosiciones();
    } catch (error) {
      $("#conexion").classList.add("error");
      toast(error.message, true);
    }
  }

  async function cargarEstadoImpresion() {
    const nodo = $("#impresora-estado");
    nodo.className = "impresora-estado comprobando";
    nodo.querySelector("span").textContent = "Comprobando impresora";
    try {
      const datos = await api("/api/impresion/estado/");
      nodo.title = `${datos.mensaje} ${datos.host}:${datos.puerto}`;
      if (datos.backend === "archivo") {
        nodo.className = "impresora-estado vista-previa";
        nodo.querySelector("span").textContent = "Sólo vista previa";
      } else if (datos.disponible) {
        nodo.className = "impresora-estado lista";
        nodo.querySelector("span").textContent = "Impresora lista";
      } else {
        nodo.className = "impresora-estado error";
        nodo.querySelector("span").textContent = "Impresora sin conexión";
      }
    } catch (error) {
      nodo.className = "impresora-estado error";
      nodo.querySelector("span").textContent = "Error de impresora";
      nodo.title = error.message;
    }
  }

  function renderPosiciones() {
    const contenedor = $("#rejilla-posiciones");
    const filtradas = posiciones.filter(p => p.canal === estado.canal).sort((a, b) => a.orden - b.orden);
    contenedor.innerHTML = filtradas.map(posicion => {
      const ticket = estado.tickets[posicion.id];
      const clase = ticket ? (ticket.estado === "abierto" ? "ocupada" : "procesada") : "";
      const detalle = ticket ? `Ticket ${ticket.folio} · ${dinero(ticket.total)}` : "Disponible";
      return `<button class="posicion ${clase}" data-id="${posicion.id}" type="button"><strong>${posicion.nombre}</strong><small>${detalle}</small></button>`;
    }).join("");
    if (!filtradas.length) contenedor.innerHTML = '<p class="vacio">No hay posiciones configuradas.</p>';
  }

  function cambiarCanal(canal) {
    estado.canal = canal;
    $$(".canal").forEach(b => b.classList.toggle("activo", b.dataset.canal === canal));
    $("#titulo-canal").textContent = nombresCanal[canal];
    renderPosiciones();
  }

  async function abrirPosicion(mesaId) {
    if (estado.operando) return;
    bloquear(true);
    try {
      const datos = await api("/api/tickets/abrir/", { method: "POST", body: JSON.stringify({ mesa_id: mesaId }) });
      estado.ticket = datos.ticket;
      mostrarTicket();
      if (datos.creado) toast(`Ticket ${datos.ticket.folio} abierto.`);
    } catch (error) {
      toast(error.message, true);
    } finally {
      bloquear(false);
    }
  }

  function mostrarTicket() {
    const ticket = estado.ticket;
    $("#vista-posiciones").classList.add("oculto");
    $("#vista-ticket").classList.remove("oculto");
    $("#ticket-canal").textContent = nombresCanal[ticket.canal];
    $("#ticket-mesa").textContent = ticket.mesa;
    $("#ticket-folio").textContent = ticket.folio;
    $("#ticket-estado").textContent = ticket.estado;
    $("#ticket-total").textContent = dinero(ticket.total);
    $("#entrega").value = ticket.entrega_aproximada || "";
    $("#comentario").value = ticket.comentario_general || "";
    const esDomicilio = ticket.canal === "domicilio";
    $("#datos-cliente").classList.toggle("oculto", !esDomicilio);
    if (esDomicilio) {
      $("#cliente-nombre").value = ticket.cliente.nombre || "";
      $("#cliente-telefono").value = ticket.cliente.telefono || "";
      $("#cliente-domicilio").value = ticket.cliente.domicilio || "";
      $("#cliente-referencia").value = ticket.cliente.referencia || "";
    }
    renderPersonas();
    estado.modoMenu = "productos";
    estado.objetivoModificador = { tipo: "persona", persona: estado.persona };
    estado.partidaSeleccionada = null;
    renderMenu();
    renderComanda();
    renderAcciones();
  }

  function renderPersonas() {
    $("#personas").innerHTML = Array.from({ length: 24 }, (_, i) => i + 1).map(numero =>
      `<button class="persona ${estado.persona === numero ? "activa" : ""}" data-persona="${numero}" type="button">${numero}</button>`
    ).join("");
  }

  function esBebida(partidaOProducto) {
    return String(partidaOProducto?.categoria || "").toLocaleLowerCase("es-MX") === "bebidas";
  }

  function bloqueComensales() {
    const inicio = Math.floor((estado.persona - 1) / 6) * 6 + 1;
    return { inicio, personas: Array.from({ length: 6 }, (_, indice) => inicio + indice) };
  }

  function cambiarModoMenu(modo, objetivo = null) {
    estado.modoMenu = modo;
    if (objetivo) estado.objetivoModificador = objetivo;
    renderMenu();
    renderComanda();
  }

  function renderMenu() {
    const abierto = estado.ticket?.estado === "abierto";
    const volverProductos = $("#menu-productos");
    volverProductos.classList.toggle("oculto", estado.modoMenu === "productos");
    if (estado.modoMenu === "modificadores") {
      const objetivo = estado.objetivoModificador.tipo === "grupo"
        ? `comensales ${estado.objetivoModificador.inicio}–${estado.objetivoModificador.inicio + 5}`
        : `comensal ${estado.objetivoModificador.persona}`;
      $("#menu-contexto").textContent = "Preparación";
      $("#menu-indicacion").textContent = `Aplicar a ${objetivo}`;
      $("#productos").innerHTML = modificadores.map(modificador =>
        `<button class="producto opcion-preparacion" data-codigo="${modificador.codigo}" data-nombre="${modificador.nombre}" type="button" ${!abierto ? "disabled" : ""}>
          <small>${modificador.codigo}</small><strong>${modificador.nombre}</strong><b>Agregar</b>
        </button>`
      ).join("");
      return;
    }
    const soloBebidas = estado.modoMenu === "bebidas";
    const disponibles = soloBebidas ? productos.filter(esBebida) : productos;
    $("#menu-contexto").textContent = soloBebidas ? "Bebidas" : "Menú completo";
    $("#menu-indicacion").textContent = soloBebidas ? "Se acumulan al final de la comanda" : "Todos los productos, en el orden del menú";
    $("#productos").innerHTML = disponibles.map(producto =>
      `<button class="producto" data-id="${producto.id}" type="button" ${!abierto ? "disabled" : ""}>
        <small>${escapar(producto.categoria)} · ${escapar(producto.corto)}</small><strong>${escapar(producto.nombre)}</strong><b>${dinero(producto.precio)}</b>
      </button>`
    ).join("") || '<div class="vacio">No hay opciones disponibles.</div>';
  }

  function formatoFechaComanda(valor) {
    const fecha = new Date(valor);
    if (Number.isNaN(fecha.getTime())) return "";
    return `${fecha.toLocaleDateString("es-MX", { day: "2-digit", month: "2-digit", year: "numeric" })} · ${fecha.toLocaleTimeString("es-MX", { hour: "2-digit", minute: "2-digit" })}`;
  }

  function renderComanda() {
    const ticket = estado.ticket;
    const contenedor = $("#comanda-preview");
    if (!ticket || !contenedor) return;
    const { inicio, personas } = bloqueComensales();
    const partidasComida = ticket.partidas.filter(partida => !esBebida(partida));
    const filas = new Map();
    for (const partida of partidasComida) {
      if (!filas.has(partida.producto_id)) {
        filas.set(partida.producto_id, { productoId: partida.producto_id, nombre: partida.nombre_corto, cantidades: new Map() });
      }
      const fila = filas.get(partida.producto_id);
      fila.cantidades.set(partida.comensal, (fila.cantidades.get(partida.comensal) || 0) + Number(partida.cantidad));
    }
    const preparacion = new Map(personas.map(persona => [persona, ticket.modificadores.filter(mod => mod.comensal === persona)]));
    const bebidas = new Map();
    for (const partida of ticket.partidas.filter(esBebida)) {
      if (!bebidas.has(partida.producto_id)) bebidas.set(partida.producto_id, { nombre: partida.nombre_corto, cantidad: 0 });
      bebidas.get(partida.producto_id).cantidad += Number(partida.cantidad);
    }
    const filasHtml = [...filas.values()].map(fila => {
      const celdas = personas.map(persona => {
        const valor = fila.cantidades.get(persona) || 0;
        const seleccionada = estado.partidaSeleccionada?.productoId === fila.productoId && estado.partidaSeleccionada?.persona === persona;
        return `<button class="comanda-celda cantidad-celda ${seleccionada ? "seleccionada" : ""}" data-celda-producto="${fila.productoId}" data-celda-persona="${persona}" type="button">${valor ? cantidad(valor) : ""}</button>`;
      }).join("");
      return `<button class="comanda-etiqueta producto-zona" data-modo-menu="productos" type="button">${escapar(fila.nombre)}</button>${celdas}`;
    }).join("") || `<button class="comanda-vacio producto-zona" data-modo-menu="productos" type="button">Toca aquí para mostrar productos y comenzar la orden</button>`;
    const bebidasHtml = [...bebidas.values()].map(bebida => `<span><strong>( ${cantidad(bebida.cantidad)} )</strong> ${escapar(bebida.nombre)}</span>`).join("");
    const seleccion = estado.partidaSeleccionada;
    const partidasSeleccionadas = seleccion
      ? ticket.partidas.filter(partida => partida.producto_id === seleccion.productoId && partida.comensal === seleccion.persona)
      : [];
    const controles = partidasSeleccionadas.length ? `
      <div class="comanda-ajuste">
        <span>Comensal ${seleccion.persona} · ${escapar(partidasSeleccionadas[0].nombre)}</span>
        <div>
          <button data-ajuste="menos" type="button">−</button>
          <button data-ajuste="mas" type="button">+</button>
          <button class="eliminar" data-ajuste="eliminar" type="button">×</button>
        </div>
      </div>` : "";
    const comentarioActual = $("#comentario")?.value || ticket.comentario_general || "";
    $("#orden-resumen").textContent = `${ticket.partidas.length} ${ticket.partidas.length === 1 ? "partida" : "partidas"}`;
    $("#ticket-total").textContent = dinero(ticket.total);
    contenedor.innerHTML = `
      <section class="comanda-papel">
        <header class="comanda-papel-encabezado">
          <em>Los Tocayos Tacos de Barbacoa</em>
          <div><span>${formatoFechaComanda(ticket.creado_en)}</span><strong>Ticket: ${ticket.folio}</strong></div>
          <div><span>Ent. Aprox: ${escapar(ticket.entrega_aproximada || "—")}</span><strong>${escapar(ticket.mesa)}</strong></div>
          <b>${dinero(ticket.total)}</b>
        </header>
        <div class="comanda-matriz">
          <span class="comanda-etiqueta encabezado">Comensal</span>
          ${personas.map(persona => `<button class="comanda-numero ${estado.persona === persona ? "activo" : ""}" data-seleccionar-persona="${persona}" type="button">${persona}</button>`).join("")}
          <button class="comanda-global" data-objetivo-modificador="grupo" data-inicio="${inicio}" type="button">+ Preparación para los seis comensales</button>
          <span class="comanda-etiqueta encabezado">Prep.</span>
          ${personas.map(persona => {
            const texto = preparacion.get(persona).map(mod => mod.codigo).join(" ") || "+";
            return `<button class="comanda-comentario ${estado.persona === persona ? "activo" : ""}" data-objetivo-modificador="persona" data-persona="${persona}" type="button">${escapar(texto)}</button>`;
          }).join("")}
          ${filasHtml}
        </div>
        <button class="comanda-bebidas" data-modo-menu="bebidas" type="button">
          <strong>Bebidas</strong>
          ${bebidasHtml || "<span>Toca aquí para elegir bebidas</span>"}
        </button>
        ${comentarioActual ? `<p class="comanda-comentario-general">${escapar(comentarioActual)}</p>` : ""}
      </section>
      ${controles}`;
  }

  function renderAcciones() {
    const abierto = estado.ticket.estado === "abierto";
    const cobrable = ["procesado", "cobrar"].includes(estado.ticket.estado);
    $("#procesar").classList.toggle("oculto", !abierto);
    $("#cobrar").classList.toggle("oculto", !cobrable);
    $("#reimprimir").classList.toggle("oculto", abierto);
    $$(".persona, .opcion-preparacion, .comanda-papel button, .producto").forEach(b => b.disabled = !abierto);
  }

  async function agregarProducto(productoId) {
    try {
      const datos = await api(`/api/tickets/${estado.ticket.id}/partidas/`, {
        method: "POST",
        body: JSON.stringify({ producto_id: productoId, comensal: estado.persona, cantidad: 1 }),
      });
      estado.ticket = datos.ticket;
      renderComanda();
      renderMenu();
    } catch (error) { toast(error.message, true); }
  }

  async function modificarGrupoPartidas(accion) {
    const seleccion = estado.partidaSeleccionada;
    if (!seleccion) return;
    const grupo = estado.ticket.partidas.filter(partida =>
      partida.producto_id === seleccion.productoId && partida.comensal === seleccion.persona
    );
    if (!grupo.length) return;
    if (accion === "mas") {
      await agregarProducto(seleccion.productoId);
      return;
    }
    try {
      let datos;
      if (accion === "eliminar") {
        for (const partida of grupo) datos = await api(`/api/partidas/${partida.id}/`, { method: "DELETE" });
        estado.partidaSeleccionada = null;
      } else {
        const partida = grupo[grupo.length - 1];
        const nuevaCantidad = Number(partida.cantidad) - 1;
        const opciones = nuevaCantidad <= 0
          ? { method: "DELETE" }
          : { method: "PATCH", body: JSON.stringify({ cantidad: nuevaCantidad }) };
        datos = await api(`/api/partidas/${partida.id}/`, opciones);
      }
      if (datos) estado.ticket = datos.ticket;
      renderComanda();
    } catch (error) { toast(error.message, true); }
  }

  async function aplicarPreparacion(codigo, nombre) {
    try {
      const objetivo = estado.objetivoModificador;
      const cuerpo = objetivo.tipo === "grupo"
        ? { comensales: Array.from({ length: 6 }, (_, indice) => objetivo.inicio + indice), codigo, nombre }
        : { comensal: objetivo.persona, codigo, nombre };
      const datos = await api(`/api/tickets/${estado.ticket.id}/modificadores/`, {
        method: "POST", body: JSON.stringify(cuerpo),
      });
      estado.ticket = datos.ticket;
      renderPersonas();
      renderComanda();
      renderMenu();
    } catch (error) { toast(error.message, true); }
  }

  async function guardarDatos() {
    const cuerpo = {
      comentario_general: $("#comentario").value,
      entrega_aproximada: $("#entrega").value,
      cliente_nombre: $("#cliente-nombre").value,
      cliente_telefono: $("#cliente-telefono").value,
      cliente_domicilio: $("#cliente-domicilio").value,
      cliente_referencia: $("#cliente-referencia").value,
    };
    const datos = await api(`/api/tickets/${estado.ticket.id}/`, { method: "PATCH", body: JSON.stringify(cuerpo) });
    estado.ticket = datos.ticket;
  }

  async function procesar() {
    bloquear(true);
    try {
      await guardarDatos();
      const datos = await api(`/api/tickets/${estado.ticket.id}/procesar/`, { method: "POST", body: "{}" });
      estado.ticket = datos.ticket;
      mostrarTicket();
      mostrarImpresiones(datos.impresiones);
    } catch (error) { toast(error.message, true); }
    finally { bloquear(false); }
  }

  async function cobrar(formaPago, importeRecibido) {
    bloquear(true);
    try {
      const datos = await api(`/api/tickets/${estado.ticket.id}/cobrar/`, {
        method: "POST", body: JSON.stringify({ forma_pago: formaPago, importe_recibido: importeRecibido }),
      });
      estado.ticket = datos.ticket;
      $("#dialogo-cobro").close();
      mostrarTicket();
      mostrarImpresiones(datos.impresiones);
    } catch (error) { toast(error.message, true); }
    finally { bloquear(false); }
  }

  async function reimprimir() {
    const formato = estado.ticket.estado === "pagado" ? "cuenta" : "comanda";
    try {
      const datos = await api(`/api/tickets/${estado.ticket.id}/imprimir/`, { method: "POST", body: JSON.stringify({ formato }) });
      mostrarImpresiones(datos.impresiones);
    } catch (error) { toast(error.message, true); }
  }

  function mostrarImpresiones(impresiones) {
    const contenedor = $("#previsualizaciones");
    contenedor.innerHTML = impresiones.map(impresion => {
      const imagen = impresion.url ? `<img src="${impresion.url}" alt="${escapar(impresion.formato)} para ${escapar(impresion.destino)}">` : "";
      if (impresion.estado === "impreso") return `<figure class="preview impreso">${imagen}<strong>${escapar(impresion.formato)} · ${escapar(impresion.destino)}</strong><p class="resultado">Enviado a la impresora térmica.</p></figure>`;
      if (impresion.estado === "generado") return `<figure class="preview generado">${imagen}<strong>${escapar(impresion.formato)} · ${escapar(impresion.destino)}</strong><p class="resultado">Vista previa guardada; no se envió papel.</p></figure>`;
      if (impresion.estado === "error") return `<figure class="preview error">${imagen}<strong>${escapar(impresion.destino)}</strong><p class="resultado">No se pudo imprimir: ${escapar(impresion.error)}</p></figure>`;
      return `<div class="preview"><strong>${escapar(impresion.destino)}</strong><p class="resultado">Trabajo en cola. El servicio de impresión lo procesará.</p></div>`;
    }).join("") || '<p class="vacio">No hubo partidas para enviar a cocina o barra.</p>';
    const errores = impresiones.filter(impresion => impresion.estado === "error").length;
    const previas = impresiones.filter(impresion => impresion.estado === "generado").length;
    const impresas = impresiones.filter(impresion => impresion.estado === "impreso").length;
    const resumen = $("#resumen-impresion");
    resumen.className = "resumen-impresion";
    if (errores) {
      resumen.classList.add("error");
      resumen.textContent = "La orden quedó guardada, pero al menos una salida no llegó a la impresora. Revisa el detalle y vuelve a imprimir.";
      toast("No se pudo completar la impresión.", true);
    } else if (previas) {
      resumen.classList.add("advertencia");
      resumen.textContent = "Se generó la vista previa, pero el sistema está configurado para no enviar a la impresora.";
      toast("Vista previa generada; no se envió a la impresora.");
    } else if (impresas) {
      resumen.textContent = "La impresora confirmó la recepción de todas las salidas.";
      toast("Impresión enviada correctamente.");
    } else {
      resumen.classList.add("advertencia");
      resumen.textContent = "La impresión está en cola y será atendida por el servicio local.";
      toast("Impresión en cola.");
    }
    $("#dialogo-impresion").showModal();
    cargarEstadoImpresion();
  }

  function volver() {
    estado.ticket = null;
    $("#vista-ticket").classList.add("oculto");
    $("#vista-posiciones").classList.remove("oculto");
    cargarEstado();
  }

  $$(".canal").forEach(boton => boton.addEventListener("click", () => cambiarCanal(boton.dataset.canal)));
  $("#actualizar-estado").addEventListener("click", cargarEstado);
  $("#impresora-estado").addEventListener("click", cargarEstadoImpresion);
  $("#rejilla-posiciones").addEventListener("click", evento => {
    const boton = evento.target.closest(".posicion");
    if (boton) abrirPosicion(boton.dataset.id);
  });
  $("#volver").addEventListener("click", volver);
  $("#personas").addEventListener("click", evento => {
    const boton = evento.target.closest(".persona");
    if (boton) {
      estado.persona = Number(boton.dataset.persona);
      estado.objetivoModificador = { tipo: "persona", persona: estado.persona };
      estado.partidaSeleccionada = null;
      renderPersonas();
      renderComanda();
    }
  });
  $("#productos").addEventListener("click", evento => {
    const preparacion = evento.target.closest(".opcion-preparacion");
    if (preparacion) {
      aplicarPreparacion(preparacion.dataset.codigo, preparacion.dataset.nombre);
      return;
    }
    const boton = evento.target.closest(".producto");
    if (boton) agregarProducto(boton.dataset.id);
  });
  $("#menu-productos").addEventListener("click", () => cambiarModoMenu("productos"));
  $("#comanda-preview").addEventListener("click", evento => {
    const persona = evento.target.closest("[data-seleccionar-persona]");
    if (persona) {
      estado.persona = Number(persona.dataset.seleccionarPersona);
      estado.objetivoModificador = { tipo: "persona", persona: estado.persona };
      estado.partidaSeleccionada = null;
      renderPersonas();
      renderComanda();
      return;
    }
    const objetivo = evento.target.closest("[data-objetivo-modificador]");
    if (objetivo) {
      if (objetivo.dataset.objetivoModificador === "grupo") {
        estado.objetivoModificador = { tipo: "grupo", inicio: Number(objetivo.dataset.inicio) };
      } else {
        estado.persona = Number(objetivo.dataset.persona);
        estado.objetivoModificador = { tipo: "persona", persona: estado.persona };
        renderPersonas();
      }
      cambiarModoMenu("modificadores");
      return;
    }
    const zonaMenu = evento.target.closest("[data-modo-menu]");
    if (zonaMenu) {
      cambiarModoMenu(zonaMenu.dataset.modoMenu);
      return;
    }
    const celda = evento.target.closest("[data-celda-producto]");
    if (celda) {
      estado.persona = Number(celda.dataset.celdaPersona);
      estado.objetivoModificador = { tipo: "persona", persona: estado.persona };
      estado.partidaSeleccionada = { productoId: celda.dataset.celdaProducto, persona: estado.persona };
      renderPersonas();
      cambiarModoMenu("productos");
      return;
    }
    const ajuste = evento.target.closest("[data-ajuste]");
    if (ajuste) modificarGrupoPartidas(ajuste.dataset.ajuste);
  });
  $("#comentario").addEventListener("input", renderComanda);
  $("#procesar").addEventListener("click", procesar);
  $("#cobrar").addEventListener("click", () => {
    $("#cobro-total").textContent = dinero(estado.ticket.total);
    $("#importe-recibido").value = estado.ticket.total;
    $("#dialogo-cobro").showModal();
  });
  $("#form-cobro").addEventListener("submit", evento => {
    evento.preventDefault();
    if (evento.submitter?.value === "cancel") { $("#dialogo-cobro").close(); return; }
    cobrar(new FormData(evento.currentTarget).get("forma_pago"), $("#importe-recibido").value);
  });
  $("#reimprimir").addEventListener("click", reimprimir);
  $("#cerrar-impresion").addEventListener("click", () => $("#dialogo-impresion").close());
  $("#terminar-impresion").addEventListener("click", () => {
    $("#dialogo-impresion").close();
    if (estado.ticket?.estado === "pagado") volver();
  });

  window.addEventListener("online", cargarEstado);
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/service-worker.js").catch(() => {});
  renderPersonas();
  renderMenu();
  cargarEstado();
  cargarEstadoImpresion();
})();
