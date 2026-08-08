"""evdev events → VirtualPad."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from evdev import InputEvent, ecodes

from .mouse_stick import MouseStick, MouseStickConfig
from .virtual_pad import AXIS_MAX, AXIS_MIN, VirtualPad


def _chord_from_toml(section: dict[str, Any] | None, default: list[str]) -> set[str]:
    raw = (section or {}).get("keys", default)
    return {str(k).upper() for k in raw}


@dataclass
class MapperConfig:
    buttons: dict[str, str] = field(default_factory=dict)
    axes: dict[str, str] = field(default_factory=dict)
    scroll: dict[str, str] = field(default_factory=dict)
    quit_keys: set[str] = field(default_factory=set)
    toggle_keys: set[str] = field(default_factory=set)
    mouse: MouseStickConfig = field(default_factory=MouseStickConfig)
    swap_sticks: bool = False

    @classmethod
    def from_toml(cls, data: dict[str, Any]) -> MapperConfig:
        mouse_raw = data.get("mouse", {})
        # old configs used sensitivity/decay
        legacy_sens = float(mouse_raw.get("sensitivity", 9000.0))
        if legacy_sens <= 20.0:
            # old scale was ~1.0; bump into pixel units
            legacy_sens *= 9000.0
        sens_x = float(mouse_raw.get("sensitivity_x", legacy_sens))
        sens_y = float(mouse_raw.get("sensitivity_y", legacy_sens))
        mouse = MouseStickConfig(
            sensitivity_x=sens_x,
            sensitivity_y=sens_y,
            smoothing=float(mouse_raw.get("smoothing", 0.05)),
            release=float(mouse_raw.get("release", mouse_raw.get("decay", 0.85))),
            deadzone=int(mouse_raw.get("deadzone", 0)),
            invert_y=bool(mouse_raw.get("invert_y", False)),
            max_delta=float(mouse_raw.get("max_delta", 200.0)),
        )
        quit_keys = _chord_from_toml(
            data.get("quit"), ["LEFTCTRL", "LEFTSHIFT", "Q"]
        )
        toggle_keys = _chord_from_toml(
            data.get("toggle"), ["LEFTCTRL", "LEFTSHIFT", "T"]
        )

        buttons = {
            str(k).upper(): str(v).upper()
            for k, v in data.get("buttons", {}).items()
        }
        axes = {
            str(k).upper(): str(v).upper()
            for k, v in data.get("axes", {}).items()
        }
        # WASD always left stick, full stop. config can't put them on look.
        axes.update(
            {
                "W": "LY-",
                "A": "LX-",
                "S": "LY+",
                "D": "LX+",
            }
        )
        scroll = {
            str(k).lower(): str(v).upper()
            for k, v in data.get("scroll", {}).items()
        }
        swap_sticks = bool(data.get("gamepad", {}).get("swap_sticks", False))
        return cls(
            buttons=buttons,
            axes=axes,
            scroll=scroll,
            quit_keys=quit_keys,
            toggle_keys=toggle_keys,
            mouse=mouse,
            swap_sticks=swap_sticks,
        )


class Mapper:
    def __init__(self, pad: VirtualPad, config: MapperConfig) -> None:
        self.pad = pad
        self.config = config
        self.mouse_stick = MouseStick(config.mouse)
        self._held_keys: set[str] = set()
        # axis -> active dirs ("+", "-")
        self._axis_dirs: dict[str, set[str]] = {
            "LX": set(),
            "LY": set(),
            "RX": set(),
            "RY": set(),
        }
        self.should_quit = False
        self.should_toggle = False
        self.mapping_enabled = True  # False = passthrough, no pad writes
        self._quit_chord_latched = False
        self._toggle_chord_latched = False

    def reset_motion_state(self) -> None:
        """Center pad / clear axes. Leaves key tracking alone for hotkeys."""
        for dirs in self._axis_dirs.values():
            dirs.clear()
        self.mouse_stick.reset()
        self.pad.neutralize()
        # stay latched if chord keys still down so we don't re-fire
        self._quit_chord_latched = bool(
            self.config.quit_keys and self.config.quit_keys.issubset(self._held_keys)
        )
        self._toggle_chord_latched = bool(
            self.config.toggle_keys
            and self.config.toggle_keys.issubset(self._held_keys)
        )

    def handle_event(
        self,
        event: InputEvent,
        *,
        allow_mouse_rel: bool = True,
    ) -> bool:
        """True if pad state might have changed."""
        if event.type == ecodes.EV_KEY:
            return self._handle_key(event)
        if event.type == ecodes.EV_REL:
            if not self.mapping_enabled:
                return False
            if event.code in (ecodes.REL_X, ecodes.REL_Y) and not allow_mouse_rel:
                return False
            return self._handle_rel(event)
        return False

    def _axis_value(self, axis: str) -> int:
        dirs = self._axis_dirs[axis]
        if "+" in dirs and "-" not in dirs:
            return AXIS_MAX
        if "-" in dirs and "+" not in dirs:
            return AXIS_MIN
        return 0

    def tick_mouse(self) -> bool:
        if not self.mapping_enabled:
            return False
        changed = False

        mx, my = self.mouse_stick.tick()
        # key bindings on RX/RY win over mouse that frame
        key_rx = self._axis_value("RX")
        key_ry = self._axis_value("RY")
        rx = key_rx if key_rx != 0 else mx
        ry = key_ry if key_ry != 0 else my

        before_rx = self.pad._axes["RX"]
        before_ry = self.pad._axes["RY"]
        self.pad.set_axis("RX", rx, force=(key_rx != 0))
        self.pad.set_axis("RY", ry, force=(key_ry != 0))
        if before_rx != self.pad._axes["RX"] or before_ry != self.pad._axes["RY"]:
            changed = True

        # re-assert WASD; some engines drop a single edge
        for axis in ("LX", "LY"):
            value = self._axis_value(axis)
            if value != 0 or self.pad._axes[axis] != 0:
                before = self.pad._axes[axis]
                self.pad.set_axis(axis, value, force=(value != 0))
                if before != self.pad._axes[axis] or value != 0:
                    changed = True

        return changed

    def _key_name(self, code: int) -> str | None:
        name = ecodes.KEY.get(code) or ecodes.BTN.get(code)
        if name is None:
            return None
        if isinstance(name, (list, tuple)):
            name = name[0]
        # KEY_A -> A, BTN_LEFT stays BTN_LEFT
        if name.startswith("KEY_"):
            return name[4:]
        if name.startswith("BTN_"):
            return name
        return name

    def _update_chords(self) -> None:
        quit_active = bool(
            self.config.quit_keys and self.config.quit_keys.issubset(self._held_keys)
        )
        if quit_active and not self._quit_chord_latched:
            self.should_quit = True
        self._quit_chord_latched = quit_active

        toggle_active = bool(
            self.config.toggle_keys
            and self.config.toggle_keys.issubset(self._held_keys)
        )
        if toggle_active and not self._toggle_chord_latched:
            self.should_toggle = True
        self._toggle_chord_latched = toggle_active

    def _handle_key(self, event: InputEvent) -> bool:
        # 0=up 1=down 2=repeat
        name = self._key_name(event.code)
        if name is None:
            return False

        if event.value == 2:
            if not self.mapping_enabled:
                return False
            if name in self.config.axes:
                self._apply_axis_key(self.config.axes[name], True)
                return True
            return False

        pressed = event.value == 1
        if pressed:
            self._held_keys.add(name)
        else:
            self._held_keys.discard(name)

        self._update_chords()

        if not self.mapping_enabled:
            return False

        changed = False

        if name in self.config.buttons:
            self.pad.set_button(self.config.buttons[name], pressed)
            changed = True

        if name in self.config.axes:
            self._apply_axis_key(self.config.axes[name], pressed)
            changed = True

        return changed

    def _apply_axis_key(self, binding: str, pressed: bool) -> None:
        # "LY-" / "LX+" etc
        if len(binding) < 3:
            return
        axis = binding[:2]
        direction = binding[2:]
        if axis not in self._axis_dirs or direction not in ("+", "-"):
            return
        dirs = self._axis_dirs[axis]
        if pressed:
            dirs.add(direction)
        else:
            dirs.discard(direction)

        self.pad.set_axis(axis, self._axis_value(axis), force=True)

    def _handle_rel(self, event: InputEvent) -> bool:
        if event.code == ecodes.REL_X:
            self.mouse_stick.add_delta(event.value, 0)
            return True
        if event.code == ecodes.REL_Y:
            self.mouse_stick.add_delta(0, event.value)
            return True
        if event.code == ecodes.REL_WHEEL:
            if event.value > 0:
                btn = self.config.scroll.get("up")
            elif event.value < 0:
                btn = self.config.scroll.get("down")
            else:
                return False
            if not btn:
                return False
            # one-frame pulse
            self.pad.set_button(btn, True)
            self.pad.sync()
            self.pad.set_button(btn, False)
            return True
        return False
