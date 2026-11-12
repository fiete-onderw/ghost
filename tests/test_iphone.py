"""Tests voor de vertaling van iPhone-situaties naar begrijpelijke problemen.

Er is hier geen echte iPhone: de functies van pymobiledevice3 zijn vervangen
door nagemaakte versies. Zo testen we ONZE logica (welke fout hoort bij welk
probleem), niet de library zelf. Of het met een echt toestel werkt, test je
met de handmatige checklist.
"""

from types import SimpleNamespace

import pytest
from pymobiledevice3.exceptions import (
    ConnectionFailedToUsbmuxdError,
    InvalidServiceError,
    PasswordRequiredError,
    UserspaceTunnelUnavailableError,
)

from ghost.device import iphone as iphone_module
from ghost.device.base import DeviceError, Problem
from ghost.device.iphone import IPhone
from ghost.geo import LatLon


class NepLockdown:
    """Doet zich voor als de basisverbinding met een iPhone."""

    def __init__(self, *, paired=True, version="27.0", developer_mode=True):
        self.paired = paired
        self.product_version = version
        self.product_type = "iPhone17,1"
        self.display_name = "iPhone 16 Pro"
        self.all_values = {"DeviceName": "Test-iPhone"}
        self._developer_mode = developer_mode
        self.closed = False

    async def get_developer_mode_status(self):
        return self._developer_mode

    async def close(self):
        self.closed = True


def nep_usb(serial="00008140-000000000000001E"):
    return SimpleNamespace(serial=serial, is_usb=True)


@pytest.fixture
def toestel(monkeypatch):
    """Een IPhone waarvan de buitenwereld is nagemaakt: één iPhone aan de kabel, alles in orde."""
    state = SimpleNamespace(devices=[nep_usb()], lockdown=NepLockdown())

    async def list_devices():
        if isinstance(state.devices, Exception):
            raise state.devices
        return state.devices

    async def create_using_usbmux(**kwargs):
        if isinstance(state.lockdown, Exception):
            raise state.lockdown
        return state.lockdown

    async def ddi_mounted(lockdown):
        return True

    monkeypatch.setattr(iphone_module.usbmux, "list_devices", list_devices)
    monkeypatch.setattr(iphone_module, "create_using_usbmux", create_using_usbmux)
    monkeypatch.setattr(IPhone, "_is_ddi_mounted", staticmethod(ddi_mounted))
    phone = IPhone()
    phone.state = state
    return phone


async def test_apple_service_ontbreekt(toestel):
    toestel.state.devices = ConnectionFailedToUsbmuxdError()
    status = await toestel._probe()
    assert status.problem is Problem.APPLE_SERVICE_MISSING
    assert status.apple_service is False


async def test_geen_iphone_aan_de_kabel(toestel):
    toestel.state.devices = []
    status = await toestel._probe()
    assert status.problem is Problem.NO_DEVICE
    assert status.apple_service is True


async def test_iphone_alleen_via_wifi_telt_niet(toestel):
    toestel.state.devices = [SimpleNamespace(serial="x", is_usb=False)]
    status = await toestel._probe()
    assert status.problem is Problem.NO_DEVICE
    assert "wifi" in status.detail


async def test_vergrendelde_iphone(toestel):
    toestel.state.lockdown = PasswordRequiredError()
    status = await toestel._probe()
    assert status.problem is Problem.DEVICE_LOCKED


async def test_niet_vertrouwd(toestel):
    toestel.state.lockdown = NepLockdown(paired=False)
    status = await toestel._probe()
    assert status.problem is Problem.NOT_TRUSTED
    assert status.trusted is False
    assert toestel.state.lockdown.closed


@pytest.mark.parametrize("versie", ["16.7.2", "17.0", "17.3.1"])
async def test_te_oude_ios(toestel, versie):
    toestel.state.lockdown = NepLockdown(version=versie)
    status = await toestel._probe()
    assert status.problem is Problem.IOS_TOO_OLD


@pytest.mark.parametrize("versie", ["17.4", "17.4.1", "18.0", "26.2", "27.0"])
async def test_ondersteunde_ios(toestel, versie):
    toestel.state.lockdown = NepLockdown(version=versie)
    status = await toestel._probe()
    assert status.problem is None
    assert status.ios_supported is True
    assert status.name == "Test-iPhone"
    assert status.model == "iPhone 16 Pro"


async def test_developer_mode_uit(toestel):
    toestel.state.lockdown = NepLockdown(developer_mode=False)
    status = await toestel._probe()
    assert status.problem is Problem.DEVELOPER_MODE_OFF
    assert status.developer_mode is False


async def test_onbekende_fout_wordt_gemeld_en_niet_doorgegooid(toestel):
    toestel.state.lockdown = RuntimeError("iets vreemds")
    status = await toestel._probe()
    assert status.problem is Problem.UNKNOWN
    assert "iets vreemds" in status.detail


# --- tunnel en locatiedienst ------------------------------------------------


class NepTunnel:
    def __init__(self, fout=None):
        self.rsd = None
        self.fout = fout
        self.closed = False

    async def aopen(self):
        if self.fout is not None:
            raise self.fout
        self.rsd = object()
        return self.rsd

    async def aclose(self):
        self.closed = True
        self.rsd = None


class NepSimulatie:
    def __init__(self):
        self.calls = []

    async def set(self, lat, lon):
        self.calls.append(("set", lat, lon))

    async def clear(self):
        self.calls.append(("clear",))


def verbind(toestel, tunnel, *, bevestiging=True):
    """Zet een toestel in de toestand 'verbonden', zonder echte tunnel."""
    sim = NepSimulatie()

    async def mach_time_info():
        if not bevestiging:
            raise TimeoutError
        return {}

    async def connect_locked():
        if toestel._sim is None:
            toestel._tunnel, toestel._sim = tunnel, sim
            toestel._info = SimpleNamespace(service=SimpleNamespace(mach_time_info=mach_time_info))

    toestel._connect_locked = connect_locked
    return sim


async def test_te_oude_ios_bij_de_tunnel(toestel, monkeypatch):
    tunnel = NepTunnel(fout=UserspaceTunnelUnavailableError("geen CoreDeviceProxy"))
    monkeypatch.setattr("pymobiledevice3.remote.userspace_tunnel.UserspaceRsdTunnel", lambda **kwargs: tunnel)
    status = await toestel.status()
    assert status.problem is Problem.IOS_TOO_OLD
    assert status.tunnel is False


async def test_tunnel_mislukt(toestel, monkeypatch):
    tunnel = NepTunnel(fout=OSError("kabel los"))
    monkeypatch.setattr("pymobiledevice3.remote.userspace_tunnel.UserspaceRsdTunnel", lambda **kwargs: tunnel)
    status = await toestel.status()
    assert status.problem is Problem.TUNNEL_FAILED
    assert status.location_service is None


async def test_locatiedienst_ontbreekt_sluit_de_tunnel_weer(toestel, monkeypatch):
    tunnel = NepTunnel()
    monkeypatch.setattr("pymobiledevice3.remote.userspace_tunnel.UserspaceRsdTunnel", lambda **kwargs: tunnel)

    def dvt_zonder_dienst(rsd):
        raise InvalidServiceError("The device does not offer the service.", "udid", "27.0")

    monkeypatch.setattr(iphone_module, "DvtProvider", dvt_zonder_dienst)
    status = await toestel.status()
    assert status.problem is Problem.LOCATION_SERVICE_UNAVAILABLE
    assert status.tunnel is True
    assert status.location_service is False
    assert tunnel.closed


async def test_set_en_clear_geven_de_juiste_opdrachten(toestel):
    sim = verbind(toestel, NepTunnel())
    await toestel.set_location(LatLon(52.3676, 4.9041))
    assert await toestel.clear_location() is True
    assert sim.calls == [("set", 52.3676, 4.9041), ("clear",)]


async def test_clear_zonder_bevestiging_geeft_false_maar_geen_fout(toestel):
    sim = verbind(toestel, NepTunnel(), bevestiging=False)
    assert await toestel.clear_location() is False
    assert sim.calls == [("clear",)]


async def test_fout_bij_set_wordt_verbinding_verbroken(toestel):
    sim = verbind(toestel, NepTunnel())

    async def kapot(lat, lon):
        raise ConnectionResetError("weg")

    sim.set = kapot
    with pytest.raises(DeviceError) as fout:
        await toestel.set_location(LatLon(1, 2))
    assert fout.value.problem is Problem.CONNECTION_LOST
    assert toestel._sim is None  # de volgende actie bouwt een verse verbinding op
