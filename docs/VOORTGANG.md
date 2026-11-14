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

### Keuzes bij routes, favorieten en zoeken (13 nov 2026, tweede ronde)

14. "Volg de weg" staat standaard AAN. De aangeklikte punten gaan dan naar
    `routing.openstreetmap.de`. Dat staat bij het vinkje; uit = rechte lijn, niets naar internet.
    Routes over de weg langer dan 500 km hemelsbreed vraagt Ghost niet aan de gratis server.
15. Aan het eind van een route blijft de nep-locatie op het eindpunt staan (tot timer of stop).
16. Een route beweegt met vaste snelheid, zonder nagemaakte "menselijke" ruis. Ghost verbergt de
    simulatie niet (zie README); dat blijft zo.
17. Verschuiven met de pijltjes kan alleen als de plek stilstaat, niet tijdens een route.
    De timer loopt daarbij door.
18. Favorieten worden bewaard op schijf (`favorieten.json`). Er is bewust GEEN lijst met
    "recent gebruikt": dat zou ongevraagd bijhouden waar je was.
19. Adres zoeken: standaard UIT (besluit 2). De schakelaar staat bij Instellingen, met de regels
    en de link naar het beleid erbij. Zoeken gebeurt alleen op de knop of Enter.
20. Ghost meldt zich bij de OSM-diensten als `Ghost-locatiesimulator/<versie>`, ZONDER de
    repo-URL. De open vraag bij besluit 5 blijft dus open; het beleid eist alleen een herkenbare
    naam.
21. In demomodus blijven favorieten en instellingen in het geheugen; je echte bestanden worden
    niet aangeraakt.

### Inloggen met een account (Supabase): NOG NIET GEBOUWD, wacht op een besluit

De gebruiker vroeg om inloggen met een account via Supabase, met tweestapsverificatie (2FA)
en een bevestigde e-mail. Stand van zaken:

- Er bestond nog geen Supabase-project voor Ghost. Het aanmaken ervan is in de cloud-sessie
  tegengehouden door de beveiliging van die sessie. Er is dus niets aangemaakt en er is geen
  inlogcode geschreven (ongetest inlogwerk is erger dan geen inlogwerk).
- Gecontroleerd in de Supabase-documentatie:
  - 2FA met een authenticator-app (TOTP) is gratis; de sms-variant kost geld.
  - Na 2FA staat in het token `aal: "aal2"`; daarop kan Ghost controleren.
  - De ingebouwde mailserver stuurt ALLEEN naar leden van de Supabase-organisatie
    (fout `email_address_not_authorized` voor anderen) en maar een paar mails per uur. Vrienden
    kunnen zich dus pas registreren nadat er een eigen mailserver (SMTP) is ingesteld.
- Eerlijke kanttekeningen die de gebruiker moet wegen:
  - Ghost draait op de eigen laptop. Een inlog houdt niemand tegen die achter die laptop zit en
    de terminal-commando's of de code kan gebruiken. Het is een drempel, geen slot.
  - Ghost wordt afhankelijk van internet en van Supabase. Gratis projecten worden na ongeveer
    een week zonder gebruik gepauzeerd; dan kan niemand inloggen tot het project is hervat.
  - E-mailadressen van gebruikers komen in een clouddatabase te staan.
  - De rode knop mag NOOIT van inloggen afhangen.
- Ontwerp als het doorgaat: Ghost praat zelf met Supabase (de pagina niet, zodat de strenge
  browserregels blijven gelden); wachtwoord + bevestigde e-mail + verplichte TOTP; pas bij
  `aal2` mag er een locatie gezet worden; stoppen, status en afsluiten werken altijd zonder
  inloggen; alleen de publieke sleutel staat in de repo.

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
- Nominatim-beleid (gelezen 13 nov 2026), strenger dan eerder genoteerd. Hoofdpunten: hooguit
  1 verzoek per seconde; een herkenbare User-Agent of Referer; bron vermelden; NIET automatisch
  aanvullen; geen reeksen zoekopdrachten; resultaten bewaren. Over taalmodellen staat er dat door
  een LLM gemaakte code aan alle regels moet voldoen, en dat gebruik alleen mag als de
  ontwikkelaar van de app daar bewust en geïnformeerd voor kiest en zelf verantwoordelijk is.
  Daarom: standaard uit, uitleg en link bij de schakelaar (`ghost/osm.py`, Instellingen).
- Routeserver opnieuw getest: antwoord `code: "Ok"` met `routes[0].geometry.coordinates` als
  `[lengtegraad, breedtegraad]`. Fouten komen soms als HTTP 200 met een andere `code`
  (bv. bij 0,0), soms als HTTP 400 met `code: "NoRoute"`. `ghost/osm.py` vangt beide af.
- Venster sluiten op Windows: Microsoft documenteert 5 seconden voor `CTRL_CLOSE_EVENT`.
  NIET gevonden: in welke volgorde meerdere programma's in hetzelfde venster worden gewaarschuwd.
  Dat is van belang omdat `start.bat` Ghost start via `uv` (en mogelijk een tussenprogramma van
  de virtuele omgeving). Zie de handmatige test hieronder.

## Wat er gebouwd is (stappen 1 t/m 7, 9, start.bat en adres zoeken)

- `ghost/device/` - interface, echte iPhone (`iphone.py`), nep-iPhone (`fake.py`)
- `ghost/cli.py` - commando's `status`, `pair`, `reveal-developer-mode`, `mount`,
  `set`, `clear`, `web`, met `--demo` en `--debug`
- `ghost/geo.py`, `ghost/coords.py` - routewiskunde en coördinaten
- Stap 3: `ghost/safety.py` (SafetyManager: schone start, altijd clear bij stoppen,
  merkbestand) en `ghost/exit_hooks.py` (Ctrl+C, en venster dicht via SetConsoleCtrlHandler).
- Stap 5: `ghost/web/security.py` (Host, Origin, sessietoken, geen CORS),
  `ghost/web/app.py` (FastAPI), `ghost/web/server.py` (alleen 127.0.0.1; afsluitvolgorde in
  één `finally`), `ghost/controller.py` (timer, bewaking van de verbinding, statuspaneel).
- Stap 7 (routes): `Controller.plan_route/start_route/pause_route/...` en
  `Controller._advance_route` (de "MovementEngine": elke seconde het volgende punt),
  `ghost/osm.py` (routeserver en zoekdienst, elk hooguit 1 verzoek per seconde),
  `ghost/net.py` (de enige plek waar Ghost iets van internet ophaalt).
- Stap 9 (favorieten): `ghost/store.py`, bestand `favorieten.json`.
- Verschuiven met pijltjes: `Controller.nudge` en `geo.destination_point`.
- Adres zoeken (Nominatim), standaard uit: `SearchClient` in `ghost/osm.py`,
  instelling in `instellingen.json`.
- Stap 6: `ghost/web/static/` (kaart met Leaflet + OpenStreetMap, statuspaneel met knoppen,
  balk "NEP-LOCATIE ACTIEF", rode stopknop + Esc, auto-stop-timer).
- `start.bat` en `start-demo.bat` (dubbelklikken; de browser gaat vanzelf open).
- `scripts/installeer-uv.ps1` (14 nov 2026): staat uv niet op de laptop, dan halen
  `start.bat` en `start-demo.bat` zelf uv 0.11.32 op van de officiele GitHub-release,
  gecontroleerd met een vaste SHA256 (x86_64 en aarch64), naar
  `%LOCALAPPDATA%\Ghost\tools\uv-0.11.32\uv.exe`. Python en cache van die uv komen in
  `%LOCALAPPDATA%\Ghost\python` en `\cache`. Geen PATH-wijziging, geen admin. Een bestaande
  uv (PATH of `%USERPROFILE%\.local\bin`) gaat voor. De gebruiker hoeft dus alleen iTunes te
  installeren. Versie staat in de .bat-bestanden en het script; `tests/test_start_scripts.py`
  bewaakt dat die gelijk zijn, en dat de bestanden CRLF en ASCII zijn.
  Bijwerken naar een nieuwere uv: versie op drie plekken aanpassen en de twee hashes uit de
  `.sha256`-bestanden van die GitHub-release overnemen.
- 379 tests, allemaal groen op Linux (cloud). De 113 oudste waren ook groen op de
  Windows-laptop; de rest is daar nog niet gedraaid.
- De pagina is in een echte browser (Chromium) getest tegen de nep-iPhone: plek kiezen,
  coördinaten plakken, zetten, +15 min, Esc, statuspaneel met problemen, kabel eruit en erin,
  afsluiten, "geen contact"; en in de tweede ronde: verschuiven, kopiëren, favorieten, een
  route als rechte lijn en over de weg (echte routeserver), pauze, snelheid onderweg, herladen
  tijdens een route, en één echte zoekopdracht. Geen meldingen over de strenge browserregels (CSP).

## Robuuste verbinding (14 nov 2026)

Aanleiding: bij een vriend van de gebruiker (iOS-versie nog onbekend) meldde Ghost eerst
langdurig "Geen iPhone gevonden" terwijl Windows de iPhone wel zag; later werkte een vaste plek,
maar kort na het starten van een route viel alles weg. Er is GEEN logbestand van dat voorval, dus
de oorzaak van het wegvallen zelf is niet vastgesteld. Wel vastgesteld in de code:

- Eén mislukt punt van een route, of één keer `connection_alive() == False`, leidde tot: route
  weggooien en de echte locatie terugvragen. Geen enkele poging om opnieuw te verbinden.
- pymobiledevice3 11.54.0 en 11.55.0 veranderen niets aan de USB-tunnel (diff bekeken); een
  update lost dit dus niet op. Versie blijft 11.53.0.

Wat er is gebouwd:

- `ghost/device/iphone.py`: `_send_with_retry` (set en clear proberen het na een mislukking één
  keer opnieuw over een verse verbinding; teller `reconnects`); `_find_usb_device` kijkt 3 keer
  met 0,5 s ertussen; `TUNNEL_TIMEOUT_S` van 30 naar 20.
- `ghost/hostusb.py`: vraagt Windows via PowerShell (`Get-PnpDevice`, VID_05AC, PID_12A8/12AB)
  of er een iPhone/iPad op USB zit. Ziet Windows hem wel en usbmux niet, dan is het probleem
  `Problem.NOT_LISTED` met eigen uitleg. NIET GETEST op Windows; bij elke fout is het antwoord
  "onbekend" en volgt de gewone melding "Geen iPhone gevonden".
- `SafetyManager.set_location(..., restore_on_failure=False)`: een actieve nep-locatie
  verplaatsen zonder bij een fout meteen op te ruimen.
- `Controller`: `_reconnect` (elke 2 s opnieuw verbinden en dezelfde plek opnieuw zetten, hooguit
  `RECONNECT_GRACE_S` = 30 s; de route loopt daarna door zonder sprong); daarna pas het oude
  gedrag (`connection_lost`, rode balk, clear zodra het kan). Een door storing afgebroken route
  wordt bewaard (`interrupted`) en is te hervatten via `continue_route` (`/api/route/continue`,
  knop "Hervat route"); `/api/route/forget` gooit hem weg. Dit is het oorspronkelijke ontwerp:
  "hervat na herverbinden pas nadat ik dat bevestig".
- `ghost/power.py`: `SetThreadExecutionState` zolang er iets actief is, zodat de laptop niet
  vanzelf gaat slapen. NIET GETEST op Windows.
- `ghost/diagnose.py` + `diagnose.bat`: rapport zonder naam, serienummer of exacte coördinaten,
  met een stabiliteitstest (20 keer clear, één per seconde).
- Pagina: oranje balk "VERBINDING EVEN WEG" en het blok "Hervat route". In Chromium getest tegen
  de nep-iPhone: korte hapering, lange onderbreking, hervatten.

Bluetooth: kan niet. usbmux/lockdown/RSD lopen via USB of wifi (netwerk), niet via Bluetooth.
Wifi is een mogelijke latere uitbreiding (pymobiledevice3 kent tunnels via wifi), maar is voor de
veiligheid zwakker: valt wifi weg, dan blijft de nep-locatie staan en kan Ghost niets terugvragen.

Nog te doen zodra er een logbestand of diagnose van de vriend is: de echte oorzaak van het
wegvallen bepalen (kandidaten: kabel/poort/hub, de iPhone die vergrendelt, USB-energiebeheer van
Windows, de userspace-tunnel zelf).

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

- [ ] `uv run pytest` op de Windows-laptop: 379 tests groen?
- [ ] `start.bat` dubbelklikken: zwart venster, browser opent `http://127.0.0.1:8765`.
- [ ] Kabel 5 seconden eruit tijdens een route en weer erin: oranje balk, daarna "Verbinding
      hersteld" en de route loopt door? Blijft de stip in Apple Kaarten bewegen?
- [ ] Kabel langer dan 30 seconden eruit tijdens een route: rode balk; kabel erin: echte locatie
      terug en de knop "Hervat route"? Werkt hervatten?
- [ ] `diagnose.bat` dubbelklikken: opent Verkenner met `ghost-diagnose.txt`? Staat er bij
      "Wat Windows aan de USB-poort ziet" een regel met de iPhone?
- [ ] iPhone laten vergrendelen (scherm uit) tijdens een actieve nep-locatie: blijft de
      verbinding staan? Dit is een van de kandidaat-oorzaken en is nooit getest.
- [ ] `start.bat` op een laptop ZONDER uv (of na het tijdelijk hernoemen van
      `%USERPROFILE%\.local\bin\uv.exe`): verschijnt "uv is klaar (controlecode klopt)",
      en start Ghost daarna? NIET getest: PowerShell kon in de cloud niet worden uitgevoerd.
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
- [ ] Route (lopen, een paar honderd meter): beweegt de stip in Apple Kaarten vloeiend mee?
      Elke seconde gaat er een punt naar de iPhone; dat is op een echt toestel nog nooit gedaan.
      Let op of Ghost na een paar minuten nog steeds reageert en of de rode knop meteen werkt.
- [ ] Pauze en snelheid veranderen tijdens een route.
- [ ] Verschuiven met de pijltjes: verspringt de stip in Apple Kaarten?

## Volgende stappen

1. Eerst: de testlijst hierboven afwerken en de uitslagen hier noteren. Vooral "venster
   sluiten", "kabel eruit" en "route op een echte iPhone".
2. Besluit van de gebruiker over inloggen met Supabase (zie hierboven). Bij "ja": project
   aanmaken, inloggen bouwen en tegen het echte project testen.
3. Stap 8: de hartslag met twee standen ("ik zit erbij" en "op de achtergrond").
   Nu geldt alleen "op de achtergrond": tabblad sluiten stopt niets, de timer is het vangnet.
4. Stap 10: herstelhulp, SECURITY.md, `update.bat`.
5. Ideeën voor later: een route uit een GPX-bestand, favorieten een naam laten wijzigen,
   een route bewaren als favoriet.
