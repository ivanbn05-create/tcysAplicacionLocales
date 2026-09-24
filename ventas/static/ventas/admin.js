(() => {
  "use strict";

  const $ = selector => document.querySelector(selector);
  const $$ = selector => [...document.querySelectorAll(selector)];
  const csrf = () => document.cookie.split("; ").find(valor => valor.startsWith("csrftoken="))?.split("=")[1] || "";
  const dinero = valor => new Intl.NumberFormat("es-MX", { style: "currency", currency: "MXN" }).format(Number(valor || 0));
  const escapar = valor => String(valor ?? "").replace(/[&<>'"]/g, caracter => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[caracter]);
  const DENOMINACIONES = ["0.5", "1", "2", "5", "10", "20", "50", "100", "200", "500", "1000"];
  const CLAVE_ORIGEN_ADMIN = "tocayos_admin_origen_v1";
  const DEVICE_ID_KEY = "tocayos_pos_device_id";
  const RUTAS = Object.freeze({
    controlEfectivo: "/api/administrador/control-efectivo/",
    configuracionesImpresion: "/api/administrador/configuracion-tecnica/impresion/",
  });

  function crearDeviceId() {
    if (window.crypto?.randomUUID) return window.crypto.randomUUID();
    const aleatorio = Math.random().toString(36).slice(2);
    return "tablet-" + Date.now().toString(36) + "-" + aleatorio;
  }

  function obtenerDeviceId() {
    try {
      const guardado = localStorage.getItem(DEVICE_ID_KEY);
      if (guardado) return guardado;
      const nuevo = crearDeviceId();
      localStorage.setItem(DEVICE_ID_KEY, nuevo);
      return nuevo;
    } catch {
      return crearDeviceId();
    }
  }

  const POS_DEVICE_ID = obtenerDeviceId();

  const estado = {
    administrador: null,
    resolucionClave: null,
    temporizadorToast: null,
    promesaResumen: null,
    operacionEnCurso: false,
    peticionesPendientes: 0,
    ticketSeleccionadoId: "",
    ticketMoverId: "",
    ticketsSeleccionados: new Set(),
    canalPedidosAbierto: "",
    movimientoEditandoId: "",
    sucursalActivaId: "",
    sucursalPedidosActivaId: "",
    asignacionesRepartidor: new Map(),
    secuenciaAsignacion: 0,
    panelInicialSolicitado: "",
    pantallaCompletaSuspendida: false,
    regresoEnCurso: false,
    configuracionesImpresion: [],
    configuracionImpresionId: "",
    configuracionesImpresionCargando: false,
  };

  function tienePermisoAdministrador(nombre) {
    return estado.administrador?.acceso?.permisos?.[nombre] === true;
  }

  function elementoPermitido(nodo) {
    const permiso = nodo?.dataset?.permisoAdmin;
    return !permiso || tienePermisoAdministrador(permiso);
  }

  function exigirPermisoAdministrador(nombre) {
    if (tienePermisoAdministrador(nombre)) return true;
    toast("Tu acceso administrativo no permite esta acción.", true);
    return false;
  }

  class ErrorAPI extends Error {
    constructor(mensaje, status, datos) {
      super(mensaje);
      this.status = status;
      this.datos = datos;
    }
  }

  async function api(url, opciones = {}) {
    const respuesta = await fetch(url, {
      cache: "no-store",
      credentials: "same-origin",
      ...opciones,
      headers: {
        "Content-Type": "application/json",
        ...(opciones.headers || {}),
        "X-CSRFToken": csrf(),
        "X-POS-Device-ID": POS_DEVICE_ID,
      },
    });
    let datos = {};
    try { datos = await respuesta.json(); } catch { /* La respuesta vacía se trata como objeto. */ }
    if (!respuesta.ok) throw new ErrorAPI(datos.error || "No fue posible completar la operación.", respuesta.status, datos);
    return datos;
  }

  function origenRegresoAdministrador() {
    try {
      return sessionStorage.getItem(CLAVE_ORIGEN_ADMIN) === "ventas" ? "ventas" : "inicio";
    } catch {
      return "inicio";
    }
  }

  function configurarRegresoAdministrador() {
    const vuelveAVentas = origenRegresoAdministrador() === "ventas";
    const etiqueta = vuelveAVentas ? "Volver a Ventas" : "Volver al inicio";
    $$("[data-regreso-admin]").forEach(control => {
      control.setAttribute("aria-label", etiqueta);
      control.title = etiqueta;
      const texto = control.querySelector("[data-regreso-admin-texto]");
      if (texto) texto.textContent = etiqueta;
    });
  }

  async function regresarDesdeAdministrador() {
    if (estado.regresoEnCurso) return;
    estado.regresoEnCurso = true;
    const vuelveAVentas = origenRegresoAdministrador() === "ventas";
    $$("[data-regreso-admin]").forEach(control => { control.disabled = true; });
    try { sessionStorage.removeItem(CLAVE_ORIGEN_ADMIN); } catch { /* El regreso sigue disponible. */ }
    if (!vuelveAVentas) {
      try {
        await api("/api/operador/salir/", { method: "POST", body: "{}" });
      } catch { /* Una sesión vencida ya conduce a Inicio. */ }
    }
    window.location.assign("/");
  }

  function toast(mensaje, error = false) {
    const nodo = $("#admin-toast");
    nodo.textContent = mensaje;
    nodo.classList.toggle("error", error);
    nodo.classList.add("visible");
    clearTimeout(estado.temporizadorToast);
    estado.temporizadorToast = setTimeout(() => nodo.classList.remove("visible"), error ? 6500 : 3500);
  }

  function ajustarEstadoOcupado(cambio) {
    estado.peticionesPendientes = Math.max(0, estado.peticionesPendientes + cambio);
    $("#administrador-app").setAttribute("aria-busy", estado.peticionesPendientes ? "true" : "false");
  }

  function mostrarEstadoPedidos(tipo = "", mensaje = "") {
    const aviso = $("#estado-pedidos");
    const mapa = $("#mapa-posiciones");
    if (!aviso || !mapa) return;
    aviso.textContent = mensaje;
    aviso.className = ["estado-pedidos", tipo].filter(Boolean).join(" ");
    aviso.hidden = !mensaje;
    mapa.setAttribute("aria-busy", tipo === "cargando" ? "true" : "false");
  }

  function controlQueDisparoLaAccion() {
    const activo = document.activeElement;
    if (activo instanceof HTMLButtonElement) return activo;
    return activo?.closest?.("form")?.querySelector('button[type="submit"]') || null;
  }

  function marcarControlPendiente(control, pendiente, etiqueta = "Procesando…") {
    if (!control) return;
    if (pendiente) {
      control.dataset.textoAntesDeEspera = control.textContent;
      control.dataset.deshabilitadoAntesDeEspera = control.disabled ? "1" : "0";
      control.textContent = etiqueta;
      control.disabled = true;
      control.setAttribute("aria-busy", "true");
      return;
    }
    if (!control.isConnected) return;
    control.textContent = control.dataset.textoAntesDeEspera || control.textContent;
    control.disabled = control.dataset.deshabilitadoAntesDeEspera === "1";
    control.removeAttribute("aria-busy");
    delete control.dataset.textoAntesDeEspera;
    delete control.dataset.deshabilitadoAntesDeEspera;
  }

  function fechaCorta(valor) {
    if (!valor) return "Sin fecha";
    const [fecha] = String(valor).split("T");
    const [anio, mes, dia] = fecha.split("-").map(Number);
    if (!anio || !mes || !dia) return valor;
    return new Intl.DateTimeFormat("es-MX", { day: "2-digit", month: "short", year: "numeric" }).format(new Date(anio, mes - 1, dia));
  }

  function fechaHora(valor) {
    if (!valor) return "—";
    return new Intl.DateTimeFormat("es-MX", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(valor));
  }

  function periodoMensual(valor) {
    if (!valor) return "Sin periodo";
    const [anio, mes] = String(valor).split("-").map(Number);
    if (!anio || !mes) return valor;
    const etiqueta = new Intl.DateTimeFormat("es-MX", { month: "long", year: "numeric" }).format(new Date(anio, mes - 1, 1));
    return etiqueta.charAt(0).toUpperCase() + etiqueta.slice(1);
  }

  function fechaLocalISO(fecha) {
    const anio = fecha.getFullYear();
    const mes = String(fecha.getMonth() + 1).padStart(2, "0");
    const dia = String(fecha.getDate()).padStart(2, "0");
    return `${anio}-${mes}-${dia}`;
  }

  function programacionPredeterminada() {
    const fecha = new Date(Date.now() + 60 * 60 * 1000);
    return {
      fecha: fechaLocalISO(fecha),
      hora: `${String(fecha.getHours()).padStart(2, "0")}:${String(fecha.getMinutes()).padStart(2, "0")}`,
      minima: fechaLocalISO(new Date()),
    };
  }

  function mostrarPanel(nombre) {
    const destino = $("[data-admin-panel=\"" + CSS.escape(nombre) + "\"]");
    if (!destino) {
      toast("La sección solicitada no está disponible.", true);
      return false;
    }
    if (!elementoPermitido(destino)) {
      toast("Tu acceso administrativo no permite abrir esta sección.", true);
      return false;
    }
    $$("[data-admin-panel]").forEach(panel => {
      const activo = panel === destino;
      panel.hidden = !activo;
      panel.classList.toggle("activo", activo);
    });
    $$(".rail-item").forEach(boton => {
      const permitido = elementoPermitido(boton);
      const activo = permitido && boton.dataset.panel === nombre;
      boton.hidden = !permitido;
      boton.classList.toggle("activo", activo);
      if (activo) boton.setAttribute("aria-current", "page");
      else boton.removeAttribute("aria-current");
    });
    history.replaceState(null, "", "#" + nombre);
    const movimientoReducido = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    window.scrollTo({ top: 0, behavior: movimientoReducido ? "auto" : "smooth" });
    if (nombre === "configuracion-tecnica") {
      cargarConfiguracionesImpresion();
    }
    return true;
  }

  async function navegarPanel(nombre) {
    const destino = $("[data-admin-panel=\"" + CSS.escape(nombre) + "\"]");
    if (!destino) return mostrarPanel(nombre);
    if (destino.dataset.permisoAdmin) {
      const actualizado = await cargarResumen(false);
      if (!actualizado) return false;
      if (!elementoPermitido(destino)) {
        toast("Tu acceso administrativo no permite abrir esta sección.", true);
        return false;
      }
    }
    return mostrarPanel(nombre);
  }

  function aplicarPermisosAdministrativos() {
    $$("[data-permiso-admin]").forEach(nodo => {
      nodo.hidden = !elementoPermitido(nodo);
    });
    const panelActivo = $(".admin-panel.activo");
    const panelDeseado = estado.panelInicialSolicitado || panelActivo?.dataset.adminPanel || "inicio";
    estado.panelInicialSolicitado = "";
    const destinoDeseado = $("[data-admin-panel=\"" + CSS.escape(panelDeseado) + "\"]");
    if (!destinoDeseado || !elementoPermitido(destinoDeseado)) {
      if (panelDeseado !== "inicio") {
        toast("Tu acceso administrativo no permite abrir esta sección.", true);
      }
      mostrarPanel("inicio");
      return;
    }
    mostrarPanel(panelDeseado);
  }

  function pedirClave(titulo, ayuda) {
    const dialogo = $("#dialogo-clave");
    $("#dialogo-clave-titulo").textContent = titulo;
    $("#dialogo-clave-ayuda").textContent = ayuda || "Ingresa la clave de administrador para continuar.";
    $("#error-clave").hidden = true;
    $("#clave-administrador").value = "";
    dialogo.showModal();
    setTimeout(() => $("#clave-administrador").focus(), 40);
    return new Promise(resolve => { estado.resolucionClave = resolve; });
  }

  function resolverClave(valor) {
    const dialogo = $("#dialogo-clave");
    if (dialogo.open) dialogo.close();
    const resolver = estado.resolucionClave;
    estado.resolucionClave = null;
    resolver?.(valor);
  }

  async function autorizarEntrada() {
    while (true) {
      const clave = await pedirClave("Abrir administrador", "Esta pantalla administra el turno de la sucursal. Clave inicial: 0000.");
      if (!clave) {
        await regresarDesdeAdministrador();
        return false;
      }
      try {
        await api("/api/administrador/acceso/", {
          method: "POST",
          body: JSON.stringify({ clave_administrador: clave }),
        });
        return true;
      } catch (error) {
        toast(error.message, true);
      }
    }
  }

  async function cargarResumen(control = null) {
    if (estado.promesaResumen) return estado.promesaResumen;
    const iniciador = control === false
      ? null
      : (control || controlQueDisparoLaAccion() || $("#actualizar-resumen"));
    const promesa = (async () => {
      ajustarEstadoOcupado(1);
      marcarControlPendiente(iniciador, true, "Actualizando…");
      mostrarEstadoPedidos("cargando", estado.administrador ? "Actualizando posiciones…" : "Cargando posiciones…");
      try {
        let datos;
        try {
          datos = await api("/api/administrador/resumen/");
        } catch (error) {
          if (error.status !== 401 || !(await autorizarEntrada())) throw error;
          datos = await api("/api/administrador/resumen/");
        }
        estado.administrador = datos.administrador;
        if (!tienePermisoAdministrador("gestionar_usuarios")) {
          estado.administrador.usuarios = [];
        }
        depurarSeleccion();
        renderTodo();
        mostrarEstadoPedidos();
        return true;
      } catch (error) {
        mostrarEstadoPedidos("error", `No se pudieron cargar las posiciones. ${error.message} Usa Actualizar para volver a intentarlo.`);
        toast(error.message, true);
        return false;
      } finally {
        marcarControlPendiente(iniciador, false);
        ajustarEstadoOcupado(-1);
      }
    })();
    estado.promesaResumen = promesa;
    try {
      return await promesa;
    } finally {
      if (estado.promesaResumen === promesa) estado.promesaResumen = null;
    }
  }

  async function ejecutarAccion({ url, method = "POST", cuerpo = {}, mensaje, refrescar = true, control = null }) {
    if (estado.operacionEnCurso) {
      toast("Espera a que termine la acción en curso.", true);
      return null;
    }
    const iniciador = control || controlQueDisparoLaAccion();
    estado.operacionEnCurso = true;
    ajustarEstadoOcupado(1);
    marcarControlPendiente(iniciador, true);
    const opciones = { method, body: JSON.stringify(cuerpo) };
    try {
      let datos;
      try {
        datos = await api(url, opciones);
      } catch (error) {
        if (error.status !== 401 || !(await autorizarEntrada())) throw error;
        datos = await api(url, opciones);
      }
      if (mensaje) toast(mensaje);
      registrarImpresion(datos.impresiones);
      if (refrescar) await cargarResumen(false);
      return datos;
    } catch (error) {
      toast(error.message, true);
      return null;
    } finally {
      marcarControlPendiente(iniciador, false);
      ajustarEstadoOcupado(-1);
      estado.operacionEnCurso = false;
    }
  }

  function registrarImpresion(impresiones = []) {
    const trabajo = impresiones.find(item => item.url);
    if (!trabajo) return;
    const enlace = $("#ultima-impresion");
    enlace.href = trabajo.url;
    enlace.hidden = false;
    toast("Reporte generado. Puedes abrir la vista previa desde Reportes y corte.");
  }

  function opcionesRepartidores(seleccionado = "") {
    const repartidores = estado.administrador?.repartidores || [];
    return '<option value="">Seleccionar repartidor</option>' + repartidores.map(item => '<option value="' + escapar(item.id) + '" ' + (String(item.id) === String(seleccionado) ? "selected" : "") + '>' + escapar(item.nombre) + '</option>').join("");
  }

  function tickets() {
    return estado.administrador?.tickets || [];
  }

  function buscarTicket(id) {
    return tickets().find(ticket => String(ticket.id) === String(id)) || null;
  }

  function depurarSeleccion() {
    const ids = new Set(tickets().map(ticket => String(ticket.id)));
    estado.ticketsSeleccionados.forEach(id => {
      if (!ids.has(String(id))) estado.ticketsSeleccionados.delete(id);
    });
    if (estado.ticketSeleccionadoId && !ids.has(String(estado.ticketSeleccionadoId))) estado.ticketSeleccionadoId = "";
    if (estado.ticketMoverId && !ids.has(String(estado.ticketMoverId))) estado.ticketMoverId = "";
  }

  function posicionesCompletas() {
    const admin = estado.administrador || {};
    if (Array.isArray(admin.posiciones)) return [...admin.posiciones];
    const posiciones = [...(admin.posiciones_disponibles || [])];
    const ids = new Set(posiciones.map(item => String(item.id)));
    tickets().filter(ticket => !["cancelado", "pagado", "programado"].includes(ticket.estado)).forEach(ticket => {
      if (!ticket.mesa_id || ids.has(String(ticket.mesa_id))) return;
      posiciones.push({
        id: ticket.mesa_id,
        nombre: ticket.mesa,
        canal: ticket.canal,
        canal_etiqueta: ticket.canal_etiqueta,
        disponible: false,
        ticket_id: ticket.id,
        ticket_folio: ticket.folio,
        ticket_estado: ticket.estado,
        cliente_sucursal_id: ticket.cliente_sucursal_id || "",
        cliente_sucursal: ticket.cliente_sucursal || "",
      });
      ids.add(String(ticket.mesa_id));
    });
    return posiciones;
  }

  function ticketDePosicion(posicion) {
    if (posicion.ticket && typeof posicion.ticket === "object") return posicion.ticket;
    if (posicion.ticket_id) return buscarTicket(posicion.ticket_id);
    return tickets().find(ticket => String(ticket.mesa_id) === String(posicion.id) && !["cancelado", "pagado", "programado"].includes(ticket.estado)) || null;
  }

  function tipoLote(ticket) {
    if (!ticket || !["procesado", "cobrar"].includes(ticket.estado)) return "";
    if (["comedor", "llevar", "recoger"].includes(ticket.canal)) return "cobrar";
    if (ticket.canal === "domicilio") return "domicilio";
    if (ticket.canal === "sucursales") return "sucursales";
    return "";
  }

  function ticketsSeleccionadosActuales() {
    return [...estado.ticketsSeleccionados]
      .map(buscarTicket)
      .filter(Boolean);
  }

  function renderDetallePedido() {
    const contenedor = $("#detalle-ticket");
    const ticket = buscarTicket(estado.ticketSeleccionadoId);
    if (!ticket) {
      contenedor.innerHTML = "";
      $("#dialogo-detalle-pedido-titulo").textContent = "Detalle del pedido";
      if ($("#dialogo-detalle-pedido").open) $("#dialogo-detalle-pedido").close();
      return;
    }
    const atendio = ticket.atendio || ticket.detalles?.atendio || "Sin operador identificado";
    const creado = ticket.creado_en || ticket.detalles?.creado_en || ticket.detalles?.creado;
    $("#dialogo-detalle-pedido-titulo").textContent = "Detalle del pedido #" + ticket.folio;
    contenedor.innerHTML =
      '<article class="pedido-ficha" data-ticket-id="' + escapar(ticket.id) + '">' +
        '<div class="pedido-ficha-folio"><span>' + escapar(ticket.canal_etiqueta) + '</span><strong>#' + escapar(ticket.folio) + '</strong></div>' +
        '<h3>' + escapar(ticket.mesa) + '</h3>' +
        '<dl>' +
          '<div><dt>Estado</dt><dd>' + escapar(ticket.estado_etiqueta) + '</dd></div>' +
          '<div><dt>Total</dt><dd>' + dinero(ticket.total) + '</dd></div>' +
          '<div><dt>Tomó el pedido</dt><dd>' + escapar(atendio) + '</dd></div>' +
          '<div><dt>Hora de apertura</dt><dd>' + fechaHora(creado) + '</dd></div>' +
          '<div><dt>Cliente</dt><dd>' + escapar(ticket.cliente_nombre || "No aplica") + '</dd></div>' +
          '<div><dt>Repartidor</dt><dd>' + escapar(ticket.repartidor || "Sin asignar") + '</dd></div>' +
        '</dl>' +
        '<div class="pedido-ficha-acciones">' +
          '<label class="pedido-descuento">Descuento porcentual<span><input type="number" min="0" max="100" step="0.01" value="' + escapar(ticket.descuento_porcentaje) + '" aria-label="Descuento porcentual"><button class="boton" data-accion-ticket="descuento" type="button">Guardar descuento</button></span></label>' +
          '<button class="boton peligro" data-pedido-accion="cancelar" data-ticket-id="' + escapar(ticket.id) + '" type="button">Cancelar pedido</button>' +
        '</div>' +
      '</article>';
  }

  function renderBarraLote() {
    const seleccionados = ticketsSeleccionadosActuales();
    const barra = $("#barra-lote");
    const total = seleccionados.length;
    const accionesIndividuales = $("#acciones-pedido-seleccionado");
    $("#conteo-seleccionados").textContent = total;
    $("#lote-resumen").textContent = total === 1 ? "1 pedido seleccionado" : total + " pedidos seleccionados";
    barra.hidden = total === 0;
    accionesIndividuales.hidden = total !== 1;
    [...accionesIndividuales.querySelectorAll("[data-pedido-accion]")].forEach(boton => {
      boton.dataset.ticketId = total === 1 ? seleccionados[0].id : "";
    });
    $$("[data-lote-grupo]").forEach(grupo => { grupo.hidden = true; });
    if (!total) return;
    const tipos = new Set(seleccionados.map(tipoLote));
    const tipo = tipos.size === 1 ? [...tipos][0] : "";
    if (tipo) {
      const grupo = $('[data-lote-grupo="' + tipo + '"]');
      if (grupo) grupo.hidden = false;
    }
    $("#lote-cobrar-conteo").textContent = total + (total === 1 ? " para cobrar" : " para cobrar");
    $("#lote-domicilio-conteo").textContent = total + (total === 1 ? " domicilio" : " domicilios");
    $("#lote-sucursales-conteo").textContent = total + (total === 1 ? " pedido de sucursal" : " pedidos de sucursales");
    $("#lote-repartidor").innerHTML = opcionesRepartidores();
  }

  function renderCeldaPosicion(posicion) {
    const ticket = ticketDePosicion(posicion);
    const seleccionado = Boolean(ticket && estado.ticketsSeleccionados.has(String(ticket.id)));
    const detalleActivo = Boolean(ticket && String(ticket.id) === String(estado.ticketSeleccionadoId));
    const destino = Boolean(estado.ticketMoverId && !ticket);
    const clases = [
      "celda-posicion-admin",
      ticket ? "ocupada" : "libre",
      seleccionado ? "seleccionada" : "",
      detalleActivo ? "detalle-activo" : "",
      destino ? "destino-disponible" : "",
    ].filter(Boolean).join(" ");
    const resumen = ticket
      ? '<small>Ticket ' + escapar(ticket.folio) + ' · ' + dinero(ticket.total) + '</small><span>' + escapar(ticket.estado_etiqueta) + '</span>'
      : '<small>Disponible</small><span>Libre</span>';
    return '<button class="' + clases + '" data-posicion-id="' + escapar(posicion.id) + '"' +
      (ticket ? ' data-ticket-id="' + escapar(ticket.id) + '"' : "") +
      ' type="button" aria-pressed="' + (seleccionado ? "true" : "false") + '"' +
      (!ticket && !estado.ticketMoverId ? ' aria-disabled="true"' : "") + '>' +
        '<b>' + escapar(posicion.nombre) + '</b>' + resumen +
      '</button>';
  }

  function renderSucursalesEnPedidos(posiciones, idPanel, idBoton, abierto) {
    const fuentes = new Map();
    (estado.administrador?.sucursales || []).forEach(item => {
      const clave = String(item.id || item.nombre || "");
      if (clave) fuentes.set(clave, { nombre: item.nombre || "Sucursal", posiciones: [] });
    });
    posiciones.forEach(posicion => {
      const ticket = ticketDePosicion(posicion);
      const clave = String(posicion.cliente_sucursal_id || ticket?.cliente_sucursal_id || posicion.cliente_sucursal || ticket?.cliente_sucursal || "sin-identificar");
      if (!fuentes.has(clave)) fuentes.set(clave, {
        nombre: posicion.cliente_sucursal || ticket?.cliente_sucursal || "Sucursal sin identificar",
        posiciones: [],
      });
      fuentes.get(clave).posiciones.push(posicion);
    });
    const sucursales = [...fuentes.entries()];
    if (!sucursales.some(([clave]) => clave === estado.sucursalPedidosActivaId)) {
      estado.sucursalPedidosActivaId = sucursales[0]?.[0] || "";
    }
    if (!sucursales.length) {
      return '<div class="mapa-sucursales-pedidos" id="' + idPanel + '" role="region" aria-labelledby="' + idBoton + '"' + (abierto ? "" : " hidden") + '><p class="vacio">No hay sucursales cliente configuradas.</p></div>';
    }
    const tabs = sucursales.map(([clave, sucursal], indice) => {
      const activa = clave === estado.sucursalPedidosActivaId;
      const ocupadas = sucursal.posiciones.filter(posicion => ticketDePosicion(posicion)).length;
      return '<button class="tab-sucursal mapa-pedidos-sucursal-tab ' + (activa ? "activo" : "") + '" id="tab-pedidos-sucursal-' + indice + '" data-pedidos-sucursal-tab="' + escapar(clave) + '" type="button" role="tab" tabindex="' + (activa ? "0" : "-1") + '" aria-selected="' + (activa ? "true" : "false") + '" aria-controls="panel-pedidos-sucursal-' + indice + '">' +
        '<span>' + escapar(sucursal.nombre) + '</span><b>' + ocupadas + '</b></button>';
    }).join("");
    const paneles = sucursales.map(([clave, sucursal], indice) => {
      const activa = clave === estado.sucursalPedidosActivaId;
      const celdas = sucursal.posiciones.length
        ? sucursal.posiciones.map(renderCeldaPosicion).join("")
        : '<p class="vacio">Esta sucursal no tiene posiciones configuradas.</p>';
      return '<div class="mapa-grupo-celdas mapa-sucursal-panel" id="panel-pedidos-sucursal-' + indice + '" role="tabpanel" tabindex="0" aria-labelledby="tab-pedidos-sucursal-' + indice + '"' + (activa ? "" : " hidden") + '>' + celdas + '</div>';
    }).join("");
    return '<div class="mapa-sucursales-pedidos" id="' + idPanel + '" role="region" aria-labelledby="' + idBoton + '"' + (abierto ? "" : " hidden") + '>' +
      '<div class="tabs-sucursales mapa-pedidos-sucursales-tabs" role="tablist" aria-label="Pedidos Sucursales por sucursal">' + tabs + '</div>' + paneles +
    '</div>';
  }

  function renderPedidos() {
    const contenedor = $("#mapa-posiciones");
    if (!contenedor) return;
    const ordenCanales = ["comedor", "llevar", "recoger", "domicilio", "sucursales"];
    const etiquetas = {
      comedor: "Comedor",
      llevar: "Llevar",
      recoger: "Recoger",
      domicilio: "Domicilio",
      sucursales: "Pedidos Sucursales",
    };
    const posiciones = posicionesCompletas();
    const canalesDisponibles = ordenCanales.filter(canal => posiciones.some(posicion => posicion.canal === canal));
    if (!canalesDisponibles.includes(estado.canalPedidosAbierto)) estado.canalPedidosAbierto = "";
    contenedor.innerHTML = canalesDisponibles.length ? canalesDisponibles
      .map(canal => {
        const grupo = posiciones.filter(posicion => posicion.canal === canal);
        const abierto = estado.canalPedidosAbierto === canal;
        const ocupadas = grupo.filter(posicion => ticketDePosicion(posicion)).length;
        const libres = grupo.length - ocupadas;
        const cobrables = grupo
          .map(ticketDePosicion)
          .filter(ticket => tipoLote(ticket) === "cobrar");
        const todosCobrablesSeleccionados = cobrables.length > 0
          && cobrables.every(ticket => estado.ticketsSeleccionados.has(String(ticket.id)));
        const idBoton = "alternar-posiciones-" + canal;
        const idPanel = "posiciones-" + canal;
        const contenido = canal === "sucursales"
          ? renderSucursalesEnPedidos(grupo, idPanel, idBoton, abierto)
          : '<div class="mapa-grupo-celdas" id="' + idPanel + '" role="region" aria-labelledby="' + idBoton + '"' + (abierto ? "" : " hidden") + '>' + grupo.map(renderCeldaPosicion).join("") + '</div>';
        const accionSeleccion = cobrables.length
          ? '<button class="mapa-grupo-seleccion' + (todosCobrablesSeleccionados ? " activo" : "") + '" data-seleccionar-canal="' + escapar(canal) + '" type="button" aria-pressed="' + (todosCobrablesSeleccionados ? "true" : "false") + '" aria-label="' + escapar((todosCobrablesSeleccionados ? "Quitar todos los pedidos cobrables de " : "Seleccionar todos los pedidos cobrables de ") + etiquetas[canal]) + '">' +
              '<span>' + (todosCobrablesSeleccionados ? "Quitar selección" : "Seleccionar todos") + '</span><strong>' + cobrables.length + '</strong></button>'
          : "";
        return '<section class="mapa-grupo" data-canal="' + escapar(canal) + '">' +
          '<header class="mapa-grupo-encabezado">' +
            '<h3><button class="mapa-grupo-toggle" id="' + idBoton + '" data-acordeon-canal="' + escapar(canal) + '" type="button" aria-expanded="' + (abierto ? "true" : "false") + '" aria-controls="' + idPanel + '">' +
              '<span class="mapa-grupo-nombre">' + etiquetas[canal] + '</span>' +
              '<span class="mapa-grupo-resumen"><strong>' + ocupadas + (ocupadas === 1 ? " ocupada" : " ocupadas") + '</strong><small>' + libres + (libres === 1 ? " libre" : " libres") + '</small></span>' +
              '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 10 5 5 5-5"/></svg>' +
            '</button></h3>' + accionSeleccion +
          '</header>' + contenido +
        '</section>';
      }).join("") : '<p class="vacio">No hay posiciones configuradas para mostrar.</p>';
    const avisoMover = $("#instruccion-mover");
    avisoMover.hidden = !estado.ticketMoverId;
    if (estado.ticketMoverId) {
      const ticket = buscarTicket(estado.ticketMoverId);
      avisoMover.textContent = "Mover ticket #" + (ticket?.folio || "") + ": selecciona ahora una casilla libre.";
    }
    renderDetallePedido();
    renderBarraLote();
  }

  function alternarSeleccionTicket(ticketId) {
    const ticket = buscarTicket(ticketId);
    if (!ticket) return;
    const id = String(ticket.id);
    const yaSeleccionado = estado.ticketsSeleccionados.has(id);
    const tipo = tipoLote(ticket);
    if (yaSeleccionado) {
      estado.ticketsSeleccionados.delete(id);
      if (estado.ticketSeleccionadoId === id) {
        const restantes = [...estado.ticketsSeleccionados];
        estado.ticketSeleccionadoId = restantes[restantes.length - 1] || "";
      }
    } else {
      const compatibles = tipo && ticketsSeleccionadosActuales().every(item => tipoLote(item) === tipo);
      if (!compatibles) estado.ticketsSeleccionados.clear();
      estado.ticketsSeleccionados.add(id);
      estado.ticketSeleccionadoId = id;
    }
    estado.canalPedidosAbierto = ticket.canal;
    renderPedidos();
    requestAnimationFrame(() => {
      $$("#mapa-posiciones [data-ticket-id]").find(celda => String(celda.dataset.ticketId) === id)?.focus();
    });
  }

  function ticketsCobrablesCanal(canal) {
    const vistos = new Set();
    return posicionesCompletas()
      .filter(posicion => posicion.canal === canal)
      .map(ticketDePosicion)
      .filter(ticket => {
        const id = String(ticket?.id || "");
        if (tipoLote(ticket) !== "cobrar" || !id || vistos.has(id)) return false;
        vistos.add(id);
        return true;
      });
  }

  function alternarSeleccionCobrablesCanal(canal) {
    const cobrables = ticketsCobrablesCanal(canal);
    if (!cobrables.length) {
      toast("Este canal no tiene pedidos listos para cobrar.", true);
      return;
    }
    const ids = cobrables.map(ticket => String(ticket.id));
    const todosSeleccionados = ids.every(id => estado.ticketsSeleccionados.has(id));
    if (todosSeleccionados) {
      ids.forEach(id => estado.ticketsSeleccionados.delete(id));
    } else {
      [...estado.ticketsSeleccionados].forEach(id => {
        if (tipoLote(buscarTicket(id)) !== "cobrar") estado.ticketsSeleccionados.delete(id);
      });
      ids.forEach(id => estado.ticketsSeleccionados.add(id));
    }
    if (!estado.ticketsSeleccionados.has(String(estado.ticketSeleccionadoId))) {
      const restantes = [...estado.ticketsSeleccionados];
      estado.ticketSeleccionadoId = restantes[restantes.length - 1] || "";
    }
    if (!todosSeleccionados) estado.ticketSeleccionadoId = ids[ids.length - 1];
    estado.canalPedidosAbierto = canal;
    renderPedidos();
    requestAnimationFrame(() => {
      $("[data-seleccionar-canal=\"" + CSS.escape(canal) + "\"]")?.focus();
    });
  }

  async function moverTicketAPosicion(posicionId) {
    const ticketId = estado.ticketMoverId;
    if (!ticketId) return;
    const resultado = await ejecutarAccion({
      url: "/api/administrador/tickets/" + ticketId + "/reasignar/",
      cuerpo: { mesa_id: posicionId },
      mensaje: "Pedido movido a la nueva posición.",
    });
    if (resultado) {
      estado.ticketMoverId = "";
      estado.ticketSeleccionadoId = ticketId;
      renderPedidos();
    }
  }

  // Mantiene compatibles las acciones existentes mientras la autorización se
  // conserva en la sesión administrativa. Ya no solicita una clave por acción.
  function ejecutarConClave({ titulo: _titulo, ayuda: _ayuda, ...opciones }) {
    return ejecutarAccion(opciones);
  }

  function limpiarFormularioMovimiento() {
    estado.movimientoEditandoId = "";
    $("#form-movimiento").reset();
    $("#movimiento-id").value = "";
    $("#titulo-form-movimiento").textContent = "Nuevo movimiento";
    $("#guardar-movimiento").textContent = "Registrar movimiento";
    $("#cancelar-edicion-movimiento").hidden = true;
  }

  function estaEnPantallaCompletaAdmin() {
    return Boolean(
      document.fullscreenElement
      || document.webkitFullscreenElement
      || window.matchMedia?.("(display-mode: fullscreen)")?.matches
      || window.matchMedia?.("(display-mode: standalone)")?.matches
      || window.navigator.standalone
    );
  }

  function actualizarBotonPantallaCompleta() {
    const boton = $("#pantalla-completa-admin");
    if (!boton) return;
    const activo = estaEnPantallaCompletaAdmin();
    const etiqueta = activo ? "Salir de pantalla completa" : "Activar pantalla completa";
    boton.setAttribute("aria-pressed", String(activo));
    boton.setAttribute("aria-label", etiqueta);
    boton.title = etiqueta;
  }

  async function solicitarPantallaCompletaAdmin({ silencioso = false } = {}) {
    if (estaEnPantallaCompletaAdmin()) {
      actualizarBotonPantallaCompleta();
      return true;
    }
    try {
      if (document.documentElement.requestFullscreen) {
        await document.documentElement.requestFullscreen({ navigationUI: "hide" });
      } else if (document.documentElement.webkitRequestFullscreen) {
        await document.documentElement.webkitRequestFullscreen();
      } else {
        actualizarBotonPantallaCompleta();
        if (!silencioso) {
          toast("Este navegador no permite activar pantalla completa. Usa la aplicación instalada.", true);
        }
        return false;
      }
      estado.pantallaCompletaSuspendida = false;
      actualizarBotonPantallaCompleta();
      return true;
    } catch {
      actualizarBotonPantallaCompleta();
      if (!silencioso) {
        toast("El navegador bloqueó la pantalla completa. Toca de nuevo el botón para autorizarla.", true);
      }
      return false;
    }
  }

  async function alternarPantallaCompletaAdmin() {
    if (document.fullscreenElement || document.webkitFullscreenElement) {
      estado.pantallaCompletaSuspendida = true;
      try {
        if (document.exitFullscreen) await document.exitFullscreen();
        else if (document.webkitExitFullscreen) await document.webkitExitFullscreen();
      } catch {
        toast("No fue posible salir de pantalla completa desde este navegador.", true);
      }
      actualizarBotonPantallaCompleta();
      return;
    }
    if (estaEnPantallaCompletaAdmin()) {
      toast("La aplicación instalada ya ocupa la pantalla completa. Usa el sistema para cambiar de aplicación.");
      actualizarBotonPantallaCompleta();
      return;
    }
    estado.pantallaCompletaSuspendida = false;
    await solicitarPantallaCompletaAdmin();
  }

  function activarPantallaCompletaAdminConPrimerToque(evento) {
    if (estado.pantallaCompletaSuspendida || estaEnPantallaCompletaAdmin()) return;
    if (evento.target.closest?.("#pantalla-completa-admin, [data-regreso-admin]")) return;
    solicitarPantallaCompletaAdmin({ silencioso: true });
  }

  function iniciarPantallaCompletaAdmin() {
    actualizarBotonPantallaCompleta();
    solicitarPantallaCompletaAdmin({ silencioso: true });
  }

  async function ejecutarLote(boton) {
    const accion = boton.dataset.accionLote;
    const tipoEsperado = {
      cobrar: "cobrar",
      asignar_repartidor: "domicilio",
      completar_sucursales: "sucursales",
    }[accion];
    const ids = ticketsSeleccionadosActuales()
      .filter(ticket => tipoLote(ticket) === tipoEsperado)
      .map(ticket => ticket.id);
    if (!ids.length) return toast("La selección no contiene pedidos compatibles con esta acción.", true);
    const cuerpo = { accion, ticket_ids: ids };
    if (accion === "cobrar") {
      cuerpo.forma_pago = $("#lote-forma-pago").value;
      if (!["efectivo", "tarjeta"].includes(cuerpo.forma_pago)) {
        return toast("Selecciona Efectivo o Terminal para el cobro.", true);
      }
    }
    if (accion === "asignar_repartidor") {
      cuerpo.repartidor_id = $("#lote-repartidor").value;
      if (!cuerpo.repartidor_id) return toast("Selecciona un repartidor.", true);
    }
    const mensajes = {
      cobrar: "Pedidos cobrados y posiciones liberadas.",
      asignar_repartidor: "Domicilios asignados al repartidor.",
      completar_sucursales: "Pedidos de sucursal completados.",
    };
    const resultado = await ejecutarAccion({
      url: "/api/administrador/tickets/acciones-lote/",
      cuerpo,
      mensaje: mensajes[accion],
      control: boton,
    });
    if (resultado) {
      estado.ticketsSeleccionados.clear();
      estado.ticketSeleccionadoId = "";
      renderPedidos();
    }
  }

  function tarjetaTicket(ticket, { programar = false, administrarProgramado = false } = {}) {
    const programacion = programacionPredeterminada();
    const fecha = ticket.fecha_programada || programacion.fecha;
    const hora = String(ticket.hora_programada || programacion.hora).slice(0, 5);
    let controles = "";
    if (programar) {
      controles = '<div class="ticket-controles programacion-control">' +
        '<input type="date" min="' + programacion.minima + '" value="' + escapar(fecha) + '" aria-label="Fecha para ticket ' + escapar(ticket.folio) + '">' +
        '<input type="time" value="' + escapar(hora) + '" aria-label="Hora para ticket ' + escapar(ticket.folio) + '">' +
        '<button class="boton mini primario" data-accion-ticket="programar" type="button">Programar</button></div>';
    } else if (administrarProgramado) {
      controles = '<div class="ticket-controles programacion-control programacion-control-agenda">' +
        '<a class="boton mini primario" data-editar-programado href="/?editar_programado=' + escapar(ticket.id) + '" aria-label="Editar contenido del pedido ' + escapar(ticket.folio) + '">Editar pedido</a>' +
        '<input type="date" min="' + programacion.minima + '" value="' + escapar(fecha) + '" aria-label="Nueva fecha para ticket ' + escapar(ticket.folio) + '">' +
        '<input type="time" value="' + escapar(hora) + '" aria-label="Nueva hora para ticket ' + escapar(ticket.folio) + '">' +
        '<button class="boton mini" data-accion-ticket="reprogramar" type="button">Guardar fecha</button>' +
        '<button class="boton mini" data-accion-ticket="desprogramar" type="button">Desprogramar</button>' +
        '<button class="boton mini peligro" data-accion-ticket="eliminar-programado" type="button">Eliminar</button></div>';
    }
    const ubicacion = [ticket.canal_etiqueta, ticket.mesa].filter(Boolean).join(" · ");
    const referencia = ticket.cliente_domicilio || ticket.estado_etiqueta || "Sin referencia capturada";
    const momento = ticket.fecha_programada
      ? '<time datetime="' + escapar(ticket.fecha_programada) + 'T' + escapar(hora || "00:00") + '">' + fechaCorta(ticket.fecha_programada) + ' · ' + escapar(hora || "Sin hora") + '</time>'
      : '<time>' + (ticket.terminal ? "Terminal" : "Efectivo") + '</time>';
    return '<article class="ticket-pendiente ' + (ticket.estado === "programado" ? "programado" : "") + '" data-ticket-id="' + escapar(ticket.id) + '">' +
      '<div class="ticket-cabecera"><strong>' + escapar(ubicacion || "Pedido") + '</strong><b>#' + escapar(ticket.folio) + '</b></div>' +
      '<div class="ticket-cliente">' + escapar(ticket.cliente_nombre || "Cliente sin nombre") + '</div>' +
      '<address>' + escapar(referencia) + '</address>' +
      '<div class="ticket-pie"><strong>' + dinero(ticket.total) + '</strong>' + momento + controles + '</div>' +
    '</article>';
  }

  function renderPendientes() {
    const admin = estado.administrador;
    $("#conteo-domicilios").textContent = admin.domicilios_sin_repartidor.length;
    $("#conteo-programados").textContent = admin.programados.length;
    $("#inicio-domicilios").innerHTML = admin.domicilios_sin_repartidor.length
      ? admin.domicilios_sin_repartidor.map(ticket => tarjetaTicket(ticket)).join("")
      : '<p class="vacio">Todos los domicilios procesados ya tienen repartidor.</p>';
    $("#inicio-programados").innerHTML = admin.programados.length
      ? admin.programados.map(ticket => tarjetaTicket(ticket)).join("")
      : '<p class="vacio">No hay pedidos programados para fechas futuras.</p>';
    renderBloqueos("#inicio-bloqueos");
  }

  function renderBloqueos(selector) {
    const bloqueos = estado.administrador.bloqueos_corte || [];
    $(selector).innerHTML = bloqueos.length
      ? bloqueos.map(texto => `<div class="bloqueo">${escapar(texto)}</div>`).join("")
      : '<div class="bloqueo resuelto">Todo está conciliado. El corte de caja puede realizarse.</div>';
  }

  function renderTotales() {
    const totales = estado.administrador.totales_parciales || {};
    const canales = ["comedor", "llevar", "domicilio", "recoger", "sucursales"];
    const total = canales.reduce((suma, canal) => suma + Number(totales[canal] || 0), 0);
    canales.forEach(canal => { $(`#total-${canal}`).textContent = dinero(totales[canal]); });
    $("#total-general").textContent = dinero(total);
    const vistaPrevia = $("#vista-previa-corte");
    const bloqueos = estado.administrador.bloqueos_corte || [];
    if (vistaPrevia) {
      vistaPrevia.disabled = bloqueos.length > 0;
      vistaPrevia.title = bloqueos.length
        ? "Resuelve los requisitos del corte antes de imprimir la vista previa."
        : "Imprimir el corte sin cerrar el turno.";
    }
    renderBloqueos("#bloqueos-reporte");
  }

  function configurarTipoUsuario(tipo = "mesero") {
    const selector = $("#usuario-tipo");
    const opcionPrincipal = $("#usuario-tipo-encargado");
    const esOperadorPrincipal = tipo === "encargado";
    opcionPrincipal.hidden = !esOperadorPrincipal;
    opcionPrincipal.disabled = !esOperadorPrincipal;
    selector.disabled = esOperadorPrincipal;
    selector.value = tipo;
  }

  function limpiarFormularioUsuario() {
    $("#form-usuario").reset();
    $("#usuario-id").value = "";
    $("#usuario-activo").checked = true;
    $("#usuario-clave").required = true;
    configurarTipoUsuario();
    $("#titulo-form-usuario").textContent = "Nuevo usuario";
  }

  function renderPersonal() {
    const lista = $("#lista-usuarios");
    if (tienePermisoAdministrador("gestionar_usuarios")) {
      const usuarios = estado.administrador.usuarios || [];
      lista.innerHTML = usuarios.length ? usuarios.map(usuario => `<button class="fila-persona ${usuario.activo ? "" : "inactivo"}" data-editar-usuario="${usuario.id}" type="button">
        <div><strong>${escapar(usuario.nombre)}</strong><span>${escapar(usuario.tipo_etiqueta)}</span></div>
        <span class="estado-chip">${usuario.activo ? "Activo" : "Inactivo"}</span><span>Editar</span>
      </button>`).join("") : '<p class="vacio">Aún no hay personal operativo registrado.</p>';
    } else {
      lista.replaceChildren();
      limpiarFormularioUsuario();
    }
    $("#liquidacion-repartidor").innerHTML = opcionesRepartidores();
  }

  function renderDomicilios() {
    const tickets = (estado.administrador.tickets || []).filter(ticket => ticket.canal === "domicilio" && ticket.estado === "procesado");
    $("#lista-domicilios").innerHTML = tickets.length ? tickets.map(ticket => {
      const estadoId = `estado-repartidor-${ticket.id}`;
      const asignado = Boolean(ticket.repartidor_id);
      return `<article class="fila-domicilio ${asignado ? "asignado" : ""}" data-ticket-id="${ticket.id}">
        <div><strong>#${escapar(ticket.folio)} · ${escapar(ticket.cliente_nombre || "Sin nombre")}</strong><span>${escapar(ticket.cliente_domicilio || "Sin domicilio")} · ${ticket.terminal ? "Terminal" : "Efectivo"}</span></div>
        <b class="total-fila">${dinero(ticket.total)}</b>
        <div class="asignacion-control">
          <label>Repartidor<select data-asignar-repartidor data-valor-anterior="${escapar(ticket.repartidor_id || "")}" aria-describedby="${estadoId}">${opcionesRepartidores(ticket.repartidor_id)}</select></label>
          <span id="${estadoId}" class="estado-asignacion ${asignado ? "guardado" : "pendiente"}" role="status" aria-live="polite">${asignado ? "Asignado" : "Selecciona un repartidor"}</span>
        </div>
      </article>`;
    }).join("") : '<p class="vacio">No hay domicilios procesados en el turno.</p>';
  }

  function renderProgramados() {
    const procesados = (estado.administrador.tickets || []).filter(ticket => ["domicilio", "recoger"].includes(ticket.canal) && ticket.estado === "procesado");
    $("#lista-programables").innerHTML = procesados.length
      ? procesados.map(ticket => tarjetaTicket(ticket, { programar: true })).join("")
      : '<p class="vacio">No hay domicilios ni pedidos para recoger listos para agendar.</p>';
    $("#lista-programados").innerHTML = estado.administrador.programados.length
      ? estado.administrador.programados.map(ticket => tarjetaTicket(ticket, { administrarProgramado: true })).join("")
      : '<p class="vacio">La agenda futura está vacía.</p>';
  }

  function tipoMovimientoCanonico(tipo) {
    if (tipo === "entrada") return "ingreso";
    if (tipo === "salida") return "gasto";
    return tipo || "ingreso";
  }

  function etiquetaMovimiento(tipo) {
    return { ingreso: "Ingreso", gasto: "Gasto", terminal: "Terminal" }[tipoMovimientoCanonico(tipo)] || "Movimiento";
  }

  function simboloMovimiento(tipo) {
    return { ingreso: "+", gasto: "−", terminal: "T" }[tipoMovimientoCanonico(tipo)] || "·";
  }

  function renderMovimientos() {
    const movimientos = estado.administrador.movimientos || [];
    const conteo = $("#conteo-movimientos");
    conteo.textContent = movimientos.length;
    conteo.setAttribute("aria-label", movimientos.length === 1 ? "1 movimiento" : movimientos.length + " movimientos");
    $("#lista-movimientos").innerHTML = movimientos.length ? movimientos.map(item => {
      const tipo = tipoMovimientoCanonico(item.tipo);
      return '<article class="fila-movimiento ' + tipo + '" data-movimiento-id="' + escapar(item.id) + '">' +
        '<span class="movimiento-senal" aria-label="' + etiquetaMovimiento(tipo) + '"><span aria-hidden="true">' + simboloMovimiento(tipo) + '</span></span>' +
        '<div class="movimiento-detalle"><strong>' + escapar(item.concepto) + '</strong><small>' + etiquetaMovimiento(tipo) + ' · ' + fechaHora(item.creado_en) + '</small></div>' +
        '<strong class="movimiento-importe">' + dinero(item.importe) + '</strong>' +
        '<div class="movimiento-acciones"><button class="boton mini" data-editar-movimiento="' + escapar(item.id) + '" type="button">Editar</button><button class="boton mini peligro" data-eliminar-movimiento="' + escapar(item.id) + '" type="button">Eliminar</button></div>' +
      '</article>';
    }).join("") : '<p class="vacio">No hay movimientos en este turno. Registra el primero en la hoja de captura.</p>';
  }

  function valorNumericoEditable(valor) {
    if (valor === null || valor === undefined || valor === "") return "";
    const numero = Number(valor);
    return Number.isFinite(numero) && numero !== 0 ? String(valor) : "";
  }

  function numeroSeguro(valor) {
    const numero = Number(valor);
    return Number.isFinite(numero) && numero >= 0 ? numero : 0;
  }

  function sumarDenominaciones(valores = {}) {
    return DENOMINACIONES.reduce((total, denominacion) => total + numeroSeguro(valores[denominacion]) * Number(denominacion), 0);
  }

  function renderDenominaciones(contenedorId, prefijo, valores = {}) {
    const contenedor = $("#" + contenedorId);
    contenedor.innerHTML = DENOMINACIONES.map(denominacion => {
      const id = prefijo + "-" + denominacion.replace(".", "-");
      const etiqueta = Number(denominacion) < 1 ? "50 ¢" : dinero(denominacion).replace(".00", "");
      const valor = valorNumericoEditable(valores[denominacion]);
      const atributoValor = valor ? ' value="' + escapar(valor) + '"' : "";
      return '<label for="' + id + '"><span>' + etiqueta + '</span><input id="' + id + '" data-denominacion="' + denominacion + '" type="number" min="0" max="99999" step="1" inputmode="numeric"' + atributoValor + ' aria-label="Cantidad de ' + escapar(etiqueta) + '"></label>';
    }).join("");
  }

  function mapaDenominaciones(contenedorId) {
    return Object.fromEntries($$("#" + contenedorId + " [data-denominacion]").map(input => [input.dataset.denominacion, Math.max(0, Math.trunc(numeroSeguro(input.value)))]));
  }

  function totalesFormula() {
    const admin = estado.administrador || {};
    const control = admin.control_efectivo || {};
    const totales = control.totales || {};
    const movimientos = admin.movimientos || [];
    const parcial = admin.totales_parciales || {};
    const sumarTipo = tipo => movimientos
      .filter(item => tipoMovimientoCanonico(item.tipo) === tipo)
      .reduce((suma, item) => suma + numeroSeguro(item.importe), 0);
    const ventasLocales = ["comedor", "llevar", "domicilio", "recoger"].reduce((suma, canal) => suma + numeroSeguro(parcial[canal]), 0);
    return {
      ingresos: numeroSeguro(totales.ingresos ?? sumarTipo("ingreso")),
      ventas: numeroSeguro(totales.ventas ?? ventasLocales),
      gastos: numeroSeguro(totales.gastos ?? sumarTipo("gasto")),
      terminales: numeroSeguro(totales.terminales ?? sumarTipo("terminal")),
    };
  }

  function actualizarTotalesControl() {
    const fondoAnterior = sumarDenominaciones(mapaDenominaciones("fondo-anterior-denominaciones"));
    const fondoSiguiente = sumarDenominaciones(mapaDenominaciones("fondo-siguiente-denominaciones"));
    const apps = numeroSeguro($("#app-rappi").value) + numeroSeguro($("#app-didi").value) + numeroSeguro($("#app-uber-eats").value);
    const base = totalesFormula();
    const saldo = fondoAnterior + base.ingresos + base.ventas - base.gastos - base.terminales - fondoSiguiente;
    $("#total-fondo-anterior").textContent = dinero(fondoAnterior);
    $("#total-fondo-siguiente").textContent = dinero(fondoSiguiente);
    $("#total-ventas-apps").textContent = dinero(apps);
    $("#saldo-efectivo-esperado").textContent = dinero(saldo);
    $("#saldo-efectivo-esperado").classList.toggle("saldo-negativo", saldo < 0);
  }

  function renderControlEfectivo() {
    const control = estado.administrador.control_efectivo || {};
    renderDenominaciones("fondo-anterior-denominaciones", "fondo-anterior", control.fondo_anterior || {});
    renderDenominaciones("fondo-siguiente-denominaciones", "fondo-siguiente", control.fondo_siguiente || {});
    const apps = control.ventas_apps || {};
    $("#app-rappi").value = valorNumericoEditable(apps.rappi);
    $("#app-didi").value = valorNumericoEditable(apps.didi);
    $("#app-uber-eats").value = valorNumericoEditable(apps.uber_eats);
    $("#control-efectivo-fecha").textContent = control.fecha ? fechaCorta(control.fecha) : "Hoy";
    if (control.fecha) $("#control-efectivo-fecha").setAttribute("datetime", control.fecha);
    actualizarTotalesControl();
  }

  function claveSucursal(item = {}) {
    return String(item.id ?? item.cliente_sucursal_id ?? item.nombre ?? item.cliente_sucursal ?? "");
  }

  function renderSucursales() {
    const configuradas = estado.administrador.sucursales || [];
    const pedidos = tickets().filter(ticket => ticket.canal === "sucursales" && !["cancelado", "pagado"].includes(ticket.estado));
    const fuentes = new Map();
    configuradas.forEach(item => {
      const clave = claveSucursal(item);
      if (clave) fuentes.set(clave, { id: item.id, nombre: item.nombre || "Sucursal", pendientes: numeroSeguro(item.pendientes), configurada: item });
    });
    pedidos.forEach(ticket => {
      const clave = String(ticket.cliente_sucursal_id || ticket.cliente_sucursal || "sin-origen");
      if (!fuentes.has(clave)) fuentes.set(clave, {
        id: ticket.cliente_sucursal_id || "",
        nombre: ticket.cliente_sucursal || "Sucursal sin identificar",
        pendientes: 0,
        configurada: null,
      });
    });
    const lista = [...fuentes.entries()];
    if (!lista.some(([clave]) => clave === estado.sucursalActivaId)) estado.sucursalActivaId = lista[0]?.[0] || "";
    $("#tabs-sucursales").innerHTML = lista.length ? lista.map(([clave, item], indice) => {
      const activa = clave === estado.sucursalActivaId;
      const total = pedidos.filter(ticket => String(ticket.cliente_sucursal_id || ticket.cliente_sucursal || "sin-origen") === clave).length;
      return '<button id="tab-sucursal-' + indice + '" class="tab-sucursal ' + (activa ? "activo" : "") + '" data-sucursal-tab="' + escapar(clave) + '" type="button" role="tab" tabindex="' + (activa ? "0" : "-1") + '" aria-selected="' + (activa ? "true" : "false") + '" aria-controls="pedidos-sucursal-activa">' +
        '<span>' + escapar(item.nombre) + '</span><b>' + total + '</b></button>';
    }).join("") : "";
    const pedidosActivos = pedidos.filter(ticket => String(ticket.cliente_sucursal_id || ticket.cliente_sucursal || "sin-origen") === estado.sucursalActivaId);
    const panelSucursal = $("#pedidos-sucursal-activa");
    const tabActiva = $("#tabs-sucursales [aria-selected=\"true\"]");
    if (tabActiva) panelSucursal.setAttribute("aria-labelledby", tabActiva.id);
    else panelSucursal.removeAttribute("aria-labelledby");
    panelSucursal.tabIndex = 0;
    panelSucursal.innerHTML = lista.length
      ? (pedidosActivos.length ? pedidosActivos.map(ticket => tarjetaTicket(ticket)).join("") : '<p class="vacio">Esta sucursal no tiene pedidos activos.</p>')
      : '<p class="vacio">No hay sucursales cliente configuradas.</p>';

    $("#lista-sucursales").innerHTML = configuradas.length ? configuradas.map(item => '<article class="tarjeta-sucursal" data-sucursal-id="' + escapar(item.id) + '">' +
      '<header><h2>' + escapar(item.nombre) + '</h2><b>' + numeroSeguro(item.pendientes) + '</b></header>' +
      '<p>' + (numeroSeguro(item.pendientes) ? numeroSeguro(item.pendientes) + ' pedido(s) procesado(s) se agruparán por producto en el ticket.' : "Sin pedidos procesados pendientes de corte.") + '</p>' +
      '<button class="boton ' + (numeroSeguro(item.pendientes) ? "primario" : "secundario") + '" data-accion-sucursal="corte" type="button" ' + (numeroSeguro(item.pendientes) ? "" : "disabled") + '>Realizar corte</button>' +
    '</article>').join("") : '<p class="vacio">No hay sucursales cliente configuradas.</p>';
  }

  function renderCancelaciones() {
    const cancelaciones = estado.administrador.cancelaciones || [];
    $("#conteo-cancelaciones").textContent = cancelaciones.length;
    $("#lista-cancelaciones").innerHTML = cancelaciones.length ? cancelaciones.map(ticket => {
      const responsable = ticket.cancelado_por_nombre || ticket.cancelado_por || "Usuario no identificado";
      const momento = ticket.cancelado_en || ticket.actualizado_en || ticket.creado_en;
      return '<article class="fila-cancelacion">' +
        '<div><strong>Ticket #' + escapar(ticket.folio) + ' · ' + escapar(ticket.canal_etiqueta || ticket.canal || "Pedido") + '</strong><span>' + escapar(ticket.mesa || ticket.cliente_nombre || "Sin referencia") + '</span></div>' +
        '<div><span>Canceló</span><strong>' + escapar(responsable) + '</strong></div>' +
        '<time datetime="' + escapar(momento || "") + '">' + fechaHora(momento) + '</time>' +
        '<b>' + dinero(ticket.total) + '</b>' +
      '</article>';
    }).join("") : '<p class="vacio">No hay cancelaciones en el turno actual.</p>';
  }

  function renderCortesCaja() {
    const cortes = estado.administrador.cortes_caja || [];
    $("#conteo-cortes-caja").textContent = cortes.length;
    $("#lista-cortes-caja").innerHTML = cortes.length ? cortes.map(corte => {
      const inicio = corte.inicio ? fechaHora(corte.inicio) : "Inicio no disponible";
      const fin = fechaHora(corte.fin);
      return '<article class="fila-corte-caja">' +
        '<div class="corte-caja-fecha"><strong>' + fin + '</strong><span>' + inicio + ' → ' + fin + '</span></div>' +
        '<dl><div><dt>Ventas</dt><dd>' + dinero(corte.total_ventas) + '</dd></div><div><dt>Resultado</dt><dd>' + dinero(corte.total_caja) + '</dd></div></dl>' +
        '<button class="boton mini" data-reimprimir-corte="' + escapar(corte.reporte_id) + '" type="button">Reimprimir reporte</button>' +
      '</article>';
    }).join("") : '<p class="vacio">Aún no hay cortes diarios para consultar.</p>';
  }

  function renderCierreMensual() {
    const cierre = estado.administrador.cierre_mensual || {};
    const aviso = $("#aviso-cierre-mensual");
    aviso.hidden = !cierre.requerido;
    if (!cierre.requerido) return;
    $("#cierre-mensual-periodo").textContent = periodoMensual(cierre.periodo);
    $("#cierre-mensual-estado").textContent = cierre.estado ? cierre.estado.replaceAll("_", " ") : "Pendiente de envío";
    $("#cierre-mensual-vps").textContent = cierre.vps_configurado ? "Configurado" : "Sin configurar";
    const purgaPendiente = Boolean(cierre.purga_fisica_pendiente);
    const error = $("#cierre-mensual-error");
    const mensajePurga = purgaPendiente
      ? "El VPS ya confirmó el mes. La tarea segura cerrará los archivos y respaldos pendientes en un máximo aproximado de 5 minutos."
      : "";
    error.textContent = cierre.ultimo_error || mensajePurga;
    error.hidden = !error.textContent;
    const boton = $("#ejecutar-cierre-mensual");
    boton.dataset.periodo = cierre.periodo || "";
    boton.disabled = !cierre.vps_configurado || !cierre.periodo || purgaPendiente;
    boton.textContent = purgaPendiente
      ? "Cierre físico en proceso"
      : "Enviar al VPS, purgar mes y reiniciar folios tras el acuse";
    boton.title = purgaPendiente
      ? "La tarea SYSTEM comprueba solicitudes cada cinco minutos."
      : (cierre.vps_configurado ? "" : "Configura la URL del VPS antes de consolidar el mes.");
  }

  function actualizarDeviceIdActual() {
    const nodo = $("#device-id-actual");
    if (nodo) nodo.textContent = POS_DEVICE_ID;
  }

  function configuracionImpresionSeleccionada() {
    return estado.configuracionesImpresion.find(
      item => String(item.id) === String(estado.configuracionImpresionId)
    ) || null;
  }

  function renderConfiguracionesImpresion() {
    const lista = $("#lista-configuraciones-impresion");
    const aviso = $("#estado-configuraciones-impresion");
    if (!lista || !aviso) return;
    const configuraciones = estado.configuracionesImpresion || [];
    aviso.textContent = configuraciones.length
      ? String(configuraciones.length) + " " + (configuraciones.length === 1 ? "terminal configurada." : "terminales configuradas.")
      : "No hay terminales configuradas; los trabajos usan las rutas generales.";
    lista.innerHTML = configuraciones.length ? configuraciones.map(configuracion => {
      const seleccionada = String(configuracion.id) === String(estado.configuracionImpresionId);
      const esActual = configuracion.device_id === POS_DEVICE_ID;
      const destinos = [
        ["Caja", configuracion.host_caja],
        ["Cocina", configuracion.host_cocina],
        ["Barra", configuracion.host_barra],
      ];
      return '<article class="terminal-configuracion' + (seleccionada ? " seleccionada" : "") + (esActual ? " terminal-actual" : "") + (configuracion.activa ? "" : " inactiva") + '">' +
        '<header><div><strong>' + escapar(configuracion.nombre) + '</strong><code>' + escapar(configuracion.device_id) + '</code></div>' +
          '<span class="estado-ruta ' + (configuracion.activa ? "activa" : "inactiva") + '">' + (configuracion.activa ? "Activa" : "Inactiva") + '</span></header>' +
        '<dl>' + destinos.map(([etiqueta, host]) => '<div><dt>' + etiqueta + '</dt><dd>' + (host ? escapar(host) + ':' + escapar(configuracion.puerto) : "Ruta general") + '</dd></div>').join("") + '</dl>' +
        (esActual ? '<p class="terminal-actual-etiqueta">Esta terminal</p>' : "") +
        '<button class="boton mini" data-editar-configuracion-impresion="' + escapar(configuracion.id) + '" type="button" aria-label="Editar ruta de ' + escapar(configuracion.nombre) + '">Editar ruta</button>' +
      '</article>';
    }).join("") : '<p class="vacio">Registra una terminal para asignarle impresoras por destino.</p>';
  }

  function limpiarFormularioConfiguracionImpresion() {
    estado.configuracionImpresionId = "";
    $("#form-configuracion-impresion").reset();
    $("#configuracion-impresion-id").value = "";
    $("#configuracion-impresion-puerto").value = "9100";
    $("#configuracion-impresion-activa").checked = true;
    $("#titulo-form-configuracion-impresion").textContent = "Nueva terminal";
    $("#cancelar-configuracion-impresion").hidden = true;
    $("#desactivar-configuracion-impresion").hidden = true;
    renderConfiguracionesImpresion();
  }

  function editarConfiguracionImpresion(id) {
    if (!exigirPermisoAdministrador("gestionar_configuracion_tecnica")) return;
    const configuracion = estado.configuracionesImpresion.find(
      item => String(item.id) === String(id)
    );
    if (!configuracion) return toast("La ruta seleccionada ya no está disponible.", true);
    estado.configuracionImpresionId = String(configuracion.id);
    $("#configuracion-impresion-id").value = configuracion.id;
    $("#configuracion-impresion-nombre").value = configuracion.nombre;
    $("#configuracion-impresion-device-id").value = configuracion.device_id;
    $("#configuracion-impresion-host-caja").value = configuracion.host_caja || "";
    $("#configuracion-impresion-host-cocina").value = configuracion.host_cocina || "";
    $("#configuracion-impresion-host-barra").value = configuracion.host_barra || "";
    $("#configuracion-impresion-puerto").value = String(configuracion.puerto || 9100);
    $("#configuracion-impresion-activa").checked = Boolean(configuracion.activa);
    $("#titulo-form-configuracion-impresion").textContent = "Editar terminal";
    $("#cancelar-configuracion-impresion").hidden = false;
    $("#desactivar-configuracion-impresion").hidden = !configuracion.activa;
    renderConfiguracionesImpresion();
    $(".hoja-formulario-ruta").scrollIntoView({
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
      block: "start",
    });
    $("#configuracion-impresion-nombre").focus({ preventScroll: true });
  }

  async function apiConfiguracionImpresion(url, opciones = {}) {
    try {
      return await api(url, opciones);
    } catch (error) {
      if (error.status !== 401 || !(await autorizarEntrada())) throw error;
      return api(url, opciones);
    }
  }

  async function cargarConfiguracionesImpresion(control = null) {
    if (!tienePermisoAdministrador("gestionar_configuracion_tecnica")) return false;
    if (estado.configuracionesImpresionCargando) return false;
    estado.configuracionesImpresionCargando = true;
    marcarControlPendiente(control, true, "Actualizando…");
    const aviso = $("#estado-configuraciones-impresion");
    aviso.textContent = "Consultando rutas de impresión…";
    try {
      const datos = await apiConfiguracionImpresion(RUTAS.configuracionesImpresion);
      estado.configuracionesImpresion = Array.isArray(datos.configuraciones)
        ? datos.configuraciones
        : [];
      if (
        estado.configuracionImpresionId
        && !configuracionImpresionSeleccionada()
      ) {
        limpiarFormularioConfiguracionImpresion();
      } else {
        renderConfiguracionesImpresion();
      }
      return true;
    } catch (error) {
      aviso.textContent = error.message;
      const listaError = $("#lista-configuraciones-impresion");
      if (listaError) listaError.innerHTML = '<p class="vacio error">No fue posible consultar las rutas.</p>';
      if (error.status === 403) await cargarResumen(false);
      toast(error.message, true);
      return false;
    } finally {
      estado.configuracionesImpresionCargando = false;
      marcarControlPendiente(control, false);
    }
  }

  function cuerpoConfiguracionImpresion() {
    return {
      nombre: $("#configuracion-impresion-nombre").value.trim(),
      device_id: $("#configuracion-impresion-device-id").value.trim(),
      host_caja: $("#configuracion-impresion-host-caja").value.trim(),
      host_cocina: $("#configuracion-impresion-host-cocina").value.trim(),
      host_barra: $("#configuracion-impresion-host-barra").value.trim(),
      puerto: Number($("#configuracion-impresion-puerto").value),
      activa: $("#configuracion-impresion-activa").checked,
    };
  }

  async function guardarConfiguracionImpresion(evento) {
    evento.preventDefault();
    if (!exigirPermisoAdministrador("gestionar_configuracion_tecnica")) return;
    const formulario = evento.currentTarget;
    if (!formulario.reportValidity()) return;
    const id = $("#configuracion-impresion-id").value;
    const boton = $("#guardar-configuracion-impresion");
    marcarControlPendiente(boton, true, "Guardando…");
    ajustarEstadoOcupado(1);
    try {
      const datos = await apiConfiguracionImpresion(
        id ? RUTAS.configuracionesImpresion + id + "/" : RUTAS.configuracionesImpresion,
        {
          method: id ? "PATCH" : "POST",
          body: JSON.stringify(cuerpoConfiguracionImpresion()),
        }
      );
      estado.configuracionImpresionId = String(datos.configuracion.id);
      await cargarConfiguracionesImpresion();
      editarConfiguracionImpresion(datos.configuracion.id);
      toast(id ? "Ruta de impresión actualizada." : "Terminal registrada.");
    } catch (error) {
      if (error.status === 403) await cargarResumen(false);
      toast(error.message, true);
    } finally {
      ajustarEstadoOcupado(-1);
      marcarControlPendiente(boton, false);
    }
  }

  async function desactivarConfiguracionImpresion() {
    if (!exigirPermisoAdministrador("gestionar_configuracion_tecnica")) return;
    const configuracion = configuracionImpresionSeleccionada();
    if (!configuracion || !configuracion.activa) return;
    if (!window.confirm("¿Desactivar la ruta de " + configuracion.nombre + "? La terminal volverá a las rutas generales.")) return;
    const boton = $("#desactivar-configuracion-impresion");
    marcarControlPendiente(boton, true, "Desactivando…");
    ajustarEstadoOcupado(1);
    try {
      await apiConfiguracionImpresion(
        RUTAS.configuracionesImpresion + configuracion.id + "/",
        { method: "PATCH", body: JSON.stringify({ activa: false }) }
      );
      await cargarConfiguracionesImpresion();
      limpiarFormularioConfiguracionImpresion();
      toast("Ruta desactivada; la terminal usará las rutas generales.");
    } catch (error) {
      if (error.status === 403) await cargarResumen(false);
      toast(error.message, true);
    } finally {
      ajustarEstadoOcupado(-1);
      marcarControlPendiente(boton, false);
    }
  }

  function renderTodo() {
    aplicarPermisosAdministrativos();
    const admin = estado.administrador;
    $("#turno-inicio").textContent = admin.inicio_turno ? `Desde ${fechaHora(admin.inicio_turno)}` : "Sin pedidos en el turno";
    renderPendientes();
    renderTotales();
    renderPersonal();
    renderDomicilios();
    renderPedidos();
    renderProgramados();
    renderMovimientos();
    renderControlEfectivo();
    renderSucursales();
    renderCancelaciones();
    renderCortesCaja();
    renderCierreMensual();
  }

  async function asignarRepartidorInmediato(select) {
    const fila = select.closest("[data-ticket-id]");
    const ticketId = fila?.dataset.ticketId;
    const contenedor = select.closest(".asignacion-control");
    const estadoNodo = contenedor?.querySelector(".estado-asignacion");
    if (!ticketId || !contenedor || !estadoNodo) return;
    const anterior = select.dataset.valorAnterior || "";
    const repartidorId = select.value;
    if (!repartidorId) {
      select.value = anterior;
      contenedor.classList.remove("guardando", "guardado");
      contenedor.classList.add("error");
      estadoNodo.textContent = "Elige un repartidor activo";
      toast("Selecciona un repartidor activo; se conservó la asignación anterior.", true);
      return;
    }
    const token = ++estado.secuenciaAsignacion;
    estado.asignacionesRepartidor.set(String(ticketId), token);
    contenedor.classList.remove("error", "guardado");
    contenedor.classList.add("guardando");
    estadoNodo.textContent = "Guardando…";
    select.disabled = true;
    select.setAttribute("aria-busy", "true");
    ajustarEstadoOcupado(1);
    try {
      const ejecutar = () => api(`/api/administrador/tickets/${ticketId}/repartidor/`, {
        method: "POST",
        body: JSON.stringify({ repartidor_id: repartidorId }),
      });
      try {
        await ejecutar();
      } catch (error) {
        if (error.status !== 401 || !(await autorizarEntrada())) throw error;
        await ejecutar();
      }
      if (estado.asignacionesRepartidor.get(String(ticketId)) !== token) return;
      select.dataset.valorAnterior = repartidorId;
      contenedor.classList.remove("guardando", "error");
      contenedor.classList.add("guardado");
      estadoNodo.textContent = "Guardado";
      toast("Repartidor guardado.");
      const seUnioAResumenEnCurso = Boolean(estado.promesaResumen);
      await cargarResumen(false);
      if (seUnioAResumenEnCurso) await cargarResumen(false);
    } catch (error) {
      if (estado.asignacionesRepartidor.get(String(ticketId)) !== token) return;
      select.value = anterior;
      contenedor.classList.remove("guardando", "guardado");
      contenedor.classList.add("error");
      estadoNodo.textContent = `No se guardó: ${error.message}`;
      toast(`${error.message} Se restauró la asignación anterior.`, true);
    } finally {
      if (estado.asignacionesRepartidor.get(String(ticketId)) === token) {
        estado.asignacionesRepartidor.delete(String(ticketId));
        if (select.isConnected) {
          select.disabled = false;
          select.removeAttribute("aria-busy");
        }
      }
      ajustarEstadoOcupado(-1);
    }
  }

  async function accionTicket(boton) {
    const fila = boton.closest("[data-ticket-id]");
    const ticketId = fila?.dataset.ticketId;
    if (!ticketId) return;
    const accion = boton.dataset.accionTicket;
    if (["programar", "reprogramar"].includes(accion)) {
      const fecha = fila.querySelector('input[type="date"]')?.value;
      const hora = fila.querySelector('input[type="time"]')?.value;
      if (!fecha || !hora) return toast("Selecciona la fecha y la hora de entrega.", true);
      await ejecutarAccion({
        url: "/api/administrador/tickets/" + ticketId + "/programar/",
        method: accion === "reprogramar" ? "PATCH" : "POST",
        cuerpo: { fecha_programada: fecha, hora_programada: hora },
        mensaje: accion === "reprogramar" ? "Fecha y hora actualizadas." : "Pedido programado para el " + fechaCorta(fecha) + " a las " + hora + ".",
        control: boton,
      });
      return;
    }
    if (accion === "desprogramar") {
      if (!window.confirm("¿Devolver este pedido al turno activo? Se asignará una posición disponible de su canal.")) return;
      await ejecutarAccion({
        url: "/api/administrador/tickets/" + ticketId + "/desprogramar/",
        mensaje: "Pedido devuelto al turno activo.",
        control: boton,
      });
      return;
    }
    if (accion === "eliminar-programado") {
      if (!window.confirm("¿Eliminar definitivamente este pedido programado? Esta acción no se puede deshacer.")) return;
      await ejecutarAccion({
        url: "/api/administrador/tickets/" + ticketId + "/programar/",
        method: "DELETE",
        mensaje: "Pedido programado eliminado.",
        control: boton,
      });
      return;
    }
    if (accion === "descuento") {
      const porcentaje = fila.querySelector('input[type="number"]')?.value;
      await ejecutarConClave({ titulo: "Aplicar descuento", ayuda: "Confirma el descuento de " + (porcentaje || 0) + "% para este pedido.", url: "/api/administrador/tickets/" + ticketId + "/descuento/", cuerpo: { porcentaje }, mensaje: "Descuento actualizado." });
    }
  }

  document.addEventListener("change", evento => {
    const select = evento.target.closest("[data-asignar-repartidor]");
    if (select) asignarRepartidorInmediato(select);
  });

  document.addEventListener("keydown", evento => {
    const tabPedidosSucursal = evento.target.closest("#mapa-posiciones [data-pedidos-sucursal-tab]");
    if (tabPedidosSucursal && ["ArrowLeft", "ArrowRight", "Home", "End"].includes(evento.key)) {
      const tabsPedidos = $$("#mapa-posiciones [data-pedidos-sucursal-tab]");
      const actualPedidos = tabsPedidos.indexOf(tabPedidosSucursal);
      const destinoPedidos = evento.key === "Home"
        ? 0
        : evento.key === "End"
          ? tabsPedidos.length - 1
          : (actualPedidos + (evento.key === "ArrowRight" ? 1 : -1) + tabsPedidos.length) % tabsPedidos.length;
      evento.preventDefault();
      estado.sucursalPedidosActivaId = tabsPedidos[destinoPedidos].dataset.pedidosSucursalTab;
      renderPedidos();
      requestAnimationFrame(() => $$("#mapa-posiciones [data-pedidos-sucursal-tab]")[destinoPedidos]?.focus());
      return;
    }
    const tabSucursal = evento.target.closest("#tabs-sucursales [data-sucursal-tab]");
    if (!tabSucursal || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(evento.key)) return;
    const tabs = $$("#tabs-sucursales [data-sucursal-tab]");
    const actual = tabs.indexOf(tabSucursal);
    const destino = evento.key === "Home"
      ? 0
      : evento.key === "End"
        ? tabs.length - 1
        : (actual + (evento.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    evento.preventDefault();
    estado.sucursalActivaId = tabs[destino].dataset.sucursalTab;
    renderSucursales();
    requestAnimationFrame(() => $$("#tabs-sucursales [data-sucursal-tab]")[destino]?.focus());
  });

  document.addEventListener("click", async evento => {
    const regreso = evento.target.closest("[data-regreso-admin]");
    if (regreso) {
      await regresarDesdeAdministrador();
      return;
    }
    const editarRuta = evento.target.closest("[data-editar-configuracion-impresion]");
    if (editarRuta) {
      editarConfiguracionImpresion(editarRuta.dataset.editarConfiguracionImpresion);
      return;
    }
    const navegacion = evento.target.closest("[data-panel], [data-panel-ir]");
    if (navegacion) {
      await navegarPanel(navegacion.dataset.panel || navegacion.dataset.panelIr);
      return;
    }
    const tabPedidosSucursal = evento.target.closest("#mapa-posiciones [data-pedidos-sucursal-tab]");
    if (tabPedidosSucursal) {
      estado.sucursalPedidosActivaId = tabPedidosSucursal.dataset.pedidosSucursalTab;
      renderPedidos();
      requestAnimationFrame(() => $("#mapa-posiciones [data-pedidos-sucursal-tab=\"" + CSS.escape(estado.sucursalPedidosActivaId) + "\"]")?.focus());
      return;
    }
    const tabSucursal = evento.target.closest("[data-sucursal-tab]");
    if (tabSucursal) {
      estado.sucursalActivaId = tabSucursal.dataset.sucursalTab;
      renderSucursales();
      requestAnimationFrame(() => $("[data-sucursal-tab=\"" + CSS.escape(estado.sucursalActivaId) + "\"]")?.focus());
      return;
    }
    const accion = evento.target.closest("[data-accion-ticket]");
    if (accion) {
      await accionTicket(accion);
      return;
    }
    const seleccionarCanal = evento.target.closest("[data-seleccionar-canal]");
    if (seleccionarCanal) {
      alternarSeleccionCobrablesCanal(seleccionarCanal.dataset.seleccionarCanal);
      return;
    }
    const acordeonPedidos = evento.target.closest("[data-acordeon-canal]");
    if (acordeonPedidos) {
      const canal = acordeonPedidos.dataset.acordeonCanal;
      estado.canalPedidosAbierto = estado.canalPedidosAbierto === canal ? "" : canal;
      renderPedidos();
      requestAnimationFrame(() => {
        $$("#mapa-posiciones [data-acordeon-canal]").find(boton => boton.dataset.acordeonCanal === canal)?.focus();
      });
      return;
    }
    const accionPedido = evento.target.closest("[data-pedido-accion]");
    if (accionPedido) {
      const ticketId = accionPedido.dataset.ticketId;
      const ticket = buscarTicket(ticketId);
      if (!ticket) return;
      if (accionPedido.dataset.pedidoAccion === "mover") {
        estado.ticketMoverId = String(ticketId);
        estado.ticketSeleccionadoId = String(ticketId);
        estado.canalPedidosAbierto = ticket.canal;
        if (ticket.canal === "sucursales") {
          estado.sucursalPedidosActivaId = String(ticket.cliente_sucursal_id || ticket.cliente_sucursal || "");
        }
        if ($("#dialogo-detalle-pedido").open) $("#dialogo-detalle-pedido").close();
        renderPedidos();
        return;
      }
      if (accionPedido.dataset.pedidoAccion === "ver_detalles") {
        estado.ticketSeleccionadoId = String(ticketId);
        renderDetallePedido();
        const dialogo = $("#dialogo-detalle-pedido");
        if (!dialogo.open) dialogo.showModal();
        return;
      }
      if (accionPedido.dataset.pedidoAccion === "cancelar") {
        if (!window.confirm("¿Cancelar este pedido? La posición quedará libre y el pedido se conservará para auditoría.")) return;
        const resultado = await ejecutarAccion({
          url: "/api/administrador/tickets/" + ticketId + "/cancelar/",
          mensaje: "Pedido cancelado y posición liberada.",
          control: accionPedido,
        });
        if (resultado) {
          estado.ticketsSeleccionados.delete(String(ticketId));
          estado.ticketSeleccionadoId = "";
          estado.ticketMoverId = "";
          if ($("#dialogo-detalle-pedido").open) $("#dialogo-detalle-pedido").close();
          renderPedidos();
        }
        return;
      }
    }
    const celdaPosicion = evento.target.closest("#mapa-posiciones [data-posicion-id]");
    if (celdaPosicion) {
      const ticketId = celdaPosicion.dataset.ticketId;
      if (estado.ticketMoverId) {
        if (ticketId) return toast("Selecciona una casilla libre como destino.", true);
        await moverTicketAPosicion(celdaPosicion.dataset.posicionId);
        return;
      }
      if (ticketId) alternarSeleccionTicket(ticketId);
      return;
    }
    const accionLote = evento.target.closest("[data-accion-lote]");
    if (accionLote) {
      await ejecutarLote(accionLote);
      return;
    }
    const editarMovimiento = evento.target.closest("[data-editar-movimiento]");
    if (editarMovimiento) {
      const item = (estado.administrador.movimientos || []).find(movimiento => String(movimiento.id) === String(editarMovimiento.dataset.editarMovimiento));
      if (!item) return;
      estado.movimientoEditandoId = String(item.id);
      $("#movimiento-id").value = item.id;
      $("#movimiento-tipo").value = tipoMovimientoCanonico(item.tipo);
      $("#movimiento-concepto").value = item.concepto;
      $("#movimiento-importe").value = item.importe;
      $("#titulo-form-movimiento").textContent = "Editar movimiento";
      $("#guardar-movimiento").textContent = "Guardar cambios";
      $("#cancelar-edicion-movimiento").hidden = false;
      $("#movimiento-concepto").focus();
      return;
    }
    const eliminarMovimiento = evento.target.closest("[data-eliminar-movimiento]");
    if (eliminarMovimiento) {
      const movimientoId = eliminarMovimiento.dataset.eliminarMovimiento;
      if (!window.confirm("¿Eliminar este movimiento de caja?")) return;
      const resultado = await ejecutarAccion({
        url: "/api/administrador/movimientos/" + movimientoId + "/",
        method: "DELETE",
        mensaje: "Movimiento eliminado.",
        control: eliminarMovimiento,
      });
      if (resultado && estado.movimientoEditandoId === String(movimientoId)) limpiarFormularioMovimiento();
      return;
    }
    const usuario = evento.target.closest("[data-editar-usuario]");
    if (usuario) {
      if (!exigirPermisoAdministrador("gestionar_usuarios")) return;
      const datos = estado.administrador.usuarios.find(item => item.id === usuario.dataset.editarUsuario);
      if (!datos) return;
      $("#usuario-id").value = datos.id;
      $("#usuario-nombre").value = datos.nombre;
      configurarTipoUsuario(datos.tipo);
      $("#usuario-clave").value = "";
      $("#usuario-clave").required = false;
      $("#usuario-activo").checked = datos.activo;
      $("#titulo-form-usuario").textContent = "Editar usuario";
      $("#usuario-nombre").focus();
      return;
    }
    const reimprimirCorte = evento.target.closest("[data-reimprimir-corte]");
    if (reimprimirCorte) {
      await ejecutarAccion({
        url: "/api/administrador/reportes/" + reimprimirCorte.dataset.reimprimirCorte + "/reimprimir/",
        mensaje: "Reporte de corte enviado a impresión.",
        refrescar: false,
        control: reimprimirCorte,
      });
      return;
    }
    const consolidarMes = evento.target.closest("#ejecutar-cierre-mensual");
    if (consolidarMes) {
      const periodo = consolidarMes.dataset.periodo;
      if (!periodo) return toast("No se recibió el periodo que debe consolidarse.", true);
      const advertencia = "¿Cerrar " + periodoMensual(periodo) + "? Se enviarán los totales al VPS. Sólo después de recibir su acuse se purgarán los comprobantes mensuales y los folios se reiniciarán a 1.";
      if (!window.confirm(advertencia)) return;
      await ejecutarAccion({
        url: "/api/administrador/consolidacion-mensual/",
        cuerpo: { periodo },
        mensaje: "El VPS confirmó el mes y reinició los folios. El cierre físico seguro quedó programado.",
        control: consolidarMes,
      });
      return;
    }
    const accionGeneral = evento.target.closest("[data-accion]")?.dataset.accion;
    if (accionGeneral === "vista-previa-corte") {
      await ejecutarAccion({
        url: "/api/administrador/corte-caja/previa/",
        mensaje: "Vista previa del corte enviada a impresión. El turno sigue abierto.",
        control: $("#vista-previa-corte"),
      });
      return;
    }
    if (accionGeneral === "reporte-parcial") {
      await ejecutarAccion({ url: "/api/administrador/reportes/parcial/", mensaje: "Reporte parcial enviado a impresión." });
      return;
    }
    if (accionGeneral === "corte-caja") {
      if (!window.confirm("¿Realizar el corte de caja? Iniciará un nuevo turno para los siguientes movimientos.")) return;
      await ejecutarConClave({ titulo: "Realizar corte de caja", ayuda: "Sólo continuará si no quedan cobros, repartidores o liquidaciones pendientes.", url: "/api/administrador/corte-caja/", mensaje: "Corte de caja generado." });
      return;
    }
    if (accionGeneral === "reiniciar-folios") {
      if (!exigirPermisoAdministrador("reiniciar_folios")) return;
      if (!window.confirm("¿Reiniciar los folios? El siguiente pedido será el número 1; no se eliminará ningún pedido existente.")) return;
      await ejecutarAccion({
        url: "/api/administrador/folios/reiniciar/",
        mensaje: "Folios reiniciados. El siguiente pedido usará el número 1.",
      });
      return;
    }
    const corteSucursal = evento.target.closest("[data-accion-sucursal='corte']");
    if (corteSucursal) {
      const tarjeta = corteSucursal.closest("[data-sucursal-id]");
      await ejecutarConClave({ titulo: "Corte de sucursal", ayuda: "Todas sus partidas procesadas se agruparán en un ticket y quedarán cobradas.", url: "/api/administrador/corte-sucursal/", cuerpo: { cliente_sucursal_id: tarjeta.dataset.sucursalId }, mensaje: "Corte de sucursal generado." });
    }
  });

  $("#form-dialogo-clave").addEventListener("submit", evento => {
    evento.preventDefault();
    const clave = $("#clave-administrador").value.trim();
    if (!/^\d{4}$/.test(clave)) {
      $("#error-clave").textContent = "La clave debe contener exactamente 4 dígitos.";
      $("#error-clave").hidden = false;
      return;
    }
    resolverClave(clave);
  });
  $("#cancelar-clave").addEventListener("click", () => resolverClave(null));
  $("#dialogo-clave").addEventListener("cancel", evento => { evento.preventDefault(); resolverClave(null); });
  $("#actualizar-resumen").addEventListener("click", evento => cargarResumen(evento.currentTarget));
  $("#actualizar-pedidos").addEventListener("click", evento => cargarResumen(evento.currentTarget));
  $("#limpiar-seleccion").addEventListener("click", () => {
    estado.ticketsSeleccionados.clear();
    estado.ticketSeleccionadoId = "";
    estado.ticketMoverId = "";
    if ($("#dialogo-detalle-pedido").open) $("#dialogo-detalle-pedido").close();
    renderPedidos();
  });
  $("#cerrar-detalle-pedido").addEventListener("click", () => $("#dialogo-detalle-pedido").close());
  $("#pantalla-completa-admin").addEventListener("click", alternarPantallaCompletaAdmin);
  document.addEventListener("fullscreenchange", actualizarBotonPantallaCompleta);
  document.addEventListener("webkitfullscreenchange", actualizarBotonPantallaCompleta);
  document.addEventListener("pointerdown", activarPantallaCompletaAdminConPrimerToque, true);
  $("#actualizar-configuraciones-impresion").addEventListener("click", evento => {
    cargarConfiguracionesImpresion(evento.currentTarget);
  });
  $("#nueva-configuracion-impresion").addEventListener("click", () => {
    if (!exigirPermisoAdministrador("gestionar_configuracion_tecnica")) return;
    limpiarFormularioConfiguracionImpresion();
    $("#configuracion-impresion-nombre").focus();
  });
  $("#usar-device-id-actual").addEventListener("click", () => {
    if (!exigirPermisoAdministrador("gestionar_configuracion_tecnica")) return;
    $("#configuracion-impresion-device-id").value = POS_DEVICE_ID;
    $("#configuracion-impresion-device-id").focus();
  });
  $("#cancelar-configuracion-impresion").addEventListener("click", limpiarFormularioConfiguracionImpresion);
  $("#desactivar-configuracion-impresion").addEventListener("click", desactivarConfiguracionImpresion);
  $("#form-configuracion-impresion").addEventListener("submit", guardarConfiguracionImpresion);

  $("#nuevo-usuario").addEventListener("click", () => {
    if (!exigirPermisoAdministrador("gestionar_usuarios")) return;
    limpiarFormularioUsuario();
  });

  $("#form-usuario").addEventListener("submit", async evento => {
    evento.preventDefault();
    if (!exigirPermisoAdministrador("gestionar_usuarios")) return;
    const id = $("#usuario-id").value;
    const cuerpo = {
      nombre: $("#usuario-nombre").value,
      tipo: $("#usuario-tipo").value,
      clave: $("#usuario-clave").value,
      activo: $("#usuario-activo").checked,
    };
    const resultado = await ejecutarConClave({ titulo: id ? "Actualizar usuario" : "Registrar usuario", ayuda: "Confirma los datos del personal operativo.", url: id ? `/api/administrador/usuarios/${id}/` : "/api/administrador/usuarios/", method: id ? "PATCH" : "POST", cuerpo, mensaje: id ? "Usuario actualizado." : "Usuario registrado." });
    if (resultado) limpiarFormularioUsuario();
  });

  $("#form-liquidacion").addEventListener("submit", async evento => {
    evento.preventDefault();
    const repartidorId = $("#liquidacion-repartidor").value;
    if (!repartidorId) return toast("Selecciona un repartidor.", true);
    await ejecutarConClave({ titulo: "Imprimir total de repartidor", ayuda: "Incluye efectivo, terminal, fondo y total a entregar.", url: "/api/administrador/liquidaciones/", cuerpo: { repartidor_id: repartidorId, fondo: $("#liquidacion-fondo").value || "0" }, mensaje: "Total de repartidor generado." });
  });

  $("#form-movimiento").addEventListener("submit", async evento => {
    evento.preventDefault();
    const id = estado.movimientoEditandoId || $("#movimiento-id").value;
    const resultado = await ejecutarAccion({
      url: id ? `/api/administrador/movimientos/${id}/` : "/api/administrador/movimientos/",
      method: id ? "PATCH" : "POST",
      cuerpo: {
        tipo: $("#movimiento-tipo").value,
        concepto: $("#movimiento-concepto").value,
        importe: $("#movimiento-importe").value,
      },
      mensaje: id ? "Movimiento actualizado." : "Movimiento registrado.",
    });
    if (resultado) limpiarFormularioMovimiento();
  });
  $("#cancelar-edicion-movimiento").addEventListener("click", limpiarFormularioMovimiento);

  $("#form-control-efectivo").addEventListener("input", actualizarTotalesControl);
  $("#form-control-efectivo").addEventListener("submit", async evento => {
    evento.preventDefault();
    const resultado = await ejecutarAccion({
      url: RUTAS.controlEfectivo,
      method: "PUT",
      cuerpo: {
        fondo_anterior: mapaDenominaciones("fondo-anterior-denominaciones"),
        fondo_siguiente: mapaDenominaciones("fondo-siguiente-denominaciones"),
        ventas_apps: {
          rappi: $("#app-rappi").value || "0",
          didi: $("#app-didi").value || "0",
          uber_eats: $("#app-uber-eats").value || "0",
        },
      },
      mensaje: "Control de efectivo guardado.",
      control: $("#guardar-control-efectivo"),
    });
    if (resultado) actualizarTotalesControl();
  });

  $("#form-clave").addEventListener("submit", async evento => {
    evento.preventDefault();
    if (!exigirPermisoAdministrador("cambiar_clave_maestra")) return;
    const claveActual = $("#clave-actual").value;
    const nuevaClave = $("#nueva-clave").value;
    const resultado = await ejecutarAccion({
      url: "/api/administrador/clave/",
      cuerpo: { clave_administrador: claveActual, nueva_clave: nuevaClave },
      mensaje: "Clave de administrador actualizada.",
      refrescar: false,
    });
    if (resultado) evento.currentTarget.reset();
  });

  const panelInicial = window.location.hash.slice(1);
  if ($(`[data-admin-panel="${CSS.escape(panelInicial)}"]`)) {
    estado.panelInicialSolicitado = panelInicial;
  }
  configurarRegresoAdministrador();
  actualizarDeviceIdActual();
  limpiarFormularioConfiguracionImpresion();
  iniciarPantallaCompletaAdmin();
  cargarResumen();
})();
