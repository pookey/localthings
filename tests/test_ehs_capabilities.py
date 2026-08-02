"""Tests for Samsung EHS (Eco Heating System) heat pump support
(TP1X_DA_AC_EHS_01001_0000).

HA-free like the rest of the suite: exercises the registry, discovery/
flatten, and the zone/dhw mode and temperature write contracts.
"""
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_model
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import (
    PLATFORM_OF, ClimateDesc, EhsZoneClimateDesc, WaterHeaterDesc,
)

from tests.conftest import _load_device


def _ehs():
    resources = _load_device('ehs')
    info = resources['/information/vs/0']
    reg = for_device_by_model(
        info['x.com.samsung.da.modelNum'], info['x.com.samsung.da.description'],
    )
    return reg, resources


def _bound():
    reg, resources = _ehs()
    return discover(resources, reg.capabilities, reg.pattern_capabilities), resources


def _state():
    bound, resources = _bound()
    return flatten(bound, resources)


def _desc(key):
    bound, _ = _bound()
    return next(b.desc for b in bound if b.desc.key == key)


def test_model_resolves_to_ehs_registry():
    reg, _ = _ehs()
    assert reg is not None and reg.name == 'ehs'


def test_no_unbound_hrefs():
    """Every resource in the real TP1X_DA_AC_EHS_01001_0000 dump binds or is
    covered -- clears the coverage-gap repair a device_type='unknown' entry
    raises."""
    reg, resources = _ehs()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_expected_state_keys_present():
    state = _state()
    for key in ('zone_climate', 'water_heater', 'away_mode', 'mute_once',
                'alarm_code', 'energy_kwh'):
        assert key in state, key


def test_zone_split_entities_are_gone():
    """zone1's power/mode/setpoint/current split was folded into the single
    composite climate entity, the same way #253 folded the dhw split into the
    water_heater. Leaving any of them bound would give HA two writable paths
    to the same resource."""
    state = _state()
    for key in ('zone_power', 'zone_mode', 'zone_temperature',
                'zone_target_temperature'):
        assert key not in state, key


def test_zone_climate_entity_is_bound():
    """The composite climate entity binds the primary /mode/vs/0 resource --
    same primary-plus-siblings shape as the DHW water_heater below and the
    AC's ClimateDesc."""
    bound, _ = _bound()
    zones = [b for b in bound if isinstance(b.desc, EhsZoneClimateDesc)]
    assert len(zones) == 1
    assert zones[0].href == '/mode/vs/0'


def test_zone_climate_is_its_own_descriptor_type():
    """Not a plain ClimateDesc: climate.py dispatches on the descriptor type
    because the EHS zone and the room AC bind the same /mode/vs/0 href and
    cannot be told apart by href the way fan.py's classes are."""
    desc = _desc('zone_climate')
    assert type(desc) is EhsZoneClimateDesc
    assert isinstance(desc, ClimateDesc)
    assert PLATFORM_OF[EhsZoneClimateDesc] == 'climate'


def test_zone_climate_reads_first_mode():
    """The flattened/golden state exposes the same representative scalar the
    entity's hvac_mode is derived from."""
    state = _state()
    assert state['zone_climate'] == 'Cool'


def test_zone_climate_write_targets():
    """ZONE.entities[0].write_fn maps each (kind, value) command to the right
    vendor POST target and body -- power, mode and temperature only; zone1
    has no fan/swing/preset for the AC's climate.py to drive."""
    write = _desc('zone_climate').write_fn
    assert write(('power', True), {}) == (
        ['power', 'vs', '0'], {'x.com.samsung.da.power': 'On'})
    assert write(('power', False), {}) == (
        ['power', 'vs', '0'], {'x.com.samsung.da.power': 'Off'})
    assert write(('mode', 'Heat'), {}) == (
        ['mode', 'vs', '0'], {'x.com.samsung.da.modes': ['Heat']})
    assert write(('temperature', 21.5), {}) == (
        ['temperatures', 'indoor', 'vs', '0'], {'x.com.samsung.da.desired': '21.5'})
    assert write(('bogus', 1), {}) is None


def test_zone_power_and_temperature_declared_as_coverage():
    """/power/vs/0 and /temperatures/indoor/vs/0 are read by the composite
    climate entity (via climate.py's sibling reads) rather than getting their
    own control entities, so they only need no-entity coverage caps for
    discover() to report no gap.

    /temperatures/indoor/vs/0 may still carry read-only sensors (the
    water-law offset lives there); what matters is that nothing binds the
    zone's power, mode or setpoint a second time.
    """
    reg, _ = _ehs()
    caps = reg.capabilities.get('/power/vs/0')
    assert caps
    assert all(c.entities == () for c in caps)
    for href in ('/power/vs/0', '/temperatures/indoor/vs/0'):
        keys = {d.key for c in reg.capabilities.get(href, []) for d in c.entities}
        assert keys.isdisjoint({'zone_power', 'zone_mode', 'zone_target_temperature'}), href


def test_water_heater_entity_is_bound():
    """The composite water_heater entity binds the primary /mode/dhw/vs/0
    resource -- same primary-plus-siblings shape as the AC's ClimateDesc
    (see test_airconditioner_capabilities.py's test_climate_entity_is_bound)."""
    bound, _ = _bound()
    water_heaters = [b for b in bound if isinstance(b.desc, WaterHeaterDesc)]
    assert len(water_heaters) == 1
    assert water_heaters[0].href == '/mode/dhw/vs/0'


def test_water_heater_reads_first_mode():
    """The flattened/golden state exposes the same representative scalar
    the entity's current_operation is derived from -- see climate.py's
    _first_mode for the identical pattern on the AC side."""
    state = _state()
    assert state['water_heater'] == 'Eco'


def test_water_heater_write_targets():
    """DHW.entities[0].write_fn maps each (kind, value) command to the right
    vendor POST target and body -- power, mode and temperature only, no fan/
    swing/preset (the AC's climate.py has those; the DHW loop doesn't)."""
    write = _desc('water_heater').write_fn
    assert write(('power', True), {}) == (
        ['power', 'dhw', 'vs', '0'], {'x.com.samsung.da.power': 'On'})
    assert write(('power', False), {}) == (
        ['power', 'dhw', 'vs', '0'], {'x.com.samsung.da.power': 'Off'})
    assert write(('mode', 'Force'), {}) == (
        ['mode', 'dhw', 'vs', '0'], {'x.com.samsung.da.modes': ['Force']})
    assert write(('temperature', 45.0), {}) == (
        ['temperatures', 'dhw', 'vs', '0'], {'x.com.samsung.da.desired': '45.0'})
    assert write(('bogus', 1), {}) is None


def test_dhw_power_and_temperature_declared_as_coverage():
    """/power/dhw/vs/0 and /temperatures/dhw/vs/0 are read by the composite
    water_heater entity (via water_heater.py's sibling reads), not given
    their own entities -- declared as no-entity coverage caps so discover()
    reports no gap, same pattern as the AC's CLIMATE_CONSUMED_HREFS."""
    reg, _ = _ehs()
    for href in ('/power/dhw/vs/0', '/temperatures/dhw/vs/0'):
        caps = reg.capabilities.get(href)
        assert caps, href
        assert all(c.entities == () for c in caps), href


def test_away_mode_reads_off():
    state = _state()
    assert state['away_mode'] is False


def test_away_mode_write_contract():
    desc = _desc('away_mode')
    path, body = desc.write_fn('On', {})
    assert path == ['option', 'outgoing', 'vs', '0']
    assert body == {'x.com.samsung.da.away': 'On'}
