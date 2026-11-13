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

## Wat er gebouwd is (stappen 1, 2 en 4)

- `ghost/device/` - interface, echte iPhone (`iphone.py`), nep-iPhone (`fake.py`)
- `ghost/cli.py` - commando's `status`, `pair`, `reveal-developer-mode`, `mount`,
  `set`, `clear`, met `--demo` en `--debug`
- `ghost/geo.py`, `ghost/coords.py` - routewiskunde en coördinaten
- 113 tests, allemaal groen (ook op de Windows-laptop van de gebruiker).

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

## Volgende stappen

1. Stap 3: SafetyManager (schone start met clear() pas als proef A geslaagd is,
   herstel bij Ctrl+C, venster sluiten via SetConsoleCtrlHandler, onverwachte fout).
3. Stap 5: backend (FastAPI op 127.0.0.1, sessietoken, Host- en Origin-controle,
   geen CORS) + MovementEngine.
4. Stap 6: webinterface voor vast punt: kaart, rode balk "NEP-LOCATIE ACTIEF",
   stopknop (ook Esc), auto-stop-timer. Daarna route (7), hartslag (8),
   favorieten (9), herstelhulp/README/SECURITY.md (10).
5. `start.bat` zodat de gebruiker met dubbelklikken start.
