from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum, auto

LIBRARY_ENV = "LIBRE_DICTUM_LAYER_SHELL"

LIBRARIES = ("libgtk4-layer-shell.so.0", "libgtk4-layer-shell.so")

PACKAGES = (
    "Arch: gtk4-layer-shell",
    "Debian/Ubuntu: libgtk4-layer-shell0 gir1.2-gtk4layershell-1.0",
    "Fedora: gtk4-layer-shell",
    "Gentoo: USE=introspection emerge gui-libs/gtk4-layer-shell",
)

BINDINGS = (
    "Arch and Fedora: included with the library",
    "Debian/Ubuntu: gir1.2-gtk4layershell-1.0",
    "Gentoo: USE=introspection emerge -1 gui-libs/gtk4-layer-shell",
)

APP_ID = "libre-dictum-hud"

NAMESPACE = "on-screen-display"

RULE_FILE = "examples/libre-dictum-hud.kwinrule"


class Placement(StrEnum):
    """What the surfaces ended up as."""

    LAYER = auto()
    PLAIN = auto()


class Trouble(StrEnum):
    """Why it is not on the layer, of the four ways that happens."""

    NONE = auto()
    NOT_WAYLAND = auto()
    NO_LIBRARY = auto()
    NO_BINDING = auto()
    NO_PROTOCOL = auto()
    NOT_APPLIED = auto()


@dataclass(frozen=True)
class Session:
    """The part of the environment that decides what a window can be."""

    wayland: bool
    desktops: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Session:
        """What GDK will conclude, read the same way GDK reads it."""
        current = env.get("XDG_CURRENT_DESKTOP", "")
        return cls(
            wayland=bool(env.get("WAYLAND_DISPLAY"))
            or env.get("XDG_SESSION_TYPE", "").lower() == "wayland",
            desktops=tuple(part for part in current.split(":") if part),
        )

    @property
    def kde(self) -> bool:
        """Whether KWin is the compositor, which changes what a failure means."""
        return any(name.upper() == "KDE" for name in self.desktops)

    @property
    def where(self) -> str:
        """A short "wayland/KDE" for a log line or a probe."""
        display = "wayland" if self.wayland else "x11"
        return f"{display}/{':'.join(self.desktops) or 'unknown'}"


@dataclass(frozen=True)
class Output:
    """Which video output the surfaces go to, and whether they are drawn at all."""

    connector: str | None = None
    visible: bool = True


def output_for(connectors: Sequence[str], *, name: str, only: bool) -> Output:
    """What the configured output means for a session that has these ones."""
    if not name:
        return Output()
    if name in connectors:
        return Output(name, True)
    return Output(None, not only)


def explain_absent(connectors: Sequence[str], *, name: str, only: bool) -> str:
    """What a configured output that is not connected costs, and how to find its name."""
    present = ", ".join(connectors) or "none at all"
    cost = (
        "nothing is drawn until it is connected"
        if only
        else "the compositor places the surfaces until it is connected"
    )
    return (
        f"overlay.output.name is {name!r} and this session has {present}, so {cost}. "
        "Run 'libre-dictum-hud --outputs' for the names this session offers."
    )


def explain_unpinnable(name: str) -> str:
    """Why a named output cannot be obeyed by an ordinary window."""
    return (
        f"overlay.output.name is {name!r}, and confining a surface to one output is a "
        "layer-shell property: without it the compositor places the surfaces, and the "
        "restriction cannot be applied at all."
    )


@dataclass(frozen=True)
class Outcome:
    """What happened when the display asked for a layer surface."""

    placement: Placement
    trouble: Trouble
    library: str | None = None
    version: int = 0

    @property
    def degraded(self) -> bool:
        """Whether the user is looking at something less than was asked for."""
        return self.placement is not Placement.LAYER


def libraries(env: Mapping[str, str]) -> tuple[str, ...]:
    """The shim's candidate names, the override first."""
    override = env.get(LIBRARY_ENV, "").strip()
    return (override, *LIBRARIES) if override else LIBRARIES


def outcome_of(
    session: Session,
    *,
    library: str | None,
    binding: bool,
    supported: bool,
    applied: bool,
    version: int = 0,
) -> Outcome:
    """Five facts in, one diagnosis out."""
    if applied:
        return Outcome(Placement.LAYER, Trouble.NONE, library, version)
    if not session.wayland:
        return Outcome(Placement.PLAIN, Trouble.NOT_WAYLAND, library, version)
    if library is None:
        return Outcome(Placement.PLAIN, Trouble.NO_LIBRARY)
    if not binding:
        return Outcome(Placement.PLAIN, Trouble.NO_BINDING, library)
    if not supported:
        return Outcome(Placement.PLAIN, Trouble.NO_PROTOCOL, library, version)
    return Outcome(Placement.PLAIN, Trouble.NOT_APPLIED, library, version)


COST = (
    "it stays on one virtual desktop, other windows cover it, and clicks land on it "
    "instead of passing through"
)


def explain(session: Session, outcome: Outcome) -> str:
    """The whole message: what happened, what it costs, and the one thing to do."""
    if outcome.trouble is Trouble.NONE:
        return (
            f"On the compositor's overlay layer (zwlr_layer_shell_v1 v{outcome.version} "
            f"via {outcome.library}): above other windows, on every virtual desktop, "
            "click-through."
        )

    reason, fix = _reason_and_fix(session, outcome)
    parts = [f"{reason} The overlay is an ordinary window, so {COST}.", fix]
    if session.kde:
        parts.append(
            f"Until then, importing {RULE_FILE} under System Settings -> Window "
            f"Management -> Window Rules gives it all-desktops and keep-above by hand "
            f"(it matches the app id {APP_ID!r})."
        )
    return " ".join(parts)


def _reason_and_fix(session: Session, outcome: Outcome) -> tuple[str, str]:
    """The half of the message that differs, per way of missing."""
    if outcome.trouble is Trouble.NOT_WAYLAND:
        return (
            "This is not a Wayland session, and X11 has no layer shell.",
            "Log in to the Wayland session -- on KDE that is the 'Plasma (Wayland)' "
            "entry at the login screen.",
        )
    if outcome.trouble is Trouble.NO_LIBRARY:
        tried = ", ".join(libraries({}))
        return (
            f"gtk4-layer-shell could not be loaded (tried {tried}).",
            f"Install it -- {'; '.join(PACKAGES)} -- or point ${LIBRARY_ENV} at the "
            "shared library.",
        )
    if outcome.trouble is Trouble.NO_BINDING:
        return (
            f"{outcome.library} is installed and its Python binding is not, so nothing "
            "can reach it: there is no Gtk4LayerShell-1.0 typelib on this machine.",
            f"Install the introspection data -- {'; '.join(BINDINGS)} -- or point "
            "$GI_TYPELIB_PATH at the directory holding the typelib.",
        )
    preload = f"LD_PRELOAD={outcome.library} libre-dictum-hud"
    if outcome.trouble is Trouble.NO_PROTOCOL and not session.kde:
        return (
            f"{outcome.library} loaded, but this compositor offers no " "zwlr_layer_shell_v1.",
            "Use a compositor that implements it (KWin, sway, Hyprland, wlroots-based "
            f"ones), or check that the library is loaded before libwayland-client: "
            f"{preload}.",
        )
    if outcome.trouble is Trouble.NOT_APPLIED:
        reason = f"{outcome.library} loaded and the window still did not become a layer surface."
    else:
        reason = (
            f"{outcome.library} loaded and reported no layer shell, on a compositor that has one."
        )
    return (
        reason,
        "That is the load order: gtk4-layer-shell defines libwayland-client's own symbols, so it "
        f"has to reach the process before libwayland-client does. Run {preload}.",
    )
