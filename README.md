# Ghost

**A visible, temporary location simulator for your own iPhone — safety first.**

Ghost is a Windows app that replaces the GPS location of your own iPhone (connected by USB cable) with a simulated one. It uses Apple's own location simulation from Developer Mode, through the open-source library [pymobiledevice3](https://github.com/doronz88/pymobiledevice3). No jailbreak, no changes to iOS.

> **Status: work in progress.** Only the terminal commands exist so far. The web interface, routes, timer and full documentation are still being built.

## What "safe" means here

Safe for **you**, the user:

- you can always see that a simulated location is active;
- Ghost always tries to put your real location back when you stop;
- your saved places and logs stay on your own computer.

"Safe" does **not** mean invisible. Ghost does nothing to hide the simulation from apps or anti-cheat systems, and it never will.

## Disclaimer

- Use it only on your own device.
- Developer Mode lowers the security of your iPhone. Turn it off when you are not using Ghost.
- Many apps and games (for example Pokémon GO) forbid location spoofing in their terms and may block accounts.

## Try it (terminal, for now)

Requirements: Windows 64-bit, iTunes from [apple.com](https://www.apple.com/itunes/download/win64), [uv](https://docs.astral.sh/uv/), an iPhone with iOS 17.4 or later.

```
uv sync
uv run pytest
uv run python -m ghost --demo status
uv run python -m ghost status
```

## Nederlandse samenvatting

Ghost zet tijdelijk een nep-locatie op je eigen iPhone, via Apple's eigen ontwikkelaarsfunctie. De app laat altijd zien dat de nep-locatie aan staat en probeert bij het stoppen altijd je echte locatie terug te zetten. Ghost is niet onzichtbaar voor apps en is daar ook niet voor bedoeld. De app is nog in ontwikkeling.

## License

GPL-3.0-or-later, see [LICENSE](LICENSE). Ghost uses pymobiledevice3, which is GPL-3.0-or-later.
