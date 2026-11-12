"""Gedeelde instellingen voor alle tests."""

import logging

import pytest


@pytest.fixture(autouse=True)
def eigen_datamap(tmp_path, monkeypatch):
    """Elke test krijgt een eigen, tijdelijke map voor logs. Zo raken tests je echte gegevens nooit aan."""
    monkeypatch.setenv("GHOST_DATA_DIR", str(tmp_path / "ghost-data"))
    yield
    # Sluit het logbestand weer, anders blijft het open staan tussen tests.
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, logging.FileHandler):
            root.removeHandler(handler)
            handler.close()
