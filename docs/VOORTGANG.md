# Voortgang en overdracht

Dit bestand is bedoeld voor een nieuwe chat (of ontwikkelaar) die verder bouwt.
Lees het samen met de oorspronkelijke opdracht (de prompt "iPhone-locatiesimulator
voor Windows, veiligheid eerst").

## Gebruiker

- Beginnende programmeur: leg alles in gewone taal uit, stap voor stap, met wat je
  hoort te zien. Geen vakjargon zonder uitleg.
- Windows-laptop. Gebruikt PowerShell. Code is als ZIP gedownload (Git gaf PATH-problemen),
  map: `Downloads\ghost-claude-youthful-dirac-ci3l53 (1)\ghost-claude-youthful-dirac-ci3l53`.
  Bij een nieuwe versie: ZIP opnieuw downloaden, of Git alsnog werkend maken
  (PATH verversen of laptop herstarten).
- Start Ghost met dubbelklikken op `start.bat`. Het stappenplan staat in `docs/STARTEN.md`.
- iPhone met iOS 27. App moet ook werken op andere iPhones met iOS 17.4+.

## Genomen besluiten (antwoorden op de ontwerpvragen)

1. Testtoestel iOS 27; ondersteuning vanaf iOS 17.4.
2. Adres zoeken (Nominatim): standaard UIT, met een schakelaar en een link naar het
   gebruiksbeleid (https://operations.osmfoundation.org/policies/nominatim/).
   Het beleid noemt LLM-gegenereerde code expliciet; de gebruiker moet bewust kiezen.
3. Gebruiker start een route en gaat daarna iets anders doen. Er moeten twee standen
   komen voor de hartslag: "ik zit erbij" (tabblad sluiten = stoppen) en
   "op de achtergrond" (afhankelijk van de auto-stop-timer, niet van de hartslag).
   Doelgroep: vrienden en ontwikkelaars; de interface moet gebruiksvriendelijk zijn.
4. Persoonlijke gegevens (logs, favorieten) in `%LOCALAPPDATA%\Ghost\`, buiten de repo.
5. Naam blijft "Ghost", met een eerlijke ondertitel ("zichtbare, tijdelijke
   locatiesimulatie"). Nog open: mag de repo-URL in de User-Agent voor OSM-diensten?

### Keuzes die bij het bouwen van de webinterface zijn gemaakt (13 nov 2026)

De gebruiker vroeg om alles in één keer te bouwen. Deze keuzes zijn daarbij gemaakt zonder
vooraf te vragen; ze zijn allemaal makkelijk te veranderen (de plek staat erbij).

6. De auto-stop-timer staat ALTIJD aan. Standaard 30 minuten, keuzes 5 min tot 4 uur,
   maximum 8 uur, met een knop "+15 min". Bij elke nieuwe plek begint de timer opnieuw.
   (`ghost/controller.py`, bovenaan.)
7. Vaste poort 8765. Dat geeft een vast adres (`http://127.0.0.1:8765`) en voorkomt dat er twee
   Ghosts tegelijk draaien (er kan maar één tunnel per proces). (`ghost/web/server.py`.)
8. Kabel eruit terwijl er iets actief is: Ghost waarschuwt hard en vraagt de echte locatie
   VANZELF terug zodra de verbinding terug is. Je kunt dus niet "met de nep-locatie weglopen".
   (`Controller.tick`.)
9. De rode knop en Esc sturen altijd een clear, ook als er niets actief lijkt.
10. Het sessietoken staat in de pagina (`<meta name="ghost-token">`), nooit in het webadres.
11. Leaflet 1.9.4 staat in de repo (`ghost/web/static/vendor/leaflet`, onveranderd uit npm,
    BSD-2-Clause), zodat de pagina geen script van een andere website laadt.
12. Clear verstuurd maar niet bevestigd: één nieuwe poging over een verse verbinding. Lukt
    versturen dan wel maar bevestigen weer niet, dan telt het als "verstuurd" en zegt de pagina
    erbij dat het niet bevestigd is. (`SafetyManager._restore_locked`.)
13. Een merkbestand `%LOCALAPPDATA%\Ghost\nep-locatie-actief.txt` bestaat zolang er (mogelijk)
    iets actief is. Er staan geen coördinaten in. Staat het er nog bij het opstarten, dan meldt
    Ghost "vorige keer niet netjes afgesloten". Niet in demomodus.

## Gecontroleerde technische feiten (pymobiledevice3 11.53.0, 12 nov 2026)

- Vastgepind op 11.53.0 (nieuwer dan de 11.52.1 uit de prompt).
- `UserspaceRsdTunnel` (pymobiledevice3.remote.userspace_tunnel) is de aanbevolen
  tunnel voor apps; één tunnel per proces. `remotepairing_fallback=False` zodat
  iOS 17.0-17.3 een duidelijke fout geeft.
- `LocationSimulation.set()` wacht op antwoord; `clear()` NIET (`expects_reply=False`).
  Ghost stuurt daarom na clear een `mach_time_info`-vraag over dezelfde DVT-verbinding
  als ontvangstbevestiging.
- Op Windows: `asyncio.SelectorEventLoop` gebruiken; uvicorn met `loop="none"`
  (zo doet pymobiledevice3's eigen tunneld het ook).
- Locatiesimulatie op iOS 17+ heeft volgens de library alleen Developer Mode nodig,
  geen DDI. Bevestigd: op het iOS 27-testtoestel werkte het zonder `mount`.
- Routeserver: `https://routing.openstreetmap.de/routed-{car|bike|foot}/route/v1/driving/{lon},{lat};{lon},{lat}?overview=full&geometries=geojson`
  (getest, HTTP 200). Regels: max 1 verzoek/s, geldige User-Agent, attributie +
  "fix the map"-link, geen zwaar gebruik.
- OSM-tegels: geen strenge Referrer-Policy instellen; sessietoken nooit in de URL.
  Getest: `tile.openstreetmap.org` geeft HTTP 200 met Referer `http://127.0.0.1:8765/`.
- `UserspaceRsdTunnel` heeft een eigen bewaker (`_watch_transport_closed`): gaat de kabel eruit,
  dan breekt hij de tunnel af en wordt `tunnel.rsd` weer `None`. Ghost leest dat uit via
  `LocationDevice.connection_alive()`; dat kost geen verkeer naar de iPhone.
- fastapi 0.162.5 en uvicorn 0.54.0 zaten al in `uv.lock` (pymobiledevice3 gebruikt ze zelf).
  Ze staan nu ook exact vastgepind in `pyproject.toml`.
- uvicorn 0.54.0 vangt Ctrl+C zelf op in `Server.capture_signals()`, en slaat bij een tweede
  Ctrl+C het netjes afsluiten over. Ghost vervangt die functie daarom door een lege
  (`GhostServer` in `ghost/web/server.py`) en vangt signalen zelf op (`ghost/exit_hooks.py`).
  Een test bewaakt dat de functie in uvicorn nog bestaat.
- Python op Windows haalt bestandstypen uit het register; daar staat soms ".js = platte tekst",
  en dan weigert de browser het script. `ghost/web/app.py` legt de typen daarom zelf vast.
- Venster sluiten op Windows: Microsoft documenteert 5 seconden voor `CTRL_CLOSE_EVENT`.
  NIET gevonden: in welke volgorde meerdere programma's in hetzelfde venster worden gewaarschuwd.
  Dat is van belang omdat `start.bat` Ghost start via `uv` (en mogelijk een tussenprogramma van
  de virtuele omgeving). Zie de handmatige test hieronder.

## Wat er gebouwd is (stappen 1 t/m 6, en start.bat)

- `ghost/device/` - interface, echte iPhone (`iphone.py`), nep-iPhone (`fake.py`)
- `ghost/cli.py` - commando's `status`, `pair`, `reveal-developer-mode`, `mount`,
  `set`, `clear`, `web`, met `--demo` en `--debug`
- `ghost/geo.py`, `ghost/coords.py` - routewiskunde en coördinaten
- Stap 3: `ghost/safety.py` (SafetyManager: schone start, altijd clear bij stoppen,
  merkbestand) en `ghost/exit_hooks.py` (Ctrl+C, en venster dicht via SetConsoleCtrlHandler).
- Stap 5: `ghost/web/security.py` (Host, Origin, sessietoken, geen CORS),
  `ghost/web/app.py` (FastAPI), `ghost/web/server.py` (alleen 127.0.0.1; afsluitvolgorde in
  één `finally`), `ghost/controller.py` (timer, bewaking van de verbinding, statuspaneel).
  De MovementEngine voor routes is er nog NIET; die komt bij stap 7.
- Stap 6: `ghost/web/static/` (kaart met Leaflet + OpenStreetMap, statuspaneel met knoppen,
  balk "NEP-LOCATIE ACTIEF", rode stopknop + Esc, auto-stop-timer).
- `start.bat` en `start-demo.bat` (dubbelklikken; de browser gaat vanzelf open).
- 248 tests, allemaal groen op Linux (cloud). De 113 oude waren ook groen op de
  Windows-laptop; de nieuwe zijn daar nog niet gedraaid.
- De pagina is in een echte browser (Chromium) getest tegen de nep-iPhone: plek kiezen,
  coördinaten plakken, zetten, +15 min, Esc, statuspaneel met problemen, kabel eruit en erin,
  afsluiten, "geen contact". Geen meldingen over de strenge browserregels (CSP).

## Handmatige testresultaten (13 nov 2026, iOS 27, Windows)

- [x] `status`: alle zeven controles OK, na `reveal-developer-mode` + Developer Mode aan.
- [x] Proef B: `set 48.8584 2.2945` -> stip in Apple Kaarten naar de Eiffeltoren,
      blijft daar zolang de opdracht loopt; na Enter binnen enkele seconden terug.
- [x] Proef A: `clear` zonder actieve simulatie is onschadelijk: stip bleef op de echte
      locatie, ontvangst bevestigd. De schone start (clear bij opstarten) mag dus.
- [x] Proef C: na `set ... --no-clear` (Ghost sluit af ZONDER clear) BLEEF de stip in
      Parijs staan. Pas na `clear` kwam de echte locatie terug. Precieze wachttijd niet
      gemeten.
      GEVOLG: de nep-locatie overleeft het wegvallen van Ghost. Ghost moet dus bij elke
      afsluitroute clear sturen, bij opstarten clear sturen (schone start), en bij een
      weggevallen verbinding hard waarschuwen dat de iPhone nog op de nep-locatie staat.
      De herstelhulp moet `clear` als eerste stap noemen.
- [ ] Nog onbekend: blijft de nep-locatie staan na een HERSTART van de iPhone? Na het
      uittrekken van de kabel?
- [x] Proef D: Ctrl+C tijdens `set` zet de echte locatie terug, ontvangst bevestigd,
      daarna "Gestopt met Ctrl+C". Werkt in PowerShell op Windows.
- [ ] Overige punten uit de handmatige checklist in de prompt.

### Nog te testen door de gebruiker (webinterface, op Windows met de echte iPhone)

Niets hiervan kon in de cloud getest worden: daar is geen Windows en geen iPhone.

- [ ] `uv run pytest` op de Windows-laptop: 248 tests groen?
- [ ] `start.bat` dubbelklikken: zwart venster, browser opent `http://127.0.0.1:8765`.
- [ ] Statuspaneel wordt groen ("Klaar: ...") met de echte iPhone, en meldt "Schone start gedaan".
- [ ] Plek zetten via de kaart: stip in Apple Kaarten verspringt, gele balk verschijnt.
- [ ] Rode knop: stip terug. Daarna hetzelfde met Esc.
- [ ] Timer op 5 minuten zetten en wachten: stip vanzelf terug?
- [ ] Knop "Ghost afsluiten" terwijl er iets actief is: stip terug?
- [ ] Ctrl+C in het zwarte venster terwijl er iets actief is: stip terug?
- [ ] BELANGRIJKSTE: het zwarte venster sluiten met het kruisje terwijl er iets actief is.
      Komt de stip terug? Kijk daarna in het logbestand of er een regel staat met
      "Echte locatie teruggevraagd (venster_gesloten". Zo niet: Windows stopte Ghost te snel
      (zie "Gecontroleerde technische feiten"). De schone start bij de volgende keer starten
      vangt het dan op, maar dan moet `start.bat` anders (Ghost zonder `uv` ertussen starten).
- [ ] Kabel eruit terwijl er iets actief is: rode balk binnen een paar seconden? Kabel erin:
      wordt de echte locatie vanzelf teruggevraagd? (Dit beantwoordt meteen de open vraag
      hierboven: blijft de nep-locatie staan na het uittrekken van de kabel?)
- [ ] Laptop dichtklappen terwijl er iets actief is, later weer open: wat doet Ghost?

## Volgende stappen

1. Eerst: de testlijst hierboven afwerken en de uitslagen hier noteren. Vooral "venster
   sluiten" en "kabel eruit".
2. Stap 7: routes. MovementEngine (stuurt elke seconde het volgende punt; `ghost/geo.py`
   heeft de wiskunde al), de routeserver van OSM (zie feiten hierboven) en route-bediening
   in de pagina. De timer en de bewaking in `Controller` blijven gelden.
3. Stap 8: de hartslag met twee standen ("ik zit erbij" en "op de achtergrond").
   Nu geldt alleen "op de achtergrond": tabblad sluiten stopt niets, de timer is het vangnet.
4. Stap 9: favorieten in `%LOCALAPPDATA%\Ghost\`.
5. Stap 10: herstelhulp, adres zoeken (Nominatim, standaard UIT), SECURITY.md,
   `update.bat`.
