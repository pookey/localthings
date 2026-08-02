"""HA-shaped entity descriptions. The subclass *type* selects the HA platform.

Frozen dataclasses so the future native HA component can consume them as
EntityDescription subclasses unchanged. Read transforms live in value_fn;
presence gating in exists_fn; write logic in write_fn on command platforms;
pre-write rejection (surfaced to the user, not just logged) in validate_fn
where a description declares one.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

WriteFn = Optional[Callable[[Any, dict], "tuple[list[str], dict] | None"]]
# (payload, rep, resources) -> a translation key, or None to allow the
# write. resources is the coordinator's full href->rep snapshot, for the same
# cross-resource lookups exists_fn needs (e.g. reading a sibling href's live
# option list).
ValidateFn = Optional[Callable[[Any, dict, dict], "str | None"]]


def _identity(v: Any) -> Any:
    return v


@dataclass(frozen=True, kw_only=True)
class SamsungEntityDescription:
    key: str
    field: str = ''
    # Defaults to `key`: entity names and states live in translations/, never
    # here, so a descriptor only sets this to share one catalog entry across
    # several descriptors, or to point at a differently-named one.
    translation_key: Any = None  # str | Callable[[dict[str, dict]], Optional[str]]
    # callable form receives the coordinator's full href->rep resource
    # snapshot and returns the key to use -- for a descriptor shared across
    # board generations whose state-code meaning isn't guaranteed consistent
    # between them; see laundry.cycle_select's table-id-gated resolver.
    translation_placeholders: Optional[Mapping[str, str]] = None
    # Dynamic resources such as fridge compartments and ice makers use a
    # device-provided or href-derived instance label inside a translated name.
    use_instance_name: bool = False
    icon: Optional[str] = None
    entity_category: Optional[str] = None      # 'diagnostic' | 'config' | None
    enabled_default: bool = True
    value_fn: Callable[[Any], Any] = _identity
    rep_fn: Optional[Callable[[dict], Any]] = None   # replaces field+value_fn; receives full rep
    # (rep, resources): rep is this entity's own href's representation;
    # resources is the coordinator's full href->rep snapshot, for gating
    # presence on a sibling resource (e.g. laundry.cycle_options's source).
    exists_fn: Optional[Callable[[dict, dict], bool]] = None


@dataclass(frozen=True, kw_only=True)
class SensorDesc(SamsungEntityDescription):
    device_class: Optional[str] = None
    state_class: Optional[str] = None
    unit: Optional[str] = None
    unit_fn: Optional[Callable[[dict], str]] = None  # overrides `unit` from the live rep, when set
    options: Optional[tuple] = None  # required by HA when device_class == 'enum'
    # Opt-in: gate this sensor's reported value behind the user-configurable
    # CONF_FINISH_TIME_HYSTERESIS_MINUTES threshold (see sensor.py). Only for
    # values that are expected to jitter around their "true" value between
    # device-side revisions -- not a general-purpose flag every sensor should set.
    hysteresis: bool = False


@dataclass(frozen=True, kw_only=True)
class BinarySensorDesc(SamsungEntityDescription):
    device_class: Optional[str] = None         # value_fn must return bool


@dataclass(frozen=True, kw_only=True)
class SelectDesc(SamsungEntityDescription):
    options: Any = ()        # tuple[str,...] | Callable[[dict[str, dict]], list[str]]
    # callable form receives the coordinator's full href->rep resource
    # snapshot (not just this entity's own href) and returns raw device
    # option values; see select.py's LocalThingsSelect._raw_options().
    options_field: Optional[str] = None  # resource field that contains the live options list
    write_fn: WriteFn = None


@dataclass(frozen=True, kw_only=True)
class SwitchDesc(SamsungEntityDescription):
    device_class: Optional[str] = None
    write_fn: WriteFn = None
    validate_fn: ValidateFn = None


@dataclass(frozen=True, kw_only=True)
class ButtonDesc(SamsungEntityDescription):
    payload: str = ''
    write_fn: WriteFn = None


@dataclass(frozen=True, kw_only=True)
class NumberDesc(SamsungEntityDescription):
    device_class: Optional[str] = None
    unit: Optional[str] = None
    unit_fn: Optional[Callable[[dict], str]] = None  # overrides `unit` from the live rep, when set
    native_min: Optional[float] = None
    native_max: Optional[float] = None
    step: Optional[float] = None
    # Override native_min/native_max/step from the live rep, when set --
    # same "static default, live override" shape as unit_fn, for resources
    # whose sane bounds depend on a per-device value (e.g. a temperature
    # setpoint reported in Celsius on one device, Fahrenheit on another).
    native_min_fn: Optional[Callable[[dict], float]] = None
    native_max_fn: Optional[Callable[[dict], float]] = None
    step_fn: Optional[Callable[[dict], float]] = None
    range_field: Optional[str] = None  # resource field containing [min, max] list
    write_fn: WriteFn = None


@dataclass(frozen=True, kw_only=True)
class TimeDesc(SamsungEntityDescription):
    write_fn: WriteFn = None


@dataclass(frozen=True, kw_only=True)
class ClimateDesc(SamsungEntityDescription):
    # A composite entity: it binds one *primary* resource (its href) but the
    # climate platform reads sibling resources (power, temperature, wind) from
    # the coordinator snapshot and writes to several of them. write_fn takes a
    # (kind, value) payload from the platform and returns the (path_segs, body)
    # for that one sub-write, so a single desc drives multi-resource writes.
    write_fn: WriteFn = None


@dataclass(frozen=True, kw_only=True)
class EhsZoneClimateDesc(ClimateDesc):
    # Same composite contract as ClimateDesc -- a distinct type only so the
    # climate platform can tell the two apart. fan.py dispatches its three
    # entity classes on the bound href; climate.py can't, because the EHS
    # space-heating zone and the room AC both bind /mode/vs/0
    # (airconditioner.HREF_MODE == ehs.HREF_ZONE_MODE). Being a *subclass*
    # means climate.async_setup_entry has to test for it before the plain
    # `isinstance(desc, ClimateDesc)` branch, which would otherwise swallow it.
    pass


@dataclass(frozen=True, kw_only=True)
class FanDesc(SamsungEntityDescription):
    # Composite fan entity: reads power from /power/0 and speed/support data
    # from its bound href.  Payloads are (kind, value), like ClimateDesc.
    write_fn: WriteFn = None


@dataclass(frozen=True, kw_only=True)
class WaterHeaterDesc(SamsungEntityDescription):
    # Composite water_heater entity: binds one primary resource (its href,
    # typically an operation-mode resource) but the water_heater platform
    # reads sibling resources (power, temperature) from the coordinator
    # snapshot and writes to several of them. Same (kind, value) -> (path_segs,
    # body) write_fn shape as ClimateDesc/FanDesc.
    write_fn: WriteFn = None


PLATFORM_OF: dict[type, str] = {
    SensorDesc:       'sensor',
    BinarySensorDesc: 'binary_sensor',
    SelectDesc:       'select',
    SwitchDesc:       'switch',
    ButtonDesc:       'button',
    NumberDesc:       'number',
    TimeDesc:         'time',
    ClimateDesc:      'climate',
    # Looked up by exact type(), not isinstance -- a ClimateDesc subclass needs
    # its own entry here or it resolves to no platform at all.
    EhsZoneClimateDesc: 'climate',
    FanDesc:          'fan',
    WaterHeaterDesc:  'water_heater',
}
