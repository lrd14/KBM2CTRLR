#!/usr/bin/env python3
"""Smoke tests. Doesn't grab real devices."""

from __future__ import annotations

import sys
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evdev import ecodes
from evdev.events import InputEvent

from src.mapper import Mapper, MapperConfig
from src.mouse_stick import MouseStick, MouseStickConfig
from src.virtual_pad import AXIS_MAX, AXIS_MIN, VirtualPad


def test_config_loads() -> None:
    with (ROOT / "config.toml").open("rb") as f:
        data = tomllib.load(f)
    cfg = MapperConfig.from_toml(data)
    assert "SPACE" in cfg.buttons
    assert cfg.buttons["SPACE"] == "A"
    assert "W" in cfg.axes
    assert "LEFTCTRL" in cfg.quit_keys
    assert "T" in cfg.toggle_keys


def test_mouse_stick_velocity() -> None:
    ms = MouseStick(
        MouseStickConfig(
            sensitivity_x=1000.0,
            sensitivity_y=1000.0,
            smoothing=0.0,
            release=1.0,
            deadzone=0,
            max_delta=80.0,
        )
    )
    ms.add_delta(20, 0)
    rx, ry = ms.tick()
    assert rx == 20000
    assert ry == 0
    # idle tick should recenter
    rx2, _ = ms.tick()
    assert rx2 == 0

    ms.add_delta(10, 5)
    rx, ry = ms.tick()
    assert rx == 10000
    assert ry == 5000


def test_mapper_wasd_and_quit() -> None:
    with (ROOT / "config.toml").open("rb") as f:
        cfg = MapperConfig.from_toml(tomllib.load(f))
    with VirtualPad(name="KBM Emulator Test Pad") as pad:
        mapper = Mapper(pad, cfg)

        def key(code: int, value: int) -> InputEvent:
            return InputEvent(0, 0, ecodes.EV_KEY, code, value)

        mapper.handle_event(key(ecodes.KEY_W, 1))
        assert pad._axes["LY"] == AXIS_MIN
        assert pad._axes["RY"] == 0
        assert pad._axes["RX"] == 0
        mapper.handle_event(key(ecodes.KEY_A, 1))
        assert pad._axes["LX"] == AXIS_MIN
        assert pad._axes["RX"] == 0  # WASD stays off look stick
        mapper.handle_event(key(ecodes.KEY_W, 0))
        mapper.handle_event(key(ecodes.KEY_A, 0))
        assert pad._axes["LY"] == 0
        assert pad._axes["LX"] == 0

        mapper.handle_event(key(ecodes.KEY_F, 1))
        assert pad._buttons["RS"] == 1
        assert pad._axes["RY"] == 0
        mapper.handle_event(key(ecodes.KEY_F, 0))
        assert pad._buttons["RS"] == 0

        mapper.handle_event(key(ecodes.KEY_SPACE, 1))
        assert pad._buttons["A"] == 1
        mapper.handle_event(key(ecodes.KEY_SPACE, 0))

        mapper.handle_event(key(ecodes.KEY_C, 1))
        assert pad._buttons["B"] == 1
        mapper.handle_event(key(ecodes.KEY_C, 0))
        mapper.handle_event(key(ecodes.KEY_R, 1))
        assert pad._buttons["X"] == 1
        mapper.handle_event(key(ecodes.KEY_R, 0))
        mapper.handle_event(key(ecodes.KEY_G, 1))
        assert pad._buttons["Y"] == 1
        mapper.handle_event(key(ecodes.KEY_G, 0))

        mapper.handle_event(key(ecodes.KEY_LEFTCTRL, 1))
        mapper.handle_event(key(ecodes.KEY_LEFTSHIFT, 1))
        mapper.handle_event(key(ecodes.KEY_Q, 1))
        assert mapper.should_quit

        pad.sync()
        time.sleep(0.05)
        path = pad.device_path
        assert path is not None
        print(f"virtual pad ok: {path}")


def test_toggle_chord() -> None:
    with (ROOT / "config.toml").open("rb") as f:
        cfg = MapperConfig.from_toml(tomllib.load(f))
    with VirtualPad(name="KBM Emulator Toggle Test") as pad:
        mapper = Mapper(pad, cfg)

        def key(code: int, value: int) -> InputEvent:
            return InputEvent(0, 0, ecodes.EV_KEY, code, value)

        mapper.handle_event(key(ecodes.KEY_LEFTCTRL, 1))
        mapper.handle_event(key(ecodes.KEY_LEFTSHIFT, 1))
        mapper.handle_event(key(ecodes.KEY_T, 1))
        assert mapper.should_toggle
        mapper.should_toggle = False

        # held/repeat shouldn't re-fire the chord
        mapper.handle_event(key(ecodes.KEY_T, 2))
        assert not mapper.should_toggle

        mapper.handle_event(key(ecodes.KEY_T, 0))
        mapper.handle_event(key(ecodes.KEY_T, 1))
        assert mapper.should_toggle

        mapper.mapping_enabled = False
        mapper.reset_motion_state()
        mapper.handle_event(key(ecodes.KEY_W, 1))
        assert pad._axes["LY"] == 0
        print("toggle chord ok")


def main() -> None:
    test_config_loads()
    print("config ok")
    test_mouse_stick_velocity()
    print("mouse stick ok")
    test_mapper_wasd_and_quit()
    print("mapper + uinput ok")
    test_toggle_chord()
    print("ALL SMOKE TESTS PASSED")


if __name__ == "__main__":
    main()
