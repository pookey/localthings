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
resource's own code spelling. Reusing HA's standard states means no *state*
translation catalog entry is needed for them (see the entity_component
fallback in homeassistant.components.water_heater.strings.json).

Naming is a separate question from that, and the answer here differs from
climate.py's: the AC *is* the device, so its climate card takes the bare
device name (`_attr_name = None`). An EHS unit has two loops, and the DHW
one is not "the device" -- its siblings are named "Zone Mode"/"Zone Target
Temperature", so a card labelled just "EHS" would misrepresent which loop
it drives. This entity is named through the catalog like every other
descriptor here, via the DHW descriptor's `translation_key='dhw'`
(entity.water_heater.dhw.name -> "Hot water").
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

# Read-side lookup, case-folded. climate.py resolves write codes from the
# unit's own supportedModes because two spellings there mean one HA value
# ('Wind'/'Fan' -> FAN_ONLY); this map is bijective, so the write side can
# use _STATE_TO_DEVICE directly. Only the read side is exposed to a board
# spelling the same code differently ('eco'/'ECO'), and case is the one
# variation worth absorbing rather than warning about.
_DEVICE_TO_STATE_CI = {k.lower(): v for k, v in _DEVICE_TO_STATE.items()}


def _to_state(code) -> str | None:
    if code is None:
        return None
    return _DEVICE_TO_STATE_CI.get(str(code).lower())


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
        # No _attr_name here: unlike climate.py's AC, this is one loop of a
        # two-loop device and takes a catalog name ("Hot water") through the
        # descriptor's translation_key -- see the module docstring.
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

    def _range(self) -> list | None:
        """The device's own (minimum, maximum) pair, or None.

        Both ends together or neither, deliberately -- same rule as
        climate._range(). A board reporting minimum but not maximum would
        otherwise pair a device minimum (40) with HA's own default maximum
        (140 °F), which looks plausible and is silently wrong on a unit
        that really allows 62.
        """
        rep = self._rep(TEMPERATURE_HREF)
        lo = _num(rep.get('x.com.samsung.da.minimum'))
        hi = _num(rep.get('x.com.samsung.da.maximum'))
        return [lo, hi] if (lo is not None and hi is not None) else None

    @property
    def min_temp(self) -> float:
        r = self._range()
        return r[0] if r else super().min_temp

    @property
    def max_temp(self) -> float:
        r = self._range()
        return r[1] if r else super().max_temp

    @property
    def target_temperature_step(self) -> float:
        # `is None`, not `or` -- see issue #160: `or` collapses a genuine 0
        # into the fallback.
        step = _num(self._rep(TEMPERATURE_HREF).get('x.com.samsung.da.increment'))
        return 0.5 if step is None else step

    # -- operation mode -------------------------------------------------------

    @property
    def current_operation(self) -> str | None:
        if not self._is_on():
            return STATE_OFF
        code = _first(self._rep(MODE_HREF).get(_MODES_FIELD))
        mapped = _to_state(code)
        if code is not None and mapped is None:
            self._warn_unmapped(code)
        return mapped

    @property
    def operation_list(self) -> list[str]:
        modes = [STATE_OFF]
        for code in self._supported():
            mapped = _to_state(code)
            if mapped is None:
                self._warn_unmapped(code)
                continue
            if mapped not in modes:
                modes.append(mapped)
        return modes

    # -- writes ---------------------------------------------------------------

    async def async_set_temperature(self, **kwargs) -> None:
        # HA's water_heater.set_temperature service takes an optional
        # operation_mode and forwards it here (SET_TEMPERATURE_SCHEMA), same
        # as climate.set_temperature does with hvac_mode. Honour it, and set
        # it first -- that also powers the loop on when it was off -- so a
        # dashboard "boost to 55" button that carries a mode actually changes
        # mode, instead of only moving the setpoint. Same fix as the AC's
        # (see climate.async_set_temperature).
        operation_mode = kwargs.get('operation_mode')
        if operation_mode is not None:
            await self.async_set_operation_mode(operation_mode)
            if operation_mode == STATE_OFF:
                return
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
