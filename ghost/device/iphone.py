"""De echte iPhone, via pymobiledevice3.

Dit is de ENIGE plek in Ghost die pymobiledevice3 importeert. Als die library
verandert, hoef je alleen hier te kijken.

Gecontroleerd tegen de broncode van pymobiledevice3 11.53.0:
- usbmux.list_devices()                       -> welke toestellen ziet Windows?
- lockdown.create_using_usbmux(autopair=False) -> basisgegevens en "vertrouwd?"
- UserspaceRsdTunnel                           -> tunnel zonder administratorrechten (iOS 17.4+)
- DvtProvider + LocationSimulation             -> set(lat, lon) en clear()

Belangrijk om te weten:
- set() wacht op antwoord van de iPhone. clear() NIET: de library verstuurt
  `stopLocationSimulation` zonder op antwoord te wachten. Daarom stellen we
  na clear() nog een onschuldige vraag over dezelfde verbinding. Komt daar
  antwoord op, dan is het clear-bericht in elk geval aangekomen.
- Er kan maar één tunnel per proces bestaan, dus één iPhone tegelijk.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from typing import Any

from pymobiledevice3 import usbmux
from pymobiledevice3.exceptions import (
    AlreadyMountedError,
    ConnectionFailedToUsbmuxdError,
    InvalidServiceError,
    MuxException,
    PairingDialogResponsePendingError,
    PasscodeRequiredError,
    PasswordRequiredError,
    UserDeniedPairingError,
    UserspaceTunnelUnavailableError,
)
from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.services.amfi import AmfiService
from pymobiledevice3.services.dvt.instruments.device_info import DeviceInfo
from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation
from pymobiledevice3.services.mobile_image_mounter import (
    MobileImageMounterService,
    auto_mount,
    image_type_for_device,
)

from ghost.device.base import DeviceError, DeviceStatus, LocationDevice, Problem, ios_supported
from ghost.geo import LatLon

log = logging.getLogger(__name__)

TUNNEL_TIMEOUT_S = 30.0
SERVICE_TIMEOUT_S = 15.0
SET_TIMEOUT_S = 10.0
CONFIRM_TIMEOUT_S = 5.0

_LOCKED_ERRORS = (PasswordRequiredError, PasscodeRequiredError)


class IPhone(LocationDevice):
    def __init__(self) -> None:
        self._lock = asyncio.Lock()  # nooit twee acties tegelijk op de iPhone
        self._serial: str | None = None
        self._stack: AsyncExitStack | None = None
        self._tunnel: Any = None
        self._dvt: Any = None
        self._sim: Any = None
        self._info: Any = None  # alleen gebruikt om na clear() een antwoord uit te lokken

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def status(self) -> DeviceStatus:
        status = await self._probe()
        if status.problem is not None:
            return status
        try:
            await self.connect()
        except DeviceError as error:
            status.problem, status.detail = error.problem, error.detail
            if error.problem is Problem.LOCATION_SERVICE_UNAVAILABLE:
                status.tunnel, status.location_service = True, False
            elif error.problem in (Problem.TUNNEL_FAILED, Problem.IOS_TOO_OLD):
                status.tunnel = False
            return status
        status.tunnel = True
        status.location_service = True
        return status

    async def _probe(self) -> DeviceStatus:
        """Alle controles die zonder tunnel kunnen."""
        status = DeviceStatus()
        try:
            device, count = await self._find_usb_device()
        except DeviceError as error:
            status.problem, status.detail = error.problem, error.detail
            status.apple_service = error.problem is not Problem.APPLE_SERVICE_MISSING
            if status.apple_service:
                status.connected = False
            return status
        status.apple_service = True
        status.connected = True
        status.device_count = count

        try:
            lockdown = await create_using_usbmux(serial=device.serial, autopair=False, connection_type="USB")
        except _LOCKED_ERRORS as error:
            status.problem, status.detail = Problem.DEVICE_LOCKED, repr(error)
            return status
        except Exception as error:
            log.exception("Basisverbinding (lockdown) met de iPhone mislukt")
            status.problem, status.detail = Problem.UNKNOWN, repr(error)
            return status

        try:
            status.name = lockdown.all_values.get("DeviceName")
            status.model = lockdown.display_name or lockdown.product_type
            status.ios_version = lockdown.product_version
            status.trusted = bool(lockdown.paired)
            log.info("iPhone gevonden: model=%s iOS=%s vertrouwd=%s", status.model, status.ios_version, status.trusted)
            if not status.trusted:
                status.problem = Problem.NOT_TRUSTED
                return status
            status.ios_supported = ios_supported(status.ios_version)
            if not status.ios_supported:
                status.problem = Problem.IOS_TOO_OLD
                return status
            status.developer_mode = bool(await lockdown.get_developer_mode_status())
            status.ddi_mounted = await self._is_ddi_mounted(lockdown)
            if not status.developer_mode:
                status.problem = Problem.DEVELOPER_MODE_OFF
        except _LOCKED_ERRORS as error:
            status.problem, status.detail = Problem.DEVICE_LOCKED, repr(error)
        except Exception as error:
            log.exception("Status opvragen mislukt")
            status.problem, status.detail = Problem.UNKNOWN, repr(error)
        finally:
            await _close_quietly(lockdown)
        return status

    async def _find_usb_device(self) -> tuple[Any, int]:
        try:
            devices = await usbmux.list_devices()
        except (ConnectionFailedToUsbmuxdError, MuxException, OSError) as error:
            raise DeviceError(Problem.APPLE_SERVICE_MISSING, repr(error)) from error
        usb_devices = [device for device in devices if device.is_usb]
        if not usb_devices:
            detail = "Alleen via wifi zichtbaar; Ghost werkt via de kabel." if devices else ""
            raise DeviceError(Problem.NO_DEVICE, detail)
        self._serial = usb_devices[0].serial
        return usb_devices[0], len(usb_devices)

    @staticmethod
    async def _is_ddi_mounted(lockdown: Any) -> bool | None:
        """True/False, of None als het niet te bepalen was. Alleen ter informatie."""
        try:
            async with MobileImageMounterService(lockdown=lockdown) as mounter:
                return bool(await mounter.is_image_mounted(image_type_for_device(lockdown)))
        except Exception:
            log.info("Kon niet bepalen of het ontwikkelaars-image gemount is", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Eenmalige stappen
    # ------------------------------------------------------------------

    async def pair(self, timeout: float = 60.0) -> None:
        device, _ = await self._find_usb_device()
        try:
            lockdown = await create_using_usbmux(serial=device.serial, autopair=False, connection_type="USB")
        except _LOCKED_ERRORS as error:
            raise DeviceError(Problem.DEVICE_LOCKED, repr(error)) from error
        try:
            if lockdown.paired:
                return
            await lockdown.pair(timeout=timeout)
        except _LOCKED_ERRORS as error:
            raise DeviceError(Problem.DEVICE_LOCKED, repr(error)) from error
        except PairingDialogResponsePendingError as error:
            raise DeviceError(Problem.NOT_TRUSTED, "Er is niet op tijd op 'Vertrouw' getikt.") from error
        except UserDeniedPairingError as error:
            raise DeviceError(Problem.NOT_TRUSTED, "Op de iPhone is 'Vertrouw niet' gekozen.") from error
        finally:
            await _close_quietly(lockdown)

    async def reveal_developer_mode(self) -> None:
        device, _ = await self._find_usb_device()
        try:
            lockdown = await create_using_usbmux(serial=device.serial, autopair=False, connection_type="USB")
        except _LOCKED_ERRORS as error:
            raise DeviceError(Problem.DEVICE_LOCKED, repr(error)) from error
        try:
            if not lockdown.paired:
                raise DeviceError(Problem.NOT_TRUSTED)
            await AmfiService(lockdown).reveal_developer_mode_option_in_ui()
        except _LOCKED_ERRORS as error:
            raise DeviceError(Problem.DEVICE_LOCKED, repr(error)) from error
        finally:
            await _close_quietly(lockdown)

    async def mount_ddi(self) -> None:
        # Na het mounten biedt de iPhone nieuwe diensten aan, en die ziet een
        # bestaande verbinding niet. Daarom: oude verbinding dicht, losse tunnel
        # voor het mounten, en daarna maakt connect() een verse.
        await self.close()
        if self._serial is None:
            await self._find_usb_device()
        async with self._lock:
            stack = AsyncExitStack()
            try:
                tunnel = await self._open_tunnel(stack)
                try:
                    await auto_mount(tunnel.rsd)
                except AlreadyMountedError:
                    log.info("Ontwikkelaars-image was al gemount")
                except Exception as error:
                    log.exception("Mounten van het ontwikkelaars-image mislukt")
                    raise DeviceError(Problem.LOCATION_SERVICE_UNAVAILABLE, repr(error)) from error
            finally:
                await _close_stack(stack)

    # ------------------------------------------------------------------
    # Verbinding
    # ------------------------------------------------------------------

    def _is_connected(self) -> bool:
        # De tunnel breekt zichzelf af als de kabel eruit gaat; `rsd` wordt dan None.
        return self._sim is not None and self._tunnel is not None and self._tunnel.rsd is not None

    def connection_alive(self) -> bool:
        return self._is_connected()

    async def connect(self) -> None:
        async with self._lock:
            await self._connect_locked()

    async def _connect_locked(self) -> None:
        if self._is_connected():
            return
        await self._drop_locked()  # ruim een eventuele dode verbinding op
        if self._serial is None:
            await self._find_usb_device()

        stack = AsyncExitStack()
        try:
            tunnel = await self._open_tunnel(stack)
            try:
                dvt = DvtProvider(tunnel.rsd)
                await asyncio.wait_for(stack.enter_async_context(dvt), SERVICE_TIMEOUT_S)
                sim = LocationSimulation(dvt)
                await asyncio.wait_for(stack.enter_async_context(sim), SERVICE_TIMEOUT_S)
            except InvalidServiceError as error:
                raise DeviceError(Problem.LOCATION_SERVICE_UNAVAILABLE, repr(error)) from error
            except Exception as error:
                log.exception("Locatiedienst openen mislukt")
                raise DeviceError(Problem.LOCATION_SERVICE_UNAVAILABLE, repr(error)) from error

            info = None
            try:
                info = DeviceInfo(dvt)
                await asyncio.wait_for(stack.enter_async_context(info), SERVICE_TIMEOUT_S)
            except Exception:
                # Niet erg: dan kunnen we na clear() alleen geen bevestiging vragen.
                log.info("Hulpkanaal voor bevestiging niet beschikbaar", exc_info=True)
                info = None
        except BaseException:
            await _close_stack(stack)
            raise

        self._stack, self._tunnel, self._dvt, self._sim, self._info = stack, tunnel, dvt, sim, info
        log.info("Verbonden met de locatiedienst van de iPhone")

    async def _open_tunnel(self, stack: AsyncExitStack) -> Any:
        # Laat importeren: dit laadt een complete netwerkstack in Python en is traag.
        from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel

        # remotepairing_fallback=False: iOS 17.0-17.3 geeft dan meteen een
        # duidelijke fout in plaats van een poging via wifi.
        tunnel = UserspaceRsdTunnel(serial=self._serial, autopair=False, remotepairing_fallback=False)
        try:
            await asyncio.wait_for(tunnel.aopen(), TUNNEL_TIMEOUT_S)
        except UserspaceTunnelUnavailableError as error:
            raise DeviceError(Problem.IOS_TOO_OLD, repr(error)) from error
        except _LOCKED_ERRORS as error:
            raise DeviceError(Problem.DEVICE_LOCKED, repr(error)) from error
        except Exception as error:
            log.exception("Tunnel opzetten mislukt")
            raise DeviceError(Problem.TUNNEL_FAILED, repr(error)) from error
        stack.push_async_callback(tunnel.aclose)
        return tunnel

    async def close(self) -> None:
        async with self._lock:
            await self._drop_locked()

    async def _drop_locked(self) -> None:
        stack, self._stack = self._stack, None
        self._tunnel = self._dvt = self._sim = self._info = None
        if stack is not None:
            await _close_stack(stack)

    # ------------------------------------------------------------------
    # Locatie
    # ------------------------------------------------------------------

    async def set_location(self, point: LatLon) -> None:
        async with self._lock:
            await self._connect_locked()
            try:
                await asyncio.wait_for(self._sim.set(point.lat, point.lon), SET_TIMEOUT_S)
            except Exception as error:
                log.exception("Locatie zetten mislukt")
                await self._drop_locked()
                raise DeviceError(Problem.CONNECTION_LOST, repr(error)) from error

    async def clear_location(self) -> bool:
        async with self._lock:
            await self._connect_locked()
            try:
                await asyncio.wait_for(self._sim.clear(), SET_TIMEOUT_S)
            except Exception as error:
                log.exception("Verzoek om de echte locatie terug te zetten mislukt")
                await self._drop_locked()
                raise DeviceError(Problem.CONNECTION_LOST, repr(error)) from error
            return await self._confirm_delivery()

    async def _confirm_delivery(self) -> bool:
        """Lok een antwoord uit over dezelfde verbinding als het clear-bericht.

        Berichten over één verbinding komen in volgorde aan. Krijgen we antwoord
        op deze latere vraag, dan is het eerdere clear-bericht dus afgeleverd.
        """
        if self._info is None:
            return False
        try:
            await asyncio.wait_for(self._info.service.mach_time_info(), CONFIRM_TIMEOUT_S)
        except Exception:
            log.warning("Geen bevestiging van de iPhone na het clear-verzoek", exc_info=True)
            return False
        return True


async def _close_quietly(lockdown: Any) -> None:
    try:
        await lockdown.close()
    except Exception:
        log.debug("Sluiten van de basisverbinding gaf een fout", exc_info=True)


async def _close_stack(stack: AsyncExitStack) -> None:
    try:
        await stack.aclose()
    except Exception:
        log.info("Verbinding sluiten gaf een fout", exc_info=True)
