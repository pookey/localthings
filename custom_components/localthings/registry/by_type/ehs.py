"""EHS (Eco Heating System) air-to-water heat pump device registry
(Samsung TP1X_DA_AC_EHS-class).

Shares the DA_AC_ board prefix with the room-AC family in
airconditioner.py, but its /mode/*/vs/0 and /temperatures/*/vs/0 resources
are its own shape (two independent loops: zone1 space heating/cooling and
dhw domestic hot water), not airconditioner.py's HREF_MODE/HREF_TEMP* OCF
pattern -- so nothing from that module is reused here except MUTE_ONCE,
whose /option/muteonce/vs/0 field shape (`muteonce`) is identical on this
family's dump.

Each loop is one composite entity -- ZONE is a climate entity and DHW a
water_heater -- with the loop's power and temperature resources listed here
purely as coverage, since the composite reads them itself.
"""

from ..capabilities import airconditioner, common, ehs, ehs_cycle, ehs_fsv, ignored
from ._base import DeviceRegistry, _build

REGISTRY = DeviceRegistry(
    name="ehs",
    capabilities=_build(
        [
            *ignored.IGNORED,
            *common.UNIVERSAL,
            airconditioner.MUTE_ONCE,
            ehs.ZONE,
            ehs.ZONE_POWER,
            ehs.ZONE_TEMPERATURE,
            ehs.DHW,
            *ehs.DHW_CONSUMED,
            ehs.AWAY_MODE,
            ehs_cycle.EHS_CYCLE,
            ehs_fsv.EHS_FSV,
            *ehs.COVERAGE,
        ]
    ),
)
