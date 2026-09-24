(() => {
  "use strict";

  const patronUuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
  const mensajeConflictoVersion = () => (
    "El pedido cambió en otra terminal. Ya mostramos la versión más reciente; "
    + "revisa la comanda y vuelve a confirmar la operación."
  );
  const CLAVE_ORIGEN_ADMIN = "tocayos_admin_origen_v1";

  function origenAdministradorDesdePanel(panel = "") {
    return panel === "movimientos" ? "ventas" : "inicio";
  }

  function esUuid(valor) {
    return patronUuid.test(String(valor || ""));
  }

  function ticketIdDeMutacion(url, ticketActualId = "") {
    const ruta = String(url).split(/[?#]/, 1)[0];
    const coincidenciaTicket = ruta.match(/^\/api\/tickets\/([^/]+)(?:\/|$)/);
    if (coincidenciaTicket && esUuid(coincidenciaTicket[1])) return coincidenciaTicket[1];
    const coincidenciaPartida = ruta.match(/^\/api\/partidas\/([^/]+)(?:\/|$)/);
    if (coincidenciaPartida && esUuid(coincidenciaPartida[1]) && esUuid(ticketActualId)) {
      return String(ticketActualId);
    }
    return "";
  }

  function requiereContratoTicket(url, metodo, ticketId = "") {
    const metodoNormalizado = String(metodo || "GET").toUpperCase();
    if (!esUuid(ticketId) || ["GET", "HEAD"].includes(metodoNormalizado)) return false;
    if (String(url).includes(`/api/tickets/${ticketId}/bloqueo/`)) return false;
    return String(url).includes(`/api/tickets/${ticketId}/`) || String(url).startsWith("/api/partidas/");
  }

  function cuerpoConContratoTicket(body, ticket, deviceId, edicionProgramada = false) {
    let datos = {};
    if (typeof body === "string" && body.trim()) {
      datos = JSON.parse(body);
    } else if (body && typeof body === "object") {
      datos = { ...body };
    }
    if (!datos || typeof datos !== "object" || Array.isArray(datos)) return body;
    return JSON.stringify({
      ...datos,
      device_id: deviceId,
      version_entidad: ticket.version_entidad,
      ...(edicionProgramada ? { edicion_programada: true } : {}),
    });
  }

  async function despacharSolicitud(fetchImpl, url, opciones = {}, contexto = {}) {
    const ticketId = String(contexto.ticketId || "");
    let body = opciones.body;
    if (ticketId) {
      if (!contexto.ticket || String(contexto.ticket.id) !== ticketId) {
        const error = new Error("La operación pendiente pertenecía a otro pedido y no se envió.");
        error.codigo = "contexto_ticket_cambio";
        throw error;
      }
      body = cuerpoConContratoTicket(
        body,
        contexto.ticket,
        contexto.deviceId,
        contexto.edicionProgramada,
      );
    }
    return fetchImpl(url, { ...opciones, body });
  }

  async function solicitarJson(fetchImpl, url, opciones = {}, contexto = {}) {
    const respuesta = await despacharSolicitud(fetchImpl, url, opciones, contexto);
    let datos;
    try { datos = await respuesta.json(); } catch { datos = {}; }
    return { respuesta, datos };
  }

  function adoptarTicketRespuesta(estadoAplicacion, datos, ticketId) {
    const ticket = datos?.ticket;
    if (
      !ticket
      || String(ticket.id) !== String(ticketId)
      || String(estadoAplicacion.ticket?.id) !== String(ticketId)
    ) return false;
    estadoAplicacion.ticket = ticket;
    return true;
  }

  function resolverGuardadoNombreClienteLlevar({
    nombreEnviado,
    nombreConfirmado,
    nombreActual,
    revisionEnviada,
    revisionActual,
  }) {
    const edicionPosterior = (
      Number(revisionActual) !== Number(revisionEnviada)
      || String(nombreActual) !== String(nombreEnviado)
    );
    return {
      edicionPosterior,
      nombreVisible: String(edicionPosterior ? nombreActual : nombreConfirmado),
    };
  }

  function clasificarPartidasPorNombres(partidas, esBebidaPartida) {
    const codigosBarbacoa = new Set(["BBQ05", "BBQ1"]);
    const codigosConsome = new Set(["CO8", "CO05", "CO1"]);
    const resultado = { principales: [], barbacoa: [], complementosGlobales: [] };
    for (const partida of partidas || []) {
      if (partida.es_promocion) continue;
      const codigo = String(partida.codigo || "").toUpperCase();
      if (codigosBarbacoa.has(codigo)) resultado.barbacoa.push(partida);
      else if (codigosConsome.has(codigo) || esBebidaPartida(partida)) {
        resultado.complementosGlobales.push(partida);
      } else resultado.principales.push(partida);
    }
    return resultado;
  }

  const contratoFrontend = {
    adoptarTicketRespuesta,
    clasificarPartidasPorNombres,
    cuerpoConContratoTicket,
    despacharSolicitud,
    mensajeConflictoVersion,
    origenAdministradorDesdePanel,
    requiereContratoTicket,
    resolverGuardadoNombreClienteLlevar,
    solicitarJson,
    ticketIdDeMutacion,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = contratoFrontend;
    return;
  }

  const productos = JSON.parse(document.getElementById("datos-productos").textContent);
  const posiciones = JSON.parse(document.getElementById("datos-posiciones").textContent);
  const permisos = JSON.parse(document.getElementById("datos-permisos").textContent);
  const nombresCanal = {
    comedor: "Comedor",
    llevar: "Llevar",
    domicilio: "Domicilio",
    recoger: "Recoger",
    sucursales: "Sucursales",
  };
  const canalesPareja = {
    comedor: "llevar",
    llevar: "comedor",
    domicilio: "recoger",
    recoger: "domicilio",
  };
  const productosAlFinal = new Set(["BBQ05", "BBQ1", "CO8", "CO05", "CO1"]);
  const terminosPreparacion = ["dorado", "medio", "blando"];
  const modificadores = [
    { codigo: "C/T", nombre: "CON TODO" },
    { codigo: "S/N", nombre: "SIN NADA" },
    { codigo: "CEB", nombre: "CEBOLLA" },
    { codigo: "CH G", nombre: "CHILE GÜERO" },
    { codigo: "CH V", nombre: "CHILE VERDE" },
    { codigo: "LLEVAR", nombre: "LLEVAR" },
  ];
  const comentariosGenerales = [
    { codigo: "TODO_PLATO", nombre: "TODO POR PLATO" },
    { codigo: "CEB_PLATO", nombre: "CEBOLLA POR PLATO" },
    { codigo: "CH_PLATO", nombre: "CHILE POR PLATO" },
    { codigo: "TODO_APARTE", nombre: "TODO A PARTE" },
    { codigo: "CEB_APARTE", nombre: "CEBOLLA A PARTE" },
    { codigo: "CH_APARTE", nombre: "CHILE A PARTE" },
    { codigo: "MAS_GUERO", nombre: "MÁS CHILE GÜERO" },
    { codigo: "MAS_VERDE", nombre: "MÁS CHILE VERDE" },
    { codigo: "MAS_CEB", nombre: "MÁS CEBOLLA" },
    ...modificadores,
  ];
  const opcionesSalsas = [
    "Con Todo", "Sin Nada", "Sólo Salsas", "Individual", "Verde", "Roja", "Pepino", "Rábano", "Cebolla", "Limón",
    "Morada", "Serrano", "Cilantro", "Cacahuate", "Chipotle", "Mexicana", "Verde Tomate", "Habanero",
    "Roja Taquera",
  ];
  const prefijosSalsas = ["", "+ Más", "Nada más"];
  const DEVICE_ID_KEY = "tocayos_pos_device_id";
  const HEARTBEAT_BLOQUEO_MS = 10000;
  const INTERVALO_ESTADO_LAN_MS = 5000;
  const modoTableta = document.body.dataset.modoTableta === "true";

  function crearDeviceId() {
    if (window.crypto?.randomUUID) return window.crypto.randomUUID();
    const aleatorio = Math.random().toString(36).slice(2);
    return `tablet-${Date.now().toString(36)}-${aleatorio}`;
  }

  function crearIdempotencyKey() {
    if (window.crypto?.randomUUID) return window.crypto.randomUUID();
    const bytes = new Uint8Array(16);
    if (window.crypto?.getRandomValues) window.crypto.getRandomValues(bytes);
    else bytes.forEach((_, indice) => { bytes[indice] = Math.floor(Math.random() * 256); });
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hexadecimal = [...bytes].map(valor => valor.toString(16).padStart(2, "0")).join("");
    return `${hexadecimal.slice(0, 8)}-${hexadecimal.slice(8, 12)}-${hexadecimal.slice(12, 16)}-${hexadecimal.slice(16, 20)}-${hexadecimal.slice(20)}`;
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

  const estado = {
    deviceId: obtenerDeviceId(),
    canal: "comedor",
    persona: 1,
    modoMenu: "productos",
    objetivoModificador: { tipo: "persona", persona: 1 },
    edicion: null,
    colaEdicion: Promise.resolve(),
    colasMutacionesTicket: new Map(),
    directorioActivo: false,
    directorioResultados: [],
    directorioPagina: 1,
    directorioHayMas: false,
    directorioTotal: null,
    directorioConsulta: "",
    directorioCargando: false,
    directorioError: "",
    temporizadorDirectorio: null,
    tokenDirectorio: 0,
    clienteFormularioContexto: "pedido",
    errorEdicion: null,
    tickets: {},
    programados: [],
    ticket: null,
    edicionProgramada: false,
    operador: null,
    sucursalSeleccionada: null,
    resolucionClave: null,
    resolucionFormaPago: null,
    operando: false,
    resultadosClientes: [],
    temporizadorCliente: null,
    temporizadorNombre: null,
    temporizadorClienteLlevar: null,
    colaGuardadoClienteLlevar: Promise.resolve(),
    clienteLlevarGuardado: "",
    clienteLlevarBorrador: "",
    clienteLlevarTicketId: "",
    revisionClienteLlevar: 0,
    errorClienteLlevar: null,
    temporizadorBloqueo: null,
    temporizadorSucursales: null,
    tokenBusquedaCliente: 0,
    clienteEditando: null,
    ultimoTerminoPorProducto: new Map(),
    prefijoSalsa: "",
    modoEntrega: "aproximada",
    entregaProgramadaDigitos: "",
    comandaVisible: 1,
    botonOperacion: null,
    idempotenciaAgregar: "",
    pinTabletaActivo: false,
    pinTabletaEnviando: false,
    claveTableta: "",
    mesaClaveTableta: "",
    tituloClaveTableta: "",
    ayudaClaveTableta: "",
    errorClaveTableta: "",
    cargaEstadoEnCurso: null,
    temporizadorEstadoLan: null,
    pantallaCompletaSuspendida: false,
  };

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];
  const csrf = () => document.cookie.split("; ").find(v => v.startsWith("csrftoken="))?.split("=")[1] || "";
  const dinero = (valor) => new Intl.NumberFormat("es-MX", { style: "currency", currency: "MXN" }).format(Number(valor || 0));
  const cantidad = (valor) => Number(valor).toLocaleString("es-MX", { maximumFractionDigits: 3 });
  const claseCantidad = valor => {
    const digitos = String(Math.max(0, Math.trunc(Number(valor) || 0))).length;
    if (digitos >= 4) return "cantidad-cuatro-digitos";
    if (digitos >= 3) return "cantidad-tres-digitos";
    return "";
  };
  const escapar = (valor) => String(valor ?? "").replace(/[&<>'"]/g, caracter => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[caracter]);

  function numeroComandaActual(ticket = estado.ticket) {
    return Math.max(1, Number(ticket?.comanda_actual || 1));
  }

  function comandasDisponibles(ticket = estado.ticket) {
    const declaradas = Array.isArray(ticket?.comandas)
      ? ticket.comandas
        .map(comanda => ({ numero: Number(comanda.numero), procesada: Boolean(comanda.procesada) }))
        .filter(comanda => Number.isInteger(comanda.numero) && comanda.numero > 0)
        .sort((a, b) => a.numero - b.numero)
      : [];
    if (declaradas.length) return declaradas;
    const total = Math.max(1, Number(ticket?.cantidad_comandas || numeroComandaActual(ticket)));
    return Array.from({ length: total }, (_, indice) => ({
      numero: indice + 1,
      procesada: indice + 1 < numeroComandaActual(ticket) || !comandaEnEdicion(ticket),
    }));
  }

  function comandaEnEdicion(ticket = estado.ticket) {
    if (!ticket) return false;
    return typeof ticket.comanda_en_edicion === "boolean"
      ? ticket.comanda_en_edicion
      : ticket.estado === "abierto";
  }

  function esEdicionProgramada(ticket = estado.ticket) {
    return Boolean(estado.edicionProgramada && ticket?.estado === "programado");
  }

  function normalizarComandaVisible(ticket = estado.ticket) {
    const disponibles = comandasDisponibles(ticket);
    const numeros = disponibles.map(comanda => comanda.numero);
    const preferida = Number(estado.comandaVisible || numeroComandaActual(ticket));
    estado.comandaVisible = numeros.includes(preferida)
      ? preferida
      : (numeros.includes(numeroComandaActual(ticket)) ? numeroComandaActual(ticket) : numeros.at(-1));
    return estado.comandaVisible;
  }

  function comandaVisibleEditable(ticket = estado.ticket) {
    return Boolean(
      ticket
      && (comandaEnEdicion(ticket) || esEdicionProgramada(ticket))
      && normalizarComandaVisible(ticket) === numeroComandaActual(ticket)
    );
  }

  function perteneceAComanda(item, numero) {
    return Number(item?.comanda_numero || 1) === Number(numero);
  }

  function ticketParaComandaVisible(ticket = estado.ticket) {
    if (!ticket) return null;
    const numero = normalizarComandaVisible(ticket);
    const contexto = numero === numeroComandaActual(ticket)
      && (comandaEnEdicion(ticket) || esEdicionProgramada(ticket))
      ? null
      : ticket.contextos_comandas?.[String(numero)];
    const vista = contexto && typeof contexto === "object"
      ? {
          ...ticket,
          ...contexto,
          fecha_programada: ticket.fecha_programada || contexto.fecha_programada || "",
          hora_programada: ticket.hora_programada || contexto.hora_programada || "",
          entrega_aproximada: ticket.hora_programada || contexto.entrega_aproximada || "",
          cliente: {
            ...(ticket.cliente || {}),
            nombre: contexto.cliente_nombre ?? ticket.cliente?.nombre ?? "",
            telefono: contexto.cliente_telefono ?? ticket.cliente?.telefono ?? "",
            domicilio: contexto.cliente_domicilio ?? ticket.cliente?.domicilio ?? "",
            referencia: contexto.cliente_referencia ?? ticket.cliente?.referencia ?? "",
            contacto_pedido_nombre: contexto.contacto_pedido_nombre ?? "",
            contacto_pedido_telefono: contexto.contacto_pedido_telefono ?? "",
          },
        }
      : ticket;
    return {
      ...vista,
      partidas: (ticket.partidas || []).filter(partida => perteneceAComanda(partida, numero)),
      modificadores: (ticket.modificadores || []).filter(modificador => perteneceAComanda(modificador, numero)),
    };
  }

  function sincronizarControlesComandaVisible() {
    const ticket = ticketParaComandaVisible();
    if (!ticket) return;
    estado.modoEntrega = ticket.tipo_entrega || "aproximada";
    estado.entregaProgramadaDigitos = (
      ticket.tipo_entrega === "programada" ? ticket.entrega_aproximada : ""
    )?.replace(":", "") || "";
    if ($("#terminal")) $("#terminal").checked = Boolean(ticket.terminal);
    if ($("#paga-con")) $("#paga-con").value = ticket.paga_con || "";
    if ($("#comentario")) $("#comentario").value = ticket.comentario_general || "";
    if ($("#cliente-directo-nombre")) {
      $("#cliente-directo-nombre").value = ticket.cliente?.nombre || "";
    }
    if ($("#cliente-directo-telefono")) {
      $("#cliente-directo-telefono").value = ticket.cliente?.telefono || "";
    }
    actualizarControlEntrega(ticket);
    if (estado.ticket?.canal === "domicilio") renderClienteDomicilio(ticket);
  }

  function ticketParaContrato(ticketId) {
    if (estado.ticket && String(estado.ticket.id) === String(ticketId)) return estado.ticket;
    const error = new Error("La operación pendiente pertenecía a otro pedido y no se envió.");
    error.codigo = "contexto_ticket_cambio";
    throw error;
  }

  async function api(url, opciones = {}) {
    const metodo = (opciones.method || "GET").toUpperCase();
    const ticketId = ticketIdDeMutacion(url, estado.ticket?.id || "");
    const conContratoTicket = requiereContratoTicket(url, metodo, ticketId);
    if (!conContratoTicket) return ejecutarApi(url, opciones);
    const contrato = { ticketId: String(ticketId) };
    const colaAnterior = estado.colasMutacionesTicket.get(contrato.ticketId) || Promise.resolve();
    const ejecutar = () => ejecutarApi(url, opciones, contrato);
    const actual = colaAnterior.then(ejecutar, ejecutar);
    const colaSilenciada = actual.catch(() => {});
    estado.colasMutacionesTicket.set(contrato.ticketId, colaSilenciada);
    colaSilenciada.finally(() => {
      if (estado.colasMutacionesTicket.get(contrato.ticketId) === colaSilenciada) {
        estado.colasMutacionesTicket.delete(contrato.ticketId);
      }
    });
    return actual;
  }

  async function ejecutarApi(url, opciones = {}, contrato = null) {
    const ticketContrato = contrato ? ticketParaContrato(contrato.ticketId) : null;
    const { respuesta, datos } = await solicitarJson(fetch, url, {
      cache: "no-store",
      credentials: "same-origin",
      ...opciones,
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrf(),
        "X-POS-Device-ID": estado.deviceId,
        ...(opciones.headers || {}),
      },
    }, {
      ticketId: contrato?.ticketId || "",
      ticket: ticketContrato,
      deviceId: estado.deviceId,
      edicionProgramada: estado.edicionProgramada,
    });
    if (respuesta.status === 401) {
      if (estado.edicionProgramada || url.includes("/editar-programado/")) {
        throw new Error("El acceso administrativo venció. Vuelve a Programados para autorizar la edición.");
      }
      const siguiente = encodeURIComponent(`${window.location.pathname}${window.location.search}`);
      window.location.replace(`/acceso/?next=${siguiente}`);
      throw new Error("La sesión expiró. Inicia sesión nuevamente.");
    }
    if (contrato) ticketParaContrato(contrato.ticketId);
    const ticketAdoptado = contrato
      ? adoptarTicketRespuesta(estado, datos, contrato.ticketId)
      : false;
    if (!respuesta.ok) {
      const conflictoVersion = Boolean(
        ticketAdoptado
        && respuesta.status === 409
        && datos.codigo === "version_entidad_desactualizada"
      );
      if (conflictoVersion) {
        mostrarTicket({ descartarBorradorClienteLlevar: true });
      } else if (ticketAdoptado && [409, 423].includes(respuesta.status)) {
        actualizarVistaPorBloqueo();
      }
      const error = new Error(
        conflictoVersion
          ? mensajeConflictoVersion()
          : (datos.error || "No fue posible completar la operación."),
      );
      error.datos = datos;
      error.status = respuesta.status;
      error.codigo = datos.codigo || "";
      error.requiereConfirmacion = conflictoVersion;
      throw error;
    }
    return datos;
  }

  function ticketActivo(ticket = estado.ticket) {
    return ["abierto", "procesado", "cobrar"].includes(ticket?.estado)
      || esEdicionProgramada(ticket);
  }

  function bloqueoPropio(ticket = estado.ticket) {
    return Boolean(ticket?.bloqueo?.activo && ticket.bloqueo.es_mio);
  }

  function bloqueoDeOtro(ticket = estado.ticket) {
    return Boolean(ticket?.bloqueo?.activo && !ticket.bloqueo.es_mio);
  }

  function textoBloqueo(ticket = estado.ticket) {
    const bloqueo = ticket?.bloqueo;
    if (!bloqueo?.activo) return "";
    if (bloqueo.es_mio) return `Tomada por ${estado.operador?.nombre || "esta tableta"}`;
    return `Tomada por ${bloqueo.tomado_por || "otra tableta"}`;
  }

  function actualizarIndicadorBloqueoTicket() {
    const nodo = $("#ticket-bloqueo");
    if (!nodo || !estado.ticket) return;
    const texto = textoBloqueo();
    nodo.hidden = !texto;
    nodo.textContent = texto;
    nodo.classList.toggle("ajeno", bloqueoDeOtro());
  }

  function actualizarVistaPorBloqueo() {
    if (!estado.ticket) return;
    actualizarIndicadorBloqueoTicket();
    renderMenu();
    renderComanda();
    renderAcciones();
  }

  function iniciarHeartbeatBloqueo() {
    clearInterval(estado.temporizadorBloqueo);
    estado.temporizadorBloqueo = null;
    if (!ticketActivo() || !bloqueoPropio()) return;
    estado.temporizadorBloqueo = setInterval(() => renovarBloqueoTicket(), HEARTBEAT_BLOQUEO_MS);
  }

  async function renovarBloqueoTicket() {
    if (!ticketActivo() || !bloqueoPropio()) {
      clearInterval(estado.temporizadorBloqueo);
      estado.temporizadorBloqueo = null;
      return;
    }
    try {
      const datos = await api(`/api/tickets/${estado.ticket.id}/bloqueo/`, {
        method: "POST",
        body: JSON.stringify({ device_id: estado.deviceId }),
      });
      estado.ticket = datos.ticket;
      actualizarIndicadorBloqueoTicket();
      renderAcciones();
    } catch (error) {
      clearInterval(estado.temporizadorBloqueo);
      estado.temporizadorBloqueo = null;
      toast(error.message, true);
    }
  }

  async function liberarBloqueoActual() {
    clearInterval(estado.temporizadorBloqueo);
    estado.temporizadorBloqueo = null;
    const ticket = estado.ticket;
    if (!bloqueoPropio(ticket)) return;
    try {
      await api(`/api/tickets/${ticket.id}/bloqueo/`, {
        method: "DELETE",
        body: JSON.stringify({ device_id: estado.deviceId }),
      });
    } catch {
      /* Si la red se cayó, el lease expira solo. */
    }
  }

  function liberarBloqueoEnSalida() {
    const ticket = estado.ticket;
    if (!bloqueoPropio(ticket)) return;
    fetch(`/api/tickets/${ticket.id}/bloqueo/`, {
      method: "DELETE",
      cache: "no-store",
      credentials: "same-origin",
      keepalive: true,
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrf(),
        "X-POS-Device-ID": estado.deviceId,
      },
      body: JSON.stringify({ device_id: estado.deviceId }),
    }).catch(() => {});
  }

  function cerrarToast() {
    const nodo = $("#toast");
    clearTimeout(toast.temporizador);
    nodo.classList.remove("visible", "persistente");
  }

  function toast(mensaje, error = false) {
    const nodo = $("#toast");
    nodo.setAttribute("role", error ? "alert" : "status");
    nodo.setAttribute("aria-live", error ? "assertive" : "polite");
    $("#toast-mensaje").textContent = mensaje;
    nodo.classList.toggle("error", error);
    nodo.classList.toggle("persistente", error);
    nodo.classList.add("visible");
    clearTimeout(toast.temporizador);
    if (!error) toast.temporizador = setTimeout(cerrarToast, 3300);
  }

  function pedirClavePos(titulo, ayuda) {
    const claveEnZonaComedor = modoTableta && !estado.ticket && !$("#vista-posiciones").classList.contains("oculto");
    if (claveEnZonaComedor) {
      estado.pinTabletaActivo = true;
      estado.pinTabletaEnviando = false;
      estado.claveTableta = "";
      estado.tituloClaveTableta = titulo;
      estado.ayudaClaveTableta = ayuda;
      renderPosiciones();
      requestAnimationFrame(() => $("#rejilla-posiciones [data-tecla-pin-tableta]")?.focus());
      return new Promise(resolve => { estado.resolucionClave = resolve; });
    }
    const dialogo = $("#dialogo-clave-pos");
    const campo = $("#clave-pos");
    $("#titulo-clave-pos").textContent = titulo;
    $("#ayuda-clave-pos").textContent = ayuda;
    campo.value = "";
    campo.setAttribute("aria-invalid", "false");
    $("#error-clave-pos").hidden = true;
    dialogo.showModal();
    setTimeout(() => campo.focus(), 40);
    return new Promise(resolve => { estado.resolucionClave = resolve; });
  }

  function resolverClavePos(clave) {
    if (modoTableta && estado.pinTabletaActivo) {
      if (clave) {
        if (estado.pinTabletaEnviando) return;
        estado.pinTabletaEnviando = true;
        estado.errorClaveTableta = "";
      } else {
        estado.pinTabletaActivo = false;
        estado.pinTabletaEnviando = false;
        estado.claveTableta = "";
        estado.errorClaveTableta = "";
      }
      renderPosiciones();
    } else {
      const dialogo = $("#dialogo-clave-pos");
      if (dialogo.open) dialogo.close();
    }
    const resolver = estado.resolucionClave;
    estado.resolucionClave = null;
    resolver?.(clave);
  }

  function manejarTeclaClaveTableta(tecla) {
    if (
      !modoTableta
      || !estado.pinTabletaActivo
      || estado.pinTabletaEnviando
      || estado.operando
    ) return;
    if (tecla === "cancelar") {
      resolverClavePos(null);
      return;
    }
    if (tecla === "borrar") {
      estado.claveTableta = estado.claveTableta.slice(0, -1);
      estado.errorClaveTableta = "";
      renderPosiciones();
      return;
    }
    if (/^\d$/.test(tecla) && estado.claveTableta.length < 4) {
      estado.claveTableta += tecla;
      estado.errorClaveTableta = "";
      if (estado.claveTableta.length === 4) {
        resolverClavePos(estado.claveTableta);
        return;
      }
      renderPosiciones();
    }
  }

  function pedirFormaPago() {
    const dialogo = $("#dialogo-forma-pago");
    dialogo.showModal();
    setTimeout(() => dialogo.querySelector("[data-forma-pago]")?.focus(), 40);
    return new Promise(resolve => { estado.resolucionFormaPago = resolve; });
  }

  function resolverFormaPago(formaPago) {
    const dialogo = $("#dialogo-forma-pago");
    if (dialogo.open) dialogo.close();
    const resolver = estado.resolucionFormaPago;
    estado.resolucionFormaPago = null;
    resolver?.(formaPago);
  }

  function renderOperadorActual() {
    const contenedor = $("#operador-actual");
    const nombre = $("#operador-actual-nombre");
    if (!contenedor || !nombre) return;
    const operador = estado.operador?.nombre?.trim() || "";
    nombre.textContent = operador;
    contenedor.hidden = !operador;
    const accesoMovimientos = Boolean(estado.operador?.puede_acceder_movimientos);
    const botonMovimientos = $("#abrir-movimientos");
    if (botonMovimientos) botonMovimientos.hidden = !operador || !accesoMovimientos;
  }

  async function reanudarSesionOperador() {
    if (modoTableta || idProgramadoSolicitado()) return false;
    try {
      const datos = await api("/api/operador/actual/");
      if (!datos.operador) return false;
      estado.operador = datos.operador;
      $("#pantalla-acceso")?.classList.add("oculto");
      $("main").classList.remove("oculto");
      document.body.classList.add("en-operacion");
      document.body.classList.remove("en-ticket");
      renderOperadorActual();
      await cargarEstado(estado.canal === "sucursales");
      programarSincronizacionSucursales();
      return true;
    } catch {
      return false;
    }
  }

  async function entrarComoMesero({ mostrarPantalla = true } = {}) {
    while (true) {
      const clave = await pedirClavePos("Acceso a Ventas", "Ingresa el código de 4 dígitos asignado a tu usuario.");
      if (!clave) return false;
      bloquear(true);
      try {
        const datos = await api("/api/operador/identificar/", {
          method: "POST",
          body: JSON.stringify({ clave }),
        });
        estado.operador = datos.operador;
        renderOperadorActual();
        estado.pinTabletaActivo = false;
        estado.pinTabletaEnviando = false;
        estado.claveTableta = "";
        estado.errorClaveTableta = "";
        if (mostrarPantalla) {
          $("#pantalla-acceso")?.classList.add("oculto");
          $("main").classList.remove("oculto");
          document.body.classList.add("en-operacion");
          document.body.classList.remove("en-ticket");
          await cargarEstado(estado.canal === "sucursales");
          programarSincronizacionSucursales();
        }
        toast(`Turno identificado: ${datos.operador.nombre}.`);
        return true;
      } catch (error) {
        toast(error.message, true);
        if (!modoTableta) return false;
        estado.pinTabletaEnviando = false;
        estado.claveTableta = "";
        estado.errorClaveTableta = error.message;
      } finally {
        bloquear(false);
      }
    }
  }

  async function abrirAdministrador(panel = "") {
    const clave = await pedirClavePos("Clave de administrador", "Autoriza el acceso a la gestión del turno de esta sucursal.");
    if (!clave) return;
    try {
      const datos = await api("/api/administrador/acceso/", {
        method: "POST",
        body: JSON.stringify({ clave_administrador: clave }),
      });
      const destino = datos.destino || "/administrador/";
      try {
        sessionStorage.setItem(CLAVE_ORIGEN_ADMIN, origenAdministradorDesdePanel(panel));
      } catch { /* El destino seguro sigue siendo Inicio cuando el almacenamiento no esta disponible. */ }
      window.location.assign(panel ? destino.replace(/#.*$/, "") + "#" + panel : destino);
    } catch (error) {
      toast(error.message, true);
    }
  }

  function bloquear(valor, botonIniciador = null) {
    estado.operando = valor;
    document.body.setAttribute("aria-busy", String(valor));
    if (valor) {
      const boton = botonIniciador || document.activeElement?.closest?.("button");
      if (boton && !boton.disabled) {
        const etiquetas = {
          procesar: "Procesando orden…",
          cobrar: "Registrando cobro…",
          "cancelar-orden": "Cancelando orden…",
          "ticket-cuenta": "Imprimiendo ticket…",
          "agregar-comanda": "Creando comanda…",
          "reactivar-sucursal": "Reactivando pedido…",
        };
        boton.dataset.textoCarga = etiquetas[boton.id] || "Cargando…";
        boton.dataset.ariaOperacionOriginal = boton.getAttribute("aria-label") || "";
        boton.classList.add("operando");
        boton.setAttribute("aria-busy", "true");
        boton.setAttribute("aria-label", boton.dataset.textoCarga);
        estado.botonOperacion = boton;
      }
    } else if (estado.botonOperacion) {
      const boton = estado.botonOperacion;
      boton.classList.remove("operando");
      boton.removeAttribute("aria-busy");
      if (boton.dataset.ariaOperacionOriginal) boton.setAttribute("aria-label", boton.dataset.ariaOperacionOriginal);
      else boton.removeAttribute("aria-label");
      delete boton.dataset.textoCarga;
      delete boton.dataset.ariaOperacionOriginal;
      estado.botonOperacion = null;
    }
    $$("button").forEach(boton => boton.disabled = valor);
    if (!valor && estado.ticket) {
      renderMenu();
      renderComanda();
      renderAcciones();
    }
  }

  async function cargarEstado(sincronizarSucursales = false, { silencioso = false } = {}) {
    if (estado.cargaEstadoEnCurso) return estado.cargaEstadoEnCurso;
    const tarea = (async () => {
      try {
        if (sincronizarSucursales && permisos.sincronizar) {
          await api("/api/sincronizacion/sucursales/", { method: "POST", body: "{}" });
        }
        const datos = await api("/api/estado/");
        estado.tickets = datos.tickets;
        estado.programados = datos.programados || [];
        if (!estado.ticket) renderPosiciones();
        return true;
      } catch (error) {
        if (!silencioso) toast(error.message, true);
        return false;
      }
    })();
    estado.cargaEstadoEnCurso = tarea;
    try {
      return await tarea;
    } finally {
      if (estado.cargaEstadoEnCurso === tarea) estado.cargaEstadoEnCurso = null;
    }
  }

  function idProgramadoSolicitado() {
    const valor = new URLSearchParams(window.location.search).get("editar_programado") || "";
    return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(valor)
      ? valor
      : "";
  }

  async function abrirProgramadoDesdeEnlace() {
    const ticketId = idProgramadoSolicitado();
    if (!ticketId) return cargarEstado();
    $("#pantalla-acceso")?.classList.add("oculto");
    $("main").classList.remove("oculto");
    document.body.classList.add("en-operacion");
    bloquear(true);
    try {
      const datos = await api(`/api/administrador/tickets/${ticketId}/editar-programado/`, {
        method: "POST",
        body: "{}",
      });
      estado.edicionProgramada = true;
      estado.ticket = datos.ticket;
      estado.comandaVisible = numeroComandaActual(datos.ticket);
      mostrarTicket();
      toast("Edita el pedido y vuelve a Programados al terminar. Su fecha y estado se conservarán.");
      return true;
    } catch (error) {
      toast(error.message, true);
      window.location.assign("/administrador/#programados");
      return false;
    } finally {
      bloquear(false);
    }
  }

  function ventasVisibles() {
    return modoTableta || document.body.classList.contains("en-operacion");
  }

  function puedeActualizarEstadoLan() {
    return !document.hidden && !estado.ticket && ventasVisibles() && !estado.operando;
  }

  function actualizarEstadoLan() {
    if (!puedeActualizarEstadoLan() || estado.cargaEstadoEnCurso) return;
    cargarEstado(false, { silencioso: true });
  }

  function iniciarActualizacionEstadoLan() {
    clearInterval(estado.temporizadorEstadoLan);
    estado.temporizadorEstadoLan = setInterval(actualizarEstadoLan, INTERVALO_ESTADO_LAN_MS);
  }

  function ticketResumenEstado(ticket) {
    return {
      ticket_id: ticket.id,
      folio: ticket.folio,
      estado: ticket.estado,
      total: ticket.total,
      cliente_nombre: ticket.cliente_nombre || ticket.cliente?.nombre || "",
      version_entidad: ticket.version_entidad,
      bloqueo: ticket.bloqueo,
      comanda_actual: ticket.comanda_actual,
      comanda_en_edicion: ticket.comanda_en_edicion,
      cantidad_comandas: ticket.cantidad_comandas,
      puede_agregar_comanda: ticket.puede_agregar_comanda,
      comandas: ticket.comandas,
    };
  }

  function renderPosiciones() {
    const contenedor = $("#rejilla-posiciones");
    if (modoTableta && estado.canal !== "comedor") {
      estado.canal = "comedor";
      estado.sucursalSeleccionada = null;
    }
    contenedor.setAttribute("aria-label", `Posiciones de ${nombresCanal[estado.canal] || estado.canal}`);
    const renderTarjeta = (posicion, opciones = {}) => {
      const ticket = estado.tickets[posicion.id];
      const ordenAbierta = ticket && comandaEnEdicion(ticket);
      const claseBase = ticket ? (ordenAbierta ? "ocupada" : "procesada") : "libre";
      const claseBloqueo = ticket?.bloqueo?.activo ? (ticket.bloqueo.es_mio ? " propia" : " bloqueada") : "";
      const clase = `${claseBase}${claseBloqueo}`;
      const etiquetaBloqueo = ticket?.bloqueo?.activo
        ? (ticket.bloqueo.es_mio ? "Tomada por esta tableta" : `Tomada por ${ticket.bloqueo.tomado_por || "otra tableta"}`)
        : "";
      const etiquetaEstado = ticket ? (etiquetaBloqueo || (ordenAbierta ? "Orden abierta" : "Procesada")) : "Libre";
      const detalle = ticket ? `Ticket ${ticket.folio} · ${dinero(ticket.total)}` : "Disponible";
      const nombreLlevar = posicion.canal === "llevar" && ticket
        ? String(ticket.cliente_nombre || "").trim()
        : "";
      const partes = String(posicion.nombre).match(/^(.*?)[\s-]*(\d+)$/);
      const tipo = partes ? partes[1].trim() : "Posición";
      const numero = partes ? partes[2] : posicion.nombre;
      const tipoVisible = opciones.tipoVisible || tipo;
      const etiquetaAccesible = `${posicion.nombre}. ${etiquetaEstado}. ${nombreLlevar ? `Cliente ${nombreLlevar}. ` : ""}${detalle}`;
      const detalleVisible = nombreLlevar
        ? `<small class="posicion-detalle-llevar"><span class="posicion-cliente-llevar">${escapar(nombreLlevar)}</span><span>${escapar(detalle)}</span></small>`
        : `<small>${escapar(detalle)}</small>`;
      return `<button class="posicion ${clase}" data-id="${posicion.id}" data-estado="${clase}" type="button" aria-label="${escapar(etiquetaAccesible)}">
        <span class="posicion-estado"><i aria-hidden="true"></i>${etiquetaEstado}</span>
        <strong><span class="posicion-tipo">${escapar(tipoVisible)}</span><span class="posicion-numero">${escapar(numero)}</span></strong>
        ${detalleVisible}
      </button>`;
    };
    const renderTarjetas = canal => posiciones
      .filter(posicion => posicion.canal === canal)
      .sort((a, b) => a.orden - b.orden)
      .map(renderTarjeta).join("");
    if (estado.canal === "sucursales") {
      const grupos = new Map();
      for (const posicion of posiciones
        .filter(item => item.canal === "sucursales" && item.cliente_sucursal_id)
        .sort((a, b) => a.cliente_sucursal_orden - b.cliente_sucursal_orden || a.orden - b.orden)) {
        if (!grupos.has(posicion.cliente_sucursal_id)) {
          grupos.set(posicion.cliente_sucursal_id, {
            nombre: posicion.cliente_sucursal_nombre,
            orden: posicion.cliente_sucursal_orden,
            posiciones: [],
          });
        }
        grupos.get(posicion.cliente_sucursal_id).posiciones.push(posicion);
      }
      contenedor.className = "rejilla-posiciones rejilla-sucursales";
      const grupoSeleccionado = estado.sucursalSeleccionada ? grupos.get(estado.sucursalSeleccionada) : null;
      if (grupoSeleccionado) {
        const ocupadas = grupoSeleccionado.posiciones.filter(posicion => estado.tickets[posicion.id]).length;
        contenedor.innerHTML = `<section class="pantalla-sucursal">
          <header class="pantalla-sucursal-cabecera">
            <button class="boton volver-sucursales" data-volver-sucursales type="button">← Sucursales</button>
            <div><strong>${escapar(grupoSeleccionado.nombre)}</strong><small>${ocupadas} pedido${ocupadas === 1 ? "" : "s"} activo${ocupadas === 1 ? "" : "s"} · ${grupoSeleccionado.posiciones.length} casillas</small></div>
          </header>
          <div class="casillas-sucursal">${grupoSeleccionado.posiciones.map(posicion => renderTarjeta(posicion, { tipoVisible: "Pedido" })).join("")}</div>
        </section>`;
      } else {
        estado.sucursalSeleccionada = null;
        contenedor.innerHTML = `<section class="selector-sucursales">
          <header><strong>Sucursales</strong><small>Selecciona una para abrir sus casillas</small></header>
          <div>${[...grupos.entries()].map(([id, grupo]) => {
            const ocupadas = grupo.posiciones.filter(posicion => estado.tickets[posicion.id]).length;
            const procesadas = grupo.posiciones.filter(posicion => ["procesado", "cobrar"].includes(estado.tickets[posicion.id]?.estado)).length;
            return `<button class="tarjeta-sucursal-pos" data-sucursal-id="${id}" type="button" aria-label="Abrir ${escapar(grupo.nombre)}. ${ocupadas} pedidos activos.">
              <span class="sucursal-pos-estado"><i aria-hidden="true"></i>${procesadas ? `${procesadas} por cerrar` : "Sin cierres urgentes"}</span>
              <strong>${escapar(grupo.nombre)}</strong>
              <span class="sucursal-pos-conteo"><b>${ocupadas}</b><small>Pedidos activos</small></span>
              <span class="sucursal-pos-accion">Ver ${grupo.posiciones.length} casillas →</span>
            </button>`;
          }).join("")}</div>
        </section>` || '<p class="vacio">No hay sucursales configuradas.</p>';
      }
      return;
    }
    if (modoTableta) {
      const tarjetasComedor = renderTarjetas("comedor");
      if (!estado.pinTabletaActivo) {
        contenedor.className = "rejilla-posiciones rejilla-simple rejilla-tableta-comedor";
        contenedor.innerHTML = tarjetasComedor || '<p class="vacio">No hay mesas de Comedor configuradas.</p>';
        return;
      }
      const posicionClave = posiciones.find(posicion => String(posicion.id) === String(estado.mesaClaveTableta));
      const nombrePosicion = posicionClave?.nombre || "Mesa";
      const digitosCapturados = estado.claveTableta.length;
      const tecladoDeshabilitado = estado.pinTabletaEnviando ? "disabled" : "";
      const estadoValidacion = estado.pinTabletaEnviando
        ? "Validando acceso…"
        : "El código se validará automáticamente al capturar el cuarto dígito.";
      contenedor.className = "rejilla-posiciones rejilla-dividida rejilla-tableta-pin";
      contenedor.innerHTML = `
        <section class="grupo-posiciones grupo-principal grupo-comedor-tableta">
          <header><strong>Identificación</strong><small>${escapar(nombrePosicion)}</small></header>
          <div>
            <section class="pin-tableta-panel" role="dialog" aria-labelledby="pin-tableta-titulo" aria-describedby="pin-tableta-ayuda pin-tableta-estado" aria-busy="${String(estado.pinTabletaEnviando)}">
              <small>Acceso a ${escapar(nombrePosicion)}</small>
              <h2 id="pin-tableta-titulo">${escapar(estado.tituloClaveTableta || "Acceso a Ventas")}</h2>
              <p id="pin-tableta-ayuda">${escapar(estado.ayudaClaveTableta || "Ingresa tu código de 4 dígitos.")}</p>
              <output class="pin-tableta-puntos" aria-label="${digitosCapturados} de 4 dígitos capturados">
                ${Array.from({ length: 4 }, (_, indice) => `<i class="${indice < digitosCapturados ? "capturado" : ""}" aria-hidden="true"></i>`).join("")}
              </output>
              <p id="pin-tableta-estado" class="pin-tableta-estado" role="status" aria-live="polite">${estadoValidacion}</p>
              <p class="pin-tableta-error" role="alert" aria-live="assertive" ${estado.errorClaveTableta ? "" : "hidden"}>${escapar(estado.errorClaveTableta)}</p>
            </section>
          </div>
        </section>
        <section class="grupo-posiciones grupo-auxiliar grupo-teclado-tableta">
          <header><strong>Teclado</strong><small>${estado.pinTabletaEnviando ? "Comprobando código" : "Código de 4 dígitos"}</small></header>
          <div class="teclado-pin-tableta" aria-label="Teclado numérico para código de acceso">
            ${[1, 2, 3, 4, 5, 6, 7, 8, 9].map(numero => `<button data-tecla-pin-tableta="${numero}" type="button" ${tecladoDeshabilitado}>${numero}</button>`).join("")}
            <button class="borrar" data-tecla-pin-tableta="borrar" type="button" aria-label="Borrar último dígito" ${tecladoDeshabilitado}>←</button>
            <button data-tecla-pin-tableta="0" type="button" ${tecladoDeshabilitado}>0</button>
            <button class="cancelar" data-tecla-pin-tableta="cancelar" type="button" ${tecladoDeshabilitado}>Cancelar</button>
          </div>
          <p class="teclado-pin-ayuda">Usa este teclado; el cuarto dígito inicia la validación.</p>
        </section>`;
      return;
    }
    const secundario = estado.canal === "comedor" ? "llevar" : (estado.canal === "domicilio" ? "recoger" : "");
    const renderProgramados = canal => {
      const lista = estado.programados.filter(ticket => ticket.canal === canal);
      if (!lista.length) return "";
      return `<section class="pedidos-programados-pos" data-canal="${escapar(canal)}">
        <header><strong>Programados · ${escapar(nombresCanal[canal] || canal)}</strong><small>Se activarán por fecha y hora en la primera casilla libre</small></header>
        <div>${lista.map(ticket => `<article class="programado-pos">
          <span>Programado</span><strong>#${escapar(ticket.folio)} · ${escapar(ticket.cliente_nombre || "Cliente")}</strong>
          <small>${escapar(ticket.fecha_programada)}${ticket.hora_programada ? ` · ${escapar(ticket.hora_programada)}` : ""}</small><b>${dinero(ticket.total)}</b>
        </article>`).join("")}</div>
      </section>`;
    };
    const principales = renderTarjetas(estado.canal);
    const programadosPrincipales = renderProgramados(estado.canal);
    if (!secundario) {
      contenedor.className = "rejilla-posiciones rejilla-simple";
      contenedor.innerHTML = `${principales || '<p class="vacio">No hay posiciones configuradas.</p>'}${programadosPrincipales}`;
      return;
    }
    const auxiliares = renderTarjetas(secundario);
    const programadosAuxiliares = renderProgramados(secundario);
    contenedor.className = "rejilla-posiciones rejilla-dividida";
    contenedor.innerHTML = `
      <section class="grupo-posiciones grupo-principal">
        <header><strong>${escapar(nombresCanal[estado.canal])}</strong><small>Pedidos activos y posiciones disponibles</small></header>
        <div>${principales || '<p class="vacio">No hay posiciones configuradas.</p>'}</div>${programadosPrincipales}
      </section>
      <section class="grupo-posiciones grupo-auxiliar">
        <header><strong>${escapar(nombresCanal[secundario])}</strong><small>${secundario === "recoger" ? "Nombre y celular" : "Nombre del cliente"}</small></header>
        <div>${auxiliares || '<p class="vacio">No hay posiciones configuradas.</p>'}</div>${programadosAuxiliares}
      </section>`;
  }

  function programarSincronizacionSucursales() {
    clearInterval(estado.temporizadorSucursales);
    estado.temporizadorSucursales = null;
    if (estado.canal === "sucursales") {
      if (permisos.sincronizar) {
        estado.temporizadorSucursales = setInterval(() => cargarEstado(true), 300000);
      }
    }
  }

  async function cambiarCanal(canal) {
    estado.canal = canal;
    estado.sucursalSeleccionada = null;
    $$(".canal").forEach(b => {
      const activo = b.dataset.canal === canal;
      b.classList.toggle("activo", activo);
      b.setAttribute("aria-pressed", String(activo));
    });
    renderPosiciones();
    programarSincronizacionSucursales();
    if (canal === "sucursales") await cargarEstado(permisos.sincronizar);
  }

  async function abrirPosicion(mesaId) {
    if (estado.operando) return;
    if (modoTableta || !estado.operador) {
      if (modoTableta) {
        estado.mesaClaveTableta = String(mesaId);
        estado.errorClaveTableta = "";
      }
      const identificado = await entrarComoMesero({ mostrarPantalla: false });
      estado.mesaClaveTableta = "";
      if (!identificado) {
        renderPosiciones();
        return;
      }
    }
    bloquear(true);
    try {
      const datos = await api("/api/tickets/abrir/", {
        method: "POST",
        body: JSON.stringify({ mesa_id: mesaId, device_id: estado.deviceId }),
      });
      estado.ticket = datos.ticket;
      estado.persona = 1;
      estado.comandaVisible = numeroComandaActual(estado.ticket);
      mostrarTicket();
    } catch (error) {
      if (error.status === 423 && error.datos?.ticket) {
        estado.tickets[error.datos.ticket.mesa_id] = ticketResumenEstado(error.datos.ticket);
        renderPosiciones();
      }
      toast(error.message, true);
    } finally {
      bloquear(false);
      if (modoTableta && !estado.ticket) renderPosiciones();
    }
  }

  function mostrarTicket({ descartarBorradorClienteLlevar = false } = {}) {
    const ticket = estado.ticket;
    const esSucursal = ticket.canal === "sucursales";
    const editandoProgramado = esEdicionProgramada(ticket);
    document.body.classList.add("en-ticket");
    document.body.classList.toggle("edicion-programada", editandoProgramado);
    $("#vista-posiciones").classList.add("oculto");
    $("#vista-ticket").classList.remove("oculto");
    $("#vista-ticket").classList.toggle("ticket-sucursal", esSucursal);
    $("#ticket-mesa").textContent = editandoProgramado
      ? `Programado · ${ticket.mesa} · ${ticket.fecha_programada} ${ticket.hora_programada || ""}`.trim()
      : ticket.mesa;
    $("#ticket-folio").textContent = ticket.folio;
    $("#volver").setAttribute("aria-label", editandoProgramado ? "Guardar y volver a Programados" : "Volver");
    $("#volver").title = editandoProgramado ? "Guardar y volver a Programados" : "Volver";
    actualizarIndicadorBloqueoTicket();
    iniciarHeartbeatBloqueo();
    estado.comandaVisible = numeroComandaActual(ticket);
    estado.prefijoSalsa = "";
    estado.modoEntrega = ticket.tipo_entrega || "aproximada";
    estado.entregaProgramadaDigitos = (ticket.tipo_entrega === "programada" ? ticket.entrega_aproximada : "")?.replace(":", "") || "";
    $("#terminal").checked = Boolean(ticket.terminal);
    $("#paga-con").value = ticket.paga_con || "";
    $("#comentario").value = ticket.comentario_general || "";
    actualizarControlEntrega();
    const esDomicilio = ticket.canal === "domicilio";
    const esRecoger = ticket.canal === "recoger";
    const esLlevar = ticket.canal === "llevar";
    if (esLlevar) {
      clearTimeout(estado.temporizadorClienteLlevar);
      const ticketId = String(ticket.id);
      const nombreServidor = String(ticket.cliente?.nombre || "");
      const conservaBorrador = Boolean(
        !descartarBorradorClienteLlevar
        && ticketId === estado.clienteLlevarTicketId
        && estado.clienteLlevarBorrador !== estado.clienteLlevarGuardado
      );
      estado.clienteLlevarGuardado = nombreServidor;
      estado.clienteLlevarTicketId = ticketId;
      if (!conservaBorrador) {
        estado.clienteLlevarBorrador = nombreServidor;
        if (descartarBorradorClienteLlevar) estado.revisionClienteLlevar += 1;
      }
      ticket.cliente = {
        ...(ticket.cliente || {}),
        nombre: estado.clienteLlevarBorrador,
      };
      estado.errorClienteLlevar = null;
    } else {
      estado.clienteLlevarGuardado = "";
      estado.clienteLlevarBorrador = "";
      estado.clienteLlevarTicketId = "";
      estado.revisionClienteLlevar += 1;
      estado.errorClienteLlevar = null;
    }
    const esEntrega = esDomicilio || esRecoger;
    const esDirecto = esRecoger || esLlevar;
    $(".panel-orden").classList.toggle("con-domicilio", esEntrega || esDirecto);
    $("#datos-cliente").classList.toggle("oculto", !esDomicilio);
    $("#datos-servicio-directo").classList.toggle("oculto", !esRecoger);
    $("#pago-domicilio").classList.toggle("oculto", !esEntrega);
    $(".campo-entrega").classList.toggle("oculto", !esEntrega);
    $("#cliente-directo-telefono-label").classList.toggle("oculto", !esRecoger);
    $("#servicio-directo-eyebrow").textContent = esRecoger ? "Pedido para recoger" : "Pedido para llevar";
    $("#servicio-directo-titulo").textContent = esRecoger ? "Nombre y celular" : "Nombre del cliente";
    $("#cliente-directo-nombre").value = esDirecto ? (ticket.cliente?.nombre || "") : "";
    $("#cliente-directo-telefono").value = esRecoger ? (ticket.cliente?.telefono || "") : "";
    const admiteSwitches = !esSucursal && Object.hasOwn(canalesPareja, ticket.canal);
    $("#ticket-switches").classList.toggle("oculto", !admiteSwitches);
    $("#switch-tipo-pedido").checked = esRecoger || esLlevar;
    $("#switch-modo-nombres").checked = Boolean(ticket.captura_por_nombres);
    $("#switch-tipo-pedido").disabled = !comandaVisibleEditable(ticket) || editandoProgramado;
    $("#switch-modo-nombres").disabled = !comandaVisibleEditable(ticket);
    $("#tipo-pedido-etiqueta").textContent = esEntrega ? (esRecoger ? "Recoger" : "Domicilio") : (esLlevar ? "Llevar" : "Mesa");
    $("#modo-nombres-etiqueta").textContent = ticket.captura_por_nombres ? "Por nombres" : "Normal";
    $("#nombre-persona-panel").classList.toggle("oculto", esSucursal || !ticket.captura_por_nombres);
    $(".panel-personas").classList.toggle("oculto", esSucursal);
    $(".datos-orden").classList.toggle("oculto", esSucursal);
    $(".orden-encabezado .eyebrow").textContent = esSucursal ? "Pedido mayorista en tiempo real" : "Comanda en tiempo real";
    if (esDomicilio) {
      $("#buscar-cliente").value = "";
      renderClienteDomicilio();
      if (!ticket.cliente.id) buscarClientes("");
    }
    renderPersonas();
    estado.modoMenu = "productos";
    estado.objetivoModificador = { tipo: "persona", persona: estado.persona };
    estado.edicion = null;
    estado.colaEdicion = Promise.resolve();
    estado.errorEdicion = null;
    estado.ultimoTerminoPorProducto.clear();
    sincronizarControlesComandaVisible();
    renderMenu();
    renderComanda();
    renderAcciones();
    renderNavegadorComandas();
  }

  function renderPersonas() {
    if (estado.ticket?.canal === "sucursales") {
      $("#personas").innerHTML = "";
      return;
    }
    const ticketVisible = ticketParaComandaVisible();
    const nombres = ticketVisible?.nombres_comensales || {};
    const porNombres = Boolean(ticketVisible?.captura_por_nombres);
    $("#personas").classList.toggle("con-nombres", porNombres);
    $("#personas").innerHTML = Array.from({ length: 24 }, (_, i) => i + 1).map(numero => {
      const nombre = nombres[String(numero)] || "";
      return `<button class="persona ${estado.persona === numero ? "activa" : ""} ${nombre ? "con-nombre" : ""}" data-persona="${numero}" type="button"><b>${numero}</b>${porNombres ? `<small>${escapar(nombre || "Sin nombre")}</small>` : ""}</button>`;
    }).join("");
    actualizarNombrePersona();
  }

  function renderNavegadorComandas() {
    const navegador = $("#navegador-comandas");
    if (!navegador || !estado.ticket) return;
    const comandas = comandasDisponibles();
    const visible = normalizarComandaVisible();
    const indice = Math.max(0, comandas.findIndex(comanda => comanda.numero === visible));
    const comanda = comandas[indice];
    const contador = $("#contador-comandas");
    contador.textContent = `${indice + 1}/${comandas.length}`;
    contador.setAttribute(
      "aria-label",
      `Comanda ${indice + 1} de ${comandas.length}${comanda?.procesada ? ", procesada" : ", en edición"}`,
    );
    $("#comanda-anterior").disabled = estado.operando || bloqueoDeOtro() || indice <= 0;
    $("#comanda-siguiente").disabled = estado.operando || bloqueoDeOtro() || indice >= comandas.length - 1;
  }

  async function cambiarComandaVisible(direccion) {
    if (estado.operando || !estado.ticket) return;
    const comandas = comandasDisponibles();
    const visible = normalizarComandaVisible();
    const indice = comandas.findIndex(comanda => comanda.numero === visible);
    const destino = comandas[indice + direccion];
    if (!destino) return;
    if (!(await finalizarEdicion())) return;
    if (!(await guardarNombreClienteLlevar())) return;
    if (comandaVisibleEditable() && estado.ticket.captura_por_nombres) {
      clearTimeout(estado.temporizadorNombre);
      try { await guardarNombrePersona(); }
      catch (error) { toast(error.message, true); return; }
    }
    estado.comandaVisible = destino.numero;
    estado.modoMenu = "productos";
    estado.edicion = null;
    sincronizarControlesComandaVisible();
    renderPersonas();
    renderMenu();
    renderComanda();
    renderAcciones();
    renderNavegadorComandas();
  }

  function actualizarNombrePersona() {
    const panel = $("#nombre-persona-panel");
    if (!panel || !estado.ticket) return;
    const ticketVisible = ticketParaComandaVisible();
    panel.classList.toggle("oculto", !ticketVisible?.captura_por_nombres);
    $("#nombre-persona-numero").textContent = estado.persona;
    $("#nombre-persona").value = ticketVisible?.nombres_comensales?.[String(estado.persona)] || "";
  }

  async function guardarNombrePersona({ avanzar = false } = {}) {
    if (!estado.ticket?.captura_por_nombres || !comandaVisibleEditable()) return;
    const nombres = { ...(estado.ticket.nombres_comensales || {}) };
    const valor = $("#nombre-persona").value.trim();
    if (valor) nombres[String(estado.persona)] = valor;
    else delete nombres[String(estado.persona)];
    const datos = await api(`/api/tickets/${estado.ticket.id}/`, {
      method: "PATCH",
      body: JSON.stringify({ nombres_comensales: nombres }),
    });
    estado.ticket = datos.ticket;
    if (avanzar && estado.persona < 24) estado.persona += 1;
    renderPersonas();
    renderComanda();
    if (avanzar) $("#nombre-persona").focus();
  }

  async function convertirTipoPedido() {
    if (!estado.ticket || !(await finalizarEdicion())) return;
    if (!(await guardarNombreClienteLlevar())) return;
    const destino = canalesPareja[estado.ticket.canal];
    if (!destino) return;
    bloquear(true);
    try {
      await guardarDatos();
      const datos = await api(`/api/tickets/${estado.ticket.id}/convertir/`, {
        method: "POST",
        body: JSON.stringify({ canal: destino }),
      });
      estado.ticket = datos.ticket;
      mostrarTicket();
      await cargarEstado();
      toast(`Pedido cambiado a ${nombresCanal[destino]}.`);
    } catch (error) {
      $("#switch-tipo-pedido").checked = ["recoger", "llevar"].includes(estado.ticket.canal);
      toast(error.message, true);
    } finally { bloquear(false); }
  }

  async function alternarCapturaPorNombres() {
    if (!estado.ticket || !(await finalizarEdicion())) return;
    if (!(await guardarNombreClienteLlevar())) return;
    const activo = $("#switch-modo-nombres").checked;
    bloquear(true);
    try {
      if (estado.ticket.captura_por_nombres) {
        clearTimeout(estado.temporizadorNombre);
        await guardarNombrePersona();
      }
      const datos = await api(`/api/tickets/${estado.ticket.id}/`, {
        method: "PATCH",
        body: JSON.stringify({ captura_por_nombres: activo }),
      });
      estado.ticket = datos.ticket;
      mostrarTicket();
      if (activo) $("#nombre-persona").focus();
    } catch (error) {
      if (!error.requiereConfirmacion) $("#switch-modo-nombres").checked = !activo;
      toast(error.message, true);
    } finally { bloquear(false); }
  }

  function esPartidaPersonalizada(partida) {
    return Boolean(partida?.personalizado);
  }

  function nombrePartida(partida) {
    if (esPartidaPersonalizada(partida)) {
      return String(partida?.nombre || partida?.nombre_corto || "Producto personalizado");
    }
    return String(partida?.nombre_corto || partida?.nombre || "Producto");
  }

  function clavePartidaEdicion(partida) {
    return esPartidaPersonalizada(partida)
      ? `personalizado:${partida.id}`
      : `${partida.producto_id}:${partida.termino || "unico"}`;
  }

  async function abrirProductoPersonalizado() {
    if (!estado.ticket || !comandaVisibleEditable() || bloqueoDeOtro()) return;
    if (!(await finalizarEdicion())) return;
    const dialogo = $("#dialogo-producto-personalizado");
    const formulario = $("#form-producto-personalizado");
    formulario.reset();
    formulario.removeAttribute("aria-busy");
    $("#error-producto-personalizado").hidden = true;
    dialogo.showModal();
    requestAnimationFrame(() => $("#producto-personalizado-nombre").focus());
  }

  function cerrarProductoPersonalizado() {
    const dialogo = $("#dialogo-producto-personalizado");
    if (!dialogo.open || $("#form-producto-personalizado").getAttribute("aria-busy") === "true") return;
    dialogo.close();
  }

  async function guardarProductoPersonalizado() {
    const dialogo = $("#dialogo-producto-personalizado");
    const formulario = $("#form-producto-personalizado");
    const errorNodo = $("#error-producto-personalizado");
    const boton = $("#guardar-producto-personalizado");
    const nombre = $("#producto-personalizado-nombre").value.trim().replace(/\s+/g, " ");
    const precio = Number($("#producto-personalizado-precio").value);
    errorNodo.hidden = true;
    if (nombre.length < 2) {
      errorNodo.textContent = "Escribe un nombre de al menos 2 caracteres.";
      errorNodo.hidden = false;
      $("#producto-personalizado-nombre").focus();
      return;
    }
    if (!Number.isFinite(precio) || precio < 0.01 || precio > 999999.99) {
      errorNodo.textContent = "Captura un precio entre $0.01 y $999,999.99.";
      errorNodo.hidden = false;
      $("#producto-personalizado-precio").focus();
      return;
    }
    formulario.setAttribute("aria-busy", "true");
    boton.disabled = true;
    boton.textContent = "Agregando…";
    try {
      const datos = await api(`/api/tickets/${estado.ticket.id}/partidas/`, {
        method: "POST",
        body: JSON.stringify({
          personalizado: true,
          nombre_producto: nombre,
          precio_unitario: Math.round(precio * 100) / 100,
          cantidad: 1,
          comensal: estado.persona,
        }),
      });
      estado.ticket = datos.ticket;
      estado.modoMenu = "productos";
      dialogo.close();
      renderMenu();
      renderComanda();
      renderAcciones();
      toast(`${nombre} agregado. Toca su cantidad en la comanda para ajustarla.`);
    } catch (error) {
      errorNodo.textContent = `${error.message} Revisa los datos e intenta de nuevo.`;
      errorNodo.hidden = false;
    } finally {
      formulario.removeAttribute("aria-busy");
      boton.disabled = false;
      boton.textContent = "Agregar producto";
    }
  }

  function esBebida(partidaOProducto) {
    return String(partidaOProducto?.categoria || "").toLocaleLowerCase("es-MX") === "bebidas";
  }

  function bloqueComensales() {
    const inicio = Math.floor((estado.persona - 1) / 6) * 6 + 1;
    return { inicio, personas: Array.from({ length: 6 }, (_, indice) => inicio + indice) };
  }

  async function cambiarModoMenu(modo, objetivo = null) {
    if (modo !== "calculadora" && !(await finalizarEdicion())) return;
    estado.modoMenu = modo;
    if (objetivo) estado.objetivoModificador = objetivo;
    renderMenu();
    renderComanda();
  }

  function configurarAtajosMenu(nombresSecciones = []) {
    const contexto = $("#menu-contexto");
    const atajos = $("#atajos-menu");
    const mostrar = estado.modoMenu === "productos" && nombresSecciones.length > 0;
    if (contexto) contexto.hidden = mostrar;
    if (!atajos) return;
    atajos.hidden = !mostrar;
    if (!mostrar) return;
    atajos.innerHTML = nombresSecciones.slice(0, 7).map((nombre, indice) => {
      const numero = indice + 1;
      const etiqueta = `${numero}. ${nombre}`;
      return `<button data-menu-atajo="${numero}" type="button" aria-controls="menu-seccion-${numero}" aria-label="${escapar(`Ir a ${etiqueta}`)}" title="${escapar(etiqueta)}" ${numero === 1 ? 'aria-current="location"' : ""}>${numero}</button>`;
    }).join("");
  }

  function desplazarASeccionMenu(numero) {
    if (estado.modoMenu !== "productos") return;
    const contenedor = $("#productos");
    const destino = $(`#menu-seccion-${numero}`);
    if (!contenedor || !destino) return;
    const desplazamiento = destino.getBoundingClientRect().top
      - contenedor.getBoundingClientRect().top
      + contenedor.scrollTop;
    const comportamiento = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ? "auto" : "smooth";
    if (typeof contenedor.scrollTo === "function") {
      contenedor.scrollTo({ top: Math.max(0, desplazamiento), behavior: comportamiento });
    } else {
      contenedor.scrollTop = Math.max(0, desplazamiento);
    }
    $$("#atajos-menu [data-menu-atajo]").forEach(boton => {
      if (boton.dataset.menuAtajo === String(numero)) boton.setAttribute("aria-current", "location");
      else boton.removeAttribute("aria-current");
    });
  }

  function renderMenu() {
    const abierto = comandaVisibleEditable();
    const ticketVisible = ticketParaComandaVisible();
    const esSucursal = estado.ticket?.canal === "sucursales";
    const modoCalculadora = ["calculadora", "calculadora-sucursal"].includes(estado.modoMenu);
    configurarAtajosMenu();
    $(".panel-productos")?.classList.toggle("modo-calculadora", modoCalculadora);
    $("#productos")?.classList.toggle("modo-calculadora", modoCalculadora);
    const volverProductos = $("#menu-productos");
    volverProductos.classList.toggle("oculto", ["productos", "calculadora", "calculadora-sucursal"].includes(estado.modoMenu));
    if (estado.modoMenu === "calculadora" && estado.edicion?.personalizado) {
      $("#menu-contexto").textContent = "Cantidad del producto";
      $("#menu-indicacion").textContent = `Comensal ${estado.edicion.persona} · ${estado.edicion.nombreProducto}`;
      $("#productos").innerHTML = `
        <section class="calculadora-cantidad calculadora-personalizada">
          <div class="calculadora-producto">
            <strong>${escapar(estado.edicion.nombreProducto)}</strong>
            <span>${dinero(estado.edicion.precioUnitario)} c/u</span>
          </div>
          <output class="pantalla-cantidad" aria-label="Cantidad">${estado.edicion.cantidadPantalla}</output>
          <div class="teclado-cantidad">
            ${[7, 8, 9, 4, 5, 6, 1, 2, 3].map(numero => `<button data-tecla="${numero}" type="button">${numero}</button>`).join("")}
            <button class="borrar" data-tecla="borrar" type="button" aria-label="Borrar un dígito">←</button>
            <button data-tecla="0" type="button">0</button>
            <button class="eliminar" data-tecla="eliminar" type="button" aria-label="Eliminar producto">×</button>
          </div>
          <button class="confirmar-edicion" data-confirmar-edicion type="button" aria-label="Confirmar y salir" title="Confirmar y salir">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4 4L19 6"/></svg>
          </button>
        </section>`;
      return;
    }
    if (esSucursal) {
      if (estado.modoMenu === "calculadora-sucursal" && estado.edicion) {
        const producto = (estado.ticket.catalogo_sucursal || []).find(item => item.id === estado.edicion.productoId);
        if (!producto) return;
        $("#menu-contexto").textContent = "Cantidad del producto";
        $("#menu-indicacion").textContent = `${producto.unidad} · ${dinero(producto.precio)}${Number(producto.cantidad_por_precio) !== 1 ? ` cada ${cantidad(producto.cantidad_por_precio)} ${producto.unidad}` : ""}`;
        $("#productos").innerHTML = `
          <section class="calculadora-cantidad calculadora-sucursal">
            <div class="calculadora-producto"><strong>${escapar(producto.nombre)}</strong><span>${escapar(producto.unidad)}</span></div>
            <output class="pantalla-cantidad" aria-label="Cantidad">${escapar(estado.edicion.cantidadPantalla)}</output>
            <div class="teclado-cantidad teclado-cantidad-sucursal">
              ${[7, 8, 9, 4, 5, 6, 1, 2, 3].map(numero => `<button data-tecla-sucursal="${numero}" type="button">${numero}</button>`).join("")}
              <button data-tecla-sucursal="." type="button">.</button>
              <button data-tecla-sucursal="0" type="button">0</button>
              <button class="borrar" data-tecla-sucursal="borrar" type="button" aria-label="Borrar un dígito">←</button>
              <button class="eliminar" data-tecla-sucursal="eliminar" type="button">Eliminar producto</button>
            </div>
            <button class="confirmar-edicion" data-confirmar-sucursal type="button" aria-label="Guardar cantidad" title="Guardar cantidad">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4 4L19 6"/></svg>
            </button>
          </section>`;
        return;
      }
      $("#menu-contexto").textContent = "Productos de sucursal";
      $("#menu-indicacion").textContent = "Selecciona un producto y captura su cantidad";
      const capturados = new Set(ticketVisible.partidas.map(partida => partida.producto_sucursal_id));
      const catalogoSucursalHtml = (estado.ticket.catalogo_sucursal || []).map(producto => `
        <button class="producto producto-sucursal ${capturados.has(producto.id) ? "en-pedido" : ""}" data-sucursal-producto="${producto.id}" type="button" ${!abierto ? "disabled" : ""}>
          <small>${escapar(producto.nombre_ticket)} · ${escapar(producto.unidad)}</small>
          <strong>${escapar(producto.nombre)}</strong>
          <b>${dinero(producto.precio)}</b>
        </button>`).join("");
      const accesoPersonalizadoSucursal = `
        <button class="producto producto-sucursal" data-producto-personalizado type="button" aria-label="Producto personalizado. Capturar nombre y precio" ${!abierto ? "disabled" : ""}>
          <small>Venta fuera de catálogo</small>
          <strong>Producto personalizado</strong>
          <b>Nombre + precio</b>
        </button>`;
      $("#productos").innerHTML = `<section class="catalogo-sucursal">${catalogoSucursalHtml}${accesoPersonalizadoSucursal}</section>`;
      return;
    }
    if (estado.modoMenu === "calculadora" && estado.edicion) {
      const producto = productos.find(item => item.id === estado.edicion.productoId);
      if (!producto) return;
      const abreviatura = producto.permite_termino
        ? producto.abreviaturas_termino[estado.edicion.termino]
        : producto.corto;
      $("#menu-contexto").textContent = "Cantidad y término";
      $("#menu-indicacion").textContent = `Comensal ${estado.edicion.persona} · ${producto.nombre}`;
      const terminos = `
        <div class="selector-termino" aria-label="Término del producto">
          ${terminosPreparacion.map(termino =>
            `<button class="termino ${estado.edicion.termino === termino ? "activo" : ""}" data-termino="${termino}" type="button" ${!abierto || !producto.permite_termino ? "disabled" : ""}>${termino[0].toUpperCase()}${termino.slice(1)}</button>`
          ).join("")}
        </div>`;
      $("#productos").innerHTML = `
        <section class="calculadora-cantidad">
          ${terminos}
          <div class="calculadora-producto"><strong>${escapar(producto.nombre)}</strong><span>${escapar(abreviatura || producto.corto)}</span></div>
          <output class="pantalla-cantidad" aria-label="Cantidad">${estado.edicion.cantidadPantalla}</output>
          <div class="teclado-cantidad">
            ${[7, 8, 9, 4, 5, 6, 1, 2, 3].map(numero => `<button data-tecla="${numero}" type="button">${numero}</button>`).join("")}
            <button class="borrar" data-tecla="borrar" type="button" aria-label="Borrar un dígito">←</button>
            <button data-tecla="0" type="button">0</button>
            <button class="eliminar" data-tecla="eliminar" type="button" aria-label="Eliminar producto">×</button>
          </div>
          <button class="confirmar-edicion" data-confirmar-edicion type="button" aria-label="Confirmar y salir" title="Confirmar y salir">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4 4L19 6"/></svg>
          </button>
        </section>`;
      return;
    }
    if (estado.modoMenu === "entrega") {
      $("#menu-contexto").textContent = "Hora de entrega";
      $("#menu-indicacion").textContent = "Elige un tiempo aproximado o captura una hora programada";
      const minutos = Array.from({ length: 12 }, (_, indice) => (indice + 1) * 10);
      const etiquetaMinutos = valor => valor < 60 ? String(valor) : (valor === 60 ? "1 hr" : valor === 120 ? "2 hr" : `1:${String(valor - 60).padStart(2, "0")} hr`);
      const programada = String(estado.entregaProgramadaDigitos || "").padStart(4, "0");
      $("#productos").innerHTML = `
        <section class="selector-entrega-panel">
          <div class="selector-entrega-tipos">
            <button class="${estado.modoEntrega === "aproximada" ? "activo" : ""}" data-modo-entrega="aproximada" type="button">Aproximado</button>
            <button class="${estado.modoEntrega === "programada" ? "activo" : ""}" data-modo-entrega="programada" type="button">Programado</button>
          </div>
          ${estado.modoEntrega === "aproximada" ? `
            <div class="tiempos-aproximados">
              ${minutos.map(valor => `<button data-minutos-entrega="${valor}" type="button">${etiquetaMinutos(valor)}</button>`).join("")}
            </div>` : `
            <output class="pantalla-hora-programada">${programada.slice(0, 2)}:${programada.slice(2)}</output>
            <div class="teclado-hora-programada">
              ${[1, 2, 3, 4, 5, 6, 7, 8, 9].map(numero => `<button data-tecla-entrega="${numero}" type="button">${numero}</button>`).join("")}
              <button class="borrar" data-tecla-entrega="borrar" type="button">←</button>
              <button data-tecla-entrega="0" type="button">0</button>
              <button class="confirmar" data-confirmar-entrega type="button">✓</button>
            </div>`}
        </section>`;
      return;
    }
    if (estado.modoMenu === "modificadores") {
      const esGeneral = estado.objetivoModificador.tipo === "grupo";
      const objetivo = esGeneral ? "toda la orden" : `comensal ${estado.objetivoModificador.persona}`;
      const opciones = esGeneral ? comentariosGenerales : modificadores;
      $("#menu-contexto").textContent = "Preparación";
      $("#menu-indicacion").textContent = `Aplicar a ${objetivo}`;
      const botonesComentarios = opciones.map(modificador => {
        const activo = esGeneral
          ? (estado.ticket.comentarios_generales || []).some(item => item.codigo === modificador.codigo)
          : ticketVisible.modificadores.some(item => item.comensal === estado.objetivoModificador.persona && item.codigo === modificador.codigo);
        return `<button class="producto opcion-preparacion ${activo ? "seleccionada" : ""}" data-tipo-comentario="${esGeneral ? "general" : "particular"}" data-codigo="${modificador.codigo}" data-nombre="${modificador.nombre}" type="button" ${!abierto ? "disabled" : ""}>
          <small>${modificador.codigo}</small><strong>${modificador.nombre}</strong>
        </button>`
      }).join("");
      $("#productos").innerHTML = `<section class="comentarios-grid ${esGeneral ? "generales" : "particulares"}">${botonesComentarios}</section>`;
      return;
    }
    if (estado.modoMenu === "salsas") {
      $("#menu-contexto").textContent = "Salsas y verduras";
      $("#menu-indicacion").textContent = "Agrupa opciones con + Más o Nada más";
      const grupos = estado.ticket.salsas_verduras || [];
      const seleccionadas = new Set(grupos.find(grupo => grupo.prefijo === estado.prefijoSalsa)?.elementos || []);
      $("#productos").innerHTML = `
        <section class="selector-salsas">
          <div class="prefijos-salsas">
            ${prefijosSalsas.map(prefijo => `<button class="${estado.prefijoSalsa === prefijo ? "activo" : ""}" data-prefijo-salsa="${escapar(prefijo)}" type="button">${prefijo || "Selección normal"}</button>`).join("")}
          </div>
          <div class="opciones-salsas">
            ${opcionesSalsas.map(opcion => `<button class="${seleccionadas.has(opcion) ? "activo" : ""}" data-opcion-salsa="${escapar(opcion)}" type="button">${escapar(opcion)}</button>`).join("")}
          </div>
        </section>`;
      return;
    }
    const soloBebidas = estado.modoMenu === "bebidas";
    const disponibles = soloBebidas ? productos.filter(esBebida) : productos;
    $("#menu-contexto").textContent = soloBebidas ? "Bebidas" : "Menú completo";
    $("#menu-indicacion").textContent = soloBebidas
      ? "Cada toque suma una bebida y abre la calculadora"
      : "";
    const segmentos = new Map();
    for (const producto of disponibles) {
      if (!segmentos.has(producto.categoria)) segmentos.set(producto.categoria, []);
      segmentos.get(producto.categoria).push(producto);
    }
    const secciones = [...segmentos.entries()];
    const accesoPersonalizado = soloBebidas ? "" : `
      <section class="segmento-menu" aria-labelledby="menu-seccion-personalizado">
        <h3 id="menu-seccion-personalizado"><span>PERSONALIZABLE</span></h3>
        <div class="segmento-productos">
          <button class="producto producto-menu" data-producto-personalizado type="button" aria-label="Producto personalizado. Capturar nombre y precio" ${!abierto ? "disabled" : ""}>
            <span class="producto-imagen producto-imagen-vacia" aria-hidden="true"><span><b>PP</b><small>Manual</small></span></span>
            <span class="producto-copy"><small>PP</small><strong>Producto personalizado</strong></span>
          </button>
        </div>
      </section>`;
    const seccionesHtml = secciones.map(([segmento, items], indice) => {
      const numeroSeccion = soloBebidas ? "" : indice + 1;
      const idSeccion = numeroSeccion ? `menu-seccion-${numeroSeccion}` : "";
      const idTitulo = numeroSeccion ? `menu-seccion-titulo-${numeroSeccion}` : "";
      return `
      <section class="segmento-menu" ${idSeccion ? `id="${idSeccion}" data-menu-seccion="${numeroSeccion}" aria-labelledby="${idTitulo}"` : ""}>
        ${soloBebidas ? "" : `<h3 id="${idTitulo}"><span>${escapar(segmento.toLocaleUpperCase("es-MX"))}</span></h3>`}
        <div class="segmento-productos">
          ${items.map(producto => `<button class="producto producto-menu ${producto.es_promocion ? "promocion-menu" : ""} ${producto.es_promocion && !producto.disponible_hoy ? "no-disponible-hoy" : ""}" data-id="${producto.id}" type="button" aria-label="${escapar(`${producto.nombre}. Abreviatura ${producto.corto}`)}" ${!abierto || !producto.disponible_hoy ? "disabled" : ""}>
            ${producto.imagen_url
              ? `<span class="producto-imagen" data-abreviatura="${escapar(producto.corto)}"><img src="${escapar(producto.imagen_url)}" alt="" width="320" height="240" loading="lazy" decoding="async"></span>`
              : `<span class="producto-imagen producto-imagen-vacia" aria-hidden="true"><span><b>${escapar(producto.corto)}</b><small>Sin foto</small></span></span>`}
            <span class="producto-copy"><small>${escapar(producto.es_promocion ? `${producto.corto} · ${producto.promocion_dias}` : producto.corto)}</small><strong>${escapar(producto.nombre)}</strong></span>
          </button>`).join("")}
        </div>
      </section>`;
    }).join("");
    $("#productos").innerHTML = (seccionesHtml + accesoPersonalizado)
      || '<div class="vacio">No hay opciones disponibles.</div>';
    configurarAtajosMenu(soloBebidas ? [] : secciones.map(([nombre]) => nombre));
  }

  function formatoFechaComanda(valor) {
    const fecha = new Date(valor);
    if (Number.isNaN(fecha.getTime())) return "";
    return `${fecha.toLocaleDateString("es-MX", { day: "2-digit", month: "2-digit", year: "numeric" })} ${fecha.toLocaleTimeString("es-MX", { hour: "numeric", minute: "2-digit" })}`;
  }

  function textoEntregaComanda(ticket) {
    if (!ticket.entrega_aproximada) return "Sin hora de entrega";
    if (ticket.fecha_programada && ticket.hora_programada) {
      const [anio, mes, dia] = ticket.fecha_programada.split("-");
      return `Programado: ${dia}/${mes}/${anio} ${ticket.hora_programada}`;
    }
    if (ticket.tipo_entrega === "programada") return `Programado: ${ticket.entrega_aproximada}`;
    const tomada = new Date(ticket.creado_en).toLocaleTimeString("es-MX", { hour: "numeric", minute: "2-digit" });
    return `${tomada} - ${ticket.entrega_aproximada}`;
  }

  function textoSalsas(grupos) {
    return (grupos || []).map(grupo => `${grupo.prefijo ? `${grupo.prefijo} ` : ""}${grupo.elementos.join(", ")}`).join(" * ");
  }

  function encabezadoComandaPreview(ticket) {
    if (["domicilio", "recoger"].includes(ticket.canal)) {
      const prefijo = ticket.canal === "recoger" ? "R" : "";
      return `
        <div><span>${formatoFechaComanda(ticket.creado_en).split(" ")[0]}</span><strong>Ticket: ${ticket.folio}, ${prefijo}${ticket.posicion_numero}</strong></div>
        <div><span>${escapar(textoEntregaComanda(ticket))}</span><strong class="comanda-total">${dinero(ticket.total)}</strong></div>
        ${ticket.cliente?.nombre ? `<b class="comanda-cliente-directo">${escapar(ticket.cliente.nombre)}${ticket.canal === "recoger" && ticket.cliente.telefono ? ` · ${escapar(ticket.cliente.telefono)}` : ""}</b>` : ""}`;
    }
    if (["comedor", "llevar"].includes(ticket.canal)) {
      const clienteLlevarEditable = comandaVisibleEditable() && !bloqueoDeOtro() && !estado.operando;
      const nombreClienteLlevar = String(ticket.id) === estado.clienteLlevarTicketId
        ? estado.clienteLlevarBorrador
        : String(ticket.cliente?.nombre || "");
      const clienteLlevarSinGuardar = (
        String(ticket.id) === estado.clienteLlevarTicketId
        && nombreClienteLlevar !== estado.clienteLlevarGuardado
      );
      const estadoClienteLlevar = !clienteLlevarEditable
        ? { texto: "Sólo lectura", clase: "lectura" }
        : estado.errorClienteLlevar
          ? { texto: "Sin guardar · Presiona Enter para reintentar", clase: "error" }
          : clienteLlevarSinGuardar
            ? { texto: "Pendiente de guardar", clase: "pendiente" }
            : { texto: "Guardado", clase: "guardado" };
      const clienteLlevar = ticket.canal === "llevar"
        ? `<label class="comanda-cliente-llevar"><span>Cliente</span><input data-cliente-llevar maxlength="180" autocomplete="name" aria-label="Nombre del cliente para llevar" value="${escapar(nombreClienteLlevar)}" placeholder="Nombre para la comanda" ${clienteLlevarEditable ? "" : "disabled"}><small data-estado-cliente-llevar class="${estadoClienteLlevar.clase}" role="status" aria-live="polite">${estadoClienteLlevar.texto}</small></label>`
        : "";
      return `
        <div><span>${formatoFechaComanda(ticket.creado_en)}</span><strong>${escapar(ticket.mesa)}</strong></div>
        <div><strong>Ticket: ${ticket.folio}</strong><strong class="comanda-total">${dinero(ticket.total)}</strong></div>
        ${clienteLlevar}`;
    }
    return `<div><span>${formatoFechaComanda(ticket.creado_en)}</span><strong>Ticket: ${ticket.folio}</strong></div><b class="comanda-total">${dinero(ticket.total)}</b>`;
  }

  function renderComandaPorNombres(ticket, contenedor) {
    const {
      principales,
      barbacoa,
      complementosGlobales,
    } = clasificarPartidasPorNombres(ticket.partidas, esBebida);
    const columnas = [];
    const indiceColumnas = new Map();
    for (const partida of principales) {
      const clave = clavePartidaEdicion(partida);
      if (!indiceColumnas.has(clave)) {
        indiceColumnas.set(clave, columnas.length);
        columnas.push({
          clave,
          productoId: partida.producto_id,
          partidaId: partida.id,
          personalizado: esPartidaPersonalizada(partida),
          termino: partida.termino || "",
          nombre: nombrePartida(partida),
          cantidades: new Map(),
        });
      }
      const columna = columnas[indiceColumnas.get(clave)];
      if (!columna.cantidades.has(partida.comensal)) columna.cantidades.set(partida.comensal, 0);
      columna.cantidades.set(partida.comensal, columna.cantidades.get(partida.comensal) + Number(partida.cantidad));
    }
    while (columnas.length < 4) columnas.push(null);
    const nombres = ticket.nombres_comensales || {};
    const personasUsadas = new Set(
      [...principales, ...barbacoa].map(partida => partida.comensal),
    );
    Object.keys(nombres).forEach(numero => personasUsadas.add(Number(numero)));
    if (!personasUsadas.size) personasUsadas.add(estado.persona);
    const personas = [...personasUsadas].sort((a, b) => a - b);
    const preparacion = new Map(personas.map(persona => [persona, ticket.modificadores.filter(mod => mod.comensal === persona).map(mod => mod.codigo).join(" ")]));
    const filas = personas.map(persona => {
      const celdas = columnas.slice(0, 4).map(columna => {
        if (!columna) return '<span class="comanda-nombre-celda vacia"></span>';
        const valor = columna.cantidades.get(persona) || 0;
        const seleccionada = estado.edicion?.clave === columna.clave && estado.edicion?.persona === persona;
        const texto = seleccionada ? estado.edicion.cantidadPantalla : (valor ? cantidad(valor) : "");
        if (columna.personalizado && !valor && !seleccionada) return '<span class="comanda-nombre-celda vacia"></span>';
        const atributoPartida = columna.personalizado
          ? `data-celda-personalizada="${columna.partidaId}"`
          : `data-celda-producto="${columna.productoId}" data-celda-termino="${columna.termino}"`;
        return `<button class="comanda-nombre-celda cantidad-celda ${claseCantidad(seleccionada ? estado.edicion.cantidadPantalla : valor)} ${seleccionada ? "seleccionada" : ""}" ${atributoPartida} data-celda-persona="${persona}" data-celda-clave="${columna.clave}" type="button">${texto}</button>`;
      }).join("");
      return `<button class="comanda-nombre-persona" data-seleccionar-persona="${persona}" type="button"><b>${persona}. ${escapar(nombres[String(persona)] || "SIN NOMBRE")}</b><small>${escapar(preparacion.get(persona) || "")}</small></button>${celdas}`;
    }).join("");
    const barbacoaPorPersona = new Map();
    for (const partida of barbacoa) {
      const persona = Number(partida.comensal);
      if (!barbacoaPorPersona.has(persona)) barbacoaPorPersona.set(persona, new Map());
      const conceptos = barbacoaPorPersona.get(persona);
      const clave = clavePartidaEdicion(partida);
      if (!conceptos.has(clave)) {
        conceptos.set(clave, {
          clave,
          productoId: partida.producto_id,
          termino: partida.termino || "",
          nombre: nombrePartida(partida),
          cantidad: 0,
        });
      }
      conceptos.get(clave).cantidad += Number(partida.cantidad);
    }
    const filasBarbacoa = [...barbacoaPorPersona.entries()]
      .sort(([personaA], [personaB]) => personaA - personaB)
      .map(([persona, conceptos]) => {
        const botones = [...conceptos.values()].map(item => {
          const seleccionada = estado.edicion?.clave === item.clave && estado.edicion?.persona === persona;
          const valor = seleccionada ? estado.edicion.cantidadPantalla : item.cantidad;
          return `<button class="comanda-barbacoa-concepto ${seleccionada ? "seleccionado" : ""}" data-celda-producto="${item.productoId}" data-celda-persona="${persona}" data-celda-termino="${escapar(item.termino)}" type="button"><b>${cantidad(valor)}</b><span>${escapar(item.nombre)}</span></button>`;
        }).join("");
        return `
          <div class="comanda-barbacoa-fila">
            <button class="comanda-barbacoa-persona" data-seleccionar-persona="${persona}" type="button">${persona}. ${escapar(nombres[String(persona)] || `PERSONA ${persona}`)}</button>
            <div class="comanda-barbacoa-conceptos">${botones}</div>
          </div>`;
      })
      .join("");
    const complementos = new Map();
    for (const partida of complementosGlobales) {
      const clave = String(partida.producto_id);
      if (!complementos.has(clave)) {
        complementos.set(clave, { nombre: nombrePartida(partida), cantidad: 0 });
      }
      complementos.get(clave).cantidad += Number(partida.cantidad);
    }
    const extras = [...complementos.values()]
      .map(item => `${item.cantidad > 1 ? `${cantidad(item.cantidad)} ` : ""}${escapar(item.nombre)}`)
      .join(" · ");
    const generalTexto = (ticket.comentarios_generales || []).map(item => item.nombre).join(" · ");
    const salsasTexto = textoSalsas(ticket.salsas_verduras);
    $("#orden-resumen").textContent = `${ticket.partidas.length} ${ticket.partidas.length === 1 ? "partida" : "partidas"}`;
    contenedor.innerHTML = `
      <section class="comanda-papel comanda-por-nombres">
        <header class="comanda-papel-encabezado">
          <em>Los Tocayos Tacos de Barbacoa</em>
          ${encabezadoComandaPreview(ticket)}
        </header>
        <button class="comanda-global ${generalTexto ? "seleccionado" : ""}" data-objetivo-modificador="grupo" data-inicio="1" type="button">${escapar(generalTexto || "Comentario General")}</button>
        <div class="comanda-matriz-nombres">
          <span class="encabezado-nombre">Nombre</span>
          ${columnas.slice(0, 4).map(columna => `<span class="encabezado-producto">${columna ? escapar(columna.nombre) : "PRODUCTO"}</span>`).join("")}
          ${filas}
        </div>
        ${filasBarbacoa ? `
          <section class="comanda-barbacoa-nombres" aria-label="Barbacoa por persona">
            <strong class="comanda-barbacoa-titulo">Barbacoa</strong>
            ${filasBarbacoa}
          </section>` : ""}
        <button class="comanda-extras-nombres" data-modo-menu="bebidas" type="button"><strong>Consomés y bebidas</strong><span>${extras || "Sin complementos"}</span></button>
        <button class="comanda-salsas" data-modo-menu="salsas" type="button"><strong>Salsas y verduras</strong><span>${escapar(salsasTexto || "Toca aquí para elegir salsas y verduras")}</span></button>
        ${ticket.comentario_general ? `<p class="comanda-comentario-general">${escapar(ticket.comentario_general)}</p>` : ""}
        ${ticket.terminal && ["domicilio", "recoger"].includes(ticket.canal) ? '<strong class="comanda-terminal">PAGO: TERMINAL</strong>' : ""}
      </section>`;
  }

  function renderPedidoSucursal(ticket, contenedor) {
    const abierto = comandaVisibleEditable(ticket);
    const filas = [...ticket.partidas].sort((a, b) => a.orden - b.orden).map(partida => {
      const personalizada = esPartidaPersonalizada(partida);
      const atributos = personalizada
        ? `data-partida-personalizada="${partida.id}"`
        : `data-partida-sucursal="${partida.id}" data-producto-sucursal="${partida.producto_sucursal_id}"`;
      return `
      <button class="fila-partida-sucursal ${personalizada ? "fila-personalizada" : ""} ${estado.edicion?.partidaId === partida.id ? "seleccionada" : ""}" ${atributos} type="button" ${!abierto ? "disabled" : ""}>
        <span class="concepto"><strong>${escapar(partida.nombre_catalogo || nombrePartida(partida))}</strong><small>${personalizada ? "Producto personalizado" : escapar(partida.nombre)}</small></span>
        <span><b>${cantidad(partida.cantidad)}</b><small>${escapar(partida.unidad || "pza")}</small></span>
        <span><small>Precio</small>${dinero(partida.precio)}</span>
        <span><small>Importe</small><b>${dinero(partida.importe)}</b></span>
      </button>`;
    }).join("");
    $("#orden-resumen").textContent = `${ticket.partidas.length} ${ticket.partidas.length === 1 ? "producto" : "productos"} · ${dinero(ticket.total)}`;
    contenedor.innerHTML = `
      <section class="pedido-sucursal-lista">
        <header>
          <div><small>Sucursal / cliente</small><strong>${escapar(ticket.cliente_sucursal.nombre)}</strong></div>
          <div><small>Pedido</small><strong>${escapar(ticket.mesa)}</strong></div>
          <div><small>Ticket</small><strong>${ticket.folio}</strong></div>
        </header>
        <div class="encabezado-lista-sucursal"><span>Concepto</span><span>Cantidad</span><span>Precio</span><span>Importe</span></div>
        <div class="partidas-sucursal">${filas || '<p class="vacio">Selecciona productos para comenzar el pedido.</p>'}</div>
        <footer><span>Total del pedido</span><strong class="comanda-total">${dinero(ticket.total)}</strong></footer>
      </section>`;
  }

  function renderComanda() {
    const ticket = ticketParaComandaVisible();
    const contenedor = $("#comanda-papel-preview");
    if (!ticket || !contenedor) return;
    if (ticket.canal === "sucursales") {
      renderPedidoSucursal(ticket, contenedor);
      return;
    }
    if (ticket.captura_por_nombres) {
      renderComandaPorNombres(ticket, contenedor);
      return;
    }
    const { inicio, personas } = bloqueComensales();
    const promocionesTicket = ticket.partidas.filter(partida => partida.es_promocion && !partida.promocion_id);
    const partidasComida = ticket.partidas.filter(partida => !esBebida(partida) && !partida.es_promocion);
    const filas = new Map();
    for (const partida of partidasComida) {
      const clave = clavePartidaEdicion(partida);
      if (!filas.has(clave)) {
        filas.set(clave, {
          clave,
          productoId: partida.producto_id,
          partidaId: partida.id,
          personalizado: esPartidaPersonalizada(partida),
          termino: partida.termino || "",
          nombre: nombrePartida(partida),
          cantidades: new Map(),
        });
      }
      const fila = filas.get(clave);
      if (!fila.cantidades.has(partida.comensal)) fila.cantidades.set(partida.comensal, { cantidad: 0, ids: [] });
      const celda = fila.cantidades.get(partida.comensal);
      celda.cantidad += Number(partida.cantidad);
      celda.ids.push(partida.id);
    }
    const promociones = new Map();
    for (const partida of promocionesTicket) {
      if (!promociones.has(partida.producto_id)) {
        promociones.set(partida.producto_id, { productoId: partida.producto_id, nombre: partida.codigo, cantidades: new Map() });
      }
      const cantidades = promociones.get(partida.producto_id).cantidades;
      if (!cantidades.has(partida.comensal)) cantidades.set(partida.comensal, { cantidad: 0, ids: [] });
      cantidades.get(partida.comensal).cantidad += Number(partida.cantidad);
      cantidades.get(partida.comensal).ids.push(partida.id);
    }
    const preparacion = new Map(personas.map(persona => [persona, ticket.modificadores.filter(mod => mod.comensal === persona)]));
    const bebidas = new Map();
    for (const partida of ticket.partidas.filter(esBebida)) {
      if (!bebidas.has(partida.producto_id)) bebidas.set(partida.producto_id, { nombre: partida.nombre_corto, cantidad: 0 });
      bebidas.get(partida.producto_id).cantidad += Number(partida.cantidad);
    }
    const filasHtml = [...filas.values()].map(fila => {
      const celdas = personas.map(persona => {
        const celda = fila.cantidades.get(persona);
        const seleccionada = estado.edicion?.clave === fila.clave && estado.edicion?.persona === persona;
        const valor = seleccionada ? estado.edicion.cantidadPantalla : (celda?.cantidad || 0);
        const texto = valor || seleccionada ? cantidad(valor) : "";
        if (fila.personalizado && !celda && !seleccionada) return '<span class="comanda-celda cantidad-celda vacia"></span>';
        const atributoPartida = fila.personalizado
          ? `data-celda-personalizada="${fila.partidaId}"`
          : `data-celda-producto="${fila.productoId}" data-celda-termino="${fila.termino}"`;
        return `<button class="comanda-celda cantidad-celda ${claseCantidad(valor)} ${seleccionada ? "seleccionada" : ""}" ${atributoPartida} data-celda-persona="${persona}" data-celda-clave="${fila.clave}" type="button">${texto}</button>`;
      }).join("");
      return `<button class="comanda-etiqueta producto-zona ${fila.personalizado ? "etiqueta-personalizada" : ""}" data-modo-menu="productos" type="button">${escapar(fila.nombre)}</button>${celdas}`;
    }).join("");
    const promocionesHtml = [...promociones.values()].map(fila => {
      const celdas = personas.map(persona => {
        const celda = fila.cantidades.get(persona);
        const valor = celda ? `${celda.cantidad > 1 ? `${cantidad(celda.cantidad)} ` : ""}P` : "";
        return `<button class="comanda-celda promocion-celda" data-promocion-producto="${fila.productoId}" data-promocion-persona="${persona}" type="button">${valor}</button>`;
      }).join("");
      return `<span class="comanda-etiqueta promocion-etiqueta">${escapar(fila.nombre)}</span>${celdas}`;
    }).join("");
    const filasProductos = promocionesHtml + filasHtml || `<button class="comanda-vacio producto-zona" data-modo-menu="productos" type="button">Toca aquí para mostrar productos y comenzar la orden</button>`;
    const bebidasHtml = [...bebidas.values()].map(bebida =>
      `${bebida.cantidad >= 2 ? `<strong>( ${cantidad(bebida.cantidad)} )</strong> ` : ""}${escapar(bebida.nombre)}`
    ).join('<b class="separador-bebida">* </b>');
    const generales = ticket.comentarios_generales || [];
    const generalTexto = generales.map(item => item.nombre).join(" · ");
    const salsasTexto = textoSalsas(ticket.salsas_verduras);
    const encabezado = encabezadoComandaPreview(ticket);
    $("#orden-resumen").textContent = `${ticket.partidas.length} ${ticket.partidas.length === 1 ? "partida" : "partidas"}`;
    contenedor.innerHTML = `
      <section class="comanda-papel">
        <header class="comanda-papel-encabezado">
          <em>Los Tocayos Tacos de Barbacoa</em>
          ${encabezado}
        </header>
        <div class="comanda-matriz">
          <span class="comanda-etiqueta encabezado">Comensal</span>
          ${personas.map(persona => `<button class="comanda-numero ${estado.persona === persona ? "activo" : ""}" data-seleccionar-persona="${persona}" type="button">${persona}</button>`).join("")}
          <button class="comanda-global ${generalTexto ? "seleccionado" : ""}" data-objetivo-modificador="grupo" data-inicio="${inicio}" type="button">${escapar(generalTexto || "Comentario General")}</button>
          ${generalTexto ? "" : `
            <span class="comanda-etiqueta encabezado">Prep.</span>
            ${personas.map(persona => {
              const texto = preparacion.get(persona).map(mod => mod.codigo).join(" ") || "+";
              return `<button class="comanda-comentario ${estado.persona === persona ? "activo" : ""}" data-objetivo-modificador="persona" data-persona="${persona}" type="button">${escapar(texto)}</button>`;
            }).join("")}`}
          ${filasProductos}
        </div>
        <button class="comanda-bebidas" data-modo-menu="bebidas" type="button">
          <strong>Bebidas</strong>
          <span>${bebidasHtml || "Toca aquí para elegir bebidas"}</span>
        </button>
        <button class="comanda-salsas" data-modo-menu="salsas" type="button">
          <strong>Salsas y verduras</strong>
          <span>${escapar(salsasTexto || "Toca aquí para elegir salsas y verduras")}</span>
        </button>
        ${ticket.comentario_general ? `<p class="comanda-comentario-general">${escapar(ticket.comentario_general)}</p>` : ""}
        ${ticket.terminal && ["domicilio", "recoger"].includes(ticket.canal) ? '<strong class="comanda-terminal">PAGO: TERMINAL</strong>' : ""}
      </section>`;
  }

  function renderClienteDomicilio(ticket = ticketParaComandaVisible()) {
    const cliente = ticket?.cliente || {};
    const seleccionado = Boolean(cliente.id);
    $("#cliente-buscador").classList.toggle("oculto", seleccionado);
    $("#resultados-clientes").classList.toggle("oculto", seleccionado);
    $("#cliente-seleccionado").classList.toggle("oculto", !seleccionado);
    if (!seleccionado) {
      renderResultadosClientes();
      return;
    }
    const contacto = [
      cliente.telefono || "Sin teléfono",
      cliente.domicilio || "Sin domicilio",
    ].join(" · ");
    $("#cliente-seleccionado").innerHTML = `
      <div class="cliente-resultado-cabecera">
        <strong>${escapar(cliente.nombre)}</strong>
        <span class="cliente-clave">${escapar(cliente.clave_corta)}</span>
      </div>
      <p>${escapar(contacto)}</p>
      ${cliente.referencia ? `<p>Referencia: ${escapar(cliente.referencia)}</p>` : ""}
      ${cliente.notas ? `<p class="cliente-nota-interna"><b>Nota interna:</b> ${escapar(cliente.notas)}</p>` : ""}
      ${cliente.comentarios_multiples ? `
        <div class="contacto-pedido">
          <p class="eyebrow">Contacto para este pedido</p>
          <label>Nombre<input id="contacto-pedido-nombre" value="${escapar(cliente.contacto_pedido_nombre || "")}" autocomplete="off" required></label>
          <label>Celular<input id="contacto-pedido-telefono" value="${escapar(cliente.contacto_pedido_telefono || "")}" inputmode="tel" autocomplete="off" required></label>
          <small>Se solicita de nuevo al levantar cada orden para esta empresa.</small>
        </div>` : ""}
      <div class="cliente-seleccionado-acciones">
        <button class="boton secundario" data-editar-cliente type="button">Editar datos</button>
        <button class="boton enlace" data-cambiar-cliente type="button">Cambiar cliente</button>
      </div>`;
  }

  function renderResultadosClientes() {
    const contenedor = $("#resultados-clientes");
    const consulta = $("#buscar-cliente")?.value.trim() || "";
    if (!estado.resultadosClientes.length) {
      contenedor.innerHTML = `<p class="cliente-sin-resultados">${consulta ? "No se encontraron coincidencias. Puedes registrar un cliente nuevo." : "Aún no hay clientes recientes."}</p>`;
      return;
    }
    contenedor.innerHTML = estado.resultadosClientes.map((resultado, indice) => {
      const telefono = resultado.telefono?.numero || "Sin teléfono";
      const domicilio = resultado.domicilio?.texto || "Sin domicilio";
      const observacion = resultado.comentarios_multiples
        ? `${resultado.motivo} · solicita contacto`
        : resultado.motivo;
      return `<button class="cliente-resultado" data-seleccionar-cliente="${indice}" type="button">
        <span class="cliente-resultado-cabecera"><strong>${escapar(resultado.nombre)}</strong><span class="cliente-clave">${escapar(resultado.clave_corta)}</span></span>
        <p>${escapar(telefono)} · ${escapar(domicilio)}</p>
        <small>${escapar(observacion)}</small>
      </button>`;
    }).join("");
  }

  async function buscarClientes(consulta) {
    const token = ++estado.tokenBusquedaCliente;
    try {
      const datos = await api("/api/clientes/buscar/", {
        method: "POST",
        body: JSON.stringify({ q: consulta, limite: 12 }),
      });
      if (token !== estado.tokenBusquedaCliente || estado.ticket?.canal !== "domicilio") return;
      estado.resultadosClientes = datos.resultados;
      renderResultadosClientes();
    } catch (error) {
      if (token === estado.tokenBusquedaCliente) {
        estado.resultadosClientes = [];
        renderResultadosClientes();
        toast(error.message, true);
      }
    }
  }

  function renderDirectorio() {
    const lista = $("#directorio-lista");
    const estadoVacio = $("#directorio-estado");
    const cargarMas = $("#directorio-cargar-mas");
    const conteo = $("#directorio-conteo");
    if (!lista || !estadoVacio || !cargarMas || !conteo) return;
    const visibles = estado.directorioResultados.length;
    const total = Number.isFinite(Number(estado.directorioTotal))
      ? Number(estado.directorioTotal)
      : null;
    conteo.textContent = total === null
      ? visibles + (visibles === 1 ? " cliente visible" : " clientes visibles")
      : visibles + " de " + total + (total === 1 ? " cliente" : " clientes");
    lista.setAttribute("aria-busy", String(estado.directorioCargando));
    if (!visibles) {
      lista.innerHTML = "";
      estadoVacio.textContent = estado.directorioError || (estado.directorioCargando
        ? "Buscando clientes…"
        : (estado.directorioConsulta
          ? "No hay coincidencias. Prueba con otro dato del registro."
          : "El directorio todavía no tiene clientes."));
      estadoVacio.hidden = false;
    } else {
      estadoVacio.textContent = estado.directorioError;
      estadoVacio.hidden = !estado.directorioError;
      lista.innerHTML = estado.directorioResultados.map((cliente, indice) => {
        const telefono = cliente.telefono?.numero
          || cliente.telefonos?.[0]?.numero
          || "Sin teléfono";
        const domicilio = cliente.domicilio?.texto
          || cliente.domicilios?.[0]?.texto
          || "Sin domicilio";
        const id = cliente.cliente_id || cliente.id || "";
        return '<article class="directorio-cliente" data-directorio-indice="' + indice + '">' +
          '<div class="directorio-cliente-identidad"><strong>' + escapar(cliente.nombre || "Cliente") + '</strong>' +
          '<span>' + escapar(cliente.clave_corta || "Sin clave") + '</span></div>' +
          '<p><b>' + escapar(telefono) + '</b><span>' + escapar(domicilio) + '</span></p>' +
          '<button class="boton secundario" data-editar-cliente-directorio="' + escapar(id) + '" type="button" aria-label="Editar a ' + escapar(cliente.nombre || "cliente") + '">Editar</button>' +
        '</article>';
      }).join("");
    }
    cargarMas.hidden = !estado.directorioHayMas;
    cargarMas.disabled = estado.directorioCargando;
    cargarMas.toggleAttribute("aria-busy", estado.directorioCargando);
  }

  async function cargarDirectorio({ reiniciar = false } = {}) {
    if (!$("#directorio-lista") || (estado.directorioCargando && !reiniciar)) return;
    if (reiniciar) {
      estado.directorioConsulta = $("#directorio-buscar").value.trim();
      estado.directorioPagina = 0;
      estado.directorioResultados = [];
      estado.directorioHayMas = false;
      estado.directorioTotal = null;
      estado.directorioError = "";
    }
    const pagina = estado.directorioPagina + 1;
    const token = ++estado.tokenDirectorio;
    estado.directorioCargando = true;
    renderDirectorio();
    try {
      const datos = await api("/api/clientes/buscar/", {
        method: "POST",
        body: JSON.stringify({
          q: estado.directorioConsulta,
          limite: 20,
          pagina,
        }),
      });
      if (token !== estado.tokenDirectorio || !estado.directorioActivo) return;
      estado.directorioError = "";
      const nuevos = Array.isArray(datos.resultados) ? datos.resultados : [];
      const combinados = reiniciar ? nuevos : estado.directorioResultados.concat(nuevos);
      const unicos = new Map();
      combinados.forEach(cliente => unicos.set(String(cliente.cliente_id || cliente.id), cliente));
      estado.directorioResultados = [...unicos.values()];
      estado.directorioPagina = Number(datos.pagina || pagina);
      estado.directorioTotal = datos.total === null || datos.total === undefined
        ? null
        : Number(datos.total);
      estado.directorioHayMas = typeof datos.hay_mas === "boolean"
        ? datos.hay_mas
        : nuevos.length >= 20;
    } catch (error) {
      if (token === estado.tokenDirectorio) {
        estado.directorioError = error.message;
        $("#directorio-estado").textContent = error.message;
        $("#directorio-estado").hidden = false;
        toast(error.message, true);
      }
    } finally {
      if (token === estado.tokenDirectorio) {
        estado.directorioCargando = false;
        renderDirectorio();
      }
    }
  }

  function programarBusquedaDirectorio() {
    clearTimeout(estado.temporizadorDirectorio);
    estado.temporizadorDirectorio = setTimeout(() => cargarDirectorio({ reiniciar: true }), 250);
  }

  function abrirDirectorio() {
    if (estado.ticket || modoTableta) return;
    estado.directorioActivo = true;
    document.body.classList.add("en-directorio");
    $("#vista-posiciones").classList.add("oculto");
    $("#vista-directorio").classList.remove("oculto");
    cargarDirectorio({ reiniciar: true });
    requestAnimationFrame(() => $("#directorio-buscar")?.focus());
  }

  function cerrarDirectorio() {
    if (!estado.directorioActivo) return;
    estado.directorioActivo = false;
    estado.tokenDirectorio += 1;
    clearTimeout(estado.temporizadorDirectorio);
    document.body.classList.remove("en-directorio");
    $("#vista-directorio").classList.add("oculto");
    $("#vista-posiciones").classList.remove("oculto");
    $("#abrir-directorio")?.focus();
  }

  async function editarClienteDirectorio(clienteId) {
    if (!clienteId) return;
    try {
      const datos = await api("/api/clientes/" + encodeURIComponent(clienteId) + "/");
      abrirFormularioCliente(datos.cliente, "directorio");
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function asignarCliente(clienteId, telefonoId, domicilioId) {
    const datos = await api(`/api/tickets/${estado.ticket.id}/cliente/`, {
      method: "POST",
      body: JSON.stringify({ cliente_id: clienteId, telefono_id: telefonoId, domicilio_id: domicilioId }),
    });
    estado.ticket = datos.ticket;
    renderClienteDomicilio();
    renderAcciones();
  }

  async function quitarCliente() {
    try {
      const datos = await api(`/api/tickets/${estado.ticket.id}/cliente/`, { method: "DELETE" });
      estado.ticket = datos.ticket;
      estado.resultadosClientes = [];
      $("#buscar-cliente").value = "";
      renderClienteDomicilio();
      buscarClientes("");
    } catch (error) { toast(error.message, true); }
  }

  function filaTelefono(telefono = {}) {
    return `<div class="fila-telefono" data-registro-id="${escapar(telefono.id || "")}" data-etiqueta="${escapar(telefono.etiqueta || "Principal")}">
      <label>Teléfono<input class="telefono-numero" value="${escapar(telefono.numero || "")}" inputmode="tel" autocomplete="tel"></label>
      <button class="quitar-fila" data-quitar-fila type="button" aria-label="Quitar teléfono">×</button>
    </div>`;
  }

  function filaDomicilio(domicilio = {}) {
    return `<div class="fila-domicilio" data-registro-id="${escapar(domicilio.id || "")}" data-etiqueta="${escapar(domicilio.etiqueta || "Principal")}">
      <label class="domicilio-calle-campo">Calle<input class="domicilio-calle" value="${escapar(domicilio.calle || "")}" autocomplete="address-line1"></label>
      <label class="domicilio-exterior-campo">Núm. exterior<input class="domicilio-exterior" value="${escapar(domicilio.numero_exterior || "")}"></label>
      <label class="domicilio-interior-campo">Núm. interior<input class="domicilio-interior" value="${escapar(domicilio.numero_interior || "")}"></label>
      <label class="domicilio-cp-campo">CP<input class="domicilio-cp" value="${escapar(domicilio.codigo_postal || "")}" inputmode="numeric"></label>
      <label class="domicilio-colonia">Colonia<input class="domicilio-colonia-valor" value="${escapar(domicilio.colonia || "")}"></label>
      <label class="domicilio-municipio">Municipio<input class="domicilio-municipio-valor" value="${escapar(domicilio.municipio || "")}"></label>
      <label class="domicilio-referencia">Referencia<textarea class="domicilio-referencia-valor" rows="2">${escapar(domicilio.referencia || "")}</textarea></label>
      <button class="quitar-fila" data-quitar-fila type="button" aria-label="Quitar domicilio">×</button>
    </div>`;
  }

  function abrirFormularioCliente(cliente = null, contexto = "pedido") {
    estado.clienteEditando = cliente;
    estado.clienteFormularioContexto = contexto;
    $("#titulo-form-cliente").textContent = cliente ? `Editar ${cliente.nombre}` : "Nuevo cliente";
    $("#cliente-form-id").value = cliente?.id || "";
    $("#cliente-form-nombre").value = cliente?.nombre || "";
    $("#cliente-form-notas").value = cliente?.notas || "";
    $("#cliente-form-multiples").checked = Boolean(cliente?.comentarios_multiples);
    $("#telefonos-form").innerHTML = (cliente?.telefonos?.length ? cliente.telefonos : [{}]).map(filaTelefono).join("");
    $("#domicilios-form").innerHTML = (cliente?.domicilios?.length ? cliente.domicilios : [{}]).map(filaDomicilio).join("");
    $("#aviso-duplicados").classList.add("oculto");
    $("#aviso-duplicados").innerHTML = "";
    $("#form-cliente").dataset.confirmarDuplicado = "false";
    $("#guardar-cliente").textContent = "Guardar cliente";
    $("#dialogo-cliente").showModal();
    $("#cliente-form-nombre").focus();
  }

  function datosFormularioCliente(confirmarDuplicado = false) {
    const telefonos = $$("#telefonos-form .fila-telefono").map(fila => ({
      id: fila.dataset.registroId || null,
      etiqueta: fila.dataset.etiqueta || "Principal",
      numero: fila.querySelector(".telefono-numero").value,
    }));
    const domicilios = $$("#domicilios-form .fila-domicilio").map(fila => ({
      id: fila.dataset.registroId || null,
      etiqueta: fila.dataset.etiqueta || "Principal",
      calle: fila.querySelector(".domicilio-calle").value,
      numero_exterior: fila.querySelector(".domicilio-exterior").value,
      numero_interior: fila.querySelector(".domicilio-interior").value,
      colonia: fila.querySelector(".domicilio-colonia-valor").value,
      codigo_postal: fila.querySelector(".domicilio-cp").value,
      municipio: fila.querySelector(".domicilio-municipio-valor").value,
      referencia: fila.querySelector(".domicilio-referencia-valor").value,
    }));
    return {
      nombre: $("#cliente-form-nombre").value,
      notas: $("#cliente-form-notas").value,
      comentarios_multiples: $("#cliente-form-multiples").checked,
      telefonos,
      domicilios,
      confirmar_duplicado: confirmarDuplicado,
    };
  }

  function mostrarDuplicados(duplicados) {
    const aviso = $("#aviso-duplicados");
    aviso.innerHTML = `<p><strong>Ya existe un cliente con este nombre.</strong> El teléfono y el domicilio pueden compartirse; esta advertencia se basa únicamente en el nombre.</p>
      <ul>${duplicados.map(cliente => `<li>${escapar(cliente.nombre)} · clave ${escapar(cliente.clave_corta)}</li>`).join("")}</ul>`;
    aviso.classList.remove("oculto");
    $("#form-cliente").dataset.confirmarDuplicado = "true";
    $("#guardar-cliente").textContent = "Guardar de todos modos";
    aviso.scrollIntoView({ block: "nearest" });
  }

  async function guardarFormularioCliente(confirmarDuplicado = false) {
    const id = $("#cliente-form-id").value;
    try {
      const datos = await api(id ? `/api/clientes/${id}/` : "/api/clientes/", {
        method: id ? "PATCH" : "POST",
        body: JSON.stringify(datosFormularioCliente(confirmarDuplicado)),
      });
      const cliente = datos.cliente;
      $("#dialogo-cliente").close();
      if (estado.clienteFormularioContexto === "directorio") {
        await cargarDirectorio({ reiniciar: true });
        toast(`Cliente ${cliente.nombre} guardado en el directorio.`);
        return;
      }
      if (!estado.ticket) {
        toast(`Cliente ${cliente.nombre} guardado.`);
        return;
      }
      const telefonoActual = cliente.telefonos.find(item => item.id === estado.ticket.cliente.telefono_id) || cliente.telefonos[0];
      const domicilioActual = cliente.domicilios.find(item => item.id === estado.ticket.cliente.domicilio_id) || cliente.domicilios[0];
      await asignarCliente(
        cliente.id,
        telefonoActual?.id || "",
        domicilioActual?.id || "",
      );
      toast(`Cliente ${cliente.nombre} guardado y seleccionado.`);
    } catch (error) {
      if (error.status === 409 && error.datos?.duplicados) mostrarDuplicados(error.datos.duplicados);
      else toast(error.message, true);
    }
  }

  async function editarClienteSeleccionado() {
    try {
      const datos = await api(`/api/clientes/${estado.ticket.cliente.id}/`);
      abrirFormularioCliente(datos.cliente);
    } catch (error) { toast(error.message, true); }
  }

  function renderAcciones() {
    const bloqueoAjeno = bloqueoDeOtro();
    const editandoProgramado = esEdicionProgramada();
    const enEdicion = comandaEnEdicion();
    const abierto = comandaVisibleEditable();
    const cobrable = ["procesado", "cobrar"].includes(estado.ticket.estado);
    const esSucursal = estado.ticket.canal === "sucursales";
    const esDomicilio = estado.ticket.canal === "domicilio";
    const iniciaNuevoTicket = ["domicilio", "recoger"].includes(estado.ticket.canal);
    const comandaAgregadaPendiente = (
      estado.ticket.estado === "procesado"
      && enEdicion
      && numeroComandaActual() > 1
    );
    const muestraCancelar = abierto && (
      (estado.ticket.estado === "abierto" && numeroComandaActual() === 1)
      || comandaAgregadaPendiente
    );
    const muestraTicket = cobrable && !enEdicion && ["comedor", "llevar", "recoger", "domicilio"].includes(estado.ticket.canal);
    const muestraAgregar = !enEdicion && Boolean(estado.ticket.puede_agregar_comanda);
    const muestraCobro = cobrable && !esDomicilio;
    const muestraReactivarSucursal = esSucursal && cobrable && !enEdicion;
    const editable = abierto && !bloqueoAjeno && !estado.operando;
    const operable = !bloqueoAjeno && !estado.operando;
    const cobroBloqueado = muestraCobro && enEdicion;
    const acciones = $(".acciones");
    $("#procesar").textContent = esSucursal
      ? "Procesar e imprimir"
      : (numeroComandaActual() > 1 ? "Procesar comanda" : "Procesar orden");
    $("#cobrar").textContent = esSucursal ? "Completar pedido" : "Cobrar";
    $("#cancelar-orden").textContent = comandaAgregadaPendiente ? "Cancelar comanda" : "Cancelar orden";
    $("#agregar-comanda").textContent = iniciaNuevoTicket ? "Agregar pedido" : "Agregar comanda";
    $("#procesar").classList.toggle("oculto", !abierto || editandoProgramado);
    $("#cancelar-orden").classList.toggle("oculto", !muestraCancelar);
    $("#ticket-cuenta").classList.toggle("oculto", !muestraTicket);
    $("#agregar-comanda").classList.toggle("oculto", !muestraAgregar);
    $("#cobrar").classList.toggle("oculto", !muestraCobro);
    $("#reactivar-sucursal").classList.toggle("oculto", !muestraReactivarSucursal);
    acciones.classList.toggle("con-ticket", muestraTicket);
    acciones.classList.toggle("con-agregar", muestraAgregar);
    acciones.classList.toggle("con-cobro-bloqueado", cobroBloqueado && abierto);
    acciones.classList.toggle("con-reactivar", muestraReactivarSucursal);
    acciones.classList.toggle("solo-agregar", muestraAgregar && !muestraTicket && !muestraCobro);
    acciones.classList.toggle(
      "oculto",
      editandoProgramado || (!abierto && !muestraCancelar && !muestraTicket && !muestraAgregar && !muestraReactivarSucursal && !muestraCobro),
    );
    $("#procesar").disabled = !editable;
    $("#cancelar-orden").disabled = !operable;
    $("#ticket-cuenta").disabled = !operable;
    $("#agregar-comanda").disabled = !operable || !estado.ticket.puede_agregar_comanda;
    $("#reactivar-sucursal").disabled = !operable;
    $("#cobrar").disabled = !operable || cobroBloqueado;
    if (cobroBloqueado) {
      $("#cobrar").setAttribute("aria-label", "Cobrar; primero procesa la comanda actual");
      $("#cobrar").title = "Procesa la comanda actual antes de cobrar";
    } else {
      $("#cobrar").removeAttribute("aria-label");
      $("#cobrar").removeAttribute("title");
    }
    $$(".persona, .opcion-preparacion, .comanda-papel button, .producto, .fila-partida-sucursal").forEach(b => {
      b.disabled = !abierto || b.classList.contains("no-disponible-hoy");
      if (bloqueoAjeno) b.disabled = true;
    });
    $$("#datos-cliente button, #datos-cliente input, #datos-cliente textarea").forEach(control => control.disabled = !editable);
    $$("#datos-servicio-directo input, #ticket-switches input, #nombre-persona, .comanda-cliente-llevar input").forEach(control => control.disabled = !editable);
    $("#switch-tipo-pedido").disabled = !editable || editandoProgramado;
    $$("#entrega, #pago-domicilio input, #comentario").forEach(control => control.disabled = !editable);
    $("#entrega").disabled = !editable || editandoProgramado;
    renderNavegadorComandas();
  }

  function partidasDeEdicion(productoId, persona, termino) {
    return estado.ticket.partidas.filter(partida =>
      partida.producto_id === productoId
      && partida.comensal === persona
      && (partida.termino || "") === (termino || "")
      && perteneceAComanda(partida, numeroComandaActual())
    );
  }

  function claveTerminoProducto(productoId, persona) {
    return `${productoId}:${persona}`;
  }

  function recordarTermino(productoId, persona, termino) {
    if (termino) estado.ultimoTerminoPorProducto.set(claveTerminoProducto(productoId, persona), termino);
  }

  function siguienteTerminoProducto(producto) {
    if (!producto.permite_termino) return "";
    const existentes = new Set(
      estado.ticket.partidas
        .filter(partida =>
          partida.producto_id === producto.id
          && partida.comensal === estado.persona
          && perteneceAComanda(partida, numeroComandaActual())
        )
        .map(partida => partida.termino)
        .filter(Boolean),
    );
    if (!existentes.size) return producto.termino_predeterminado;
    const clave = claveTerminoProducto(producto.id, estado.persona);
    const ultimo = estado.ultimoTerminoPorProducto.get(clave) || producto.termino_predeterminado;
    const indice = Math.max(0, terminosPreparacion.indexOf(ultimo));
    for (let desplazamiento = 1; desplazamiento <= terminosPreparacion.length; desplazamiento += 1) {
      const candidato = terminosPreparacion[(indice + desplazamiento) % terminosPreparacion.length];
      if (!existentes.has(candidato)) return candidato;
    }
    return terminosPreparacion[(indice + 1) % terminosPreparacion.length];
  }

  function abrirCalculadora(producto, partidas, termino) {
    const cantidadActual = partidas.reduce((total, partida) => total + Number(partida.cantidad), 0) || 1;
    estado.edicion = {
      productoId: producto.id,
      persona: estado.persona,
      termino: termino || "",
      clave: `${producto.id}:${termino || "unico"}`,
      ids: partidas.map(partida => partida.id),
      cantidadPantalla: Math.min(9999, Math.max(1, Math.trunc(cantidadActual))),
      reemplazar: true,
    };
    recordarTermino(producto.id, estado.persona, termino);
    estado.modoMenu = "calculadora";
    renderMenu();
    renderComanda();
  }

  function abrirCalculadoraSucursal(producto, partida) {
    estado.edicion = {
      productoId: producto.id,
      partidaId: partida.id,
      cantidadPantalla: String(Number(partida.cantidad)),
      reemplazar: true,
    };
    estado.modoMenu = "calculadora-sucursal";
    renderMenu();
    renderComanda();
  }

  function abrirCalculadoraPersonalizada(partida) {
    estado.edicion = {
      personalizado: true,
      productoId: "",
      partidaId: partida.id,
      nombreProducto: nombrePartida(partida),
      precioUnitario: Number(partida.precio || 0),
      persona: Number(partida.comensal || estado.persona || 1),
      termino: "",
      clave: `personalizado:${partida.id}`,
      ids: [partida.id],
      cantidadPantalla: Math.min(9999, Math.max(1, Math.trunc(Number(partida.cantidad) || 1))),
      reemplazar: true,
    };
    estado.persona = estado.edicion.persona;
    estado.modoMenu = "calculadora";
    renderPersonas();
    renderMenu();
    renderComanda();
  }

  async function editarPartidaPersonalizada(partidaId) {
    if (!(await finalizarEdicion())) return;
    const partida = estado.ticket?.partidas.find(item => String(item.id) === String(partidaId));
    if (!esPartidaPersonalizada(partida)) return;
    abrirCalculadoraPersonalizada(partida);
  }

  async function seleccionarProductoSucursal(productoId) {
    if (!(await finalizarEdicion())) return;
    const producto = (estado.ticket.catalogo_sucursal || []).find(item => item.id === productoId);
    if (!producto) return;
    let partida = estado.ticket.partidas.find(item => item.producto_sucursal_id === productoId);
    if (!partida) {
      try {
        const datos = await api(`/api/tickets/${estado.ticket.id}/partidas/`, {
          method: "POST",
          body: JSON.stringify({ producto_sucursal_id: productoId, cantidad: "1" }),
        });
        estado.ticket = datos.ticket;
        partida = estado.ticket.partidas.find(item => item.producto_sucursal_id === productoId);
      } catch (error) {
        toast(error.message, true);
        return;
      }
    }
    abrirCalculadoraSucursal(producto, partida);
  }

  async function editarPartidaSucursal(partidaId) {
    if (!(await finalizarEdicion())) return;
    const partida = estado.ticket.partidas.find(item => item.id === partidaId);
    const producto = (estado.ticket.catalogo_sucursal || []).find(item => item.id === partida?.producto_sucursal_id);
    if (partida && producto) abrirCalculadoraSucursal(producto, partida);
  }

  async function guardarEdicionSucursal(eliminar = false) {
    if (!estado.edicion || estado.ticket?.canal !== "sucursales") return true;
    const edicion = { ...estado.edicion };
    let datos;
    try {
      if (eliminar) {
        datos = await api(`/api/partidas/${edicion.partidaId}/`, { method: "DELETE" });
      } else {
        const valor = Number(edicion.cantidadPantalla);
        if (!Number.isFinite(valor) || valor < 0.001 || valor > 999.999) {
          toast("Captura una cantidad entre 0.001 y 999.999.", true);
          return false;
        }
        datos = await api(`/api/partidas/${edicion.partidaId}/`, {
          method: "PATCH",
          body: JSON.stringify({ cantidad: edicion.cantidadPantalla }),
        });
      }
      estado.ticket = datos.ticket;
      estado.edicion = null;
      estado.modoMenu = "productos";
      renderMenu();
      renderComanda();
      return true;
    } catch (error) {
      toast(error.message, true);
      return false;
    }
  }

  async function manejarTeclaCantidadSucursal(tecla) {
    if (!estado.edicion) return;
    if (tecla === "eliminar") {
      await guardarEdicionSucursal(true);
      return;
    }
    let texto = String(estado.edicion.cantidadPantalla || "0");
    if (tecla === "borrar") {
      texto = texto.slice(0, -1) || "0";
      estado.edicion.reemplazar = false;
    } else if (tecla === ".") {
      if (!texto.includes(".")) texto = estado.edicion.reemplazar ? "0." : `${texto}.`;
      estado.edicion.reemplazar = false;
    } else {
      texto = estado.edicion.reemplazar || texto === "0" ? String(tecla) : `${texto}${tecla}`;
      const [enteros, decimales = ""] = texto.split(".");
      texto = `${enteros.slice(0, 3)}${texto.includes(".") ? `.${decimales.slice(0, 3)}` : ""}`;
      estado.edicion.reemplazar = false;
    }
    if (Number(texto) <= 999.999) estado.edicion.cantidadPantalla = texto;
    renderMenu();
    renderComanda();
  }

  async function seleccionarProducto(productoId) {
    if (!(await finalizarEdicion())) return;
    const producto = productos.find(item => item.id === productoId);
    if (!producto) return;
    const termino = siguienteTerminoProducto(producto);
    let partidas = partidasDeEdicion(producto.id, estado.persona, termino);
    if (esBebida(producto) && partidas.length) {
      const totalActual = partidas.reduce((total, partida) => total + Number(partida.cantidad), 0);
      try {
        const datos = await api(`/api/tickets/${estado.ticket.id}/partidas/ajustar/`, {
          method: "POST",
          body: JSON.stringify({ partida_ids: partidas.map(partida => partida.id), cantidad: totalActual + 1, termino }),
        });
        estado.ticket = datos.ticket;
        partidas = partidasDeEdicion(producto.id, estado.persona, termino);
      } catch (error) {
        toast(error.message, true);
        return;
      }
    }
    if (!partidas.length) {
      try {
        const datos = await api(`/api/tickets/${estado.ticket.id}/partidas/`, {
          method: "POST",
          body: JSON.stringify({
            producto_id: productoId,
            comensal: estado.persona,
            cantidad: 1,
            termino,
          }),
        });
        estado.ticket = datos.ticket;
        partidas = partidasDeEdicion(producto.id, estado.persona, termino);
      } catch (error) {
        toast(error.message, true);
        return;
      }
    }
    abrirCalculadora(producto, partidas, termino);
  }

  function persistirEdicion(eliminar = false, sincronizarCantidad = false) {
    if (!estado.edicion || (!eliminar && estado.edicion.cantidadPantalla === 0)) return Promise.resolve();
    const captura = {
      ticketId: estado.ticket.id,
      productoId: estado.edicion.productoId,
      persona: estado.edicion.persona,
      termino: estado.edicion.termino,
      clave: estado.edicion.clave,
      ids: [...estado.edicion.ids],
      personalizado: Boolean(estado.edicion.personalizado),
      cantidad: estado.edicion.cantidadPantalla,
      eliminar,
      sincronizarCantidad,
    };
    estado.colaEdicion = estado.colaEdicion.then(async () => {
      const datos = await api(`/api/tickets/${captura.ticketId}/partidas/ajustar/`, {
        method: "POST",
        body: JSON.stringify({
          partida_ids: captura.ids,
          cantidad: captura.cantidad || 1,
          termino: captura.termino,
          eliminar: captura.eliminar,
        }),
      });
      estado.ticket = datos.ticket;
      estado.errorEdicion = null;
      if (estado.edicion?.clave === captura.clave && estado.edicion?.persona === captura.persona) {
        const partidasActuales = captura.personalizado
          ? estado.ticket.partidas.filter(partida => captura.ids.some(id => String(id) === String(partida.id)))
          : partidasDeEdicion(captura.productoId, captura.persona, captura.termino);
        estado.edicion.ids = partidasActuales.map(partida => partida.id);
        if (captura.sincronizarCantidad && estado.edicion.cantidadPantalla === captura.cantidad) {
          estado.edicion.cantidadPantalla = partidasActuales.reduce(
            (total, partida) => total + Number(partida.cantidad),
            0,
          );
          renderMenu();
        }
      }
      renderComanda();
    }).catch(error => {
      estado.errorEdicion = error;
      toast(error.message, true);
    });
    return estado.colaEdicion;
  }

  async function finalizarEdicion() {
    if (!estado.edicion) return true;
    if (estado.ticket?.canal === "sucursales" && !estado.edicion.personalizado) return guardarEdicionSucursal();
    const edicion = estado.edicion;
    await estado.colaEdicion;
    if (estado.errorEdicion) return false;
    if (estado.edicion !== edicion) return true;
    if (edicion.cantidadPantalla === 0) {
      edicion.cantidadPantalla = 1;
      await persistirEdicion();
      if (estado.errorEdicion) return false;
    }
    if (estado.edicion === edicion) estado.edicion = null;
    return true;
  }

  async function manejarTeclaCantidad(tecla) {
    if (!estado.edicion) return;
    if (tecla === "eliminar") {
      await persistirEdicion(true);
      if (estado.errorEdicion) return;
      estado.edicion = null;
      estado.modoMenu = "productos";
      renderMenu();
      renderComanda();
      return;
    }
    let texto = String(estado.edicion.cantidadPantalla);
    if (tecla === "borrar") {
      texto = texto.slice(0, -1) || "0";
      estado.edicion.reemplazar = false;
    } else {
      texto = estado.edicion.reemplazar || texto === "0" ? String(tecla) : `${texto}${tecla}`;
      texto = texto.slice(0, 4);
      estado.edicion.reemplazar = false;
    }
    estado.edicion.cantidadPantalla = Math.min(9999, Number(texto) || 0);
    renderMenu();
    renderComanda();
    if (estado.edicion.cantidadPantalla > 0) persistirEdicion();
  }

  async function confirmarEdicion() {
    if (!(await finalizarEdicion())) return;
    estado.modoMenu = "productos";
    renderMenu();
    renderComanda();
  }

  function seleccionarTermino(termino) {
    if (!estado.edicion || estado.edicion.personalizado) return;
    const producto = productos.find(item => item.id === estado.edicion.productoId);
    if (!producto?.permite_termino || !producto.abreviaturas_termino[termino]) return;
    estado.edicion.termino = termino;
    estado.edicion.clave = `${producto.id}:${termino}`;
    recordarTermino(producto.id, estado.edicion.persona, termino);
    renderMenu();
    renderComanda();
    persistirEdicion(false, true);
  }

  async function seleccionarCelda(productoId, persona, termino) {
    if (!(await finalizarEdicion())) return;
    estado.persona = persona;
    estado.objetivoModificador = { tipo: "persona", persona };
    const producto = productos.find(item => item.id === productoId);
    if (!producto) return;
    let partidas = partidasDeEdicion(productoId, persona, termino);
    if (!partidas.length) {
      try {
        const datos = await api(`/api/tickets/${estado.ticket.id}/partidas/`, {
          method: "POST",
          body: JSON.stringify({ producto_id: productoId, comensal: persona, cantidad: 1, termino }),
        });
        estado.ticket = datos.ticket;
        partidas = partidasDeEdicion(productoId, persona, termino);
      } catch (error) {
        toast(error.message, true);
        return;
      }
    }
    renderPersonas();
    recordarTermino(producto.id, persona, termino);
    abrirCalculadora(producto, partidas, termino);
  }

  async function seleccionarPromocionCelda(productoId, persona) {
    if (!(await finalizarEdicion())) return;
    estado.persona = persona;
    renderPersonas();
    const producto = productos.find(item => item.id === productoId);
    if (!producto) return;
    const partidas = partidasDeEdicion(productoId, persona, "");
    if (!partidas.length) {
      await seleccionarProducto(productoId);
      return;
    }
    abrirCalculadora(producto, partidas, "");
  }

  async function seleccionarPersona(persona) {
    if (!(await finalizarEdicion())) return;
    if (estado.ticket?.captura_por_nombres && persona !== estado.persona) {
      clearTimeout(estado.temporizadorNombre);
      try { await guardarNombrePersona(); }
      catch (error) { toast(error.message, true); return; }
    }
    estado.persona = persona;
    estado.objetivoModificador = { tipo: "persona", persona };
    estado.modoMenu = "productos";
    renderPersonas();
    renderMenu();
    renderComanda();
    if (estado.ticket?.captura_por_nombres) $("#nombre-persona").focus();
  }

  async function aplicarPreparacion(codigo, nombre) {
    try {
      const objetivo = estado.objetivoModificador;
      let cuerpo;
      if (objetivo.tipo === "grupo") {
        cuerpo = { tipo: "general", codigo, nombre };
      } else {
        cuerpo = { comensal: objetivo.persona, codigo, nombre };
      }
      const datos = await api(`/api/tickets/${estado.ticket.id}/modificadores/`, {
        method: "POST", body: JSON.stringify(cuerpo),
      });
      estado.ticket = datos.ticket;
      renderPersonas();
      renderComanda();
      renderMenu();
    } catch (error) { toast(error.message, true); }
  }

  function actualizarControlEntrega(ticket = ticketParaComandaVisible()) {
    const boton = $("#entrega");
    if (!boton || !ticket) return;
    boton.textContent = ticket.entrega_aproximada
      ? (ticket.tipo_entrega === "programada" ? `Programado · ${ticket.entrega_aproximada}` : `Aproximado · ${ticket.entrega_aproximada}`)
      : "Seleccionar hora";
  }

  async function guardarEntrega(tipo, hora) {
    try {
      const datos = await api(`/api/tickets/${estado.ticket.id}/`, {
        method: "PATCH",
        body: JSON.stringify({ tipo_entrega: tipo, entrega_aproximada: hora }),
      });
      estado.ticket = datos.ticket;
      estado.modoEntrega = tipo;
      actualizarControlEntrega();
      estado.modoMenu = "productos";
      renderMenu();
      renderComanda();
    } catch (error) { toast(error.message, true); }
  }

  async function seleccionarMinutosEntrega(minutos) {
    const entrega = new Date(new Date(estado.ticket.creado_en).getTime() + Number(minutos) * 60000);
    const hora = `${String(entrega.getHours()).padStart(2, "0")}:${String(entrega.getMinutes()).padStart(2, "0")}`;
    await guardarEntrega("aproximada", hora);
  }

  function manejarTeclaEntrega(tecla) {
    if (tecla === "borrar") estado.entregaProgramadaDigitos = estado.entregaProgramadaDigitos.slice(0, -1);
    else estado.entregaProgramadaDigitos = `${estado.entregaProgramadaDigitos}${tecla}`.slice(-4);
    renderMenu();
  }

  async function confirmarEntregaProgramada() {
    const digitos = String(estado.entregaProgramadaDigitos || "").padStart(4, "0");
    const horas = Number(digitos.slice(0, 2));
    const minutos = Number(digitos.slice(2));
    if (horas > 23 || minutos > 59 || !estado.entregaProgramadaDigitos) {
      toast("Captura una hora válida en formato de 24 horas, por ejemplo 1330.", true);
      return;
    }
    await guardarEntrega("programada", `${String(horas).padStart(2, "0")}:${String(minutos).padStart(2, "0")}`);
  }

  async function alternarSalsa(opcion) {
    const grupos = (estado.ticket.salsas_verduras || []).map(grupo => ({ prefijo: grupo.prefijo, elementos: [...grupo.elementos] }));
    let grupo = grupos.find(item => item.prefijo === estado.prefijoSalsa);
    if (!grupo) {
      grupo = { prefijo: estado.prefijoSalsa, elementos: [] };
      grupos.push(grupo);
    }
    const indice = grupo.elementos.indexOf(opcion);
    if (indice >= 0) grupo.elementos.splice(indice, 1);
    else grupo.elementos.push(opcion);
    const seleccion = grupos.filter(item => item.elementos.length);
    try {
      const datos = await api(`/api/tickets/${estado.ticket.id}/`, {
        method: "PATCH", body: JSON.stringify({ salsas_verduras: seleccion }),
      });
      estado.ticket = datos.ticket;
      renderMenu();
      renderComanda();
    } catch (error) { toast(error.message, true); }
  }

  function actualizarEstadoNombreClienteLlevar(texto, clase) {
    const estadoNodo = $("[data-estado-cliente-llevar]");
    const campo = $("[data-cliente-llevar]");
    if (estadoNodo) {
      estadoNodo.textContent = texto;
      estadoNodo.className = clase;
    }
    if (campo) {
      campo.toggleAttribute("aria-busy", clase === "guardando");
      if (clase === "error") campo.setAttribute("aria-invalid", "true");
      else campo.removeAttribute("aria-invalid");
    }
  }

  function programarGuardadoNombreClienteLlevar() {
    clearTimeout(estado.temporizadorClienteLlevar);
    actualizarEstadoNombreClienteLlevar("Pendiente de guardar", "pendiente");
    estado.temporizadorClienteLlevar = setTimeout(() => {
      guardarNombreClienteLlevar();
    }, 650);
  }

  function guardarNombreClienteLlevar() {
    clearTimeout(estado.temporizadorClienteLlevar);
    const ejecutar = async () => {
      const ticket = estado.ticket;
      if (!ticket || ticket.canal !== "llevar") return true;
      const ticketId = String(ticket.id);
      const nombre = ticketId === estado.clienteLlevarTicketId
        ? estado.clienteLlevarBorrador
        : String(ticket.cliente?.nombre || "");
      const revisionEnviada = estado.revisionClienteLlevar;
      if (ticketId === estado.clienteLlevarTicketId && nombre === estado.clienteLlevarGuardado) {
        estado.errorClienteLlevar = null;
        actualizarEstadoNombreClienteLlevar("Guardado", "guardado");
        return true;
      }
      actualizarEstadoNombreClienteLlevar("Guardando…", "guardando");
      try {
        const datos = await api(`/api/tickets/${ticket.id}/`, {
          method: "PATCH",
          body: JSON.stringify({ cliente_nombre: nombre, cliente_telefono: "" }),
        });
        if (String(estado.ticket?.id) !== ticketId) return true;
        const nombreConfirmado = String(datos.ticket?.cliente?.nombre ?? nombre);
        const nombreActual = ticketId === estado.clienteLlevarTicketId
          ? estado.clienteLlevarBorrador
          : String(estado.ticket.cliente?.nombre || "");
        const resolucion = resolverGuardadoNombreClienteLlevar({
          nombreEnviado: nombre,
          nombreConfirmado,
          nombreActual,
          revisionEnviada,
          revisionActual: estado.revisionClienteLlevar,
        });
        estado.ticket.version_entidad = datos.ticket?.version_entidad ?? estado.ticket.version_entidad;
        estado.clienteLlevarGuardado = nombreConfirmado;
        estado.clienteLlevarBorrador = resolucion.nombreVisible;
        estado.clienteLlevarTicketId = ticketId;
        estado.errorClienteLlevar = null;
        estado.ticket.cliente = {
          ...(estado.ticket.cliente || {}),
          nombre: resolucion.nombreVisible,
        };
        $("#cliente-directo-nombre").value = resolucion.nombreVisible;
        const campo = $("[data-cliente-llevar]");
        if (campo) campo.value = resolucion.nombreVisible;
        if (resolucion.edicionPosterior) {
          programarGuardadoNombreClienteLlevar();
          return false;
        }
        actualizarEstadoNombreClienteLlevar("Guardado", "guardado");
        return true;
      } catch (error) {
        if (error.requiereConfirmacion) {
          estado.errorClienteLlevar = error;
          actualizarEstadoNombreClienteLlevar(
            "Pedido actualizado · revisa el nombre antes de volver a escribirlo",
            "error",
          );
          toast(error.message, true);
          return false;
        }
        if (String(estado.ticket?.id) === ticketId) {
          const nombrePendiente = ticketId === estado.clienteLlevarTicketId
            ? estado.clienteLlevarBorrador
            : nombre;
          estado.ticket.cliente = {
            ...(estado.ticket.cliente || {}),
            nombre: nombrePendiente,
          };
          $("#cliente-directo-nombre").value = nombrePendiente;
          const campo = $("[data-cliente-llevar]");
          if (campo) campo.value = nombrePendiente;
          estado.errorClienteLlevar = error;
          actualizarEstadoNombreClienteLlevar("Sin guardar · Presiona Enter para reintentar", "error");
        }
        toast(`${error.message} El nombre se conserva; reintenta antes de salir.`, true);
        return false;
      }
    };
    const tarea = estado.colaGuardadoClienteLlevar.then(ejecutar, ejecutar);
    estado.colaGuardadoClienteLlevar = tarea.catch(() => false);
    return tarea;
  }

  async function guardarDatos() {
    if (estado.ticket?.canal === "sucursales") return;
    clearTimeout(estado.temporizadorNombre);
    const nombres = { ...(estado.ticket.nombres_comensales || {}) };
    if (estado.ticket.captura_por_nombres) {
      const nombreActual = $("#nombre-persona").value.trim();
      if (nombreActual) nombres[String(estado.persona)] = nombreActual;
      else delete nombres[String(estado.persona)];
    }
    const cuerpo = {
      comentario_general: $("#comentario").value,
      terminal: $("#terminal").checked,
      paga_con: $("#paga-con").value,
      contacto_pedido_nombre: $("#contacto-pedido-nombre")?.value || "",
      contacto_pedido_telefono: $("#contacto-pedido-telefono")?.value || "",
      ...(estado.ticket.captura_por_nombres ? { nombres_comensales: nombres } : {}),
      ...(["recoger", "llevar"].includes(estado.ticket.canal) ? {
        cliente_nombre: $("#cliente-directo-nombre").value,
        cliente_telefono: estado.ticket.canal === "recoger" ? $("#cliente-directo-telefono").value : "",
      } : {}),
    };
    const datos = await api(`/api/tickets/${estado.ticket.id}/`, { method: "PATCH", body: JSON.stringify(cuerpo) });
    estado.ticket = datos.ticket;
  }

  async function procesar() {
    if (!(await finalizarEdicion())) return;
    if (!(await guardarNombreClienteLlevar())) return;
    bloquear(true);
    try {
      await guardarDatos();
      const datos = await api(`/api/tickets/${estado.ticket.id}/procesar/`, { method: "POST", body: "{}" });
      estado.ticket = datos.ticket;
      mostrarTicket();
      resumirImpresiones(datos.impresiones, "Orden procesada.");
    } catch (error) { toast(error.message, true); }
    finally { bloquear(false); }
  }

  async function cobrar(claveAdministrador, formaPago) {
    bloquear(true, $("#cobrar"));
    try {
      const datos = await api(`/api/tickets/${estado.ticket.id}/cobrar/`, {
        method: "POST", body: JSON.stringify({
          clave_administrador: claveAdministrador,
          forma_pago: formaPago,
        }),
      });
      estado.ticket = datos.ticket;
      resumirImpresiones(datos.impresiones, "Cobro registrado sin imprimir ticket.");
      await volver(true);
    } catch (error) { toast(error.message, true); }
    finally { bloquear(false); }
  }

  async function completarSucursal() {
    const claveAdministrador = await pedirClavePos("Completar pedido de sucursal", "Confirma con la clave de administrador que el pedido puede cerrarse.");
    if (!claveAdministrador) return;
    bloquear(true);
    try {
      await api(`/api/tickets/${estado.ticket.id}/completar-sucursal/`, {
        method: "POST",
        body: JSON.stringify({ clave_administrador: claveAdministrador }),
      });
      toast("Pedido de sucursal completado; la posición quedó disponible.");
      await volver(true);
    } catch (error) { toast(error.message, true); }
    finally { bloquear(false); }
  }

  async function reactivarSucursal() {
    if (!estado.ticket || estado.ticket.canal !== "sucursales") return;
    const claveAdministrador = await pedirClavePos(
      "Reactivar pedido de sucursal",
      "Autoriza que el pedido vuelva a edición con una clave elevada o de administrador.",
    );
    if (!claveAdministrador) return;
    bloquear(true, $("#reactivar-sucursal"));
    try {
      const datos = await api("/api/tickets/" + estado.ticket.id + "/reactivar-sucursal/", {
        method: "POST",
        body: JSON.stringify({ clave_administrador: claveAdministrador }),
      });
      estado.ticket = datos.ticket;
      mostrarTicket();
      toast("Pedido de sucursal reactivado. Ya puedes editarlo.");
    } catch (error) {
      toast(error.message, true);
    } finally {
      bloquear(false);
    }
  }

  async function imprimirTicketCuenta() {
    if (!estado.ticket) return;
    bloquear(true, $("#ticket-cuenta"));
    try {
      const datos = await api(`/api/tickets/${estado.ticket.id}/imprimir/`, {
        method: "POST",
        body: JSON.stringify({ formato: "cuenta" }),
      });
      resumirImpresiones(datos.impresiones, "Ticket total solicitado.");
    } catch (error) {
      toast(error.message, true);
    } finally {
      bloquear(false);
    }
  }

  async function crearComandaAdicional() {
    if (!estado.ticket?.puede_agregar_comanda || !(await finalizarEdicion())) return;
    const canalOrigen = estado.ticket.canal;
    const creaTicketIndependiente = ["domicilio", "recoger"].includes(canalOrigen);
    if (creaTicketIndependiente && !estado.idempotenciaAgregar) {
      estado.idempotenciaAgregar = crearIdempotencyKey();
    }
    bloquear(true, $("#agregar-comanda"));
    try {
      const cuerpo = creaTicketIndependiente
        ? { idempotency_key: estado.idempotenciaAgregar }
        : {};
      const datos = await api(`/api/tickets/${estado.ticket.id}/comandas/`, {
        method: "POST",
        body: JSON.stringify(cuerpo),
      });
      estado.ticket = datos.ticket;
      estado.idempotenciaAgregar = "";
      estado.persona = 1;
      estado.comandaVisible = numeroComandaActual(datos.ticket);
      mostrarTicket();
      if (creaTicketIndependiente) {
        const mensaje = datos.creado === false
          ? `Pedido #${datos.ticket.folio} recuperado y listo para capturar.`
          : `Pedido #${datos.ticket.folio} creado para el mismo cliente.`;
        toast(mensaje);
      } else {
        toast(`Comanda ${estado.comandaVisible} lista para capturar.`);
      }
    } catch (error) {
      toast(`${error.message} Puedes volver a intentar sin duplicar el pedido.`, true);
    } finally {
      bloquear(false);
    }
  }

  function resumirImpresiones(impresiones, mensajeBase) {
    const errores = impresiones.filter(impresion => impresion.estado === "error").length;
    const previas = impresiones.filter(impresion => impresion.estado === "generado").length;
    const impresas = impresiones.filter(impresion => impresion.estado === "impreso").length;
    if (errores) {
      const detalle = impresiones.find(impresion => impresion.estado === "error")?.error;
      toast(`${mensajeBase} No se pudo imprimir${detalle ? `: ${detalle}` : "."}`, true);
    } else if (previas) {
      toast(`${mensajeBase} Vista previa generada; no se envió papel.`);
    } else if (impresas) {
      toast(`${mensajeBase} Impresión enviada correctamente.`);
    } else if (!impresiones.length) {
      toast(mensajeBase);
    } else {
      toast(`${mensajeBase} Impresión en cola.`);
    }
  }

  async function cancelarOrden() {
    if (!estado.ticket) return;
    const esComandaAgregada = (
      estado.ticket.estado === "procesado"
      && comandaEnEdicion()
      && numeroComandaActual() > 1
    );
    const pregunta = esComandaAgregada
      ? "¿Cancelar esta comanda agregada? Se descartarán sólo sus productos; el pedido anterior se conservará."
      : "¿Cancelar esta orden? Se borrarán todos los productos y la posición quedará disponible.";
    if (!window.confirm(pregunta)) return;
    let claveAdministrador = "";
    if (estado.ticket.estado !== "abierto" && !esComandaAgregada) {
      claveAdministrador = await pedirClavePos("Cancelar pedido procesado", "Sólo el administrador puede cancelar un pedido después de imprimirlo.");
      if (!claveAdministrador) return;
    }
    clearTimeout(estado.temporizadorNombre);
    bloquear(true);
    try {
      const datos = await api(`/api/tickets/${estado.ticket.id}/cancelar/`, {
        method: "POST",
        body: JSON.stringify({ clave_administrador: claveAdministrador }),
      });
      if (esComandaAgregada) {
        estado.ticket = datos.ticket;
        estado.comandaVisible = numeroComandaActual(datos.ticket);
        mostrarTicket();
        toast("Comanda agregada cancelada; el pedido anterior se conservó.");
      } else {
        toast("Orden cancelada; la posición quedó disponible.");
        await volver(true);
      }
    } catch (error) {
      toast(error.message, true);
    } finally {
      bloquear(false);
    }
  }

  async function volver(forzar = false) {
    if (!forzar && !(await finalizarEdicion())) return;
    if (!forzar && !(await guardarNombreClienteLlevar())) return;
    clearTimeout(estado.temporizadorNombre);
    clearTimeout(estado.temporizadorClienteLlevar);
    if (estado.edicionProgramada) {
      bloquear(true);
      try {
        await guardarDatos();
        await liberarBloqueoActual();
        window.location.assign("/administrador/#programados");
      } catch (error) {
        toast(`${error.message} El pedido sigue programado; corrige el dato y vuelve a intentar.`, true);
      } finally {
        bloquear(false);
      }
      return;
    }
    await liberarBloqueoActual();
    estado.idempotenciaAgregar = "";
    estado.clienteLlevarGuardado = "";
    estado.clienteLlevarBorrador = "";
    estado.clienteLlevarTicketId = "";
    estado.revisionClienteLlevar += 1;
    estado.errorClienteLlevar = null;
    estado.ticket = null;
    document.body.classList.remove("en-ticket");
    $("#vista-ticket").classList.add("oculto");
    $("#vista-posiciones").classList.remove("oculto");
    cargarEstado();
  }

  async function salirModoMesero() {
    if (estado.directorioActivo) cerrarDirectorio();
    if (estado.edicionProgramada) {
      await volver();
      return;
    }
    if (!(await finalizarEdicion())) return;
    if (!(await guardarNombreClienteLlevar())) return;
    await liberarBloqueoActual();
    estado.idempotenciaAgregar = "";
    estado.clienteLlevarGuardado = "";
    estado.clienteLlevarBorrador = "";
    estado.clienteLlevarTicketId = "";
    estado.revisionClienteLlevar += 1;
    estado.errorClienteLlevar = null;
    estado.ticket = null;
    document.body.classList.remove("en-ticket");
    $("#vista-ticket").classList.add("oculto");
    $("#vista-posiciones").classList.remove("oculto");
    $("main").classList.add("oculto");
    $("#pantalla-acceso")?.classList.remove("oculto");
    document.body.classList.remove("en-operacion");
    clearInterval(estado.temporizadorSucursales);
    estado.temporizadorSucursales = null;
    estado.operador = null;
    renderOperadorActual();
    try { await api("/api/operador/salir/", { method: "POST", body: "{}" }); }
    catch (error) { toast(error.message, true); }
  }

  function estaEnPantallaCompleta() {
    return Boolean(
      document.fullscreenElement
      || document.webkitFullscreenElement
      || window.matchMedia?.("(display-mode: fullscreen)")?.matches
      || window.matchMedia?.("(display-mode: standalone)")?.matches
      || window.navigator.standalone
    );
  }

  function actualizarEstadoPantallaCompleta() {
    const activa = estaEnPantallaCompleta();
    const boton = $("#pantalla-completa");
    if (!boton) return;
    const etiqueta = activa ? "Salir de pantalla completa" : "Activar pantalla completa";
    boton.setAttribute("aria-label", etiqueta);
    boton.title = etiqueta;
    boton.setAttribute("aria-pressed", String(activa));
  }

  async function solicitarPantallaCompleta({ silencioso = false } = {}) {
    if (estaEnPantallaCompleta()) {
      actualizarEstadoPantallaCompleta();
      return true;
    }
    try {
      if (document.documentElement.requestFullscreen) {
        await document.documentElement.requestFullscreen({ navigationUI: "hide" });
      } else if (document.documentElement.webkitRequestFullscreen) {
        await document.documentElement.webkitRequestFullscreen();
      } else {
        actualizarEstadoPantallaCompleta();
        if (!silencioso) toast("Este navegador no permite activar pantalla completa. Usa la PWA instalada.", true);
        return false;
      }
      estado.pantallaCompletaSuspendida = false;
      actualizarEstadoPantallaCompleta();
      return true;
    } catch {
      actualizarEstadoPantallaCompleta();
      if (!silencioso) toast("El navegador bloqueó la pantalla completa. Toca de nuevo el botón para autorizarla.", true);
      return false;
    }
  }

  async function salirModoTableta() {
    if (!(await finalizarEdicion())) return;
    if (!(await guardarNombreClienteLlevar())) return;
    estado.pantallaCompletaSuspendida = true;
    if (document.fullscreenElement || document.webkitFullscreenElement) {
      try {
        if (document.exitFullscreen) await document.exitFullscreen();
        else if (document.webkitExitFullscreen) await document.webkitExitFullscreen();
      } catch { /* El navegador puede conservar el modo standalone de la PWA. */ }
    }
    if (estado.ticket) await volver(true);
    actualizarEstadoPantallaCompleta();
    toast("Pantalla completa desactivada. Ya puedes cerrar o cambiar de aplicación.");
  }

  async function alternarPantallaCompleta() {
    if (document.fullscreenElement || document.webkitFullscreenElement) {
      estado.pantallaCompletaSuspendida = true;
      try {
        if (document.exitFullscreen) await document.exitFullscreen();
        else if (document.webkitExitFullscreen) await document.webkitExitFullscreen();
      } catch {
        toast("No fue posible salir de pantalla completa desde este navegador.", true);
      }
      actualizarEstadoPantallaCompleta();
      return;
    }
    estado.pantallaCompletaSuspendida = false;
    await solicitarPantallaCompleta();
  }

  function activarPantallaCompletaConPrimerToque(evento) {
    if (estado.pantallaCompletaSuspendida || estaEnPantallaCompleta()) return;
    if (evento.target.closest?.("#pantalla-completa, #salir-tableta")) return;
    solicitarPantallaCompleta({ silencioso: true });
  }

  function iniciarPantallaCompletaPredeterminada() {
    actualizarEstadoPantallaCompleta();
    solicitarPantallaCompleta({ silencioso: true });
  }

  $$(".canal").forEach(boton => boton.addEventListener("click", () => cambiarCanal(boton.dataset.canal)));
  $("#entrar-mesero")?.addEventListener("click", () => entrarComoMesero());
  $("#entrar-administrador")?.addEventListener("click", () => abrirAdministrador());
  $("#form-clave-pos")?.addEventListener("submit", evento => {
    evento.preventDefault();
    const clave = $("#clave-pos").value.trim();
    if (!/^\d{4}$/.test(clave)) {
      $("#error-clave-pos").textContent = "El código debe contener exactamente 4 dígitos.";
      $("#error-clave-pos").hidden = false;
      return;
    }
    resolverClavePos(clave);
  });
  $("#cancelar-clave-pos")?.addEventListener("click", () => resolverClavePos(null));
  $("#dialogo-clave-pos")?.addEventListener("cancel", evento => {
    evento.preventDefault();
    resolverClavePos(null);
  });
  $("#dialogo-forma-pago")?.addEventListener("click", evento => {
    const opcion = evento.target.closest("[data-forma-pago]");
    if (opcion) resolverFormaPago(opcion.dataset.formaPago);
  });
  $("#cancelar-forma-pago")?.addEventListener("click", () => resolverFormaPago(null));
  $("#dialogo-forma-pago")?.addEventListener("cancel", evento => {
    evento.preventDefault();
    resolverFormaPago(null);
  });
  $("#toast-cerrar")?.addEventListener("click", cerrarToast);
  $$('[data-salir-mesero]').forEach(boton => boton.addEventListener("click", salirModoMesero));
  $("#pantalla-completa")?.addEventListener("click", alternarPantallaCompleta);
  $("#abrir-directorio")?.addEventListener("click", abrirDirectorio);
  $("#cerrar-directorio")?.addEventListener("click", cerrarDirectorio);
  $("#directorio-nuevo-cliente")?.addEventListener("click", () => abrirFormularioCliente(null, "directorio"));
  $("#directorio-buscar")?.addEventListener("input", programarBusquedaDirectorio);
  $("#directorio-cargar-mas")?.addEventListener("click", () => cargarDirectorio());
  $("#directorio-lista")?.addEventListener("click", evento => {
    const editar = evento.target.closest("[data-editar-cliente-directorio]");
    if (editar) editarClienteDirectorio(editar.dataset.editarClienteDirectorio);
  });
  $("#abrir-movimientos")?.addEventListener("click", () => abrirAdministrador("movimientos"));
  $("#salir-tableta")?.addEventListener("click", salirModoTableta);
  $("#rejilla-posiciones").addEventListener("click", evento => {
    const teclaPin = evento.target.closest("[data-tecla-pin-tableta]");
    if (teclaPin) {
      manejarTeclaClaveTableta(teclaPin.dataset.teclaPinTableta);
      return;
    }
    const sucursal = evento.target.closest("[data-sucursal-id]");
    if (sucursal) {
      estado.sucursalSeleccionada = sucursal.dataset.sucursalId;
      renderPosiciones();
      return;
    }
    if (evento.target.closest("[data-volver-sucursales]")) {
      estado.sucursalSeleccionada = null;
      renderPosiciones();
      return;
    }
    const boton = evento.target.closest(".posicion");
    if (boton) abrirPosicion(boton.dataset.id);
  });
  $("#volver").addEventListener("click", () => volver());
  $("#personas").addEventListener("click", async evento => {
    const boton = evento.target.closest(".persona");
    if (boton) await seleccionarPersona(Number(boton.dataset.persona));
  });
  $("#productos").addEventListener("click", async evento => {
    const productoPersonalizado = evento.target.closest("[data-producto-personalizado]");
    if (productoPersonalizado) {
      await abrirProductoPersonalizado();
      return;
    }
    const productoSucursal = evento.target.closest("[data-sucursal-producto]");
    if (productoSucursal) {
      await seleccionarProductoSucursal(productoSucursal.dataset.sucursalProducto);
      return;
    }
    const teclaSucursal = evento.target.closest("[data-tecla-sucursal]");
    if (teclaSucursal) {
      await manejarTeclaCantidadSucursal(teclaSucursal.dataset.teclaSucursal);
      return;
    }
    if (evento.target.closest("[data-confirmar-sucursal]")) {
      await guardarEdicionSucursal();
      return;
    }
    const modoEntrega = evento.target.closest("[data-modo-entrega]");
    if (modoEntrega) {
      estado.modoEntrega = modoEntrega.dataset.modoEntrega;
      if (estado.modoEntrega === "programada" && !estado.entregaProgramadaDigitos) estado.entregaProgramadaDigitos = "";
      renderMenu();
      return;
    }
    const minutosEntrega = evento.target.closest("[data-minutos-entrega]");
    if (minutosEntrega) {
      await seleccionarMinutosEntrega(Number(minutosEntrega.dataset.minutosEntrega));
      return;
    }
    const teclaEntrega = evento.target.closest("[data-tecla-entrega]");
    if (teclaEntrega) {
      manejarTeclaEntrega(teclaEntrega.dataset.teclaEntrega);
      return;
    }
    if (evento.target.closest("[data-confirmar-entrega]")) {
      await confirmarEntregaProgramada();
      return;
    }
    const prefijoSalsa = evento.target.closest("[data-prefijo-salsa]");
    if (prefijoSalsa) {
      estado.prefijoSalsa = prefijoSalsa.dataset.prefijoSalsa;
      renderMenu();
      return;
    }
    const opcionSalsa = evento.target.closest("[data-opcion-salsa]");
    if (opcionSalsa) {
      await alternarSalsa(opcionSalsa.dataset.opcionSalsa);
      return;
    }
    const confirmar = evento.target.closest("[data-confirmar-edicion]");
    if (confirmar) {
      await confirmarEdicion();
      return;
    }
    const termino = evento.target.closest("[data-termino]");
    if (termino) {
      seleccionarTermino(termino.dataset.termino);
      return;
    }
    const tecla = evento.target.closest("[data-tecla]");
    if (tecla) {
      await manejarTeclaCantidad(tecla.dataset.tecla);
      return;
    }
    const preparacion = evento.target.closest(".opcion-preparacion");
    if (preparacion) {
      await aplicarPreparacion(preparacion.dataset.codigo, preparacion.dataset.nombre);
      return;
    }
    const boton = evento.target.closest(".producto");
    if (boton) await seleccionarProducto(boton.dataset.id);
  });
  $("#productos").addEventListener("error", evento => {
    const imagen = evento.target.closest?.(".producto-imagen img");
    if (!imagen) return;
    const contenedor = imagen.closest(".producto-imagen");
    const abreviatura = contenedor.dataset.abreviatura || "Producto";
    contenedor.classList.add("producto-imagen-vacia");
    contenedor.setAttribute("aria-hidden", "true");
    contenedor.innerHTML = `<span><b>${escapar(abreviatura)}</b><small>Sin foto</small></span>`;
  }, true);
  $("#atajos-menu")?.addEventListener("click", evento => {
    const atajo = evento.target.closest("[data-menu-atajo]");
    if (atajo) desplazarASeccionMenu(Number(atajo.dataset.menuAtajo));
  });
  $("#menu-productos").addEventListener("click", async () => cambiarModoMenu("productos"));
  $("#comanda-preview").addEventListener("click", async evento => {
    const partidaPersonalizada = evento.target.closest("[data-partida-personalizada], [data-celda-personalizada]");
    if (partidaPersonalizada) {
      await editarPartidaPersonalizada(
        partidaPersonalizada.dataset.partidaPersonalizada || partidaPersonalizada.dataset.celdaPersonalizada,
      );
      return;
    }
    const partidaSucursal = evento.target.closest("[data-partida-sucursal]");
    if (partidaSucursal) {
      await editarPartidaSucursal(partidaSucursal.dataset.partidaSucursal);
      return;
    }
    const persona = evento.target.closest("[data-seleccionar-persona]");
    if (persona) {
      await seleccionarPersona(Number(persona.dataset.seleccionarPersona));
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
      await cambiarModoMenu("modificadores");
      return;
    }
    const promocion = evento.target.closest("[data-promocion-producto]");
    if (promocion) {
      await seleccionarPromocionCelda(
        promocion.dataset.promocionProducto,
        Number(promocion.dataset.promocionPersona),
      );
      return;
    }
    const celda = evento.target.closest("[data-celda-producto]");
    if (celda) {
      await seleccionarCelda(
        celda.dataset.celdaProducto,
        Number(celda.dataset.celdaPersona),
        celda.dataset.celdaTermino,
      );
      return;
    }
    const zonaMenu = evento.target.closest("[data-modo-menu]");
    if (zonaMenu) await cambiarModoMenu(zonaMenu.dataset.modoMenu);
  });
  $("#buscar-cliente").addEventListener("input", evento => {
    clearTimeout(estado.temporizadorCliente);
    const consulta = evento.currentTarget.value;
    estado.temporizadorCliente = setTimeout(() => buscarClientes(consulta), 250);
  });
  $("#datos-cliente").addEventListener("click", async evento => {
    const resultado = evento.target.closest("[data-seleccionar-cliente]");
    if (resultado) {
      const cliente = estado.resultadosClientes[Number(resultado.dataset.seleccionarCliente)];
      if (!cliente) return;
      try {
        await asignarCliente(cliente.cliente_id, cliente.telefono?.id || "", cliente.domicilio?.id || "");
        toast(`${cliente.nombre} seleccionado.`);
      } catch (error) { toast(error.message, true); }
      return;
    }
    if (evento.target.closest("[data-cambiar-cliente]")) {
      await quitarCliente();
      return;
    }
    if (evento.target.closest("[data-editar-cliente]")) await editarClienteSeleccionado();
  });
  $("#nuevo-cliente").addEventListener("click", () => abrirFormularioCliente(null, "pedido"));
  $("#agregar-telefono").addEventListener("click", () => $("#telefonos-form").insertAdjacentHTML("beforeend", filaTelefono()));
  $("#agregar-domicilio").addEventListener("click", () => $("#domicilios-form").insertAdjacentHTML("beforeend", filaDomicilio()));
  $("#form-cliente").addEventListener("click", evento => {
    const quitar = evento.target.closest("[data-quitar-fila]");
    if (quitar) quitar.parentElement.remove();
  });
  $("#form-cliente").addEventListener("submit", evento => {
    evento.preventDefault();
    guardarFormularioCliente(evento.currentTarget.dataset.confirmarDuplicado === "true");
  });
  $("#cancelar-cliente").addEventListener("click", () => $("#dialogo-cliente").close());
  $("#descartar-cliente").addEventListener("click", () => $("#dialogo-cliente").close());
  $("#form-producto-personalizado").addEventListener("submit", evento => {
    evento.preventDefault();
    guardarProductoPersonalizado();
  });
  $("#cancelar-producto-personalizado").addEventListener("click", cerrarProductoPersonalizado);
  $("#descartar-producto-personalizado").addEventListener("click", cerrarProductoPersonalizado);
  $("#dialogo-producto-personalizado").addEventListener("cancel", evento => {
    evento.preventDefault();
    cerrarProductoPersonalizado();
  });
  $("#entrega").addEventListener("click", async () => cambiarModoMenu("entrega"));
  $("#switch-tipo-pedido").addEventListener("change", convertirTipoPedido);
  $("#switch-modo-nombres").addEventListener("change", alternarCapturaPorNombres);
  $("#nombre-persona").addEventListener("input", evento => {
    if (!estado.ticket?.captura_por_nombres) return;
    const nombres = { ...(estado.ticket.nombres_comensales || {}) };
    const valor = evento.currentTarget.value;
    if (valor.trim()) nombres[String(estado.persona)] = valor;
    else delete nombres[String(estado.persona)];
    estado.ticket.nombres_comensales = nombres;
    const persona = $(`.persona[data-persona="${estado.persona}"]`);
    if (persona) {
      persona.classList.toggle("con-nombre", Boolean(valor.trim()));
      persona.querySelector("small").textContent = valor.trim() || "Sin nombre";
    }
    renderComanda();
    clearTimeout(estado.temporizadorNombre);
    estado.temporizadorNombre = setTimeout(() => guardarNombrePersona().catch(error => toast(error.message, true)), 650);
  });
  $("#nombre-persona").addEventListener("keydown", async evento => {
    if (evento.key !== "Enter") return;
    evento.preventDefault();
    clearTimeout(estado.temporizadorNombre);
    try { await guardarNombrePersona({ avanzar: true }); }
    catch (error) { toast(error.message, true); }
  });
  $("#comanda-papel-preview").addEventListener("input", evento => {
    const campo = evento.target.closest("[data-cliente-llevar]");
    if (!campo || estado.ticket?.canal !== "llevar" || !comandaVisibleEditable()) return;
    estado.revisionClienteLlevar += 1;
    estado.clienteLlevarBorrador = campo.value;
    estado.clienteLlevarTicketId = String(estado.ticket.id);
    estado.ticket.cliente.nombre = campo.value;
    $("#cliente-directo-nombre").value = campo.value;
    estado.errorClienteLlevar = null;
    campo.removeAttribute("aria-invalid");
    programarGuardadoNombreClienteLlevar();
  });
  $("#comanda-papel-preview").addEventListener("focusout", evento => {
    if (evento.target.matches("[data-cliente-llevar]")) guardarNombreClienteLlevar();
  });
  $("#comanda-papel-preview").addEventListener("keydown", async evento => {
    if (!evento.target.matches("[data-cliente-llevar]") || evento.key !== "Enter") return;
    evento.preventDefault();
    if (await guardarNombreClienteLlevar()) evento.target.blur();
  });
  $("#cliente-directo-nombre").addEventListener("input", evento => {
    if (!estado.ticket || !["recoger", "llevar"].includes(estado.ticket.canal)) return;
    if (estado.ticket.canal === "llevar") {
      estado.revisionClienteLlevar += 1;
      estado.clienteLlevarBorrador = evento.currentTarget.value;
      estado.clienteLlevarTicketId = String(estado.ticket.id);
    }
    estado.ticket.cliente.nombre = evento.currentTarget.value;
    renderComanda();
  });
  $("#cliente-directo-telefono").addEventListener("input", evento => {
    evento.currentTarget.value = evento.currentTarget.value.replace(/[^\d +()-]/g, "");
    if (estado.ticket?.canal !== "recoger") return;
    estado.ticket.cliente.telefono = evento.currentTarget.value;
    renderComanda();
  });
  $("#terminal").addEventListener("change", evento => {
    if (evento.currentTarget.checked) $("#paga-con").value = "";
  });
  $("#paga-con").addEventListener("input", evento => {
    evento.currentTarget.value = evento.currentTarget.value.replace(/\D/g, "");
    if (evento.currentTarget.value) $("#terminal").checked = false;
  });
  $("#comentario").addEventListener("input", evento => {
    if (estado.ticket) estado.ticket.comentario_general = evento.currentTarget.value;
    renderComanda();
  });
  $("#procesar").addEventListener("click", procesar);
  $("#cancelar-orden").addEventListener("click", cancelarOrden);
  $("#cobrar").addEventListener("click", async () => {
    if (estado.ticket?.canal === "sucursales") {
      await completarSucursal();
      return;
    }
    const formaPago = await pedirFormaPago();
    if (!formaPago) return;
    const claveAdministrador = await pedirClavePos("Autorizar cobro", "Marcar el pedido como cobrado requiere la clave de administrador.");
    if (claveAdministrador) await cobrar(claveAdministrador, formaPago);
  });
  $("#ticket-cuenta").addEventListener("click", imprimirTicketCuenta);
  $("#agregar-comanda").addEventListener("click", crearComandaAdicional);
  $("#reactivar-sucursal").addEventListener("click", reactivarSucursal);
  $("#comanda-anterior").addEventListener("click", () => cambiarComandaVisible(-1));
  $("#comanda-siguiente").addEventListener("click", () => cambiarComandaVisible(1));

  async function iniciarAplicacion() {
    const sesionReanudada = await reanudarSesionOperador();
    if (!sesionReanudada || idProgramadoSolicitado()) {
      await abrirProgramadoDesdeEnlace();
    }
  }

  window.addEventListener("online", () => {
    if (bloqueoPropio()) renovarBloqueoTicket();
    cargarEstado(estado.canal === "sucursales");
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") return;
    if (bloqueoPropio()) renovarBloqueoTicket();
    actualizarEstadoLan();
  });
  document.addEventListener("fullscreenchange", actualizarEstadoPantallaCompleta);
  document.addEventListener("webkitfullscreenchange", actualizarEstadoPantallaCompleta);
  document.addEventListener("pointerdown", activarPantallaCompletaConPrimerToque, true);
  window.addEventListener("pagehide", liberarBloqueoEnSalida);
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/service-worker.js").catch(() => {});
  renderOperadorActual();
  renderPersonas();
  renderMenu();
  iniciarActualizacionEstadoLan();
  iniciarPantallaCompletaPredeterminada();
  iniciarAplicacion();
})();
