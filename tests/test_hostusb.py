"""Tests voor de vraag aan Windows: hangt er een iPhone aan de kabel?"""

from ghost import hostusb


def test_regels_van_powershell_worden_een_lijst():
    uitvoer = "Apple Mobile Device USB Composite Device | USB | OK\r\n\r\nApple iPhone | WPD | OK\r\n"
    assert hostusb.apple_devices_seen_by_windows(lambda: uitvoer) == [
        "Apple Mobile Device USB Composite Device | USB | OK",
        "Apple iPhone | WPD | OK",
    ]


def test_geen_uitvoer_betekent_windows_ziet_niets():
    assert hostusb.apple_devices_seen_by_windows(lambda: "\r\n") == []


def test_een_fout_betekent_onbekend_en_niet_niets():
    def kapot():
        raise OSError("powershell ontbreekt")

    assert hostusb.apple_devices_seen_by_windows(kapot) is None


def test_buiten_windows_is_het_antwoord_onbekend(monkeypatch):
    monkeypatch.setattr(hostusb.sys, "platform", "linux")
    assert hostusb.apple_devices_seen_by_windows() is None


def test_alleen_iphones_en_ipads_tellen_mee():
    """Een Apple-toetsenbord of -muis op USB mag niet als iPhone worden gezien."""
    assert "PID_12A[8B]" in hostusb._COMMAND
    assert "VID_05AC" in hostusb._COMMAND
