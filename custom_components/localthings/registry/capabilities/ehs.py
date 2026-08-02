"""Capabilities for the Samsung EHS (Eco Heating System) air-to-water heat
pump family (TP1X_DA_AC_EHS-class, model TP1X_DA_AC_EHS_01001_0000).

An EHS unit runs two independently-controlled loops off one outdoor unit:
space heating/cooling ("zone1", through /mode/vs/0, /power/vs/0,
/temperatures/indoor/vs/0) and domestic hot water ("dhw", through
/mode/dhw/vs/0, /power/dhw/vs/0, /temperatures/dhw/vs/0). There's no shared
vocabulary with the room-AC family in airconditioner.py beyond the DA_AC_
board prefix -- EHS reports its own /mode/*/vs/0 and /temperatures/*/vs/0
shapes, not airconditioner.py's HREF_MODE/HREF_TEMP* OCF-pattern hrefs.

zone1 has no HA platform with matching semantics (it's a leaving-water-
temperature setpoint, not a thermostat with HVAC modes airconditioner.py's
climate.py would fit), so it stays switch/select/number/sensor -- same shape
as dehumidifier.py's power/mode/humidity split. dhw is a real HA
water_heater.py -- see DHW below and water_heater.py's module docstring --
following the same primary-resource-plus-sibling-reads pattern as
airconditioner.py's CLIMATE/climate.py.

Verified against a real TP1X_DA_AC_EHS_01001_0000 diagnostics dump
(firmware AEH-WW-TP1-22-AE6000_17260402, TizenRT 3.1 / DAWIT 2.0).
"""

from ..capability import Capability
from ..entities import NumberDesc, SelectDesc, SensorDesc, SwitchDesc, WaterHeaterDesc
from .common import normalize_temp_unit


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _first_mode(rep):
    """Representative scalar for a mode select -- `modes` is a single-element
    list on every dump seen so far, mirroring airconditioner._first_mode /
    dehumidifier._first_mode's handling of the same field shape."""
    modes = rep.get("x.com.samsung.da.modes")
    if isinstance(modes, (list, tuple)):
        return modes[0] if modes else None
    return modes


def _temp_unit(rep):
    return normalize_temp_unit(rep.get("x.com.samsung.da.unit"), "°C")


def _bounds(rep, default_min, default_max):
    """The resource's own (minimum, maximum) pair, or the defaults.

    Both ends together or neither -- a board reporting only one would
    otherwise pair a real device bound with an invented default, which
    looks plausible and is silently wrong. Same rule as
    climate._range()/water_heater._range(), and the same reason
    oven._setpoint_bounds resolves its pair in one place.
    """
    lo = _num(rep.get("x.com.samsung.da.minimum"))
    hi = _num(rep.get("x.com.samsung.da.maximum"))
    return (lo, hi) if (lo is not None and hi is not None) else (default_min, default_max)


def _step(rep, default):
    """`is None`, not `or` -- `or` collapses a genuine 0 (issue #160)."""
    step = _num(rep.get("x.com.samsung.da.increment"))
    return default if step is None else step


ZONE_POWER = Capability(
    href="/power/vs/0",
    poll_tier="warm",
    entities=(
        SwitchDesc(
            key="zone_power",
            field="x.com.samsung.da.power",
            icon="mdi:radiator",
            value_fn=lambda v: v == "On",
            write_fn=lambda p, rep, href=None: (
                ["power", "vs", "0"],
                {"x.com.samsung.da.power": "On" if p == "On" else "Off"},
            ),
        ),
    ),
)

ZONE_MODE = Capability(
    href="/mode/vs/0",
    poll_tier="warm",
    entities=(
        SelectDesc(
            key="zone_mode",
            rep_fn=_first_mode,
            icon="mdi:sun-snowflake-variant",
            options_field="x.com.samsung.da.supportedModes",
            write_fn=lambda p, rep, href=None: (
                ["mode", "vs", "0"],
                {"x.com.samsung.da.modes": [p]},
            ),
        ),
    ),
)

# type=Water/unit=Celsius on this dump names the space-heating loop's flow/
# room setpoint, not a literal water temperature -- Samsung EHS zone control
# is leaving-water-temperature-based, same convention as the dhw loop below.
ZONE_TEMPERATURE = Capability(
    href="/temperatures/indoor/vs/0",
    poll_tier="warm",
    entities=(
        SensorDesc(
            key="zone_temperature",
            field="x.com.samsung.da.current",
            device_class="temperature",
            unit_fn=_temp_unit,
            state_class="measurement",
            value_fn=_num,
        ),
        # Water-law (weather-compensation) offset applied to the calculated
        # flow setpoint. 'diagnostic' specifically because it's read-only on
        # this dump -- reclassify to 'config' if/when a write_fn is added.
        # No min/max/step bound to it: the rep's minimum/maximum/increment
        # fields scope `desired` (see the NumberDesc below), not `offset`.
        SensorDesc(
            key="zone_water_law_offset",
            field="x.com.samsung.da.offset",
            device_class="temperature",
            unit_fn=_temp_unit,
            state_class="measurement",
            entity_category="diagnostic",
            value_fn=_num,
        ),
        NumberDesc(
            key="zone_target_temperature",
            field="x.com.samsung.da.desired",
            device_class="temperature",
            unit_fn=_temp_unit,
            entity_category="config",
            value_fn=_num,
            native_min_fn=lambda rep: _bounds(rep, 5.0, 30.0)[0],
            native_max_fn=lambda rep: _bounds(rep, 5.0, 30.0)[1],
            step_fn=lambda rep: _step(rep, 0.5),
            write_fn=lambda p, rep, href=None: (
                ["temperatures", "indoor", "vs", "0"],
                {"x.com.samsung.da.desired": str(float(p))},
            ),
        ),
    ),
)

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
    # /ehscycle/vs/0 is bound -- see ehs_cycle.EHS_CYCLE.
    # /ehsfsv/vs/0 is bound -- see ehs_fsv.EHS_FSV.
    "/option/dhwdisplay/vs/0",  # front-panel DHW-display show/hide, cosmetic only
    "/reserverulesets/vs/0",  # opaque hex-encoded schedule reservation blob
    "/sac/installationinfo/vs/0",  # static outdoor/indoor installation info, diagnostic only
    "/actions/zone1/vs/0",  # zone1 schedule/timer program -- unmodeled for now
    "/actions/dhw/vs/0",  # DHW schedule/timer program -- unmodeled for now
]

COVERAGE = [Capability(href=h) for h in _EHS_IGNORED]
