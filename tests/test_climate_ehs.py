"""HA climate-entity mapping tests for the EHS zone1 (space heating) loop.

Mirrors test_water_heater_ehs.py -- the two loops are the same composite
shape, so the harness is deliberately identical.
"""

from typing import ClassVar, cast

from homeassistant.components.climate import HVACMode
from homeassistant.components.climate.const import DEFAULT_MAX_TEMP, DEFAULT_MIN_TEMP
from homeassistant.const import UnitOfTemperature

from custom_components.localthings.climate import LocalThingsEhsZoneClimate
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.by_type import ehs
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import EhsZoneClimateDesc
from tests.conftest import _load_device


class _FakeCoordinator:
    device_serial = "TEST-EHS-SERIAL"
    device_info: ClassVar[dict] = {}
    data: ClassVar[dict] = {}

    def __init__(self, resources):
        self.last_resources = resources
        self.commands = []

    def resource(self, href):
        return self.last_resources.get(href, {})

    def canonical_resources(self, subdevice):
        # Every bound entity in this test uses the default MAIN subdevice,
        # so the canonical view is just the raw snapshot (issue #177 --
        # see LocalThingsEntity._resources).
        return self.last_resources

    async def async_send_command(self, bound, payload):
        self.commands.append((bound, payload))


def _entity(resources, coordinator=None):
    bound = discover(
        resources,
        ehs.REGISTRY.capabilities,
        ehs.REGISTRY.pattern_capabilities,
    )
    zone_bound = next(item for item in bound if isinstance(item.desc, EhsZoneClimateDesc))
    return LocalThingsEhsZoneClimate(
        cast(LocalThingsCoordinator, coordinator or _FakeCoordinator(resources)), zone_bound
    )


def _on(resources):
    """The fixture was captured with zone1 powered off; most of these tests
    care about the running unit."""
    resources["/power/vs/0"]["x.com.samsung.da.power"] = "On"
    return resources


def test_hvac_mode_is_off_when_powered_off():
    """Fixture default: /power/vs/0 reports Off even though /mode/vs/0 still
    says Cool -- power wins, exactly as it does for the DHW loop."""
    entity = _entity(_load_device("ehs"))
    assert entity.hvac_mode == HVACMode.OFF


def test_hvac_mode_reads_mode_when_on():
    entity = _entity(_on(_load_device("ehs")))
    assert entity.hvac_mode == HVACMode.COOL


def test_hvac_modes_include_off_and_mapped_modes():
    """Fixture's supportedModes is Cool/Heat/Auto -- a much smaller
    vocabulary than the room AC's (no Dry, no fan-only, no AIComfort)."""
    entity = _entity(_load_device("ehs"))
    assert entity.hvac_modes == [
        HVACMode.OFF,
        HVACMode.COOL,
        HVACMode.HEAT,
        HVACMode.AUTO,
    ]


def test_hvac_mode_tolerates_lowercase_device_codes():
    resources = _on(_load_device("ehs"))
    resources["/mode/vs/0"]["x.com.samsung.da.modes"] = ["heat"]
    entity = _entity(resources)

    assert entity.hvac_mode == HVACMode.HEAT


def test_unmapped_mode_does_not_guess_auto():
    """The AC falls back to AUTO on an unknown code; this loop must not --
    reporting "auto" for a unit that is actually heating is worse than
    reporting nothing, and the code is logged either way."""
    resources = _on(_load_device("ehs"))
    resources["/mode/vs/0"]["x.com.samsung.da.modes"] = ["Bogus"]
    entity = _entity(resources)

    assert entity.hvac_mode != HVACMode.AUTO


def test_temperature_reads_current_and_target():
    """The fixture's zone1 rep: current 30.0 flow, desired 5.0, bounds
    5.0-25.0 (FSV #1011/#1012's cooling range, since the unit is in Cool)."""
    entity = _entity(_load_device("ehs"))
    assert entity.current_temperature == 30.0
    assert entity.target_temperature == 5.0
    assert entity.temperature_unit == UnitOfTemperature.CELSIUS
    assert entity.min_temp == 5.0
    assert entity.max_temp == 25.0
    assert entity.target_temperature_step == 0.5


def test_temperature_bounds_fall_back_together():
    """A board reporting only one end of the range must not pair a real
    device bound with an HA default -- both ends or neither, same rule as
    climate._range() and water_heater._range()."""
    resources = _load_device("ehs")
    del resources["/temperatures/indoor/vs/0"]["x.com.samsung.da.maximum"]
    entity = _entity(resources)

    # The device minimum (5.0) is dropped along with the missing maximum, so
    # both fall back to HA's own climate defaults rather than being mixed.
    # (Compared against the constants rather than ClimateEntity.min_temp
    # itself: unlike WaterHeaterEntity's plain property, climate's is a
    # cached_property, so there's no .fget to call unbound.)
    assert entity.min_temp != 5.0
    assert (entity.min_temp, entity.max_temp) == (DEFAULT_MIN_TEMP, DEFAULT_MAX_TEMP)


def test_bounds_track_the_current_mode():
    """min/max come from the resource on every access, not from the
    descriptor -- the unit reports the cooling range in Cool and the heating
    range in Heat (FSV #1011/#1012 vs #1031/#1032), so a cached pair would be
    wrong for half the year."""
    resources = _on(_load_device("ehs"))
    entity = _entity(resources)
    assert (entity.min_temp, entity.max_temp) == (5.0, 25.0)

    rep = resources["/temperatures/indoor/vs/0"]
    rep["x.com.samsung.da.minimum"] = "15.0"
    rep["x.com.samsung.da.maximum"] = "70.0"

    assert (entity.min_temp, entity.max_temp) == (15.0, 70.0)


def test_zero_increment_is_not_collapsed_into_the_default():
    """`or` would turn a genuine 0 into 0.5 (issue #160)."""
    resources = _load_device("ehs")
    resources["/temperatures/indoor/vs/0"]["x.com.samsung.da.increment"] = "0"
    entity = _entity(resources)

    assert entity.target_temperature_step == 0.0


def test_entity_is_named_rather_than_taking_the_device_name():
    """Unlike climate.py's AC, zone1 is one loop of a two-loop device, so it
    takes a catalog name instead of presenting as the device itself."""
    entity = _entity(_load_device("ehs"))

    assert "_attr_name" not in entity.__dict__
    assert entity.translation_key == "zone1"


async def test_set_temperature_writes_zone_temperature():
    resources = _on(_load_device("ehs"))
    coordinator = _FakeCoordinator(resources)
    entity = _entity(resources, coordinator)

    await entity.async_set_temperature(temperature=21.5)

    assert coordinator.commands == [(entity._bound, ("temperature", 21.5))]


async def test_set_temperature_honours_hvac_mode():
    """climate.set_temperature carries an optional hvac_mode; dropping it
    would move the setpoint without ever changing mode or powering on."""
    resources = _load_device("ehs")  # powered off in the fixture
    coordinator = _FakeCoordinator(resources)
    entity = _entity(resources, coordinator)

    await entity.async_set_temperature(temperature=45.0, hvac_mode=HVACMode.HEAT)

    assert coordinator.commands == [
        (entity._bound, ("power", True)),
        (entity._bound, ("mode", "Heat")),
        (entity._bound, ("temperature", 45.0)),
    ]


async def test_set_temperature_with_off_mode_skips_the_setpoint_write():
    resources = _on(_load_device("ehs"))
    coordinator = _FakeCoordinator(resources)
    entity = _entity(resources, coordinator)

    await entity.async_set_temperature(temperature=45.0, hvac_mode=HVACMode.OFF)

    assert coordinator.commands == [(entity._bound, ("power", False))]


async def test_turn_on_and_off_write_zone_power():
    resources = _load_device("ehs")
    coordinator = _FakeCoordinator(resources)
    entity = _entity(resources, coordinator)

    await entity.async_turn_on()
    await entity.async_turn_off()

    assert coordinator.commands == [
        (entity._bound, ("power", True)),
        (entity._bound, ("power", False)),
    ]


async def test_set_hvac_mode_off_turns_off_without_writing_mode():
    resources = _on(_load_device("ehs"))
    coordinator = _FakeCoordinator(resources)
    entity = _entity(resources, coordinator)

    await entity.async_set_hvac_mode(HVACMode.OFF)

    assert coordinator.commands == [(entity._bound, ("power", False))]


async def test_set_hvac_mode_writes_mapped_device_code():
    resources = _on(_load_device("ehs"))
    coordinator = _FakeCoordinator(resources)
    entity = _entity(resources, coordinator)

    await entity.async_set_hvac_mode(HVACMode.HEAT)

    # Already on -- no redundant power write.
    assert coordinator.commands == [(entity._bound, ("mode", "Heat"))]


async def test_set_hvac_mode_powers_on_first_when_off():
    resources = _load_device("ehs")  # powered off in the fixture
    coordinator = _FakeCoordinator(resources)
    entity = _entity(resources, coordinator)

    await entity.async_set_hvac_mode(HVACMode.AUTO)

    assert coordinator.commands == [
        (entity._bound, ("power", True)),
        (entity._bound, ("mode", "Auto")),
    ]


async def test_set_hvac_mode_resolves_the_code_from_supported_modes():
    """The code written back comes from the unit's own supportedModes, so a
    board spelling a mode differently still gets a code it accepts."""
    resources = _on(_load_device("ehs"))
    resources["/mode/vs/0"]["x.com.samsung.da.supportedModes"] = ["COOL", "HEAT"]
    coordinator = _FakeCoordinator(resources)
    entity = _entity(resources, coordinator)

    await entity.async_set_hvac_mode(HVACMode.HEAT)

    assert coordinator.commands == [(entity._bound, ("mode", "HEAT"))]


async def test_unsupported_hvac_mode_writes_nothing():
    resources = _on(_load_device("ehs"))
    coordinator = _FakeCoordinator(resources)
    entity = _entity(resources, coordinator)

    await entity.async_set_hvac_mode(HVACMode.DRY)

    assert coordinator.commands == []
