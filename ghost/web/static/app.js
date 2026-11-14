// De Ghost-pagina.
//
// Deze pagina beslist zelf niets over je iPhone. Hij vraagt elke seconde aan
// Ghost (op je eigen laptop) wat de stand van zaken is, laat dat zien, en
// stuurt jouw klikken door. Alle veiligheid zit in Ghost zelf: valt deze
// pagina weg, dan blijven de timer en het opruimen gewoon werken.
//
// Tekst uit Ghost of van internet (de naam van je iPhone, een zoekresultaat,
// de naam van een favoriet) wordt altijd als platte tekst getoond via
// textContent, nooit als HTML. Zo kan een rare naam geen kwaad.
//
// Indeling van dit bestand:
//   1. Praten met Ghost
//   2. De kaart
//   3. Kleine hulpjes
//   4. Tekenen (alles wat je ziet volgt uit `state`)
//   5. Vaste plek, favorieten en zoeken
//   6. Route
//   7. Het venster "geen contact"
//   8. Jouw klikken

(function () {
  "use strict";

  const TOKEN = document.querySelector('meta[name="ghost-token"]').content;
  const POLL_MS = 1000;
  const EXTEND_MINUTES = 15;
  const MAX_WAYPOINTS = 25;
  const $ = (id) => document.getElementById(id);

  let state = null; // laatste stand van zaken uit Ghost
  let tab = "fixed"; // "fixed" (vaste plek) of "route"
  let failedPolls = 0;
  let stopped = false; // true nadat je op "Ghost afsluiten" hebt geklikt
  let stopBusy = false;
  let setBusy = false;
  let dismissedNotice = 0;
  let centeredOnActive = false;
  let lastDeviceKey = "";
  let lastBannerMode = "none";
  let pollTimer = null;

  // Vaste plek
  let selected = null; // de plek die je hebt aangeklikt: {lat, lon, text}

  // Favorieten en zoeken
  let favorites = [];
  let favoritesVersion = -1;
  let favoritesLoading = false;
  let saveTarget = null; // de plek die je aan het bewaren bent
  let settingBusy = false;

  // Route
  let waypoints = []; // de punten die je hebt aangeklikt: [{lat, lon}]
  let transport = "foot"; // "foot", "bike" of "car"
  let plan = null; // de uitgerekende route: {id, length_m, path, key}
  let planBusy = false;
  let planError = "";
  let planTimer = null;
  let routeBusy = false;
  let interruptedBusy = false; // de knop "Hervat route" is ingedrukt
  let drawnRouteId = null; // welke lopende route er op de kaart getekend is
  let speedDragging = false;

  // ---------------------------------------------------------------------
  // 1. Praten met Ghost
  // ---------------------------------------------------------------------

  class ApiError extends Error {
    constructor(message, status, data) {
      super(message);
      this.status = status;
      this.data = data || {};
    }
  }

  async function api(path, body, method) {
    const options = { method: method || "GET", headers: { "X-Ghost-Token": TOKEN }, cache: "no-store" };
    if (body !== undefined) {
      options.method = method || "POST";
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    let response;
    try {
      response = await fetch(path, options);
    } catch (error) {
      throw new ApiError("Geen contact met Ghost.", 0);
    }
    if (response.status === 401) {
      // Ghost is opnieuw gestart en heeft een nieuw sessietoken. Herlaad de
      // pagina om het nieuwe token op te halen (hooguit één keer per 10 seconden).
      const last = Number(sessionStorage.getItem("ghost-reload") || 0);
      if (Date.now() - last > 10000) {
        sessionStorage.setItem("ghost-reload", String(Date.now()));
        location.reload();
      }
      throw new ApiError("Het sessietoken klopt niet meer. Herlaad de pagina.", 401);
    }
    let data = {};
    try {
      data = await response.json();
    } catch (error) {
      // geen leesbaar antwoord; de status hieronder zegt genoeg
    }
    failedPolls = 0;
    hideOverlay();
    if (data.state) {
      render(data.state);
    }
    if (!response.ok) {
      throw new ApiError(data.error || "Er ging iets mis.", response.status, data);
    }
    return data;
  }

  async function poll() {
    if (stopped) {
      return;
    }
    try {
      await api("/api/state");
    } catch (error) {
      if (error.status === 0) {
        failedPolls += 1;
        if (failedPolls >= 2) {
          showOffline();
        }
      }
    }
  }

  // ---------------------------------------------------------------------
  // 2. De kaart
  // ---------------------------------------------------------------------

  const map = L.map("map", { worldCopyJump: true }).setView([52.2, 5.3], 8);
  map.attributionControl.setPrefix(
    '<a href="https://leafletjs.com" target="_blank" rel="noopener noreferrer">Leaflet</a>'
  );
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a>-bijdragers',
  }).addTo(map);

  function pin(kind, size, label) {
    return L.divIcon({
      className: "pin pin-" + kind,
      html: label === undefined ? "" : String(Number(label)), // alleen een getal, nooit vrije tekst
      iconSize: [size, size],
      iconAnchor: [size / 2, size / 2],
    });
  }

  const selectedMarker = L.marker([0, 0], { icon: pin("selected", 22), interactive: false, keyboard: false });
  const activeMarker = L.marker([0, 0], {
    icon: pin("active", 24),
    interactive: false,
    keyboard: false,
    zIndexOffset: 500,
  });
  activeMarker.bindTooltip("Nep-locatie", { permanent: true, direction: "top", offset: [0, -14], className: "pin-label" });

  const waypointLayer = L.layerGroup(); // de genummerde punten van je route
  const draftLine = L.polyline([], { color: "#14508c", weight: 3, opacity: 0.75, dashArray: "6 9" }); // nog niet uitgerekend
  const planLine = L.polyline([], { color: "#14508c", weight: 5, opacity: 0.85 }); // uitgerekende route
  const activeLine = L.polyline([], { color: "#c4122f", weight: 5, opacity: 0.75 }); // de route die nu loopt

  map.on("click", (event) => {
    const point = event.latlng.wrap();
    pick(point.lat, point.lng, false);
  });

  // Een plek aanwijzen. Op het tabblad "Vaste plek" kies je die plek; op het
  // tabblad "Route" wordt het een volgend punt van de route.
  function pick(lat, lon, moveMap) {
    if (tab === "route") {
      addWaypoint(lat, lon);
    } else {
      select(lat, lon);
    }
    if (moveMap) {
      map.setView([lat, lon], Math.max(map.getZoom(), 14));
    }
  }

  // ---------------------------------------------------------------------
  // 3. Kleine hulpjes
  // ---------------------------------------------------------------------

  function show(element) {
    element.hidden = false;
  }

  function hide(element) {
    element.hidden = true;
  }

  function setError(id, message) {
    $(id).textContent = message || "";
    $(id).hidden = !message;
  }

  function el(tag, className, text) {
    const element = document.createElement(tag);
    if (className) {
      element.className = className;
    }
    if (text !== undefined && text !== null) {
      element.textContent = text;
    }
    return element;
  }

  function formatCoords(lat, lon) {
    return lat.toFixed(5) + ", " + lon.toFixed(5);
  }

  function formatRemaining(seconds) {
    const s = Math.max(0, Math.round(seconds));
    const hours = Math.floor(s / 3600);
    const minutes = Math.floor((s % 3600) / 60);
    const secs = String(s % 60).padStart(2, "0");
    if (hours > 0) {
      return hours + ":" + String(minutes).padStart(2, "0") + ":" + secs;
    }
    return minutes + ":" + secs;
  }

  function formatMinutes(minutes) {
    if (minutes < 60) {
      return minutes + " minuten";
    }
    const hours = minutes / 60;
    return hours === 1 ? "1 uur" : hours + " uur";
  }

  function formatDistance(meters) {
    if (meters < 1000) {
      return Math.round(meters) + " m";
    }
    const km = meters / 1000;
    return km.toFixed(km < 100 ? 1 : 0).replace(".", ",") + " km";
  }

  function formatDuration(seconds) {
    if (seconds < 60) {
      return "minder dan een minuut";
    }
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) {
      return minutes + " min";
    }
    const rest = minutes % 60;
    return Math.floor(minutes / 60) + " uur" + (rest ? " " + rest + " min" : "");
  }

  function simulationMode() {
    const sim = state.simulation;
    if (sim.connection_lost || (sim.uncertain && sim.active)) {
      return "lost";
    }
    if (sim.uncertain) {
      return "uncertain";
    }
    if (sim.active && sim.reconnecting) {
      return "reconnecting"; // verbinding even weg; Ghost herstelt het zelf
    }
    return sim.active ? "active" : "none";
  }

  // Waarom je nu geen nep-locatie kunt zetten. Leeg betekent: het kan.
  function blockReason() {
    const mode = simulationMode();
    if (state.app.closing) {
      return "Ghost wordt afgesloten.";
    }
    if (mode === "lost" || mode === "uncertain") {
      return "Eerst moet je echte locatie teruggevraagd zijn (rode knop).";
    }
    if (mode === "reconnecting") {
      return "Even wachten: Ghost verbindt opnieuw met je iPhone.";
    }
    if (!state.device.ready) {
      return "Je iPhone is nog niet klaar. Kijk bij '1. Je iPhone'.";
    }
    return "";
  }

  // ---------------------------------------------------------------------
  // 4. Tekenen: alles wat je ziet volgt uit `state`
  // ---------------------------------------------------------------------

  function render(newState) {
    state = newState;
    renderBanner();
    renderStop();
    renderNotice();
    renderDevice();
    renderActive();
    renderInterrupted();
    renderFixed();
    renderRoute();
    renderSearch();
    renderMap();
    renderFooter();
    if (state.favorites_version !== favoritesVersion) {
      loadFavorites();
    }
  }

  function renderBanner() {
    const sim = state.simulation;
    const banner = $("banner");
    const mode = simulationMode();
    if (mode !== lastBannerMode) {
      // De balk verschijnt of verdwijnt, dus de kaart wordt iets lager of hoger.
      // Vertel dat aan de kaart zodra de pagina opnieuw is ingedeeld.
      lastBannerMode = mode;
      setTimeout(() => map.invalidateSize(), 0);
    }
    if (mode === "none") {
      hide(banner);
      document.title = "Ghost";
      return;
    }
    banner.className = "banner banner-" + mode;
    if (mode === "active") {
      $("banner-title").textContent = "NEP-LOCATIE ACTIEF";
      const parts = [sim.text];
      if (sim.route && !sim.route.finished) {
        parts.unshift(sim.route.paused ? "route op pauze" : "onderweg, " + sim.route.speed_kmh + " km/u");
      }
      if (sim.remaining_s !== null) {
        parts.push("stopt vanzelf over " + formatRemaining(sim.remaining_s));
      }
      $("banner-text").textContent = parts.join(" · ");
      document.title = "● NEP-LOCATIE ACTIEF · Ghost";
    } else if (mode === "reconnecting") {
      $("banner-title").textContent = "VERBINDING EVEN WEG · GHOST VERBINDT OPNIEUW";
      const info = sim.reconnecting;
      const parts = [
        "Je iPhone staat nog op de nep-locatie (" + sim.text + ").",
        "Controleer de kabel en ontgrendel de iPhone.",
        "Al " + info.seconds + " van de " + info.grace_s + " seconden geprobeerd.",
      ];
      if (info.why) {
        parts.push("Reden: " + info.why + ".");
      }
      $("banner-text").textContent = parts.join(" ");
      document.title = "⚠ VERBINDING EVEN WEG · Ghost";
    } else if (mode === "lost") {
      $("banner-title").textContent = sim.connection_lost
        ? "VERBINDING MET DE IPHONE WEG"
        : "ECHTE LOCATIE NOG NIET TERUG";
      $("banner-text").textContent =
        "Je iPhone staat waarschijnlijk nog op de nep-locatie. Steek de kabel erin en ontgrendel de iPhone. " +
        "Ghost blijft proberen je echte locatie terug te vragen.";
      document.title = "⚠ LET OP · Ghost";
    } else {
      $("banner-title").textContent = "MOGELIJK NOG EEN NEP-LOCATIE ACTIEF";
      $("banner-text").textContent =
        "Ghost kon je echte locatie nog niet terugvragen. Sluit je iPhone aan en ontgrendel hem. " +
        "Ghost blijft het proberen.";
      document.title = "⚠ LET OP · Ghost";
    }
    show(banner);
  }

  function renderStop() {
    const sim = state.simulation;
    const line = $("stop-state");
    const mode = simulationMode();
    $("stop").classList.toggle("is-busy", stopBusy);
    $("stop-label").textContent = stopBusy ? "Bezig met terugvragen…" : "Stop – echte locatie terug";
    line.className = "stop-state";
    if (mode === "active") {
      line.textContent = "Er is nu een nep-locatie actief.";
      line.classList.add("is-active");
    } else if (mode === "reconnecting") {
      line.textContent = "Ghost verbindt opnieuw. Stoppen mag altijd.";
      line.classList.add("is-active");
    } else if (mode !== "none") {
      line.textContent = "Klik hier zodra je iPhone weer is aangesloten.";
      line.classList.add("is-active");
    } else if (sim.clean_start_done) {
      line.textContent = "Geen nep-locatie actief. Deze knop mag altijd, ook als je twijfelt.";
      line.classList.add("is-clean");
    } else {
      line.textContent = "Deze knop mag altijd, ook als je twijfelt.";
    }
  }

  function renderNotice() {
    const notice = state.notice;
    const box = $("notice");
    if (!notice || notice.id <= dismissedNotice) {
      hide(box);
      return;
    }
    box.className = "notice notice-" + notice.kind;
    $("notice-title").textContent = notice.title;
    $("notice-text").textContent = notice.text;
    $("notice-hint").textContent = notice.hint || "";
    $("notice-hint").hidden = !notice.hint;
    show(box);
  }

  function renderDevice() {
    const device = state.device;
    // Alleen opnieuw opbouwen als er iets veranderd is. Anders zou een
    // opengeklapt lijstje elke seconde weer dichtklappen.
    const key = JSON.stringify([device, state.simulation.clean_start_done]);
    $("check").disabled = device.checking || device.busy !== null;
    $("check").textContent = device.checking ? "Bezig…" : "Opnieuw controleren";
    if (key === lastDeviceKey) {
      return;
    }
    const wasOpen = Boolean(document.querySelector("#device details[open]"));
    lastDeviceKey = key;
    const box = $("device");
    box.replaceChildren();

    if (!device.checked) {
      box.append(summaryLine("busy", "Bezig met controleren van je iPhone…", "Dit duurt een paar seconden."));
      return;
    }

    if (device.ready) {
      const details = [device.model, device.ios ? "iOS " + device.ios : ""].filter(Boolean).join(", ");
      const name = (device.name || "iPhone") + (details ? " (" + details + ")" : "");
      const sub = state.simulation.clean_start_done
        ? "Schone start gedaan: bij het opstarten is je echte locatie teruggevraagd."
        : "";
      box.append(summaryLine("ok", "Klaar: " + name, sub));
    } else if (device.problem) {
      box.append(problemBox(device));
    }

    if (device.count > 1) {
      box.append(el("p", "muted", "Er zijn " + device.count + " iPhones aangesloten; Ghost gebruikt de eerste."));
    }

    const details = el("details");
    details.open = wasOpen || !device.ready;
    details.append(el("summary", "", "Alle controles"));
    const list = el("ul", "checks");
    for (const check of device.checks) {
      const kind = check.ok === true ? "ok" : check.ok === false ? "bad" : "skip";
      const item = el("li", "check-" + kind);
      item.append(el("span", "check-mark", kind === "ok" ? "✓" : kind === "bad" ? "✗" : "–"));
      item.append(el("span", "", check.text + (kind === "skip" ? " (nog niet gecontroleerd)" : "")));
      list.append(item);
    }
    details.append(list);
    box.append(details);
  }

  function summaryLine(dotKind, text, sub) {
    const line = el("div", "device-summary");
    line.append(el("span", "dot dot-" + dotKind));
    const words = el("div");
    words.append(el("div", "", text));
    if (sub) {
      words.append(el("div", "device-sub", sub));
    }
    line.append(words);
    return line;
  }

  function problemBox(device) {
    const problem = device.problem;
    const box = el("div", "problem");
    box.append(el("h3", "", problem.title));
    box.append(el("p", "", problem.what));
    if (problem.detail) {
      box.append(el("p", "", problem.detail));
    }
    const steps = el("ol");
    for (const step of problem.steps) {
      steps.append(el("li", "", step));
    }
    box.append(steps);

    if (problem.action) {
      const busy = device.busy === problem.action.id;
      const button = el("button", "btn", busy ? "Bezig… kijk op je iPhone" : problem.action.label);
      button.type = "button";
      button.disabled = device.busy !== null;
      button.addEventListener("click", () => runAction(problem.action.id));
      box.append(button);
    }
    if (problem.link) {
      const link = el("a", "", problem.link.label);
      link.href = problem.link.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      box.append(link);
    }
    box.append(el("p", "muted", "Ghost controleert elke paar seconden vanzelf opnieuw."));
    return box;
  }

  // Het gele blok dat je ziet zolang er een nep-locatie actief is.
  function renderActive() {
    const sim = state.simulation;
    const route = sim.route;
    const mode = simulationMode();
    const active = mode === "active" || mode === "reconnecting";
    $("active-box").hidden = !active;
    if (!active) {
      $("active-box").dataset.key = "";
      return;
    }
    const activeKey = "actief:" + (route ? route.plan_id : "vast");
    if ($("active-box").dataset.key !== activeKey) {
      // Er is net iets gestart: schuif het gele blok in beeld, want daar zit de bediening.
      $("active-box").dataset.key = activeKey;
      setTimeout(() => $("active-box").scrollIntoView({ behavior: "smooth", block: "center" }), 0);
    }
    const moving = Boolean(route && !route.finished);
    let title = "Nep-locatie actief op";
    if (route && route.finished) {
      title = "Route klaar. Nep-locatie staat op";
    } else if (mode === "reconnecting") {
      title = route ? "Route wacht op de verbinding bij" : "Nep-locatie staat (verbinding even weg) op";
    } else if (route && route.paused) {
      title = "Route op pauze bij";
    } else if (route) {
      title = "Onderweg, nu bij";
    }
    $("active-title").textContent = title;
    $("active-coords").textContent = sim.text;
    $("active-remaining").textContent = sim.remaining_s === null ? "–" : formatRemaining(sim.remaining_s);

    $("route-live").hidden = !moving;
    $("nudge-box").hidden = moving; // verschuiven kan alleen als de plek stilstaat
    if (moving) {
      const percent = route.length_m > 0 ? Math.min(100, (100 * route.travelled_m) / route.length_m) : 0;
      $("route-bar").style.width = percent.toFixed(1) + "%";
      $("route-bar").parentElement.setAttribute("aria-valuenow", String(Math.round(percent)));
      const parts = [formatDistance(route.remaining_m) + " te gaan"];
      if (!route.paused && route.eta_s !== null) {
        parts.push("nog ongeveer " + formatDuration(route.eta_s));
      }
      if (route.repeat) {
        parts.push("ronde " + route.lap);
      }
      $("route-progress").textContent = parts.join(" · ");
      $("route-pause").textContent = route.paused ? "Verder" : "Pauze";
      if (!speedDragging) {
        $("live-speed").value = String(Math.round(route.speed_kmh));
        $("live-speed-out").textContent = Math.round(route.speed_kmh) + " km/u";
      }
    }
  }

  // Een route die door een weggevallen verbinding is afgebroken: bied aan om verder te gaan.
  function renderInterrupted() {
    const item = state.simulation.interrupted;
    const box = $("interrupted-box");
    box.hidden = !item || state.simulation.active;
    if (box.hidden) {
      return;
    }
    $("interrupted-text").textContent =
      "Je route is afgebroken doordat de verbinding wegviel, na " +
      formatDistance(item.travelled_m) +
      " van de " +
      formatDistance(item.length_m) +
      ". Je echte locatie is teruggevraagd.";
    const reason = blockReason();
    $("interrupted-continue").disabled = interruptedBusy || reason !== "";
    $("interrupted-continue").textContent = interruptedBusy ? "Bezig…" : "Hervat route";
    $("interrupted-hint").textContent = reason;
    $("interrupted-hint").hidden = reason === "";
  }

  async function continueRoute() {
    interruptedBusy = true;
    setError("interrupted-error", "");
    renderInterrupted();
    try {
      await api("/api/route/continue", {});
    } catch (error) {
      setError("interrupted-error", error.message);
    } finally {
      interruptedBusy = false;
      renderInterrupted();
    }
  }

  async function forgetRoute() {
    try {
      await api("/api/route/forget", {});
    } catch (error) {
      // niets: de pagina toont bij de volgende verversing de echte stand
    }
  }

  function renderFixed() {
    const mode = simulationMode();
    fillTimer($("minutes"));
    $("selected-line").textContent = selected ? "Gekozen plek: " + selected.text : "Nog geen plek gekozen.";
    $("selected-save").hidden = !selected;

    let hint = blockReason();
    if (!hint && !selected) {
      hint = "Klik eerst op de kaart om een plek te kiezen.";
    }
    const button = $("set");
    button.disabled = setBusy || hint !== "";
    button.textContent = setBusy
      ? "Bezig…"
      : mode === "active"
        ? "Verplaats nep-locatie hierheen"
        : "Zet nep-locatie hier";
    $("set-hint").textContent = hint;
    $("set-hint").hidden = hint === "";
  }

  function renderRoute() {
    if (!state) {
      return;
    }
    fillTimer($("route-minutes"));

    // De lijst met punten.
    const list = $("waypoints");
    const key = JSON.stringify(waypoints);
    if (list.dataset.key !== key) {
      list.dataset.key = key;
      list.replaceChildren();
      waypoints.forEach((point, index) => {
        const item = el("li");
        item.append(el("span", "", formatCoords(point.lat, point.lon)));
        const remove = el("button", "icon-btn", "×");
        remove.type = "button";
        remove.setAttribute("aria-label", "Punt " + (index + 1) + " weghalen");
        remove.addEventListener("click", () => {
          waypoints.splice(index, 1);
          routeChanged();
        });
        item.append(remove);
        list.append(item);
      });
    }
    $("waypoints-empty").hidden = waypoints.length > 0;
    if (waypoints.length === 1) {
      $("waypoints-empty").hidden = false;
      $("waypoints-empty").textContent = "Klik nu op de kaart voor het volgende punt.";
    } else {
      $("waypoints-empty").textContent =
        "Nog geen punten. Klik op de kaart voor het beginpunt, en daarna voor elk volgend punt.";
    }
    $("waypoint-buttons").hidden = waypoints.length === 0;

    const speed = Number($("speed").value);
    $("speed-out").textContent = speed + " km/u";
    $("route-credit").hidden = !$("road").checked;

    // Samenvatting en waarschuwing.
    const summary = $("route-summary");
    const warning = $("route-warning");
    hide(warning);
    if (plan) {
      const seconds = plan.length_m / (speed / 3.6);
      const repeat = $("repeat").checked;
      summary.textContent =
        formatDistance(plan.length_m) +
        " · ongeveer " +
        formatDuration(seconds) +
        " bij " +
        speed +
        " km/u" +
        (repeat ? " per ronde" : "");
      show(summary);
      const timerMinutes = Number($("route-minutes").value);
      if (repeat) {
        warning.textContent = "De route herhaalt zich tot de timer afloopt (" + formatMinutes(timerMinutes) + ") of tot je stopt.";
        show(warning);
      } else if (seconds > timerMinutes * 60) {
        warning.textContent =
          "Let op: de route duurt langer dan de timer (" + formatMinutes(timerMinutes) + "). Kies een langere tijd, anders stopt de timer onderweg.";
        show(warning);
      }
    } else {
      hide(summary);
    }

    let hint = blockReason();
    if (!hint && waypoints.length < 2) {
      hint = "Klik minstens twee punten aan op de kaart.";
    } else if (!hint && planBusy) {
      hint = "Route wordt uitgerekend…";
    } else if (!hint && !plan && !planError) {
      hint = "Route wordt uitgerekend…";
    }
    const button = $("route-start");
    button.disabled = routeBusy || !plan || blockReason() !== "";
    button.textContent = routeBusy ? "Bezig…" : "Start route";
    $("route-hint").textContent = planError ? "" : hint;
    $("route-hint").hidden = planError !== "" || hint === "";
    if (planError) {
      setError("route-error", planError);
    } else if (!routeBusy && $("route-error").dataset.kind !== "start") {
      setError("route-error", "");
    }
  }

  function fillTimer(select) {
    if (select.options.length > 0) {
      return;
    }
    for (const minutes of state.timer.choices) {
      const option = el("option", "", formatMinutes(minutes));
      option.value = String(minutes);
      option.selected = minutes === state.timer.default;
      select.append(option);
    }
  }

  function renderSearch() {
    const enabled = state.settings.address_search;
    $("search-form").hidden = !enabled;
    $("search-off").hidden = enabled;
    if (!enabled) {
      hide($("search-results"));
      hide($("search-credit"));
      setError("search-error", "");
    }
    if (!settingBusy) {
      $("setting-search").checked = enabled;
    }
  }

  function renderMap() {
    const sim = state.simulation;
    if (sim.active) {
      activeMarker.setLatLng([sim.lat, sim.lon]).addTo(map);
      const moving = Boolean(sim.route && !sim.route.finished && !sim.route.paused);
      if (!centeredOnActive) {
        // Bij het openen van de pagina terwijl er al iets actief is: laat zien waar.
        centeredOnActive = true;
        if (!map.getBounds().contains([sim.lat, sim.lon])) {
          map.setView([sim.lat, sim.lon], 14);
        }
      } else if (moving && !map.getBounds().pad(-0.12).contains([sim.lat, sim.lon])) {
        map.panTo([sim.lat, sim.lon]); // volg de bewegende stip
      }
    } else {
      activeMarker.remove();
      centeredOnActive = true;
    }

    // De lijn van de route die nu loopt.
    const routeId = sim.active && sim.route ? sim.route.plan_id : null;
    if (routeId !== drawnRouteId) {
      drawnRouteId = routeId;
      if (routeId === null) {
        activeLine.remove();
      } else if (plan && plan.id === routeId) {
        activeLine.setLatLngs(plan.path).addTo(map);
      } else {
        loadActivePath(routeId);
      }
    }
  }

  async function loadActivePath(routeId) {
    try {
      const data = await api("/api/route/path");
      if (drawnRouteId === routeId) {
        activeLine.setLatLngs(data.path).addTo(map);
      }
    } catch (error) {
      // De route is intussen gestopt; dan valt er niets te tekenen.
    }
  }

  function renderFooter() {
    $("demo-badge").hidden = !state.app.demo;
    $("version").textContent = state.app.version;
    $("log-path").textContent = state.app.log;
    $("log-line").hidden = !state.app.log;
  }

  // ---------------------------------------------------------------------
  // 5. Vaste plek, favorieten en zoeken
  // ---------------------------------------------------------------------

  function select(lat, lon) {
    selected = { lat: lat, lon: lon, text: formatCoords(lat, lon) };
    selectedMarker.setLatLng([lat, lon]).addTo(map);
    $("coords").value = selected.text;
    setError("coords-error", "");
    setError("set-error", "");
    if (state) {
      renderFixed();
    }
  }

  function clearSelected() {
    selected = null;
    selectedMarker.remove();
  }

  async function loadFavorites() {
    if (favoritesLoading) {
      return;
    }
    favoritesLoading = true;
    try {
      const data = await api("/api/favorites");
      favorites = data.favorites;
      favoritesVersion = data.state.favorites_version;
      renderFavorites();
    } catch (error) {
      // poll() probeert het vanzelf opnieuw
    } finally {
      favoritesLoading = false;
    }
  }

  function placeButton(name, lat, lon) {
    const button = el("button", "pick");
    button.type = "button";
    button.append(el("span", "pick-name", name));
    button.append(el("span", "pick-sub", formatCoords(lat, lon)));
    button.addEventListener("click", () => pick(lat, lon, true));
    return button;
  }

  function renderFavorites() {
    const list = $("favorites");
    list.replaceChildren();
    for (const favorite of favorites) {
      const item = el("li");
      item.append(placeButton(favorite.name, favorite.lat, favorite.lon));
      const remove = el("button", "icon-btn", "×");
      remove.type = "button";
      remove.setAttribute("aria-label", "Favoriet " + favorite.name + " verwijderen");
      remove.addEventListener("click", () => removeFavorite(favorite));
      item.append(remove);
      list.append(item);
    }
    $("favorites-count").textContent = String(favorites.length);
    $("favorites-empty").hidden = favorites.length > 0;
  }

  function openSave(point) {
    saveTarget = point;
    $("save-coords").textContent = point.text;
    $("save-name").value = "";
    setError("save-error", "");
    show($("save-form"));
    $("favorites-box").open = true;
    $("save-name").focus();
  }

  function closeSave() {
    saveTarget = null;
    hide($("save-form"));
  }

  async function saveFavorite(event) {
    event.preventDefault();
    if (!saveTarget) {
      return;
    }
    try {
      const data = await api("/api/favorites", {
        name: $("save-name").value,
        lat: saveTarget.lat,
        lon: saveTarget.lon,
      });
      favorites = data.favorites;
      favoritesVersion = data.state.favorites_version;
      renderFavorites();
      closeSave();
    } catch (error) {
      setError("save-error", error.message);
    }
  }

  async function removeFavorite(favorite) {
    if (!window.confirm('Favoriet "' + favorite.name + '" verwijderen?')) {
      return;
    }
    try {
      const data = await api("/api/favorites/" + encodeURIComponent(favorite.id), undefined, "DELETE");
      favorites = data.favorites;
      favoritesVersion = data.state.favorites_version;
      renderFavorites();
    } catch (error) {
      // de lijst blijft zoals hij was
    }
  }

  // Zoeken gebeurt ALLEEN als je op de knop klikt of op Enter drukt, nooit
  // terwijl je typt. Dat is een regel van de zoekdienst.
  async function searchAddress(event) {
    event.preventDefault();
    const query = $("search").value.trim();
    if (!query) {
      return;
    }
    const button = $("search-button");
    button.disabled = true;
    button.textContent = "Zoeken…";
    setError("search-error", "");
    try {
      const data = await api("/api/search", { q: query });
      const list = $("search-results");
      list.replaceChildren();
      for (const place of data.places) {
        const item = el("li");
        item.append(placeButton(place.name, place.lat, place.lon));
        list.append(item);
      }
      list.hidden = data.places.length === 0;
      $("search-credit").hidden = data.places.length === 0;
      if (data.places.length === 0) {
        setError("search-error", "Niets gevonden. Probeer een andere schrijfwijze, of voeg de plaatsnaam toe.");
      }
    } catch (error) {
      setError("search-error", error.message);
    } finally {
      button.disabled = false;
      button.textContent = "Zoek";
    }
  }

  async function changeSearchSetting() {
    settingBusy = true;
    try {
      await api("/api/settings", { address_search: $("setting-search").checked });
    } catch (error) {
      // de schakelaar springt bij de volgende verversing terug naar de echte stand
    } finally {
      settingBusy = false;
      if (state) {
        renderSearch();
      }
    }
  }

  // ---------------------------------------------------------------------
  // 6. Route
  // ---------------------------------------------------------------------

  function routeMode() {
    return $("road").checked ? transport : "line";
  }

  // Alles wat de route bepaalt, als één tekst. Verandert die, dan moet de
  // route opnieuw uitgerekend worden.
  function routeKey() {
    return JSON.stringify([waypoints, routeMode(), $("round-trip").checked, $("repeat").checked]);
  }

  function addWaypoint(lat, lon) {
    if (waypoints.length >= MAX_WAYPOINTS) {
      planError = "Een route mag hooguit " + MAX_WAYPOINTS + " punten hebben.";
      renderRoute();
      return;
    }
    waypoints.push({ lat: lat, lon: lon });
    routeChanged();
  }

  function routeChanged() {
    plan = null;
    planError = "";
    $("route-error").dataset.kind = "";
    drawRouteBuilder();
    clearTimeout(planTimer);
    if (waypoints.length >= 2) {
      // Een rechte lijn rekent Ghost zelf uit. Voor een route over de weg
      // wachten we even, zodat snel achter elkaar klikken niet steeds een
      // nieuwe vraag aan de routeserver stuurt.
      planTimer = setTimeout(computePlan, routeMode() === "line" ? 50 : 700);
    }
    if (state) {
      renderRoute();
    }
  }

  async function computePlan() {
    if (planBusy) {
      planTimer = setTimeout(computePlan, 300);
      return;
    }
    const key = routeKey();
    planBusy = true;
    renderRoute();
    try {
      const data = await api("/api/route/plan", {
        waypoints: waypoints.map((point) => [point.lat, point.lon]),
        mode: routeMode(),
        round_trip: $("round-trip").checked,
        repeat: $("repeat").checked,
      });
      if (key === routeKey()) {
        plan = data.plan;
        plan.key = key;
        planError = "";
      }
    } catch (error) {
      if (key === routeKey()) {
        plan = null;
        planError = error.message;
      }
    } finally {
      planBusy = false;
      drawRouteBuilder();
      renderRoute();
    }
  }

  // Teken de punten en de lijn van de route die je aan het maken bent.
  function drawRouteBuilder() {
    waypointLayer.clearLayers();
    waypoints.forEach((point, index) => {
      const marker = L.marker([point.lat, point.lon], {
        icon: pin("waypoint", 26, index + 1),
        draggable: true,
        keyboard: false,
        title: "Punt " + (index + 1) + " (sleep om te verplaatsen)",
      });
      marker.on("dragend", () => {
        const moved = marker.getLatLng().wrap();
        waypoints[index] = { lat: moved.lat, lon: moved.lng };
        routeChanged();
      });
      waypointLayer.addLayer(marker);
    });
    if (plan) {
      planLine.setLatLngs(plan.path);
      draftLine.setLatLngs([]);
    } else {
      planLine.setLatLngs([]);
      draftLine.setLatLngs(waypoints.map((point) => [point.lat, point.lon]));
    }
  }

  function applyTab() {
    const route = tab === "route";
    $("tab-fixed").setAttribute("aria-selected", String(!route));
    $("tab-route").setAttribute("aria-selected", String(route));
    $("panel-fixed").hidden = route;
    $("panel-route").hidden = !route;
    $("coords-button").textContent = route ? "Voeg toe" : "Toon op kaart";
    $("tab-help").textContent = route
      ? "Klik op de kaart om punten toe te voegen. Je nep-locatie beweegt van punt 1 naar het laatste punt. Punten kun je verslepen."
      : "Klik op de kaart om een plek te kiezen. Je iPhone staat dan op die ene plek.";
    // Op de kaart alleen laten zien wat bij dit tabblad hoort.
    if (route) {
      selectedMarker.remove();
      waypointLayer.addTo(map);
      draftLine.addTo(map);
      planLine.addTo(map);
    } else {
      waypointLayer.remove();
      draftLine.remove();
      planLine.remove();
      if (selected) {
        selectedMarker.addTo(map);
      }
    }
  }

  async function startRoute() {
    if (!plan || routeBusy) {
      return;
    }
    routeBusy = true;
    $("route-error").dataset.kind = "";
    setError("route-error", "");
    renderRoute();
    try {
      await api("/api/route/start", {
        plan_id: plan.id,
        speed_kmh: Number($("speed").value),
        minutes: Number($("route-minutes").value),
      });
    } catch (error) {
      $("route-error").dataset.kind = "start";
      setError("route-error", error.message);
      if (error.message.indexOf("verlopen") !== -1) {
        routeChanged(); // reken de route opnieuw uit
      }
    } finally {
      routeBusy = false;
      renderRoute();
    }
  }

  async function toggleRoutePause() {
    const route = state && state.simulation.route;
    if (!route) {
      return;
    }
    try {
      await api(route.paused ? "/api/route/resume" : "/api/route/pause", {});
    } catch (error) {
      // de knop laat bij de volgende verversing de echte stand zien
    }
  }

  async function changeLiveSpeed() {
    try {
      await api("/api/route/speed", { speed_kmh: Number($("live-speed").value) });
    } catch (error) {
      // idem
    } finally {
      speedDragging = false;
    }
  }

  async function nudge(direction) {
    setError("nudge-error", "");
    try {
      await api("/api/nudge", { direction: direction, meters: Number($("nudge-step").value) });
    } catch (error) {
      setError("nudge-error", error.message);
    }
  }

  // ---------------------------------------------------------------------
  // 7. Het venster "geen contact"
  // ---------------------------------------------------------------------

  function showOverlay(title, text, warning, foot) {
    $("overlay-title").textContent = title;
    $("overlay-text").textContent = text;
    $("overlay-warning").textContent = warning || "";
    $("overlay-warning").hidden = !warning;
    $("overlay-foot").textContent = foot || "";
    show($("overlay"));
  }

  function hideOverlay() {
    if (!stopped) {
      hide($("overlay"));
    }
  }

  function showOffline() {
    const wasActive = Boolean(state && (state.simulation.active || state.simulation.uncertain));
    showOverlay(
      "Geen contact met Ghost",
      "Ghost draait niet meer, of het zwarte venster is gesloten. Bij het stoppen vraagt Ghost altijd eerst " +
        "je echte locatie terug.",
      wasActive
        ? "Er was een nep-locatie actief toen het contact wegviel. Controleer in Apple Kaarten of je echte " +
            "locatie weer klopt. Klopt het niet: start Ghost opnieuw, dan wordt je echte locatie meteen teruggevraagd."
        : "",
      "Start Ghost opnieuw met start.bat. Deze pagina maakt dan vanzelf weer contact."
    );
  }

  // ---------------------------------------------------------------------
  // 8. Jouw klikken
  // ---------------------------------------------------------------------

  async function stop() {
    if (stopBusy || stopped) {
      return;
    }
    stopBusy = true;
    if (state) {
      renderStop();
    }
    try {
      await api("/api/stop", {});
    } catch (error) {
      if (error.status === 0) {
        showOffline();
      }
      // Andere fouten staan al in de melding en de balk (die komen mee in `state`).
    } finally {
      stopBusy = false;
      if (state) {
        render(state);
      }
    }
  }

  async function setLocation() {
    if (!selected || setBusy) {
      return;
    }
    setBusy = true;
    setError("set-error", "");
    renderFixed();
    try {
      await api("/api/location", {
        lat: selected.lat,
        lon: selected.lon,
        minutes: Number($("minutes").value),
      });
      clearSelected();
    } catch (error) {
      setError("set-error", error.message);
    } finally {
      setBusy = false;
      renderFixed();
    }
  }

  async function showCoords(event) {
    event.preventDefault();
    const text = $("coords").value.trim();
    if (!text) {
      return;
    }
    try {
      const point = await api("/api/parse", { text: text });
      setError("coords-error", "");
      pick(point.lat, point.lon, true);
      if (tab === "route") {
        $("coords").value = "";
      }
    } catch (error) {
      setError("coords-error", error.message);
    }
  }

  async function runAction(action) {
    try {
      await api("/api/device/" + action, {});
    } catch (error) {
      // De uitleg staat in de melding (die komt mee in `state`).
    }
  }

  async function check() {
    $("check").disabled = true;
    $("check").textContent = "Bezig…";
    try {
      await api("/api/device/check", {});
    } catch (error) {
      // poll() laat het zien als Ghost niet meer bereikbaar is
    }
  }

  async function extend() {
    try {
      await api("/api/timer", { add_minutes: EXTEND_MINUTES });
    } catch (error) {
      // niets: de timer in beeld blijft kloppen met Ghost
    }
  }

  async function copyActive() {
    const button = $("active-copy");
    try {
      await navigator.clipboard.writeText(state.simulation.text);
      button.textContent = "Gekopieerd";
    } catch (error) {
      button.textContent = "Kopiëren lukt niet";
    }
    setTimeout(() => {
      button.textContent = "Kopieer coördinaten";
    }, 1600);
  }

  async function quit() {
    const sure = window.confirm(
      "Ghost afsluiten?\n\nJe echte locatie wordt eerst teruggevraagd. Daarna kun je dit tabblad sluiten."
    );
    if (!sure) {
      return;
    }
    let result;
    try {
      result = await api("/api/quit", {});
    } catch (error) {
      showOffline();
      return;
    }
    stopped = true;
    clearInterval(pollTimer);
    document.title = "Ghost (afgesloten)";
    let text = "Er was geen nep-locatie actief.";
    let warning = "";
    if (result.needs_restore) {
      text = "";
      warning =
        "LET OP: je echte locatie kon NIET worden teruggevraagd. Controleer de kabel, ontgrendel je iPhone en " +
        "start Ghost opnieuw. Ghost vraagt dan meteen je echte locatie terug.";
    } else if (result.restored) {
      text =
        (result.confirmed
          ? "Je echte locatie is teruggevraagd en het verzoek is bij de iPhone aangekomen. "
          : "Je echte locatie is teruggevraagd, maar de iPhone heeft de ontvangst niet bevestigd. ") +
        "Controleer in Apple Kaarten of je echte locatie weer klopt.";
    }
    showOverlay(
      "Ghost is afgesloten",
      text,
      warning,
      "Je kunt dit tabblad en het zwarte venster nu sluiten. Opnieuw starten: dubbelklik op start.bat."
    );
  }

  // --- knoppen: stoppen, vaste plek, status ---
  $("stop").addEventListener("click", stop);
  $("set").addEventListener("click", setLocation);
  $("coords-form").addEventListener("submit", showCoords);
  $("check").addEventListener("click", check);
  $("extend").addEventListener("click", extend);
  $("interrupted-continue").addEventListener("click", continueRoute);
  $("interrupted-forget").addEventListener("click", forgetRoute);
  $("quit").addEventListener("click", quit);
  $("notice-close").addEventListener("click", () => {
    if (state && state.notice) {
      dismissedNotice = state.notice.id;
      renderNotice();
    }
  });

  // --- knoppen: het actief-blok ---
  $("active-copy").addEventListener("click", copyActive);
  $("active-show").addEventListener("click", () => {
    map.setView([state.simulation.lat, state.simulation.lon], Math.max(map.getZoom(), 15));
  });
  $("active-save").addEventListener("click", () => {
    const sim = state.simulation;
    openSave({ lat: sim.lat, lon: sim.lon, text: sim.text });
  });
  for (const button of document.querySelectorAll(".pad-btn")) {
    button.addEventListener("click", () => nudge(button.dataset.direction));
  }
  $("route-pause").addEventListener("click", toggleRoutePause);
  $("live-speed").addEventListener("input", () => {
    speedDragging = true;
    $("live-speed-out").textContent = $("live-speed").value + " km/u";
  });
  $("live-speed").addEventListener("change", changeLiveSpeed);

  // --- knoppen: favorieten, zoeken, instellingen ---
  $("selected-save").addEventListener("click", () => {
    if (selected) {
      openSave(selected);
    }
  });
  $("save-form").addEventListener("submit", saveFavorite);
  $("save-cancel").addEventListener("click", closeSave);
  $("search-form").addEventListener("submit", searchAddress);
  $("setting-search").addEventListener("change", changeSearchSetting);
  $("search-enable").addEventListener("click", () => {
    const box = $("settings-box");
    box.open = true;
    box.scrollIntoView({ behavior: "smooth", block: "center" });
    $("setting-search").focus();
  });

  // --- knoppen: tabbladen en route ---
  $("tab-fixed").addEventListener("click", () => {
    tab = "fixed";
    applyTab();
  });
  $("tab-route").addEventListener("click", () => {
    tab = "route";
    applyTab();
  });
  $("wp-undo").addEventListener("click", () => {
    waypoints.pop();
    routeChanged();
  });
  $("wp-clear").addEventListener("click", () => {
    waypoints = [];
    routeChanged();
  });
  for (const button of document.querySelectorAll(".seg")) {
    button.addEventListener("click", () => {
      transport = button.dataset.transport;
      for (const other of document.querySelectorAll(".seg")) {
        other.setAttribute("aria-checked", String(other === button));
      }
      // Bij ander vervoer hoort een andere gewone snelheid (lopen 5, fietsen 15, auto 40).
      const presets = state ? state.speeds.presets : { lopen: 5, fietsen: 15, auto: 40 };
      const names = { foot: "lopen", bike: "fietsen", car: "auto" };
      $("speed").value = String(presets[names[transport]]);
      routeChanged();
    });
  }
  $("speed").addEventListener("input", () => {
    if (state) {
      renderRoute();
    }
  });
  for (const id of ["road", "round-trip", "repeat"]) {
    $(id).addEventListener("change", routeChanged);
  }
  $("route-minutes").addEventListener("change", () => {
    if (state) {
      renderRoute();
    }
  });
  $("route-start").addEventListener("click", startRoute);

  // Esc doet hetzelfde als de rode knop. Altijd.
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      stop();
    }
  });

  // Waarschuw bij het sluiten van het tabblad als er nog iets actief is.
  window.addEventListener("beforeunload", (event) => {
    if (!stopped && state && state.simulation.active) {
      event.preventDefault();
      event.returnValue = "";
    }
  });

  applyTab();
  poll();
  pollTimer = setInterval(poll, POLL_MS);
})();
