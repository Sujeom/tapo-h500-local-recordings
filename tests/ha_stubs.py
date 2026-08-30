"""Home Assistant, stubbed far enough to import and drive this integration.

Fifteen of the thirty component modules had no test that imported them at all
-- binary_sensor.py at 569 lines, config_flow.py at 412 -- because importing
one pulls in `homeassistant.components.*`, which nothing here provided. What
stood in for coverage was reading the module as text and asserting on the
source, and two defects walked straight through that: a storage warning whose
branch could never run, and a silence watchdog that switched itself off. Both
sat behind a green suite for months.

So this stubs the whole surface the component imports -- 37 modules, measured
from its own import statements rather than guessed -- and makes the entity
bases real enough to construct and call.

Two rules kept it honest and small:

- Names that only need to EXIST are manufactured on demand by a module-level
  `__getattr__`. Device-class and feature enums are only ever assigned or
  compared, never computed with, so `BinarySensorDeviceClass.PROBLEM` can be
  the string "PROBLEM" and no assertion here can tell. Listing ninety names by
  hand would be a standing tax that buys nothing.
- Names a test will actually EXERCISE are written out properly: the entity
  description dataclass, the `_attr_` entity conventions, and
  CoordinatorEntity. A stub that quietly swallows a call is worse than no
  stub, because the test still passes.

The description fields are exactly the ones the component passes at its own
construction sites. A field nobody passes would be a guess about Home
Assistant's shape that nothing here could check.
"""
from __future__ import annotations

import datetime
import importlib.abc
import importlib.util
import pathlib
import sys
import types
from dataclasses import dataclass

COMPONENT_PATH = None       # set by install(); the component directory
_REAL_MEDIA = None          # the genuine media module, loaded on first use


class _AnyMeta(type):
    """Attribute access yields the attribute's own name, once, and caches it.

    This is what makes the enums work without enumerating them.
    """

    def __getattr__(cls, name):
        if name.startswith("__"):
            raise AttributeError(name)
        # A class rather than the bare name. Enum members are only ever
        # assigned or compared, so either would do for them -- but schema
        # helpers are CALLED (`DEVICE_TRIGGER_BASE_SCHEMA.extend({...})`),
        # and a string is not callable.
        made = type(name, (_Any,), {})
        setattr(cls, name, made)
        return made


class _Any(metaclass=_AnyMeta):
    """A stand-in that accepts any construction and keeps what it was given."""

    def __init_subclass__(cls, **kwargs):
        # Home Assistant declares `class Flow(ConfigFlow, domain=DOMAIN)`, a
        # class keyword the real base consumes. Swallow it rather than let
        # object.__init_subclass__ reject it.
        return None

    def __init__(self, *args, **kwargs):
        for key, value in kwargs.items():
            object.__setattr__(self, key, value)


@dataclass(frozen=True, kw_only=True)
class _EntityDescription:
    """Every field this component passes, and only those.

    Frozen and kw_only because the component's own subclasses are declared
    `@dataclass(frozen=True, kw_only=True)`, and Python refuses to build a
    frozen dataclass on a non-frozen base.
    """

    key: str
    translation_key: str | None = None
    device_class: object = None
    entity_category: object = None
    native_unit_of_measurement: object = None
    native_max_value: float | None = None
    native_min_value: float | None = None
    native_step: float | None = None
    state_class: object = None
    suggested_display_precision: int | None = None


class _Entity:
    """The `_attr_` convention, which is all these platforms use.

    `async_write_ha_state` counts rather than no-ops: "did writing state
    happen" is a real assertion, and a silent no-op would answer it wrongly.
    """

    _attr_has_entity_name = True
    entity_description = None
    hass = None

    def __init__(self, *args, **kwargs):
        self.writes = 0
        self._removers: list = []

    def __getattr__(self, name):
        """Home Assistant exposes `_attr_x` as the read-only property `x`.

        Mirrored generically rather than by listing unique_id, translation_key,
        device_info, icon, entity_category and the rest: a test that has to
        reach for `_attr_unique_id` is testing the stub's shape instead of the
        entity's. Only reached when normal lookup fails, so a real property on
        the component's own class still wins.
        """
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return object.__getattribute__(self, f"_attr_{name}")
        except AttributeError:
            raise AttributeError(name) from None

    def async_write_ha_state(self) -> None:
        self.writes += 1

    def async_on_remove(self, remover) -> None:
        self._removers.append(remover)

    async def async_added_to_hass(self) -> None:
        return None

    @property
    def available(self) -> bool:
        return True


class _CoordinatorEntity(_Entity):
    def __init__(self, coordinator, context=None):
        super().__init__()
        self.coordinator = coordinator

    def __class_getitem__(cls, item):
        return cls

    async def async_added_to_hass(self) -> None:
        return None


class _StubCoordinatorBase:
    """Enough DataUpdateCoordinator for the subclass to construct and run."""

    def __init__(self, hass, logger, name=None, config_entry=None,
                 update_interval=None):
        self.hass = hass
        self.config_entry = config_entry
        self.update_interval = update_interval
        # The real one sets this before the first refresh, and code that reads
        # it defensively must be exercised against None rather than a missing
        # attribute -- those fail differently.
        self.data = None
        # The real one starts true and is set from each refresh. Without it
        # the repair checks raised on every poll and were skipped, silently
        # until the failure was made audible.
        self.last_update_success = True

    def __class_getitem__(cls, item):
        return cls


class _EventEntity(_Entity):
    """An EventEntity that keeps what it fired, in order."""

    def _trigger_event(self, event_type, event_attributes=None) -> None:
        if not hasattr(self, "triggered"):
            self.triggered = []
        self.triggered.append((event_type, dict(event_attributes or {})))


def _module(path: str, **names) -> types.ModuleType:
    """Register `path` now, with real behaviour. For names a test exercises."""
    module = types.ModuleType(path)
    module.__dict__.update(names)
    module.__getattr__ = _manufacture(module)
    sys.modules[path] = module
    return module


def _manufacture(module):
    """Any unlisted attribute becomes a class, once, and is cached."""
    def __getattr__(name, _module=module):
        if name.startswith("__"):
            raise AttributeError(name)
        # `from homeassistant.helpers import issue_registry` asks the PARENT
        # for the attribute before importing the submodule, so a manufactured
        # class here would shadow the real module -- and shadow a test's own
        # stub of it, which is worse.
        child = f"{_module.__name__}.{name}"
        if child in _StubFinder.MODULES:
            return importlib.import_module(child)
        made = type(name, (_Any,), {})
        setattr(_module, name, made)
        return made
    return __getattr__


class _StubLoader(importlib.abc.Loader):
    def create_module(self, spec):
        module = types.ModuleType(spec.name)
        module.__getattr__ = _manufacture(module)
        module.__path__ = []          # so submodules of it can be imported too
        return module

    def exec_module(self, module):
        return None


class _StubFinder(importlib.abc.MetaPathFinder):
    """Manufacture a homeassistant MODULE only when something imports it.

    Registering the whole surface up front looked simpler and was wrong: four
    test files install their own stubs with `sys.modules.setdefault`, and a
    module already sitting there turns those into silent no-ops -- which is
    how a working ffmpeg stub got replaced by one that could not run ffmpeg.
    Deferring to import time fixes that by construction: whoever registers
    first wins, and this only answers for a name nobody else supplied.

    Scoped to a known list rather than any dotted name under `homeassistant`.
    A finder that answers for everything turns `from ...network import get_url`
    into a module instead of a callable, and `from ...helpers import
    issue_registry` into a class instead of a test's own stub. The list is the
    component's own import statements, so it cannot drift from what is needed
    without an import failing loudly.

    Appended to `sys.meta_path` rather than inserted, so a genuinely installed
    Home Assistant is still found first.
    """

    MODULES = frozenset({
        # Not a Home Assistant module, but preview.py imports `aiohttp.web` for
        # web.Response and the suite runs on a python without it. The finder is
        # appended to sys.meta_path, so a real aiohttp is still found first.
        "aiohttp", "aiohttp.web",
        # voluptuous, likewise: config_flow and device_trigger build their
        # schemas with it and it is not installed on the suite's python.
        "voluptuous",
        # pytapo, likewise: api.py imports Tapo at module level.
        "pytapo",
        "homeassistant",
        "homeassistant.components", "homeassistant.components.binary_sensor",
        "homeassistant.components.button", "homeassistant.components.calendar",
        "homeassistant.components.camera",
        "homeassistant.components.device_automation",
        "homeassistant.components.event", "homeassistant.components.ffmpeg",
        "homeassistant.components.frontend",
        "homeassistant.components.homeassistant",
        "homeassistant.components.homeassistant.triggers",
        "homeassistant.components.http", "homeassistant.components.http.auth",
        "homeassistant.components.image",
        "homeassistant.components.media_player",
        "homeassistant.components.media_source",
        "homeassistant.components.number", "homeassistant.components.repairs",
        "homeassistant.components.select", "homeassistant.components.sensor",
        "homeassistant.components.siren", "homeassistant.components.switch",
        "homeassistant.components.update",
        "homeassistant.config_entries", "homeassistant.const",
        "homeassistant.core", "homeassistant.exceptions",
        "homeassistant.helpers", "homeassistant.helpers.config_validation",
        "homeassistant.helpers.device_registry",
        "homeassistant.helpers.dispatcher",
        "homeassistant.helpers.entity_platform",
        "homeassistant.helpers.entity_registry",
        "homeassistant.helpers.event", "homeassistant.helpers.intent",
        "homeassistant.helpers.issue_registry",
        "homeassistant.helpers.network", "homeassistant.helpers.selector",
        "homeassistant.helpers.trigger", "homeassistant.helpers.typing",
        "homeassistant.helpers.update_coordinator",
        "homeassistant.loader", "homeassistant.util", "homeassistant.util.dt",
    })

    def find_spec(self, name, path=None, target=None):
        # pytapo by prefix: api.py reaches into media_stream.crypto and
        # friends, and listing a third-party package's internals here would
        # be a copy of it that goes stale.
        if name not in self.MODULES and not name.startswith("pytapo."):
            return None
        return importlib.util.spec_from_loader(name, _StubLoader())


class _FakeNow:
    @staticmethod
    def timestamp():
        return 1_786_600_000


def install(component_path=None):
    """Put the stubs in sys.modules. Idempotent; safe to import twice."""
    global COMPONENT_PATH
    if component_path is not None:
        COMPONENT_PATH = component_path

    if not any(isinstance(f, _StubFinder) for f in sys.meta_path):
        sys.meta_path.append(_StubFinder())

    # Everything below needs REAL behaviour, so it is registered rather than
    # manufactured. Anything not here is left to the finder -- notably ffmpeg,
    # media_player, media_source and issue_registry, which individual tests
    # stub themselves and must be allowed to win.
    _module("homeassistant.config_entries",
            ConfigEntry=type("ConfigEntry", (_Any,), {}))
    _module("homeassistant.core",
            HomeAssistant=type("HomeAssistant", (_Any,), {}),
            Event=type("Event", (_Any,), {}),
            CALLBACK_TYPE=object,
            callback=lambda fn: fn)
    errors = _module("homeassistant.exceptions",
                     HomeAssistantError=type("HomeAssistantError",
                                             (Exception,), {}))
    for name in ("ConfigEntryNotReady", "ConfigEntryAuthFailed",
                 "ServiceValidationError"):
        setattr(errors, name, type(name, (errors.HomeAssistantError,), {}))

    # intent.py registers Assist handlers; bare bases are enough here.
    _module("homeassistant.helpers.intent",
            IntentHandler=type("IntentHandler", (), {}),
            Intent=type("Intent", (), {}),
            IntentResponse=type("IntentResponse", (), {}),
            async_register=lambda hass, handler: None)

    dispatcher = _module("homeassistant.helpers.dispatcher")
    dispatcher.sent = []
    dispatcher.async_dispatcher_send = lambda hass, signal, *a: (
        dispatcher.sent.append((signal, a)))
    dispatcher.async_dispatcher_connect = (
        lambda hass, signal, target: (lambda: None))

    _module("homeassistant.helpers.update_coordinator",
            DataUpdateCoordinator=_StubCoordinatorBase,
            CoordinatorEntity=_CoordinatorEntity,
            UpdateFailed=type("UpdateFailed", (Exception,), {}))
    _module("homeassistant.helpers.device_registry", DeviceInfo=dict)

    dt = _module("homeassistant.util.dt")
    dt.utcnow = lambda: _FakeNow()
    # Real datetimes, so anything deriving a local calendar day or hour from a
    # timestamp is exercised rather than stubbed into always agreeing.
    dt.utc_from_timestamp = lambda ts: datetime.datetime.fromtimestamp(
        ts, datetime.timezone.utc)
    # Deliberately NOT the machine's own zone, and deliberately not UTC.
    #
    # On a UTC build server "local" and UTC agree, so code that computes a
    # calendar day or an hour in UTC by mistake passes every test. A fixed
    # -07:00 keeps that honest and has no daylight saving to make the result
    # depend on the date being tested.
    dt.LOCAL = datetime.timezone(datetime.timedelta(hours=-7))
    dt.as_local = lambda value: value.astimezone(dt.LOCAL)
    _module("homeassistant.util", dt=dt)

    # The four platforms whose EntityDescription this component subclasses as a
    # frozen dataclass. One shared description class: the component subclasses
    # all four identically, and four copies would drift apart.
    for platform in ("binary_sensor", "sensor", "switch", "number"):
        camel = _camel(platform)
        _module(f"homeassistant.components.{platform}", **{
            f"{camel}EntityDescription": _EntityDescription,
            f"{camel}Entity": type(f"{camel}Entity", (_Entity,), {}),
        })
    # The event platform, registered rather than manufactured because a test
    # has to read back what an entity fired. The real base sets state and
    # attributes; what a test needs is the type and the payload.
    _module("homeassistant.components.event",
            EventEntity=_EventEntity)
    _module("homeassistant.components.http.auth",
            async_sign_path=lambda hass, path, expiry: f"{path}?authSig=stub")

    package = types.ModuleType("tapo_h500")
    package.__path__ = [str(COMPONENT_PATH)]
    sys.modules["tapo_h500"] = package

    # media.py pulls in more of Home Assistant than the coordinator tests need,
    # and the download path is not what they exercise. Tests that DO exercise
    # it import the real module instead of going through here.
    media = types.ModuleType("tapo_h500.media")
    media.EmptyRecordingError = type(
        "EmptyRecordingError", (errors.HomeAssistantError,), {})
    for name in ("async_download_clip", "async_latest_image",
                 "async_preview_clip", "async_prune", "async_prune_previews",
                 "async_verify", "async_export"):
        setattr(media, name, None)
    media.existing_clip = lambda *a, **k: None
    # Everything NOT named above comes from the real module. media.py is
    # mostly path arithmetic -- clip_path, camera_dir, media_root -- and a
    # test asserting against a manufactured path would prove nothing. Only
    # the download coroutines above are replaced, because those are what
    # reach the hub.
    media.__getattr__ = _real_media_attr
    sys.modules["tapo_h500.media"] = media
    return dispatcher


def real_module(name: str):
    """Load one of the component's own modules for real, under a private name.

    `tapo_h500` in `sys.modules` is the stub package this file installs, so
    importing by name gives back the stub. Loading from the file with a name
    inside that package is what lets `from .const import ...` resolve.

    Cached, because executing a module twice gives two sets of classes and
    `isinstance` stops meaning anything between them.
    """
    private = f"tapo_h500._real_{name}"
    loaded = sys.modules.get(private)
    if loaded is not None:
        return loaded
    filename = "__init__.py" if name == "init" else f"{name}.py"
    spec = importlib.util.spec_from_file_location(
        private, str(pathlib.Path(COMPONENT_PATH) / filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[private] = module
    spec.loader.exec_module(module)
    return module


def _real_media_attr(name):
    """Load the genuine media module once, under a private name, and read it.

    Imported from its file rather than by name: `tapo_h500.media` is the stub
    we just installed, so importing it by name would return the stub itself.
    """
    if name.startswith("__"):
        raise AttributeError(name)
    global _REAL_MEDIA
    if _REAL_MEDIA is None:
        # Named inside the package on purpose: media.py does `from .const
        # import ...`, and a module loaded with no parent has nothing for the
        # dot to resolve against.
        module_name = "tapo_h500._real_media"
        spec = importlib.util.spec_from_file_location(
            module_name, str(pathlib.Path(COMPONENT_PATH) / "media.py"))
        _REAL_MEDIA = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = _REAL_MEDIA
        spec.loader.exec_module(_REAL_MEDIA)
    return getattr(_REAL_MEDIA, name)


def _camel(platform: str) -> str:
    return "".join(part.capitalize() for part in platform.split("_"))
