// De Ghost-pagina.
//
// Deze pagina beslist zelf niets over je iPhone. Hij vraagt elke seconde aan
// Ghost (op je eigen laptop) wat de stand van zaken is, laat dat zien, en
// stuurt jouw klikken door. Alle veiligheid zit in Ghost zelf: valt deze
// pagina weg, dan blijven de timer en het opruimen gewoon werken.
//
// Tekst uit Ghost (zoals de naam van je iPhone) wordt altijd als platte tekst
// getoond via textContent, nooit als HTML. Zo kan een rare naam geen kwaad.

(function () {
  "use strict";

  const TOKEN = document.querySelector('meta[name="ghost-token"]').content;
  const POLL_MS = 1000;
  const EXTEND_MINUTES = 15;
  const $ = (id) => document.getElementById(id);

  let state = null; // laatste stand van zaken uit Ghost
  let selected = null; // de plek die je hebt aangeklikt: {lat, lon, text}
  let failedPolls = 0;
  let stopped = false; // true nadat je op "Ghost afsluiten" hebt geklikt
  let stopBusy = false;
  let setBusy = false;
  let dismissedNotice = 0;
  let centeredOnActive = false;
  let lastDeviceKey = "";
  let lastBannerMode = "none";
  let pollTimer = null;

  // ---------------------------------------------------------------------
  // Praten met Ghost
  // ---------------------------------------------------------------------

  class ApiError extends Error {
    constructor(message, status, data) {
      super(message);
      this.status = status;
      this.data = data || {};
    }
  }

  async function api(path, body) {
    const options = { method: "GET", headers: { "X-Ghost-Token": TOKEN }, cache: "no-store" };
    if (body !== undefined) {
      options.method = "POST";
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
  // De kaart
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

  function pin(kind, size) {
    return L.divIcon({ className: "pin pin-" + kind, iconSize: [size, size], iconAnchor: [size / 2, size / 2] });
  }

  const selectedMarker = L.marker([0, 0], { icon: pin("selected", 22), interactive: false, keyboard: false });
  const activeMarker = L.marker([0, 0], { icon: pin("active", 24), interactive: false, keyboard: false, zIndexOffset: 500 });
  activeMarker.bindTooltip("Nep-locatie", { permanent: true, direction: "top", offset: [0, -14], className: "pin-label" });

  map.on("click", (event) => {
    const point = event.latlng.wrap();
    select(point.lat, point.lng, false);
  });

  function formatCoords(lat, lon) {
    return lat.toFixed(5) + ", " + lon.toFixed(5);
  }

  function select(lat, lon, moveMap) {
    selected = { lat: lat, lon: lon, text: formatCoords(lat, lon) };
    selectedMarker.setLatLng([lat, lon]).addTo(map);
    if (moveMap) {
      map.setView([lat, lon], Math.max(map.getZoom(), 14));
    }
    $("coords").value = selected.text;
    hide($("coords-error"));
    hide($("set-error"));
    renderLocation();
  }

  // ---------------------------------------------------------------------
  // Kleine hulpjes
  // ---------------------------------------------------------------------

  function show(element) {
    element.hidden = false;
  }

  function hide(element) {
    element.hidden = true;
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

  // ---------------------------------------------------------------------
  // Tekenen: alles wat je ziet volgt uit `state`
  // ---------------------------------------------------------------------

  function render(newState) {
    state = newState;
    renderBanner();
    renderStop();
    renderNotice();
    renderDevice();
    renderLocation();
    renderMap();
    renderFooter();
  }

  function simulationMode() {
    const sim = state.simulation;
    if (sim.connection_lost || (sim.uncertain && sim.active)) {
      return "lost";
    }
    if (sim.uncertain) {
      return "uncertain";
    }
    return sim.active ? "active" : "none";
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
      const remaining =
        sim.remaining_s === null ? "" : " · stopt vanzelf over " + formatRemaining(sim.remaining_s);
      $("banner-text").textContent = sim.text + remaining;
      document.title = "● NEP-LOCATIE ACTIEF · Ghost";
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

  function renderLocation() {
    if (!state) {
      return;
    }
    const sim = state.simulation;
    const mode = simulationMode();

    $("active-box").hidden = mode !== "active";
    if (mode === "active") {
      $("active-coords").textContent = sim.text;
      $("active-remaining").textContent = sim.remaining_s === null ? "–" : formatRemaining(sim.remaining_s);
    }

    const select = $("minutes");
    if (select.options.length === 0) {
      for (const minutes of state.timer.choices) {
        const option = el("option", "", formatMinutes(minutes));
        option.value = String(minutes);
        option.selected = minutes === state.timer.default;
        select.append(option);
      }
    }

    $("selected-line").textContent = selected ? "Gekozen plek: " + selected.text : "Nog geen plek gekozen.";

    const button = $("set");
    let hint = "";
    if (state.app.closing) {
      hint = "Ghost wordt afgesloten.";
    } else if (mode === "lost" || mode === "uncertain") {
      hint = "Eerst moet je echte locatie teruggevraagd zijn (rode knop).";
    } else if (!state.device.ready) {
      hint = "Je iPhone is nog niet klaar. Kijk bij '1. Je iPhone'.";
    } else if (!selected) {
      hint = "Klik eerst op de kaart om een plek te kiezen.";
    }
    button.disabled = setBusy || hint !== "";
    button.textContent = setBusy
      ? "Bezig…"
      : mode === "active"
        ? "Verplaats nep-locatie hierheen"
        : "Zet nep-locatie hier";
    $("set-hint").textContent = hint;
    $("set-hint").hidden = hint === "";
  }

  function renderMap() {
    const sim = state.simulation;
    if (sim.active) {
      activeMarker.setLatLng([sim.lat, sim.lon]).addTo(map);
      if (!centeredOnActive) {
        // Bij het openen van de pagina terwijl er al iets actief is: laat zien waar.
        centeredOnActive = true;
        if (!map.getBounds().contains([sim.lat, sim.lon])) {
          map.setView([sim.lat, sim.lon], 14);
        }
      }
    } else {
      activeMarker.remove();
      centeredOnActive = true;
    }
  }

  function renderFooter() {
    $("demo-badge").hidden = !state.app.demo;
    $("version").textContent = state.app.version;
    $("log-path").textContent = state.app.log;
    $("log-line").hidden = !state.app.log;
  }

  // ---------------------------------------------------------------------
  // Het venster "geen contact"
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
  // Jouw klikken
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
      selectedMarker.remove();
      selected = null;
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
    hide($("set-error"));
    renderLocation();
    try {
      await api("/api/location", {
        lat: selected.lat,
        lon: selected.lon,
        minutes: Number($("minutes").value),
      });
      selectedMarker.remove();
      selected = null;
    } catch (error) {
      $("set-error").textContent = error.message;
      show($("set-error"));
    } finally {
      setBusy = false;
      renderLocation();
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
      select(point.lat, point.lon, true);
    } catch (error) {
      $("coords-error").textContent = error.message;
      show($("coords-error"));
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

  $("stop").addEventListener("click", stop);
  $("set").addEventListener("click", setLocation);
  $("coords-form").addEventListener("submit", showCoords);
  $("check").addEventListener("click", check);
  $("extend").addEventListener("click", extend);
  $("quit").addEventListener("click", quit);
  $("notice-close").addEventListener("click", () => {
    if (state && state.notice) {
      dismissedNotice = state.notice.id;
      renderNotice();
    }
  });

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

  poll();
  pollTimer = setInterval(poll, POLL_MS);
})();
