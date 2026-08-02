"""Capabilities for the Samsung EHS (Eco Heating System) air-to-water heat
pump family (TP1X_DA_AC_EHS-class, model TP1X_DA_AC_EHS_01001_0000).

An EHS unit runs two independently-controlled loops off one outdoor unit:
space heating/cooling ("zone1", through /mode/vs/0, /power/vs/0,
/temperatures/indoor/vs/0) and domestic hot water ("dhw", through
/mode/dhw/vs/0, /power/dhw/vs/0, /temperatures/dhw/vs/0). There's no shared
vocabulary with the room-AC family in airconditioner.py beyond the DA_AC_
board prefix -- EHS reports its own /mode/*/vs/0 and /temperatures/*/vs/0
shapes, not airconditioner.py's HREF_MODE/HREF_TEMP* OCF-pattern hrefs.

Each loop is one composite HA entity, both built the same way -- one primary
resource bound by a descriptor, its siblings read straight off the coordinator
snapshot, and a (kind, value) write_fn fanning writes back out across all
three. That's the same shape airconditioner.py's CLIMATE/climate.py uses.
zone1 is a climate entity (see ZONE below and climate.py's
LocalThingsEhsZoneClimate); dhw is a water_heater (see DHW below and
water_heater.py's module docstring).

zone1 being a climate entity is worth justifying, because it isn't a room
thermostat: /temperatures/indoor/vs/0 reports `type: Water`, and its `desired`
is the *leaving-water* (flow) setpoint, with `current` the actual flow
temperature. The tell is the bounds -- this unit reports 5.0/25.0 while in
Cool mode, which is exactly FSV #1011/#1012 ("Water Outlet Temp. for Cooling",
5-25 C by default); they swap to the heating pair #1031/#1032 when the mode
changes. Modelling a flow-temperature zone as a climate entity is nonetheless
the settled HA convention for air-to-water heat pumps, and the resources map
onto it one-for-one (power -> HVACMode.OFF, Cool/Heat/Auto -> COOL/HEAT/AUTO,
desired/current -> target/current temperature). The one thing it costs is that
min/max track the *current* mode, so they must be read live from the rep on
every access rather than baked into the descriptor.

Verified against a real TP1X_DA_AC_EHS_01001_0000 diagnostics dump
(firmware AEH-WW-TP1-22-AE6000_17260402, TizenRT 3.1 / DAWIT 2.0).
"""

from ..capability import Capability
from ..entities import EhsZoneClimateDesc, SwitchDesc, WaterHeaterDesc


def _first_mode(rep):
    """Representative scalar for a mode select -- `modes` is a single-element
    list on every dump seen so far, mirroring airconditioner._first_mode /
    dehumidifier._first_mode's handling of the same field shape."""
    modes = rep.get("x.com.samsung.da.modes")
    if isinstance(modes, (list, tuple)):
        return modes[0] if modes else None
    return modes


# Canonical zone1 (space heating/cooling) resource hrefs. climate.py binds the
# primary HREF_ZONE_MODE via ZONE below and reads the sibling power/temperature
# hrefs off the coordinator snapshot. Declared once here and imported by
# climate.py, so a new sibling read can't drift out of sync with the
# entity-less capabilities that give those siblings their coverage -- exactly
# the arrangement HREF_DHW_* / DHW_CONSUMED_HREFS use for the dhw loop below.
#
# HREF_ZONE_MODE collides with airconditioner.HREF_MODE. That's why the zone
# descriptor is its own EhsZoneClimateDesc type rather than a plain
# ClimateDesc: climate.py cannot dispatch on the href the way fan.py does.
HREF_ZONE_POWER = "/power/vs/0"  # on/off -> HVACMode.OFF
HREF_ZONE_MODE = "/mode/vs/0"  # primary (bound by ZONE) -- hvac_mode
HREF_ZONE_TEMPERATURE = "/temperatures/indoor/vs/0"  # current/target temperature


def _zone_write(payload, rep, href=None):
    """Map a (kind, value) command from the climate platform to the
    (path_segs, body) for that one sub-write -- same contract as
    _dhw_write below and airconditioner._climate_write, across the zone1
    loop's three resources."""
    kind, value = payload
    if kind == "power":
        return (["power", "vs", "0"], {"x.com.samsung.da.power": "On" if value else "Off"})
    if kind == "mode":
        return (["mode", "vs", "0"], {"x.com.samsung.da.modes": [value]})
    if kind == "temperature":
        return (
            ["temperatures", "indoor", "vs", "0"],
            {"x.com.samsung.da.desired": str(float(value))},
        )
    return None


ZONE = Capability(
    href=HREF_ZONE_MODE,
    poll_tier="warm",
    entities=(
        EhsZoneClimateDesc(
            key="zone_climate", translation_key="zone1", rep_fn=_first_mode, write_fn=_zone_write
        ),
    ),
)

# Power and temperature are read by the composite ZONE entity above, not given
# their own entities -- coverage-only caps so discover() reports no gap, the
# same shape as DHW_CONSUMED below.
ZONE_POWER = Capability(href=HREF_ZONE_POWER, poll_tier="warm")
ZONE_TEMPERATURE = Capability(href=HREF_ZONE_TEMPERATURE, poll_tier="warm")

# Canonical dhw resource hrefs. water_heater.py binds the primary HREF_DHW_MODE
# via DHW below and reads the sibling power/temperature hrefs off the
# coordinator snapshot -- same primary-plus-siblings shape as
# airconditioner.py's HREF_MODE/CLIMATE_CONSUMED_HREFS. Declared once here
# and imported by water_heater.py, so a new sibling read can't drift out of
# sync with its DHW_CONSUMED_HREFS coverage entry below.
HREF_DHW_POWER = "/power/dhw/vs/0"  # on/off
HREF_DHW_MODE = "/mode/dhw/vs/0"  # primary (bound by DHW) -- current_operation
HREF_DHW_TEMPERATURE = "/temperatures/dhw/vs/0"  # current/target temperature

DHW_CONSUMED_HREFS = [HREF_DHW_POWER, HREF_DHW_TEMPERATURE]


def _dhw_write(payload, rep, href=None):
    """Map a (kind, value) command from the water_heater platform to the
    (path_segs, body) for that one sub-write -- same contract as
    airconditioner._climate_write, just across the dhw loop's three
    resources instead of the AC's power/mode/temperature/wind set."""
    kind, value = payload
    if kind == "power":
        return (["power", "dhw", "vs", "0"], {"x.com.samsung.da.power": "On" if value else "Off"})
    if kind == "mode":
        return (["mode", "dhw", "vs", "0"], {"x.com.samsung.da.modes": [value]})
    if kind == "temperature":
        return (["temperatures", "dhw", "vs", "0"], {"x.com.samsung.da.desired": str(float(value))})
    return None


DHW = Capability(
    href=HREF_DHW_MODE,
    poll_tier="warm",
    entities=(
        WaterHeaterDesc(
            key="water_heater", translation_key="dhw", rep_fn=_first_mode, write_fn=_dhw_write
        ),
    ),
)

# Power and temperature are read by the composite DHW entity above, not
# given their own entities -- coverage-only caps so discover() reports no
# gap (see airconditioner.py's CLIMATE_CONSUMED_HREFS for the same pattern).
DHW_CONSUMED = [Capability(href=h, poll_tier="warm") for h in DHW_CONSUMED_HREFS]

# Deliberately a plain config switch, not water_heater's AWAY_MODE feature.
# HA core's smartthings water_heater does wire this same Samsung capability
# (CUSTOM_OUTING_MODE) up to WaterHeaterEntityFeature.AWAY_MODE, and the DHW
# operation-mode map above is taken from that integration -- so the
# divergence is worth stating. /option/outgoing/vs/0 is device-wide: one
# `away` flag covering the whole unit, zone1 included (it has no dhw-scoped
# sibling href, unlike every other resource in this loop). Hanging it off
# the DHW card would present a device-wide setting as if it only affected
# hot water. It stays a switch until a board turns up with a per-loop away
# resource to bind instead.
AWAY_MODE = Capability(
    href="/option/outgoing/vs/0",
    poll_tier="cold",
    entities=(
        SwitchDesc(
            key="away_mode",
            field="x.com.samsung.da.away",
            icon="mdi:home-export-outline",
            entity_category="config",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["option", "outgoing", "vs", "0"],
                {"x.com.samsung.da.away": "On" if p == "On" else "Off"},
            ),
        ),
    ),
)

# ---------------------------------------------------------------------------
# EHS-scoped coverage: opaque vendor plumbing (hex-encoded factory/cycle/
# schedule blobs) or resources with no confirmed write contract on this
# dump, following the same 'don't guess' rule as dehumidifier._DHM_IGNORED.
# Not in the global ignored.IGNORED since these are EHS-only shapes that
# would need their own verification on other device families.
# ---------------------------------------------------------------------------
_EHS_IGNORED = [
    "/availablecontrolsets/vs/0",  # opaque hex-encoded control-set bitmap (id: EHS)
    "/da/softreset/vs/0",  # soft-reset trigger plumbing
    "/diagnosis/vs/0",  # empty {} on this dump
    "/ehscycle/vs/0",  # opaque hex-encoded indoor/outdoor cycle log
    "/ehsfsv/vs/0",  # opaque hex-encoded factory setting values
    "/option/dhwdisplay/vs/0",  # front-panel DHW-display show/hide, cosmetic only
    "/reserverulesets/vs/0",  # opaque hex-encoded schedule reservation blob
    "/sac/installationinfo/vs/0",  # static outdoor/indoor installation info, diagnostic only
    "/actions/zone1/vs/0",  # zone1 schedule/timer program -- unmodeled for now
    "/actions/dhw/vs/0",  # DHW schedule/timer program -- unmodeled for now
]

COVERAGE = [Capability(href=h) for h in _EHS_IGNORED]
