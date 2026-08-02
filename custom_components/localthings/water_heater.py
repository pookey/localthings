"""Water heater platform for Local Things.

Second composite entity in this integration (see climate.py's module
docstring for the general pattern this follows): a single HA water_heater
card for a Samsung EHS heat pump's domestic hot water (DHW) loop. It binds
the primary `WaterHeaterDesc` (the `/mode/dhw/vs/0` capability, DHW.entities
in registry/capabilities/ehs.py) so the registry still tracks it, and reads
the sibling `/power/dhw/vs/0` and `/temperatures/dhw/vs/0` resources straight
from the coordinator snapshot -- the same cross-resource read climate.py uses
for the AC's power/temperature/wind siblings.

Writes go through `coordinator.async_send_command(bound, (kind, value))`:
DHW's `write_fn` (ehs._dhw_write) maps each `(kind, value)` payload to the
right `(path_segs, body)`, and `async_send_command` POSTs to those path_segs
and applies the optimistic value/settle guard to that same href -- not the
bound `/mode/dhw/vs/0` href -- so one descriptor drives writes to, and gets
fresh state back for, power, mode and temperature alike.

Operation-mode vocabulary: the DHW loop's four device modes (Eco/Std/Force/
Power) map onto HA's own standard water_heater states -- the same mapping
Home Assistant's core `smartthings` integration uses for this exact Samsung
capability over the cloud API (`samsungce.ehsThermostat` /
`airConditionerMode`: eco/std/force/power -> STATE_ECO/STATE_HEAT_PUMP/
STATE_HIGH_DEMAND/STATE_PERFORMANCE), just title-cased to match this OCF
resource's own code spelling. Reusing HA's standard states means no
translation catalog entry is needed for them (see the entity_component
fallback in homeassistant.components.water_heater.strings.json) --
water_heater.dhw is in test_translations.py's UNNAMED_DESCRIPTORS for that
reason, same as fan.py's whole-device fan entities.
"""
from __future__ import annotations

import logging

from homeassistant.components.water_heater import (
    STATE_ECO,
    STATE_HEAT_PUMP,
    STATE_HIGH_DEMAND,
    STATE_PERFORMANCE,
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .registry.capabilities.ehs import (
    HREF_DHW_MODE as MODE_HREF,
    HREF_DHW_POWER as POWER_HREF,
    HREF_DHW_TEMPERATURE as TEMPERATURE_HREF,
)
from .registry.capabilities.common import normalize_temp_unit
from .registry.entities import WaterHeaterDesc

from .const import DOMAIN
from .coordinator import LocalThingsCoordinator
from .entity import LocalThingsEntity, _is_included

_LOGGER = logging.getLogger(__name__)

_MODES_FIELD = 'x.com.samsung.da.modes'
_SUPPORTED_FIELD = 'x.com.samsung.da.supportedModes'

# Device mode <-> HA water_heater operation state -- see the module
# docstring above for the SmartThings-cloud precedent this mirrors.
_DEVICE_TO_STATE: dict[str, str] = {
    'Eco': STATE_ECO,
    'Std': STATE_HEAT_PUMP,
    'Force': STATE_HIGH_DEMAND,
    'Power': STATE_PERFORMANCE,
}
_STATE_TO_DEVICE = {v: k for k, v in _DEVICE_TO_STATE.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        LocalThingsWaterHeater(coordinator, b)
        for b in coordinator.bound
        if isinstance(b.desc, WaterHeaterDesc) and _is_included(b, coordinator)
    )


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first(value):
    """Samsung `modes` is a single-element list on this resource. Return the
    first element of a list, else the value itself."""
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


class LocalThingsWaterHeater(LocalThingsEntity, WaterHeaterEntity):
    """Composite water_heater entity for a Samsung EHS DHW loop."""

    def __init__(self, coordinator: LocalThingsCoordinator, bound) -> None:
        super().__init__(coordinator, bound)
        # Primary/main entity for the device: no name suffix, just the device name.
        self._attr_name = None
        self._attr_supported_features = (
            WaterHeaterEntityFeature.TARGET_TEMPERATURE
            | WaterHeaterEntityFeature.OPERATION_MODE
            | WaterHeaterEntityFeature.ON_OFF
        )
        # Raw device codes already logged by _warn_unmapped -- these
        # properties are read on every coordinator refresh, so an un-deduped
        # warning would spam the log for any unit reporting a genuinely
        # unrecognized code.
        self._warned_unmapped: set[str] = set()

    def _rep(self, href: str) -> dict:
        """`href` is one of this module's canonical HREF_* constants --
        translated through this bound entity's own subdevice (issue #177),
        same as climate.py's identical helper."""
        return self.coordinator.resource(self._bound.subdevice.to_actual(href)) or {}

    def _is_on(self) -> bool:
        return str(self._rep(POWER_HREF).get('x.com.samsung.da.power', '')).lower() == 'on'

    def _supported(self) -> list[str]:
        return list(self._rep(MODE_HREF).get(_SUPPORTED_FIELD) or [])

    def _warn_unmapped(self, code: str) -> None:
        if code in self._warned_unmapped:
            return
        self._warned_unmapped.add(code)
        _LOGGER.warning(
            "%s: device DHW mode %r has no HA mapping and was dropped; "
            "please file an issue with your diagnostics dump",
            self.entity_id, code,
        )

    # -- temperature --------------------------------------------------------

    @property
    def temperature_unit(self) -> str:
        raw = self._rep(TEMPERATURE_HREF).get('x.com.samsung.da.unit')
        return (UnitOfTemperature.FAHRENHEIT
                if normalize_temp_unit(raw, '°C') == '°F'
                else UnitOfTemperature.CELSIUS)

    @property
    def current_temperature(self):
        return _num(self._rep(TEMPERATURE_HREF).get('x.com.samsung.da.current'))

    @property
    def target_temperature(self):
        return _num(self._rep(TEMPERATURE_HREF).get('x.com.samsung.da.desired'))

    @property
    def min_temp(self) -> float:
        return _num(self._rep(TEMPERATURE_HREF).get('x.com.samsung.da.minimum')) or super().min_temp

    @property
    def max_temp(self) -> float:
        return _num(self._rep(TEMPERATURE_HREF).get('x.com.samsung.da.maximum')) or super().max_temp

    @property
    def target_temperature_step(self) -> float:
        return _num(self._rep(TEMPERATURE_HREF).get('x.com.samsung.da.increment')) or 0.5

    # -- operation mode -------------------------------------------------------

    @property
    def current_operation(self) -> str | None:
        if not self._is_on():
            return STATE_OFF
        code = _first(self._rep(MODE_HREF).get(_MODES_FIELD))
        if code is not None and code not in _DEVICE_TO_STATE:
            self._warn_unmapped(code)
        return _DEVICE_TO_STATE.get(code)

    @property
    def operation_list(self) -> list[str]:
        modes = [STATE_OFF]
        for code in self._supported():
            mapped = _DEVICE_TO_STATE.get(code)
            if mapped is None:
                self._warn_unmapped(code)
                continue
            if mapped not in modes:
                modes.append(mapped)
        return modes

    # -- writes ---------------------------------------------------------------

    async def async_set_temperature(self, **kwargs) -> None:
        temp = kwargs.get('temperature')
        if temp is None:
            return
        await self.coordinator.async_send_command(self._bound, ('temperature', temp))

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        if operation_mode == STATE_OFF:
            await self.coordinator.async_send_command(self._bound, ('power', False))
            return
        device = _STATE_TO_DEVICE.get(operation_mode)
        if device is None:
            return
        if not self._is_on():
            await self.coordinator.async_send_command(self._bound, ('power', True))
        await self.coordinator.async_send_command(self._bound, ('mode', device))

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_send_command(self._bound, ('power', True))

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_send_command(self._bound, ('power', False))
