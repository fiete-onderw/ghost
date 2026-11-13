"""Wat Ghost voor jou bewaart: favoriete plekken en instellingen.

Alles staat in gewone tekstbestanden (JSON) in %LOCALAPPDATA%\\Ghost, dus op
je eigen laptop en buiten de projectmap. Er gaat niets naar internet.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any

from ghost.coords import validate
from ghost.geo import LatLon

log = logging.getLogger(__name__)

MAX_FAVORITES = 200
MAX_NAME_LENGTH = 60


class StoreError(Exception):
    """Bewaren of lezen ging niet. `message` is voor de gebruiker."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class JsonFile:
    """Een JSON-bestand dat veilig geschreven wordt. Zonder pad blijft alles alleen in het geheugen (demomodus)."""

    def __init__(self, path: Path | None) -> None:
        self.path = path

    def load(self, default: Any) -> Any:
        if self.path is None:
            return default
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default
        except (OSError, ValueError):
            # Kapot bestand: niet weggooien maar opzij zetten, zodat er niets verloren gaat.
            log.warning("Bestand %s is onleesbaar en wordt opzij gezet", self.path.name, exc_info=True)
            try:
                self.path.replace(self.path.with_suffix(self.path.suffix + ".kapot"))
            except OSError:
                pass
            return default

    def save(self, data: Any) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Eerst naar een tijdelijk bestand, dan in één klap vervangen. Zo
            # blijft er nooit een half geschreven bestand achter.
            temporary = self.path.with_suffix(self.path.suffix + ".nieuw")
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError as error:
            log.error("Bewaren in %s mislukt", self.path.name, exc_info=True)
            raise StoreError("Bewaren is niet gelukt. Is de schijf vol?") from error


class Favorites:
    """Je bewaarde plekken, op naam."""

    def __init__(self, path: Path | None) -> None:
        self._file = JsonFile(path)
        self._items: list[dict[str, Any]] = []
        self.version = 0  #: telt op bij elke wijziging, zodat de pagina weet wanneer hij opnieuw moet ophalen
        stored = self._file.load([])
        for item in stored if isinstance(stored, list) else []:
            try:
                point = validate(item["lat"], item["lon"])
                name = self._clean_name(item["name"])
                self._items.append({"id": str(item["id"]), "name": name, "lat": point.lat, "lon": point.lon})
            except (KeyError, TypeError, ValueError, StoreError):
                continue  # één kapotte regel mag de rest niet tegenhouden

    @staticmethod
    def _clean_name(name: Any) -> str:
        cleaned = " ".join(str(name).split())[:MAX_NAME_LENGTH]
        if not cleaned:
            raise StoreError("Geef de plek een naam.")
        return cleaned

    def list(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._items]

    def add(self, name: str, point: LatLon) -> dict[str, Any]:
        if len(self._items) >= MAX_FAVORITES:
            raise StoreError(f"Je kunt hooguit {MAX_FAVORITES} plekken bewaren. Verwijder er eerst een.")
        item = {"id": secrets.token_hex(6), "name": self._clean_name(name), "lat": point.lat, "lon": point.lon}
        self._items.append(item)
        self._changed()
        return dict(item)

    def remove(self, item_id: str) -> bool:
        before = len(self._items)
        self._items = [item for item in self._items if item["id"] != item_id]
        if len(self._items) == before:
            return False
        self._changed()
        return True

    def _changed(self) -> None:
        self.version += 1
        self._file.save(self._items)


class Settings:
    """Je keuzes. Nu één: of adres zoeken aan staat (standaard UIT)."""

    def __init__(self, path: Path | None) -> None:
        self._file = JsonFile(path)
        data = self._file.load({})
        self.address_search = bool(data.get("address_search", False)) if isinstance(data, dict) else False

    def set_address_search(self, enabled: bool) -> None:
        self.address_search = bool(enabled)
        self._file.save({"address_search": self.address_search})

    def snapshot(self) -> dict[str, Any]:
        return {"address_search": self.address_search}
