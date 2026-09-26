from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field, replace
from difflib import get_close_matches
from enum import StrEnum
from glob import glob
from pathlib import Path
from typing import Any

from .errors import CommandSyntaxError, ConfigError
from .input.dsl import (
    VerbToken,
    apply_aliases,
    check_hud_target,
    invalid_placeholders,
    validate_response,
)
from .input.executor import DEFAULT_TYPE_DELAY
from .input.keymap import untypeable
from .layers import DEFAULT_CONFIRM_SECONDS, Layer, parse_layers, parse_target
from .pattern import CommandPattern
from .pedals.reader import DEFAULT_SEQUENCE_MS, DEFAULT_TAP_MS
from .text import normalize, to_ascii
from .tracking.gestures import FACE_FEATURES
from .tracking.gestures import HOLD_KEY as GESTURE_HOLD_KEY
from .tracking.hands import CALIBRATION_KEYS, HAND_FEATURES, Calibration
from .tracking.screenmap import TERMS, ScreenBounds, ScreenMap

logger = logging.getLogger(__name__)

CONFIG_FILE_NAME = "config.json"
SCRIPT_DIR_NAME = "scripts"

TEMPLATE_TYPE = "import"

_KEYS_NOT_IMPORTED = ("type", "imports")

_KEYS_NOT_MERGED = ("icon", "imports")

_TYPED_BLOCKS = ("vosk", "transformer")

DEFAULT_RELOAD_COMMAND = "reload config"

DEFAULT_PANIC_COMMAND = "release everything"

DEFAULT_PREVIOUS_MODE_KEYWORD = "previous mode"

DEFAULT_SHEET_OPEN = "overlay open"
DEFAULT_SHEET_CLOSE = "overlay close"
DEFAULT_SHEET_MODES = "overlay modes"
DEFAULT_SHEET_PEDALS = "overlay pedals"

DEFAULT_SHEET_GESTURES = "overlay gestures"
DEFAULT_CHIP_COMMAND = "chip toggle"

DEFAULT_METERS_COMMAND = "overlay meters"

DEFAULT_NAVIGATE = "open"

MAX_QUIET_GRAMMAR_VARIANTS = 500

DEFAULT_PEDAL_VENDOR_ID = 0x0FD9
DEFAULT_PEDAL_PRODUCT_ID = 0x0086
DEFAULT_PEDAL_BUTTONS: dict[str, int] = {"left": 4, "middle": 5, "right": 6}
DEFAULT_PEDAL_REPORT_LENGTH = 8

DEFAULT_PEDAL_READ_TIMEOUT_MS = 10

KNOWN_FEATURES: frozenset[str] = frozenset(FACE_FEATURES) | frozenset(HAND_FEATURES)

DEFAULT_GESTURE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "pucker": {"mouthPucker": {"min": 0.9, "release": 0.1}},
    "surprise": {
        "jawOpen": {"min": 0.4, "release": 0.2},
        "browInnerUp": {"min": 0.5, "release": 0.2},
    },
    "left_smirk": {
        "mouthSmileLeft": {"min": 0.6, "release": 0.3},
        "mouthSmileRight": {"max": 0.2, "release": 0.3},
    },
    "left_wink": {
        "eyeBlinkLeft": {"min": 0.6, "release": 0.3},
        "eyeBlinkRight": {"max": 0.2, "release": 0.3},
    },
    "blink": {
        "eyeBlinkLeft": {"min": 0.5, "release": 0.2},
        "eyeBlinkRight": {"min": 0.5, "release": 0.2},
    },
}


def spoken_phrase(text: str) -> str:
    """Normalize a configured phrase the way a recognized utterance is normalized."""
    return normalize(text).lower()


def _optional_phrase(value: Any) -> str | None:
    """A configured phrase, or None when the setting is unset or explicitly null."""
    return spoken_phrase(value) if value else None


class ModeKind(StrEnum):
    """The recognizer backing a mode, or IMPORT for one that has none."""

    VOSK = "vosk"
    TRANSFORMER = "transformer"
    IMPORT = TEMPLATE_TYPE


@dataclass(frozen=True, slots=True)
class Origin:
    """Where one command or gesture action was declared."""

    group: str
    source: str = CONFIG_FILE_NAME


@dataclass(frozen=True, slots=True)
class Command:
    """One entry of a mode's command table."""

    pattern: CommandPattern
    response: str
    origin: Origin | None = field(default=None, compare=False)


@dataclass(frozen=True, slots=True)
class VoskSettings:
    """Where to find the Kaldi model for a command mode."""

    model_path: str


@dataclass(frozen=True, slots=True)
class TransformerSettings:
    """Model and voice-activity thresholds for a dictation mode."""

    model_name: str
    silence_seconds: float = 0.3
    max_chunk_seconds: float = 30.0
    energy_threshold: float = 0.01
    pre_roll_seconds: float = 0.25
    lang: str = "en"
    device: str = "auto"


POINTER_RATE = "rate"

POINTER_ABSOLUTE = "absolute"

POINTER_LAWS = (POINTER_RATE, POINTER_ABSOLUTE)


@dataclass(frozen=True, slots=True)
class PointerSettings:
    """Per-mode head-to-pointer response curve."""

    enabled: bool = False
    law: str = "rate"
    dead_angle_h: float = 2.0
    dead_angle_v: float = 2.0
    speed_power: float = 1.5
    full_speed_angle: float = 18.0
    max_speed_px_per_sec: float = 800.0
    invert_x: bool = False
    invert_y: bool = False


@dataclass(frozen=True, slots=True)
class CaptureSettings:
    """Webcam capture request; None leaves a field at the driver's default."""

    fourcc: str | None = "MJPG"
    width: int | None = 1920
    height: int | None = 1080
    fps: int | None = 60


@dataclass(frozen=True, slots=True)
class HandTrackingSettings:
    """The second model on the same frame; None when hand scores are off."""

    model_path: str
    max_hands: int = 2
    stride: int = 4
    min_detection_confidence: float = 0.5
    min_presence_confidence: float = 0.5
    min_tracking_confidence: float = 0.5
    calibration: Calibration = Calibration()


@dataclass(frozen=True, slots=True)
class HeadTrackingSettings:
    """Global head-tracking configuration; None when the feature is off."""

    model_path: str
    camera: int | str | None = None
    min_detection_confidence: float = 0.5
    min_tracking_confidence: float = 0.5
    offset_x: float = 0.0
    offset_y: float = 0.0
    filter_min_cutoff: float = 1.0
    filter_beta: float = 0.05
    auto_recenter_seconds: float | None = None
    gesture_definitions: dict[str, dict[str, Any]] = field(default_factory=dict)
    face_baseline: dict[str, float] = field(default_factory=dict)
    screen: ScreenMap = ScreenMap()
    placement: ScreenBounds = ScreenBounds()
    debug_gestures: tuple[str, ...] = ()
    capture: CaptureSettings = CaptureSettings()
    hands: HandTrackingSettings | None = None


@dataclass(frozen=True, slots=True)
class EdgeBinding:
    """What one pedal or gesture does on each edge."""

    press: str | None = None
    release: str | None = None

    def response(self, *, pressed: bool) -> str | None:
        """The response for one edge, or None when this binding ignores it."""
        return self.press if pressed else self.release

    @property
    def responses(self) -> tuple[tuple[str, str], ...]:
        """Each half that exists, labelled, for validation and for a display."""
        return tuple(
            (edge, response)
            for edge, response in (("press", self.press), ("release", self.release))
            if response
        )


@dataclass(frozen=True, slots=True)
class PedalSettings:
    """The one pedal device: how to find it, and which byte carries which pedal."""

    vendor_id: int = DEFAULT_PEDAL_VENDOR_ID
    product_id: int = DEFAULT_PEDAL_PRODUCT_ID
    buttons: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_PEDAL_BUTTONS))
    report_length: int = DEFAULT_PEDAL_REPORT_LENGTH
    read_timeout_ms: int = DEFAULT_PEDAL_READ_TIMEOUT_MS
    tap_ms: int = DEFAULT_TAP_MS
    sequence_ms: int = DEFAULT_SEQUENCE_MS

    def taps(self, binding: str) -> tuple[str, ...]:
        """ "left left" as the taps it names, or () when it names one pedal."""
        if binding in self.buttons:
            return ()
        parts = tuple(binding.split())
        return parts if len(parts) > 1 and all(part in self.buttons for part in parts) else ()

    def describe(self) -> str:
        """ "0fd9:0086 (left, middle, right)" -- for a log line or an error."""
        return (
            f"{self.vendor_id:04x}:{self.product_id:04x} "
            f"({', '.join(self.buttons) or 'no pedals bound'})"
        )


@dataclass(frozen=True, slots=True)
class GroupDisplay:
    """How one group is shown, when the default is not what the author wants."""

    say: str | None = None
    hide: bool = False


@dataclass(frozen=True, slots=True)
class OutputSettings:
    """Which video output the surfaces may appear on."""

    name: str | None = None
    only: bool = True


@dataclass(frozen=True, slots=True)
class OverlaySettings:
    """The phrases that drive the on-screen display, and any per-group overrides."""

    chip_command: str | None = DEFAULT_CHIP_COMMAND
    chip_enabled: bool = True
    meters_command: str | None = DEFAULT_METERS_COMMAND
    meters_enabled: bool = True
    sheet_open: str | None = DEFAULT_SHEET_OPEN
    sheet_close: str | None = DEFAULT_SHEET_CLOSE
    sheet_modes: str | None = DEFAULT_SHEET_MODES
    sheet_pedals: str | None = DEFAULT_SHEET_PEDALS
    sheet_gestures: str | None = DEFAULT_SHEET_GESTURES
    navigate: str | None = DEFAULT_NAVIGATE
    output: OutputSettings = OutputSettings()
    sleep_hides: bool = False
    groups: dict[str, GroupDisplay] = field(default_factory=dict)

    @property
    def phrases(self) -> dict[str, str]:
        """Phrase -> the setting that reserved it, for the reserved-phrase machinery."""
        declared = (
            (self.chip_command, "overlay.chip.command"),
            (self.sheet_open, "overlay.sheet.open"),
            (self.sheet_close, "overlay.sheet.close"),
            (self.sheet_modes, "overlay.sheet.modes"),
            (self.sheet_pedals, "overlay.sheet.pedals"),
            (self.sheet_gestures, "overlay.sheet.gestures"),
            (self.meters_command, "overlay.meters.command"),
        )
        return {phrase: setting for phrase, setting in declared if phrase}

    def display(self, group: str) -> GroupDisplay:
        """The overrides for one group; the defaults when there are none."""
        return self.groups.get(group, GroupDisplay())


@dataclass(frozen=True, slots=True)
class ControlSettings:
    """The commands another program may run, by normalized name."""

    commands: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModeSettings:
    """A named recognizer plus everything that is scoped to it."""

    name: str
    kind: ModeKind
    commands: tuple[Command, ...] = ()
    aliases: dict[str, str] = field(default_factory=dict)
    banned_strings: frozenset[str] = frozenset()
    gestures: dict[str, EdgeBinding] = field(default_factory=dict)
    gesture_origins: dict[str, Origin] = field(default_factory=dict)
    pedals: dict[str, EdgeBinding] = field(default_factory=dict)
    pedal_origins: dict[str, Origin] = field(default_factory=dict)
    source: str = CONFIG_FILE_NAME
    icon: tuple[int, int, int] | None = None
    input_delay: float = 0.01
    type_delay: float = DEFAULT_TYPE_DELAY
    enter_command: str | None = None
    exit_command: str | None = None
    vosk: VoskSettings | None = None
    transformer: TransformerSettings | None = None
    pointer: PointerSettings = PointerSettings()

    def labelled_responses(self) -> tuple[tuple[str, str], ...]:
        """Every response the mode can execute, each labelled with where it came from."""
        lifecycle = (("enter_command", self.enter_command), ("exit_command", self.exit_command))
        return (
            *(
                (self._attribute(f"Command {c.pattern.template!r}", c.origin), c.response)
                for c in self.commands
            ),
            *(
                (
                    self._attribute(f"Gesture {name!r} on {edge}", self.gesture_origins.get(name)),
                    response,
                )
                for name, binding in self.gestures.items()
                for edge, response in binding.responses
            ),
            *(
                (
                    self._attribute(f"Pedal {name!r} on {edge}", self.pedal_origins.get(name)),
                    response,
                )
                for name, binding in self.pedals.items()
                for edge, response in binding.responses
            ),
            *((f"The {key}", text) for key, text in lifecycle if text),
        )

    def _attribute(self, label: str, origin: Origin | None) -> str:
        """label, naming the template it came from when that is not this mode."""
        if origin is None or origin.group == self.name:
            return label
        return f"{label} (imported from {origin.group} in {origin.source})"


@dataclass(frozen=True, slots=True)
class AppSettings:
    """The whole configuration, ready to run."""

    config_dir: Path
    modes: dict[str, ModeSettings] = field(default_factory=dict)
    layer_modes: dict[str, ModeSettings] = field(default_factory=dict)
    reload_command: str = DEFAULT_RELOAD_COMMAND
    sleep_confirm_seconds: float = DEFAULT_CONFIRM_SECONDS
    panic_command: str | None = DEFAULT_PANIC_COMMAND
    previous_mode_keyword: str | None = DEFAULT_PREVIOUS_MODE_KEYWORD
    enable_systray: bool = False
    head_tracking: HeadTrackingSettings | None = None
    pedal: PedalSettings | None = None
    starting_mode: str | None = None
    starting_gesture_mode: str | None = None
    starting_pedal_mode: str | None = None
    templates: tuple[str, ...] = ()
    overlay: OverlaySettings = OverlaySettings()
    control: ControlSettings | None = None

    @property
    def config_file(self) -> Path:
        return self.config_dir / CONFIG_FILE_NAME

    @property
    def script_dir(self) -> Path:
        return self.config_dir / SCRIPT_DIR_NAME

    @property
    def reserved_phrases(self) -> dict[str, str]:
        """Reserved phrase -> the setting that reserved it, in dispatch order."""
        reserved = (
            (self.panic_command, "panic_command"),
            (self.reload_command, "reload_command"),
            (self.previous_mode_keyword, "previous_mode_keyword"),
        )
        return {
            **{phrase: setting for phrase, setting in reserved if phrase},
            **self.overlay.phrases,
        }

    def mode_named(self, phrase: str) -> str | None:
        """The runnable mode a bare utterance names, or None."""
        for name in self.modes:
            if spoken_phrase(name) == phrase:
                return name
        return None

    def layer_mode_named(self, phrase: str) -> str | None:
        """The mode, template or runnable, that a layer target names, or None."""
        for name in self.layer_modes:
            if spoken_phrase(name) == phrase:
                return name
        return None

    def starting_for(self, layer: Layer) -> str | None:
        """Which mode one layer is configured to start in."""
        return {
            Layer.VOICE: self.starting_mode,
            Layer.GESTURE: self.starting_gesture_mode,
            Layer.PEDAL: self.starting_pedal_mode,
        }[layer]


@dataclass(frozen=True, slots=True)
class Document:
    """One configuration file, parsed but not yet interpreted."""

    source: str
    data: dict[str, Any]


def _include_entries(data: dict[str, Any], source: str) -> list[str]:
    """The include list of a document, checked but not yet expanded."""
    entries = data.get("include", [])
    if not isinstance(entries, list) or not all(isinstance(entry, str) for entry in entries):
        raise ConfigError(
            f"'include' in {source} must be a list of paths or globs, got {entries!r}."
        )
    return list(entries)


def _included_paths(root: Document, config_dir: Path) -> list[tuple[Path, str]]:
    """Expand the root document's include entries into files, in order."""
    paths: list[tuple[Path, str]] = []
    seen = {(config_dir / CONFIG_FILE_NAME).resolve()}

    for entry in _include_entries(root.data, root.source):
        pattern = str(Path(entry).expanduser())
        matches = [
            match
            for match in sorted(glob(pattern, root_dir=config_dir, recursive=True))
            if (config_dir / match).is_file()
        ]
        if not matches:
            raise ConfigError(
                f"'include' entry {entry!r} of {root.source} matches no file under "
                f"{config_dir}."
            )

        for match in matches:
            path = config_dir / match
            if path.resolve() in seen:
                continue
            seen.add(path.resolve())
            paths.append((path, match))

    return paths


def _read_document(path: Path, source: str) -> Document:
    """Read and parse one configuration file."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(
            f"Configuration file {str(path)!r} is missing; libre-dictum has nothing to do."
        ) from exc
    except OSError as exc:
        raise ConfigError(f"Configuration file {str(path)!r} cannot be read: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError(f"{source} must be a JSON object, got {type(data).__name__}.")
    return Document(source=source, data=data)


def read_documents(config_dir: Path) -> list[Document]:
    """Read config.json and every file it includes, root first."""
    root = _read_document(config_dir / CONFIG_FILE_NAME, CONFIG_FILE_NAME)
    documents = [root]

    for path, source in _included_paths(root, config_dir):
        document = _read_document(path, source)
        if "include" in document.data:
            raise ConfigError(
                f"{document.source} has an 'include' of its own, which is not supported: "
                f"only {CONFIG_FILE_NAME} includes other files. Move those entries into it."
            )
        documents.append(document)

    return documents


def _merge_modes(
    raw: Any, document: Document, modes: dict[str, Any], sources: dict[str, str]
) -> None:
    """Add one document's modes to the collection, rejecting a name already taken."""
    if not isinstance(raw, dict):
        raise ConfigError(
            f"'modes' in {document.source} must be a JSON object mapping mode name to mode."
        )

    for name, mode in raw.items():
        if name in sources:
            raise ConfigError(
                f"Mode {name!r} is defined in both {sources[name]} and {document.source}. "
                "A mode may only be defined once; to share commands between two modes, put "
                "them in a template and import it from both."
            )
        sources[name] = document.source
        modes[name] = mode


def merge_documents(documents: Sequence[Document]) -> tuple[dict[str, Any], dict[str, str]]:
    """Combine documents into one configuration, and say where each mode came from."""
    merged: dict[str, Any] = {}
    key_sources: dict[str, str] = {}
    modes: dict[str, Any] = {}
    mode_sources: dict[str, str] = {}

    for document in documents:
        for key, value in document.data.items():
            if key == "include":
                continue
            if key == "modes":
                _merge_modes(value, document, modes, mode_sources)
                continue
            if key in key_sources:
                raise ConfigError(
                    f"{key!r} is set in both {key_sources[key]} and {document.source}. "
                    "A setting may only be given once."
                )
            key_sources[key] = document.source
            merged[key] = value

    merged["modes"] = modes
    return merged, mode_sources


_PROVENANCE_SECTIONS = ("commands", "gestures", "pedals")


@dataclass(frozen=True, slots=True)
class ResolvedMode:
    """One mode with its imports flattened, and where each entry came from."""

    raw: dict[str, Any]
    declared_by: dict[str, dict[str, str]] = field(default_factory=dict)


def _merge_mode(target: dict[str, Any], imported: dict[str, Any]) -> None:
    """Merge an imported mode into an importing one, in place."""
    target_type = target.get("type")

    for key, value in imported.items():
        if key in _KEYS_NOT_IMPORTED:
            continue

        if key in _TYPED_BLOCKS and isinstance(value, dict):
            if target_type == key:
                _merge_mode(target, value)
            continue

        if key not in target:
            target[key] = deepcopy(value)
            continue

        if key in _KEYS_NOT_MERGED:
            continue

        existing = target[key]
        if isinstance(existing, dict) and isinstance(value, dict):
            _merge_mode(existing, value)
        elif isinstance(existing, list) and isinstance(value, list):
            existing.extend(deepcopy(value))


def _record_declarations(
    raw: dict[str, Any],
    declared_by: dict[str, dict[str, str]],
    *,
    by: str,
    inherited: dict[str, dict[str, str]] | None = None,
) -> None:
    """Credit every entry that has appeared in raw since the last call."""
    for section in _PROVENANCE_SECTIONS:
        entries = raw.get(section)
        if not isinstance(entries, dict):
            continue

        known = declared_by.setdefault(section, {})
        passed_along = (inherited or {}).get(section, {})
        for key in entries:
            known.setdefault(key, passed_along.get(key, by))


def resolve_imports(raw_modes: dict[str, dict[str, Any]]) -> dict[str, ResolvedMode]:
    """Flatten every mode's imports chain, keeping track of what came from where."""
    resolved: dict[str, ResolvedMode] = {}

    def resolve(name: str, stack: tuple[str, ...]) -> ResolvedMode:
        if name in resolved:
            return resolved[name]
        if name in stack:
            chain = " -> ".join((*stack[stack.index(name) :], name))
            raise ConfigError(f"Import cycle between modes: {chain}")

        raw = deepcopy(raw_modes[name])
        declared_by: dict[str, dict[str, str]] = {}
        _record_declarations(raw, declared_by, by=name)

        for imported_name in raw.get("imports", []):
            if imported_name not in raw_modes:
                raise ConfigError(f"Mode {name!r} imports {imported_name!r}, which is not defined.")
            imported = resolve(imported_name, (*stack, name))
            _merge_mode(raw, imported.raw)
            _record_declarations(raw, declared_by, by=imported_name, inherited=imported.declared_by)

        mode = ResolvedMode(raw=raw, declared_by=declared_by)
        resolved[name] = mode
        return mode

    for mode_name in raw_modes:
        resolve(mode_name, ())
    return resolved


def _require(raw: dict[str, Any], key: str, mode_name: str, what: str) -> Any:
    value = raw.get(key)
    if not value:
        raise ConfigError(f"Mode {mode_name!r} is of type {raw.get('type')!r} but {what}.")
    return value


def _icon(raw: dict[str, Any], mode_name: str) -> tuple[int, int, int] | None:
    icon = raw.get("icon")
    if icon is None:
        return None
    if len(icon) != 3 or not all(isinstance(x, int) and 0 <= x <= 255 for x in icon):
        raise ConfigError(
            f"Icon of mode {mode_name!r} must be three integers between 0 and 255, got {icon!r}."
        )
    return (int(icon[0]), int(icon[1]), int(icon[2]))


def _origins(resolved: ResolvedMode, section: str, sources: Mapping[str, str]) -> dict[str, Origin]:
    """Where each entry of one section was declared, keyed by entry name."""
    return {
        key: Origin(group=group, source=sources.get(group, CONFIG_FILE_NAME))
        for key, group in resolved.declared_by.get(section, {}).items()
    }


def _commands(
    raw: dict[str, Any], mode_name: str, kind: ModeKind, origins: Mapping[str, Origin]
) -> tuple[Command, ...]:
    commands = []
    for template, response in raw.get("commands", {}).items():
        pattern = CommandPattern.compile(template)
        if kind is ModeKind.VOSK:
            if pattern.has_rest:
                raise ConfigError(
                    f"Command {template!r} in mode {mode_name!r} uses {{rest}}, which a VOSK "
                    "grammar cannot express. Use {any} or move the command to a dictation mode."
                )
            _warn_about_grammar_size(pattern, template, mode_name)
        commands.append(Command(pattern=pattern, response=response, origin=origins.get(template)))
    return tuple(commands)


def _warn_about_grammar_size(pattern: CommandPattern, template: str, mode_name: str) -> None:
    """Report a pattern whose grammar expansion is large enough to hurt."""
    variants = 10 ** pattern.placeholders.count("numeric")
    if variants > MAX_QUIET_GRAMMAR_VARIANTS:
        logger.warning(
            "Command %r in mode %r expands to %d grammar phrases; every {numeric} "
            "multiplies the vocabulary by ten. Consider a shorter pattern.",
            template,
            mode_name,
            variants,
        )


def _edge_bindings(
    raw: dict[str, Any], mode_name: str, *, section: str, what: str
) -> dict[str, EdgeBinding]:
    """A mode's pedals or gestures block: name to what it does on each edge."""
    bindings = {}
    for name, entry in raw.get(section, {}).items():
        if entry is None:
            continue
        if isinstance(entry, str):
            bindings[name] = EdgeBinding(press=entry)
            continue
        if not isinstance(entry, dict):
            raise ConfigError(
                f"{what.capitalize()} {name!r} in mode {mode_name!r} is {entry!r}. It is a "
                "response, which fires on the way in, or an object with 'press' and/or "
                "'release'."
            )

        unknown = set(entry) - {"press", "release"}
        if unknown:
            raise ConfigError(
                f"{what.capitalize()} {name!r} in mode {mode_name!r} has "
                f"{', '.join(sorted(unknown))}, which mean nothing. It has 'press' and "
                "'release' and no other edge."
            )
        bindings[name] = EdgeBinding(press=entry.get("press"), release=entry.get("release"))
    return bindings


_POINTER_KEYS = frozenset(
    {
        "enabled",
        "pointer_law",
        "dead_angle_h",
        "dead_angle_v",
        "speed_power",
        "full_speed_angle",
        "max_speed_px_per_sec",
        "invert_x",
        "invert_y",
    }
)

_GLOBAL_HT_KEYS = frozenset(
    {
        "model_path",
        "min_detection_confidence",
        "min_tracking_confidence",
        "offset_x",
        "offset_y",
        "custom_gestures",
        "debug_gestures",
        "capture_fourcc",
        "capture_width",
        "capture_height",
        "capture_fps",
        "filter_min_cutoff",
        "filter_beta",
        "auto_recenter_seconds",
        "hand_enabled",
        "hand_model_path",
        "hand_max_hands",
        "hand_stride",
        "hand_min_detection_confidence",
        "hand_min_presence_confidence",
        "hand_min_tracking_confidence",
        "hand_calibration",
        "face_baseline",
        "screen_calibration",
        "screen_bounds",
    }
)

_RETIRED_HT_KEYS: dict[str, str] = {
    "ht_max_speed": (
        "was pixels per frame. Use 'ht_max_speed_px_per_sec'; at the 60 fps it assumed, "
        "{value} corresponds to {scaled:g}."
    ),
    "ht_speed_mult": (
        "scaled a per-frame step, and coupled the gain to the dead zone -- turning the pointer "
        "down moved the first-movement threshold further out. Gain is now "
        "'ht_max_speed_px_per_sec', with 'ht_full_speed_angle' saying where it is reached."
    ),
}


def _check_head_tracking_keys(
    source: Mapping[str, Any], where: str, allowed: frozenset[str]
) -> None:
    """Reject retired and misspelled ht_* keys."""
    for key in sorted(source):
        if not key.startswith("ht_"):
            continue
        if key in _RETIRED_HT_KEYS:
            value = source[key]
            scaled = value * 60 if isinstance(value, (int, float)) else value
            detail = _RETIRED_HT_KEYS[key].format(value=value, scaled=scaled)
            raise ConfigError(f"{where}: {key!r} {detail}")
        if key[3:] not in allowed:
            known = ", ".join(sorted(f"ht_{name}" for name in allowed))
            raise ConfigError(
                f"{where}: {key!r} is not a head-tracking setting, so nothing reads it. "
                f"Settings available here: {known}."
            )


def _pointer(raw: dict[str, Any], defaults: dict[str, Any], mode_name: str) -> PointerSettings:
    def knob(key: str, fallback: float | bool | str) -> Any:
        return raw.get(f"ht_{key}", defaults.get(f"ht_{key}", fallback))

    settings = PointerSettings(
        enabled=bool(raw.get("ht_enabled", False)),
        law=str(knob("pointer_law", POINTER_RATE)),
        dead_angle_h=float(knob("dead_angle_h", 2.0)),
        dead_angle_v=float(knob("dead_angle_v", 2.0)),
        speed_power=float(knob("speed_power", 1.5)),
        full_speed_angle=float(knob("full_speed_angle", 18.0)),
        max_speed_px_per_sec=float(knob("max_speed_px_per_sec", 800.0)),
        invert_x=bool(knob("invert_x", False)),
        invert_y=bool(knob("invert_y", False)),
    )
    _check_pointer(settings, mode_name)
    return settings


def _check_pointer(settings: PointerSettings, mode_name: str) -> None:
    """Reject a curve that cannot produce movement."""
    if settings.law not in POINTER_LAWS:
        raise ConfigError(
            f"Mode {mode_name!r} has 'ht_pointer_law' of {settings.law!r}. It is one of "
            f"{', '.join(repr(law) for law in POINTER_LAWS)}."
        )
    if settings.law == POINTER_ABSOLUTE:
        return
    widest_dead_angle = max(settings.dead_angle_h, settings.dead_angle_v)
    if settings.full_speed_angle <= widest_dead_angle:
        raise ConfigError(
            f"Mode {mode_name!r} has 'ht_full_speed_angle' of {settings.full_speed_angle:g} but a "
            f"dead angle of {widest_dead_angle:g}. Full speed has to be reached outside the dead "
            "zone, or the pointer has only two speeds: nothing and everything."
        )
    for key, value in (
        ("ht_max_speed_px_per_sec", settings.max_speed_px_per_sec),
        ("ht_speed_power", settings.speed_power),
    ):
        if value <= 0:
            raise ConfigError(f"Mode {mode_name!r} has {key!r} of {value:g}; it must be positive.")
    for key, value in (
        ("ht_dead_angle_h", settings.dead_angle_h),
        ("ht_dead_angle_v", settings.dead_angle_v),
    ):
        if value < 0:
            raise ConfigError(
                f"Mode {mode_name!r} has {key!r} of {value:g}; it cannot be negative."
            )


def _build_mode(
    name: str, resolved: ResolvedMode, data: dict[str, Any], sources: Mapping[str, str]
) -> ModeSettings:
    raw = resolved.raw
    try:
        kind = ModeKind(raw["type"])
    except ValueError as exc:
        raise ConfigError(
            f"Mode {name!r} has unknown type {raw['type']!r}; "
            f"expected one of {', '.join(k.value for k in ModeKind)} or {TEMPLATE_TYPE!r}."
        ) from exc

    _check_head_tracking_keys(raw, f"Mode {name!r}", _POINTER_KEYS)

    vosk = transformer = None
    if kind is ModeKind.VOSK:
        vosk = VoskSettings(
            model_path=_require(raw, "path", name, "has no model 'path' set"),
        )
    elif kind is not ModeKind.IMPORT:
        transformer = TransformerSettings(
            model_name=_require(raw, "model_name", name, "has no 'model_name' set"),
            silence_seconds=float(raw.get("silence_seconds", 0.3)),
            max_chunk_seconds=float(raw.get("max_chunk_seconds", 30.0)),
            energy_threshold=float(raw.get("energy_threshold", 0.01)),
            pre_roll_seconds=float(raw.get("pre_roll_seconds", 0.25)),
            lang=raw.get("lang", "en"),
            device=raw.get("transformer_device", "auto"),
        )

    return ModeSettings(
        name=name,
        kind=kind,
        commands=(
            ()
            if kind is ModeKind.IMPORT
            else _commands(raw, name, kind, _origins(resolved, "commands", sources))
        ),
        aliases=dict(raw.get("aliases", {})),
        banned_strings=frozenset(spoken_phrase(s) for s in raw.get("banned_strings", [])),
        gestures=_edge_bindings(raw, name, section="gestures", what="gesture"),
        gesture_origins=_origins(resolved, "gestures", sources),
        pedals=_edge_bindings(raw, name, section="pedals", what="pedal"),
        pedal_origins=_origins(resolved, "pedals", sources),
        source=sources.get(name, CONFIG_FILE_NAME),
        icon=_icon(raw, name),
        input_delay=float(raw.get("input_delay", 0.01)),
        type_delay=float(raw.get("type_delay", DEFAULT_TYPE_DELAY)),
        enter_command=raw.get("enter_command"),
        exit_command=raw.get("exit_command"),
        vosk=vosk,
        transformer=transformer,
        pointer=_pointer(raw, data, name),
    )


def _build_overlay(data: dict[str, Any]) -> OverlaySettings:
    """Read the overlay block, or hand back the defaults when there is none."""
    block = data.get("overlay")
    if block is None:
        return OverlaySettings()
    if not isinstance(block, dict):
        raise ConfigError("'overlay' must be a JSON object; see docs/reference/configuration.md.")

    raw = _overlay_section(block, "chip")
    chip_command = _optional_phrase(raw.get("command", DEFAULT_CHIP_COMMAND))
    chip_enabled = bool(raw.get("enabled", True))

    raw = _overlay_section(block, "meters")
    meters_command = _optional_phrase(raw.get("command", DEFAULT_METERS_COMMAND))
    meters_enabled = bool(raw.get("enabled", True))

    raw = _overlay_section(block, "sheet")
    sheet_open = _optional_phrase(raw.get("open", DEFAULT_SHEET_OPEN))
    sheet_close = _optional_phrase(raw.get("close", DEFAULT_SHEET_CLOSE))
    sheet_modes = _optional_phrase(raw.get("modes", DEFAULT_SHEET_MODES))
    sheet_pedals = _optional_phrase(raw.get("pedals", DEFAULT_SHEET_PEDALS))
    sheet_gestures = _optional_phrase(raw.get("gestures", DEFAULT_SHEET_GESTURES))
    navigate = _optional_phrase(raw.get("navigate", DEFAULT_NAVIGATE))

    raw = _overlay_section(block, "output")
    output = OutputSettings(name=_connector(raw.get("name")), only=bool(raw.get("only", True)))

    raw = _overlay_section(block, "sleep")
    sleep_hides = bool(raw.get("hide", False))

    groups = {}
    for name, entry in _overlay_section(block, "groups").items():
        if not isinstance(entry, dict):
            raise ConfigError(
                f"'overlay.groups.{name}' must be a JSON object with 'say' and/or 'hide'."
            )
        raw = entry
        groups[name] = GroupDisplay(
            say=_optional_phrase(raw.get("say")), hide=bool(raw.get("hide", False))
        )

    return OverlaySettings(
        chip_command=chip_command,
        chip_enabled=chip_enabled,
        meters_command=meters_command,
        meters_enabled=meters_enabled,
        sheet_open=sheet_open,
        sheet_close=sheet_close,
        sheet_modes=sheet_modes,
        sheet_pedals=sheet_pedals,
        sheet_gestures=sheet_gestures,
        navigate=navigate,
        output=output,
        sleep_hides=sleep_hides,
        groups=groups,
    )


def _connector(value: Any) -> str | None:
    """The video output an overlay block names, or None to leave the choice open."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(
            f"'overlay.output.name' is {value!r}; it must be the name of a video output, "
            'such as "DP-1", or null to let the compositor place the surfaces. Run '
            "'libre-dictum-hud --outputs' for the names this session has."
        )
    return value.strip()


def _overlay_section(block: dict[str, Any], key: str) -> dict[str, Any]:
    """One sub-object of the overlay block, or an empty one."""
    section = block.get(key, {})
    if not isinstance(section, dict):
        raise ConfigError(f"'overlay.{key}' must be a JSON object.")
    return section


def _check_navigate(overlay: OverlaySettings) -> None:
    """Reject a navigation prefix that is more than one word."""
    if overlay.navigate is None:
        return
    if len(overlay.navigate.split()) != 1:
        raise ConfigError(
            f"'overlay.sheet.navigate' is {overlay.navigate!r}, which is not one word. "
            "The generated phrases are '<navigate> <group name>', so the prefix has to be "
            "a single word -- or null, to generate none."
        )


def _build_head_tracking(data: dict[str, Any]) -> HeadTrackingSettings | None:
    if not data.get("enable_head_tracking", False):
        return None

    model_path = data.get("ht_model_path")
    if not model_path:
        raise ConfigError(
            "'enable_head_tracking' is on but 'ht_model_path' is not set; it must point at "
            "mediapipe's face_landmarker.task."
        )

    return HeadTrackingSettings(
        model_path=model_path,
        camera=_camera(data),
        min_detection_confidence=float(data.get("ht_min_detection_confidence", 0.5)),
        min_tracking_confidence=float(data.get("ht_min_tracking_confidence", 0.5)),
        offset_x=float(data.get("ht_offset_x", 0.0)),
        offset_y=float(data.get("ht_offset_y", 0.0)),
        filter_min_cutoff=float(data.get("ht_filter_min_cutoff", 1.0)),
        filter_beta=float(data.get("ht_filter_beta", 0.05)),
        auto_recenter_seconds=_auto_recenter(data),
        gesture_definitions=data.get("ht_custom_gestures", deepcopy(DEFAULT_GESTURE_DEFINITIONS)),
        face_baseline=face_baseline(data),
        screen=screen_calibration(data),
        placement=screen_bounds(data),
        debug_gestures=tuple(data.get("ht_debug_gestures", ())),
        capture=CaptureSettings(
            fourcc=data.get("ht_capture_fourcc", "MJPG"),
            width=data.get("ht_capture_width", 1920),
            height=data.get("ht_capture_height", 1080),
            fps=data.get("ht_capture_fps", 60),
        ),
        hands=_build_hand_tracking(data),
    )


def _build_hand_tracking(data: dict[str, Any]) -> HandTrackingSettings | None:
    """Read the ht_hand_* keys, or hand back None when hand scores are off."""
    if not data.get("ht_hand_enabled", False):
        return None

    model_path = data.get("ht_hand_model_path")
    if not model_path:
        raise ConfigError(
            "'ht_hand_enabled' is on but 'ht_hand_model_path' is not set; it must point at "
            "mediapipe's hand_landmarker.task, which is a different file from the face one."
        )

    stride = int(data.get("ht_hand_stride", 4))
    if stride < 1:
        raise ConfigError(
            f"'ht_hand_stride' is {stride}; it is how many frames apart the hand model runs, "
            "so 1 (every frame) is the smallest that means anything."
        )
    max_hands = int(data.get("ht_hand_max_hands", 2))
    if max_hands < 1:
        raise ConfigError(
            f"'ht_hand_max_hands' is {max_hands}; tracking no hands is what "
            "'ht_hand_enabled': false is for."
        )

    return HandTrackingSettings(
        model_path=model_path,
        max_hands=max_hands,
        stride=stride,
        min_detection_confidence=float(data.get("ht_hand_min_detection_confidence", 0.5)),
        min_presence_confidence=float(data.get("ht_hand_min_presence_confidence", 0.5)),
        min_tracking_confidence=float(data.get("ht_hand_min_tracking_confidence", 0.5)),
        calibration=hand_calibration(data),
    )


def hand_calibration(data: dict[str, Any]) -> Calibration:
    """ht_hand_calibration: where each hand score's 0 and 1 sit."""
    block = data.get("ht_hand_calibration")
    if block is None:
        return Calibration()
    if not isinstance(block, dict):
        raise ConfigError(
            "'ht_hand_calibration' must be a JSON object of "
            f"{', '.join(CALIBRATION_KEYS)}; see docs/reference/configuration.md."
        )

    unknown = sorted(set(block) - set(CALIBRATION_KEYS))
    if unknown:
        raise ConfigError(
            f"'ht_hand_calibration' has {', '.join(unknown)}, which mean nothing. "
            f"The constants are: {', '.join(CALIBRATION_KEYS)}."
        )
    try:
        calibration = Calibration(**{key: float(value) for key, value in block.items()})
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"'ht_hand_calibration' takes numbers: {exc}") from exc

    problems = calibration.problems()
    if problems:
        raise ConfigError("'ht_hand_calibration' will not work: " + "; ".join(problems) + ".")
    return calibration


def face_baseline(data: dict[str, Any]) -> dict[str, float]:
    """ht_face_baseline: what each blendshape sits at on this face doing nothing."""
    block = data.get("ht_face_baseline")
    if block is None:
        return {}
    if not isinstance(block, dict):
        raise ConfigError(
            "'ht_face_baseline' must be a JSON object of blendshape name to resting level; "
            "see docs/reference/configuration.md."
        )
    levels: dict[str, float] = {}
    for feature, value in block.items():
        if feature not in FACE_FEATURES:
            close = get_close_matches(feature, FACE_FEATURES, n=3, cutoff=0.6)
            suggestion = f" Did you mean {' or '.join(repr(n) for n in close)}?" if close else ""
            raise ConfigError(
                f"'ht_face_baseline' names {feature!r}, which is not one of mediapipe's 52 face "
                f"blendshapes.{suggestion} Hand scores do not belong here: they are already "
                "calibrated, and a hand that is not in shot is absent rather than resting."
            )
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(
                f"'ht_face_baseline' has a {feature!r} of {value!r}; a resting level is a number."
            )
        if not 0.0 <= float(value) < 1.0:
            raise ConfigError(
                f"'ht_face_baseline' has {feature!r} at {value}. A resting level is in [0, 1): at "
                "1 the feature has no range left above it and every threshold over it would be "
                "unreachable."
            )
        levels[feature] = float(value)
    return levels


def screen_bounds(data: dict[str, Any]) -> ScreenBounds:
    """ht_screen_bounds: where the calibrated screen sits on the desktop, in pixels."""
    block = data.get("ht_screen_bounds")
    if block is None:
        return ScreenBounds()
    if not isinstance(block, dict):
        raise ConfigError(
            "'ht_screen_bounds' must be a JSON object with 'x', 'y', 'width', 'height', "
            "'desktop_width' and 'desktop_height', all in pixels. See "
            "docs/reference/configuration.md."
        )

    wanted = ("x", "y", "width", "height", "desktop_width", "desktop_height")
    missing = [key for key in wanted if key not in block]
    if missing:
        raise ConfigError(
            f"'ht_screen_bounds' is missing {', '.join(repr(key) for key in missing)}. All six "
            "are needed: the first four are the monitor you calibrated against, the last two "
            "are every monitor together, which is what the pointer is spread across."
        )
    try:
        numbers = {key: float(block[key]) for key in wanted}
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"'ht_screen_bounds' takes numbers of pixels: {exc}") from exc

    try:
        return ScreenBounds.from_pixels(
            numbers["x"],
            numbers["y"],
            numbers["width"],
            numbers["height"],
            desktop=(numbers["desktop_width"], numbers["desktop_height"]),
        )
    except ValueError as exc:
        raise ConfigError(f"'ht_screen_bounds' will not work: {exc}") from exc


def screen_calibration(data: dict[str, Any]) -> ScreenMap:
    """ht_screen_calibration: where on the screen a head angle points."""
    block = data.get("ht_screen_calibration")
    if block is None:
        return ScreenMap()
    if not isinstance(block, dict):
        raise ConfigError(
            "'ht_screen_calibration' must be a JSON object -- either width_mm/height_mm/"
            "distance_mm, or the fitted block --calibrate-screen writes. See "
            "docs/reference/configuration.md."
        )

    geometric = {"width_mm", "height_mm", "distance_mm"}
    fitted = {"x", "y"}
    if geometric <= set(block):
        try:
            return ScreenMap.from_geometry(
                width_mm=float(block["width_mm"]),
                height_mm=float(block["height_mm"]),
                distance_mm=float(block["distance_mm"]),
            )
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"'ht_screen_calibration' will not work: {exc}") from exc
    if not fitted <= set(block):
        raise ConfigError(
            "'ht_screen_calibration' has neither 'width_mm'/'height_mm'/'distance_mm' nor the "
            "fitted 'x'/'y' coefficients, so it describes no mapping at all."
        )

    axes = {}
    for axis in ("x", "y"):
        values = block[axis]
        if not isinstance(values, list) or len(values) != TERMS:
            raise ConfigError(
                f"'ht_screen_calibration' has {axis!r} of {values!r}; it is a list of "
                f"{TERMS} coefficients, as --calibrate-screen writes it."
            )
        try:
            axes[axis] = tuple(float(v) for v in values)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"'ht_screen_calibration' takes numbers in {axis!r}: {exc}") from exc

    origin = block.get("origin", (0.0, 0.0, 0.0))
    perspective = block.get("perspective", (0.0, 0.0))
    if not isinstance(perspective, (list, tuple)) or len(perspective) != 2:
        raise ConfigError(
            f"'ht_screen_calibration' has 'perspective' of {perspective!r}; it is the two "
            "shared denominator terms, and [0, 0] is a screen square on to the head."
        )
    return ScreenMap(
        x=axes["x"],
        y=axes["y"],
        perspective=(float(perspective[0]), float(perspective[1])),
        residual=float(block.get("residual", 0.0)),
        samples=int(block.get("samples", 0)),
        origin=(float(origin[0]), float(origin[1]), float(origin[2])),
    )


def _auto_recenter(data: dict[str, Any]) -> float | None:
    """ht_auto_recenter_seconds, or None when neutral should stay where it is put."""
    value = data.get("ht_auto_recenter_seconds")
    if value is None:
        return None
    seconds = float(value)
    if seconds <= 0:
        raise ConfigError(
            f"'ht_auto_recenter_seconds' is {seconds:g}. It is a time constant in seconds, so it "
            "must be positive; use null to leave neutral where recenter() put it."
        )
    return seconds


def _camera(data: dict[str, Any]) -> int | str | None:
    """Which camera to open: camera, or the camera_index it replaced."""
    spec = data.get("camera")
    index = data.get("camera_index")

    if spec is not None and index is not None:
        raise ConfigError(
            "both 'camera' and 'camera_index' are set. Keep 'camera': it takes the same "
            "index, and also a name or a device path, neither of which a reboot renumbers."
        )
    if spec is None:
        if index is None:
            return None
        logger.warning(
            "'camera_index' is deprecated: the kernel renumbers video nodes, so %r is a "
            "guess that expires. Delete the key to have the camera found, or use 'camera' "
            "with a name or a /dev/v4l/by-id path.",
            index,
        )
        return _camera_index(index)
    if isinstance(spec, str):
        if not spec.strip():
            raise ConfigError("'camera' is empty; delete the key to have one found.")
        return spec
    return _camera_index(spec)


def _camera_index(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise ConfigError(
        f"camera index is {value!r}; it must be a whole number. A name or a device path "
        "goes in 'camera' instead."
    )


def _usb_id(value: Any, *, what: str) -> int:
    """A USB vendor or product id, as a number or as the "0x0fd9" lsusb prints."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        for base in (0, 16):
            try:
                return int(value, base)
            except ValueError:
                continue
    raise ConfigError(f"{what} is {value!r}; it must be a number or a string like '0x0fd9'.")


def _pedal_buttons(raw: dict[str, Any], report_length: int) -> dict[str, int]:
    """pedal.buttons: pedal name to the report byte that carries it."""
    buttons = raw.get("buttons", DEFAULT_PEDAL_BUTTONS)
    if not isinstance(buttons, dict) or not buttons:
        raise ConfigError(
            "'pedal.buttons' must be a JSON object mapping a pedal name to the report byte "
            f"that carries it, got {buttons!r}."
        )

    checked = {}
    for name, offset in buttons.items():
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ConfigError(
                f"'pedal.buttons.{name}' is {offset!r}; it must be a byte offset into the "
                "device's HID report, counting from zero."
            )
        if offset >= report_length:
            raise ConfigError(
                f"'pedal.buttons.{name}' is byte {offset}, but 'pedal.report_length' is "
                f"{report_length}, so that byte never arrives. Raise the report length or "
                "correct the offset."
            )
        checked[name] = offset
    return checked


def _build_pedal(data: dict[str, Any]) -> PedalSettings | None:
    """Read the pedal block, or hand back None when pedals are switched off."""
    if not data.get("enable_pedals", False):
        return None

    block = data.get("pedal", {})
    if not isinstance(block, dict):
        raise ConfigError("'pedal' must be a JSON object; see docs/reference/configuration.md.")

    raw = block
    report_length = int(raw.get("report_length", DEFAULT_PEDAL_REPORT_LENGTH))
    if report_length < 1:
        raise ConfigError(f"'pedal.report_length' is {report_length}; a report has bytes in it.")

    return PedalSettings(
        vendor_id=_usb_id(raw.get("vendor_id", DEFAULT_PEDAL_VENDOR_ID), what="'pedal.vendor_id'"),
        product_id=_usb_id(
            raw.get("product_id", DEFAULT_PEDAL_PRODUCT_ID), what="'pedal.product_id'"
        ),
        buttons=_pedal_buttons(raw, report_length),
        report_length=report_length,
        read_timeout_ms=int(raw.get("read_timeout_ms", DEFAULT_PEDAL_READ_TIMEOUT_MS)),
        tap_ms=_positive_ms(raw.get("tap_ms", DEFAULT_TAP_MS), "pedal.tap_ms"),
        sequence_ms=_positive_ms(raw.get("sequence_ms", DEFAULT_SEQUENCE_MS), "pedal.sequence_ms"),
    )


def _build_control(data: dict[str, Any]) -> ControlSettings | None:
    """Read the control block, or hand back None when control commands are switched off."""
    raw = data.get("control", {})
    if not isinstance(raw, dict):
        raise ConfigError("'control' must be a JSON object; see docs/reference/configuration.md.")
    if not data.get("enable_control", False):
        return None

    declared = raw.get("commands", {})
    if not isinstance(declared, dict):
        raise ConfigError("'control.commands' must be a JSON object mapping a name to a response.")

    commands: dict[str, str] = {}
    spelled: dict[str, str] = {}
    for name, response in declared.items():
        key = spoken_phrase(name)
        if not key:
            raise ConfigError("'control.commands' has a command with an empty name.")
        if not isinstance(response, str) or not response.strip():
            raise ConfigError(f"Control command {name!r} needs a response string.")
        if key in commands:
            raise ConfigError(
                f"Control commands {spelled[key]!r} and {name!r} are one name once normalized."
            )
        commands[key] = response
        spelled[key] = name

    if not commands:
        logger.warning("'enable_control' is on, but 'control.commands' names nothing to run.")
    return ControlSettings(commands=commands)


def _starting_mode(
    data: dict[str, Any], modes: dict[str, ModeSettings], templates: tuple[str, ...]
) -> str:
    if not modes:
        raise ConfigError("No runnable modes are defined; add a 'vosk' or 'transformer' mode.")

    requested = data.get("starting_mode")
    if requested is None:
        return next(iter(modes))
    if requested in templates:
        raise ConfigError(
            f"'starting_mode' is {requested!r}, which is an import template, not a runnable mode."
        )
    if requested not in modes:
        raise ConfigError(
            f"'starting_mode' is {requested!r}, which is not a defined mode. "
            f"Known modes: {', '.join(sorted(modes))}."
        )
    return requested


def _starting_layer_mode(
    data: dict[str, Any], key: str, layer_modes: dict[str, ModeSettings]
) -> str | None:
    """Which mode a layer starts in, or None to follow voice."""
    requested = data.get(key)
    if requested is None:
        return None
    if requested not in layer_modes:
        known = ", ".join(sorted(layer_modes))
        raise ConfigError(
            f"{key!r} is {requested!r}, which is not a defined mode. Known modes: {known}."
        )
    return str(requested)


def _check_reserved_phrases(modes: dict[str, ModeSettings], reserved: dict[str, str]) -> None:
    """Reject a mode name or command pattern that a reserved phrase would shadow."""
    for name, mode in modes.items():
        setting = reserved.get(spoken_phrase(name))
        if setting is not None:
            raise ConfigError(
                f"Mode {name!r} is named the same as {setting!r}, so the mode could never "
                f"be reached: the reserved phrase is handled first. Rename the mode or "
                f"change {setting!r}."
            )

        for command in mode.commands:
            setting = reserved.get(spoken_phrase(command.pattern.normalized))
            if setting is not None:
                raise ConfigError(
                    f"Command {command.pattern.template!r} in mode {name!r} is the same "
                    f"phrase as {setting!r}, so the command could never fire: the reserved "
                    f"phrase is handled first."
                )


_GESTURE_LIMITS = frozenset({"min", "max", "release"})


def _check_gesture_definitions(definitions: Mapping[str, Any]) -> None:
    """Reject a gesture whose shape would break on the camera thread."""
    for name, features in definitions.items():
        if not isinstance(features, Mapping):
            raise ConfigError(
                f"Gesture {name!r} in 'ht_custom_gestures' is {type(features).__name__}, not an "
                "object of feature names."
            )
        hold = features.get(GESTURE_HOLD_KEY)
        if hold is not None and (isinstance(hold, bool) or not isinstance(hold, (int, float))):
            raise ConfigError(
                f"Gesture {name!r} has a {GESTURE_HOLD_KEY!r} of {hold!r}. It is a dwell time in "
                "milliseconds, so it has to be a number."
            )
        if isinstance(hold, (int, float)) and hold < 0:
            raise ConfigError(
                f"Gesture {name!r} has a {GESTURE_HOLD_KEY!r} of {hold}; it cannot be negative."
            )

        conditions = {f: limits for f, limits in features.items() if f != GESTURE_HOLD_KEY}
        if not conditions:
            raise ConfigError(
                f"Gesture {name!r} names no features, so nothing could ever make it fire."
            )
        for feature, limits in conditions.items():
            _check_feature_name(f"Gesture {name!r}", feature)
            _check_gesture_limits(name, feature, limits)


def _check_feature_name(where: str, feature: str) -> None:
    """Reject a feature no model reports."""
    if feature in KNOWN_FEATURES:
        return
    close = get_close_matches(feature, KNOWN_FEATURES, n=3, cutoff=0.6)
    suggestion = f" Did you mean {' or '.join(repr(name) for name in close)}?" if close else ""
    raise ConfigError(
        f"{where} names the feature {feature!r}, which no model reports, so it would score "
        f"zero for ever and the gesture would never fire.{suggestion} The features are "
        "mediapipe's 52 face blendshapes and the hand scores in "
        "docs/reference/configuration.md."
    )


def _check_absolute_modes(settings: AppSettings) -> None:
    """Reject a mode asking for the absolute law with nothing to point at."""
    head_tracking = settings.head_tracking
    if head_tracking is None:
        return
    absolute = sorted(
        name
        for name, mode in settings.modes.items()
        if mode.pointer.enabled and mode.pointer.law == POINTER_ABSOLUTE
    )
    if not absolute:
        return
    if not head_tracking.screen.measured:
        raise ConfigError(
            f"{'Modes' if len(absolute) > 1 else 'Mode'} "
            f"{', '.join(repr(n) for n in absolute)} asks for 'ht_pointer_law': 'absolute', but "
            "'ht_screen_calibration' is not set, so there is nowhere for the head to point and "
            "the pointer would never move. Run 'libre-dictum --calibrate-screen', or set "
            "width_mm/height_mm/distance_mm off a tape measure."
        )
    if head_tracking.auto_recenter_seconds is not None:
        raise ConfigError(
            f"'ht_auto_recenter_seconds' is set and "
            f"{', '.join(repr(n) for n in absolute)} uses 'ht_pointer_law': 'absolute'. "
            "Auto-recentring moves neutral, and an absolute mapping is measured against the "
            "screen, which does not move -- so the two would fight and the mapping would "
            "quietly stop being true. Drop one of them."
        )


def _check_debug_gestures(head_tracking: HeadTrackingSettings) -> None:
    """Reject a name in ht_debug_gestures that is neither a gesture nor a feature."""
    defined = set(head_tracking.gesture_definitions)
    for name in head_tracking.debug_gestures:
        if name in defined or name in KNOWN_FEATURES:
            continue
        close = get_close_matches(name, defined | KNOWN_FEATURES, n=3, cutoff=0.6)
        suggestion = f" Did you mean {' or '.join(repr(m) for m in close)}?" if close else ""
        raise ConfigError(
            f"'ht_debug_gestures' names {name!r}, which is neither a defined gesture nor a "
            f"feature, so nothing would be printed for it.{suggestion} Defined gestures: "
            f"{', '.join(sorted(defined)) or '(none)'}."
        )


def _warn_about_inert_hand_gestures(head_tracking: HeadTrackingSettings) -> None:
    """Say so when a gesture needs hand scores that nothing is going to produce."""
    if head_tracking.hands is not None:
        return
    hand_features = frozenset(HAND_FEATURES)
    inert = sorted(
        name
        for name, features in head_tracking.gesture_definitions.items()
        if hand_features & set(features)
    )
    if inert:
        logger.warning(
            "Gestures needing hand scores cannot fire while 'ht_hand_enabled' is off: %s",
            ", ".join(inert),
        )


def _check_gesture_limits(gesture: str, feature: str, limits: Any) -> None:
    """Reject one feature's limits block."""
    where = f"Feature {feature!r} of gesture {gesture!r}"
    if not isinstance(limits, Mapping):
        raise ConfigError(
            f"{where} is {type(limits).__name__}, not an object. A feature takes "
            f"{', '.join(sorted(_GESTURE_LIMITS))} -- did you mean {GESTURE_HOLD_KEY!r}?"
        )
    unknown = set(limits) - _GESTURE_LIMITS
    if unknown:
        raise ConfigError(
            f"{where} has {', '.join(sorted(unknown))}, which mean nothing. "
            f"A feature takes {', '.join(sorted(_GESTURE_LIMITS))}."
        )
    if not set(limits) & {"min", "max"}:
        raise ConfigError(f"{where} has neither 'min' nor 'max', so it can never hold.")
    for key, value in limits.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{where} has a {key!r} of {value!r}; it has to be a number.")


def _check_gestures(
    layer_modes: dict[str, ModeSettings], head_tracking: HeadTrackingSettings
) -> None:
    """Reject a gesture action naming a gesture nobody defined."""
    defined = set(head_tracking.gesture_definitions)
    for name, mode in layer_modes.items():
        for gesture in mode.gestures:
            if gesture not in defined:
                raise ConfigError(
                    f"Mode {name!r} maps the gesture {gesture!r}, which is not defined. "
                    f"Defined gestures: {', '.join(sorted(defined)) or '(none)'}."
                )


def _positive_ms(value: Any, where: str) -> int:
    """One of the pedal block's millisecond windows, which may not be zero or negative."""
    window = int(value)
    if window <= 0:
        raise ConfigError(f"{where!r} is {window}; it is a window in milliseconds.")
    return window


def _check_pedals(layer_modes: dict[str, ModeSettings], pedal: PedalSettings) -> None:
    """Reject a binding for a pedal the device has not got."""
    for name, mode in layer_modes.items():
        for bound in mode.pedals:
            if bound in pedal.buttons or pedal.taps(bound):
                continue
            parts = bound.split()
            unknown = [part for part in parts if part not in pedal.buttons] or parts
            raise ConfigError(
                f"Mode {name!r} binds the pedal {bound!r}, which 'pedal.buttons' does not "
                f"define: {', '.join(repr(part) for part in unknown)}. Defined pedals: "
                f"{', '.join(pedal.buttons) or '(none)'}. Several names separated by "
                "spaces are a sequence of bare taps."
            )


def _check_mode_target(target: str, settings: AppSettings, *, where: str) -> None:
    """Reject a mode(...) that names something no session could switch to."""
    layer, name = parse_target(target)
    phrase = spoken_phrase(name)
    if layer in (None, Layer.VOICE):
        table, what = settings.modes, "a runnable mode"
        named = settings.mode_named(phrase)
    else:
        table, what = settings.layer_modes, "a defined mode"
        named = settings.layer_mode_named(phrase)
        if phrase == Layer.VOICE.value:
            return

    if name in table or named is not None or phrase == settings.previous_mode_keyword:
        return

    known = ", ".join(sorted(table))
    raise ConfigError(f"{where} switches to {target!r}, which is not {what}. Known modes: {known}.")


def _check_typeable(text: str, *, where: str) -> None:
    """Reject a literal type() the keyboard cannot produce."""
    missing = untypeable(to_ascii(text))
    if missing:
        raise ConfigError(
            f"{where} types {', '.join(repr(c) for c in missing)}, "
            "which no key produces. Key codes are a US layout; see "
            "docs/reference/response-dsl.md."
        )


def _check_hud(argument: str, *, where: str) -> None:
    """Reject a hud(...) target no display could act on."""
    try:
        check_hud_target(argument)
    except CommandSyntaxError as exc:
        raise ConfigError(f"{where} cannot run: {exc}") from exc


def _confirm_seconds(data: dict[str, Any]) -> float:
    """sleep_confirm_seconds: the window a sleep or wake request waits to be repeated in."""
    seconds = float(data.get("sleep_confirm_seconds", DEFAULT_CONFIRM_SECONDS))
    if seconds <= 0:
        raise ConfigError(
            f"'sleep_confirm_seconds' is {seconds:g}; it is the window a sleep() or wake() "
            "must be repeated in, so it has to be positive. It is the only thing standing "
            "between one misfire and a session that has gone deaf."
        )
    return seconds


def _check_sleep(argument: str, *, where: str, verb: str) -> None:
    """sleep(mouth) names no mechanism, and would put nothing to sleep for ever."""
    try:
        parse_layers(argument)
    except ValueError as exc:
        raise ConfigError(f"{where} has {verb}({argument}): {exc}.") from exc


def _check_responses(settings: AppSettings) -> None:
    """Reject every response that could never run, before a single model is loaded."""
    for name, mode in settings.layer_modes.items():
        for label, response in mode.labelled_responses():
            _check_response(
                apply_aliases(response, mode.aliases), settings, where=f"{label} of mode {name!r}"
            )
    if settings.control is not None:
        for name, response in settings.control.commands.items():
            _check_response(response, settings, where=f"Control command {name!r}")


def _check_response(expanded: str, settings: AppSettings, *, where: str) -> None:
    """Reject one response, aliases already applied, that could never run."""
    unusable = invalid_placeholders(expanded)
    if unusable:
        raise ConfigError(
            f"{where} uses {', '.join(unusable)}: placeholders are "
            "numbered from 1, so {1} is the first capture group."
        )

    try:
        tokens = validate_response(expanded)
    except CommandSyntaxError as exc:
        raise ConfigError(f"{where} cannot run: {exc}") from exc

    for token in tokens:
        if isinstance(token, VerbToken) and token.verb == "mode":
            _check_mode_target(token.argument, settings, where=where)
        if isinstance(token, VerbToken) and token.verb == "type":
            _check_typeable(token.argument, where=where)
        if isinstance(token, VerbToken) and token.verb == "hud":
            _check_hud(token.argument, where=where)
        if isinstance(token, VerbToken) and token.verb in ("sleep", "wake"):
            _check_sleep(token.argument, where=where, verb=token.verb)


def parse_settings(
    data: dict[str, Any], config_dir: Path, sources: Mapping[str, str] | None = None
) -> AppSettings:
    """Turn parsed JSON into validated settings, or raise ConfigError."""
    if not isinstance(data, dict):
        raise ConfigError("The configuration must be a JSON object.")

    raw_modes = data.get("modes", {})
    if not isinstance(raw_modes, dict):
        raise ConfigError("'modes' must be a JSON object mapping mode name to mode.")

    _check_head_tracking_keys(data, "Configuration", _GLOBAL_HT_KEYS | _POINTER_KEYS)

    resolved = resolve_imports(raw_modes)
    for mode in resolved.values():
        mode.raw.setdefault("type", TEMPLATE_TYPE)

    declared_in = sources or {}
    templates = tuple(n for n, mode in resolved.items() if mode.raw["type"] == TEMPLATE_TYPE)
    layer_modes = {
        name: _build_mode(name, mode, data, declared_in) for name, mode in resolved.items()
    }
    modes = {name: mode for name, mode in layer_modes.items() if name not in templates}

    settings = AppSettings(
        config_dir=config_dir,
        modes=modes,
        layer_modes=layer_modes,
        reload_command=spoken_phrase(data.get("reload_command", DEFAULT_RELOAD_COMMAND)),
        sleep_confirm_seconds=_confirm_seconds(data),
        panic_command=_optional_phrase(data.get("panic_command", DEFAULT_PANIC_COMMAND)),
        previous_mode_keyword=_optional_phrase(
            data.get("previous_mode_keyword", DEFAULT_PREVIOUS_MODE_KEYWORD)
        ),
        enable_systray=bool(data.get("enable_systray", False)),
        head_tracking=_build_head_tracking(data),
        pedal=_build_pedal(data),
        starting_mode=_starting_mode(data, modes, templates),
        starting_gesture_mode=_starting_layer_mode(data, "starting_gesture_mode", layer_modes),
        starting_pedal_mode=_starting_layer_mode(data, "starting_pedal_mode", layer_modes),
        templates=templates,
        overlay=_build_overlay(data),
        control=_build_control(data),
    )
    _check_navigate(settings.overlay)
    _check_reserved_phrases(settings.modes, settings.reserved_phrases)
    if settings.head_tracking is not None:
        _check_gesture_definitions(settings.head_tracking.gesture_definitions)
        _check_gestures(settings.layer_modes, settings.head_tracking)
        _check_debug_gestures(settings.head_tracking)
        _warn_about_inert_hand_gestures(settings.head_tracking)
    _check_absolute_modes(settings)
    if settings.pedal is not None:
        _check_pedals(settings.layer_modes, settings.pedal)
    _check_responses(settings)
    return settings


def _resolve_model_path(raw: str, config_dir: Path, *, what: str) -> str:
    """Find a configured model on disk, or raise naming every place that was tried."""
    candidate = Path(raw).expanduser()
    tried = (
        [candidate] if candidate.is_absolute() else [Path.cwd() / candidate, config_dir / candidate]
    )
    for path in tried:
        if path.exists():
            return str(path)

    places = ", ".join(str(path) for path in tried)
    raise ConfigError(f"{what} is {raw!r}, which does not exist. Looked in: {places}.")


def resolve_model_paths(settings: AppSettings) -> AppSettings:
    """Check every configured model path and rewrite it to what was found on disk."""
    layer_modes = {}
    for name, mode in settings.layer_modes.items():
        if mode.vosk is not None:
            path = _resolve_model_path(
                mode.vosk.model_path, settings.config_dir, what=f"Mode {name!r} model 'path'"
            )
            mode = replace(mode, vosk=replace(mode.vosk, model_path=path))
        layer_modes[name] = mode
    modes = {name: layer_modes[name] for name in settings.modes}

    head_tracking = settings.head_tracking
    if head_tracking is not None:
        head_tracking = replace(
            head_tracking,
            model_path=_resolve_model_path(
                head_tracking.model_path, settings.config_dir, what="'ht_model_path'"
            ),
            hands=_resolve_hand_model(head_tracking.hands, settings.config_dir),
        )

    return replace(settings, modes=modes, layer_modes=layer_modes, head_tracking=head_tracking)


def _resolve_hand_model(
    hands: HandTrackingSettings | None, config_dir: Path
) -> HandTrackingSettings | None:
    """The same treatment for the hand model."""
    if hands is None:
        return None
    return replace(
        hands,
        model_path=_resolve_model_path(hands.model_path, config_dir, what="'ht_hand_model_path'"),
    )


def load_settings(config_dir: Path) -> AppSettings:
    """Read and validate <config_dir>/config.json and every file it includes."""
    data, sources = merge_documents(read_documents(config_dir))
    return resolve_model_paths(parse_settings(data, config_dir, sources))
