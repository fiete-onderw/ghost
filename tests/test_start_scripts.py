"""Controles op de Windows-startbestanden.

Deze bestanden kunnen hier niet worden uitgevoerd (dat kan alleen op Windows),
maar we kunnen wel controleren dat ze bij elkaar passen en goed zijn opgeslagen.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BATS = [ROOT / "start.bat", ROOT / "start-demo.bat", ROOT / "diagnose.bat"]
PS1 = ROOT / "scripts" / "installeer-uv.ps1"
WINDOWS_FILES = [*BATS, PS1]


def _ps1_version() -> str:
    match = re.search(r"^\$UvVersion = '([0-9.]+)'", PS1.read_text(encoding="ascii"), re.M)
    assert match, "versie niet gevonden in installeer-uv.ps1"
    return match.group(1)


@pytest.mark.parametrize("path", WINDOWS_FILES, ids=lambda p: p.name)
def test_bestand_heeft_windows_regeleinden(path):
    data = path.read_bytes()
    assert b"\r\n" in data
    assert data.count(b"\n") == data.count(b"\r\n"), "losse LF-regeleinden: cmd.exe kan dan labels missen"


@pytest.mark.parametrize("path", WINDOWS_FILES, ids=lambda p: p.name)
def test_bestand_bevat_alleen_gewone_tekens(path):
    """Windows PowerShell 5.1 en cmd.exe lezen andere tekens soms verkeerd."""
    path.read_bytes().decode("ascii")


@pytest.mark.parametrize("bat", BATS, ids=lambda p: p.name)
def test_uv_versie_in_startbestand_is_gelijk_aan_het_script(bat):
    match = re.search(r'set "GHOST_UV_VERSION=([0-9.]+)"', bat.read_text(encoding="ascii"))
    assert match, f"GHOST_UV_VERSION ontbreekt in {bat.name}"
    assert match.group(1) == _ps1_version()


@pytest.mark.parametrize("bat", BATS, ids=lambda p: p.name)
def test_startbestand_roept_het_bestaande_script_aan(bat):
    text = bat.read_bytes().decode("ascii")  # zonder omzetten van regeleinden
    assert r'-File "%~dp0scripts\installeer-uv.ps1"' in text
    for label in ("start", "geen_uv", "niet_uitgepakt"):
        assert f"goto {label}" in text
        assert f"\r\n:{label}\r\n" in text, f"label :{label} ontbreekt"


def test_script_heeft_een_controlecode_per_processor():
    text = PS1.read_text(encoding="ascii")
    hashes = dict(re.findall(r"'(x86_64|aarch64)'\s*=\s*'([0-9a-f]+)'", text))
    assert set(hashes) == {"x86_64", "aarch64"}
    assert all(len(value) == 64 for value in hashes.values())


def test_script_controleert_de_download_voordat_het_uitpakt():
    text = PS1.read_text(encoding="ascii")
    assert text.index("Get-FileHash") < text.index("Expand-Archive")
    assert "https://github.com/astral-sh/uv/releases/download/$UvVersion/" in text


def test_gitattributes_beschermt_de_regeleinden():
    rules = (ROOT / ".gitattributes").read_text().split()
    assert "*.bat" in rules and "*.ps1" in rules
