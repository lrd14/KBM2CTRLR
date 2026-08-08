#!/usr/bin/env python3
"""Grab keyboard/mouse, spit out a virtual Xbox 360 pad."""

from __future__ import annotations

import argparse
import os
import select
import signal
import sys
import time
import tomllib
from pathlib import Path

from evdev import InputDevice, InputEvent, ecodes, list_devices

from .mapper import Mapper, MapperConfig
from .passthrough import PassthroughKBM
from .virtual_pad import VirtualPad

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.toml"

MOUSE_TICK_HZ = 250


def die(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def load_config(path: Path) -> MapperConfig:
    if not path.is_file():
        die(f"Config not found: {path}")
    with path.open("rb") as f:
        data = tomllib.load(f)
    return MapperConfig.from_toml(data)


def is_virtual_device(dev: InputDevice) -> bool:
    name = (dev.name or "").lower()
    skip = (
        "ydotool",
        "virtual",
        "opentabletdriver",
        "js",
        "xbox360",
        "x-box",
        "kbm emulator",
        "passthrough",
    )
    return any(s in name for s in skip)


def is_keyboard(dev: InputDevice) -> bool:
    if is_virtual_device(dev):
        return False
    caps = dev.capabilities().get(ecodes.EV_KEY, [])
    # need A..Z so we don't grab bare mouse button devices
    return ecodes.KEY_A in caps and ecodes.KEY_Z in caps


def is_mouse(dev: InputDevice) -> bool:
    if is_virtual_device(dev):
        return False
    caps = dev.capabilities()
    rel = caps.get(ecodes.EV_REL, [])
    keys = caps.get(ecodes.EV_KEY, [])
    has_move = ecodes.REL_X in rel and ecodes.REL_Y in rel
    has_btn = ecodes.BTN_LEFT in keys
    return has_move and has_btn


def is_aim_mouse(dev: InputDevice) -> bool:
    """Real mice only. Keyboards often emit REL noise that looks like look-stick."""
    if not is_mouse(dev):
        return False
    name = (dev.name or "").lower()
    if "mouse" in name:
        return True
    # pointer/receiver that isn't also a keyboard
    if not is_keyboard(dev):
        return True
    return False


def open_devices() -> list[InputDevice]:
    devices = []
    for path in list_devices():
        try:
            devices.append(InputDevice(path))
        except (OSError, PermissionError):
            continue
    return devices


def list_input_devices() -> None:
    devices = open_devices()
    if not devices:
        die(
            "No readable input devices. Are you in the 'input' group?\n"
            "See README.md for udev / permissions setup."
        )
    print(f"{'N':>3}  {'path':<22}  type        name")
    print("-" * 72)
    for i, dev in enumerate(devices):
        kinds = []
        if is_keyboard(dev):
            kinds.append("keyboard")
        if is_mouse(dev):
            kinds.append("mouse")
        kind = ",".join(kinds) if kinds else "other"
        print(f"{i:3d}  {dev.path:<22}  {kind:<10}  {dev.name}")


def resolve_watched(
    devices: list[InputDevice],
    keyboard_indexes: list[int] | None,
    mouse_indexes: list[int] | None,
) -> list[InputDevice]:
    """All physical kb/mice unless --keyboard/--mouse indexes are given."""
    selected: list[InputDevice] = []
    seen: set[str] = set()

    def add(dev: InputDevice) -> None:
        if dev.path not in seen:
            seen.add(dev.path)
            selected.append(dev)

    restricting = bool(keyboard_indexes or mouse_indexes)

    if restricting:
        for label, indexes in (("keyboard", keyboard_indexes), ("mouse", mouse_indexes)):
            for index in indexes or []:
                if index < 0 or index >= len(devices):
                    die(f"Invalid --{label} index {index}. Use --list.")
                add(devices[index])
    else:
        for dev in devices:
            if is_keyboard(dev) or is_mouse(dev):
                add(dev)

    if not selected:
        die(
            "No keyboards/mice selected.\n"
            "Use --list, or pass --keyboard N / --mouse M."
        )
    return selected


def check_uinput() -> None:
    if not os.path.exists("/dev/uinput"):
        die(
            "/dev/uinput not found. Run: sudo modprobe uinput\n"
            "See README.md for persistent setup."
        )
    if not os.access("/dev/uinput", os.W_OK):
        die(
            "Cannot write /dev/uinput. Add your user to the 'input' group "
            "(then re-login), or run with sudo.\n"
            "See README.md."
        )


def _chord_label(keys: set[str]) -> str:
    return "+".join(sorted(keys)) if keys else "N/A"


def set_grabbed(devices: list[InputDevice], enabled: bool) -> None:
    for dev in devices:
        try:
            if enabled:
                dev.grab()
            else:
                dev.ungrab()
        except OSError as exc:
            action = "grab" if enabled else "ungrab"
            die(f"Failed to {action} {dev.path} ({dev.name}): {exc}")


def release_all_keys(
    devices: list[InputDevice],
    passthrough: PassthroughKBM,
    *,
    reason: str,
) -> None:
    """Synth key-ups for everything on the watched devices."""
    n = passthrough.release_all_from_devices(devices)
    time.sleep(0.03)  # compositor needs a beat to see the ups
    print(f"Released {n} key/button codes ({reason}).")


def seed_mapper_from_active_keys(mapper: Mapper, devices: list[InputDevice]) -> bool:
    """Re-apply keys that were already down when we grabbed."""
    dirty = False
    seen: set[int] = set()
    for dev in devices:
        try:
            active = dev.active_keys()
        except OSError:
            continue
        for code in active:
            if code in seen:
                continue
            seen.add(code)
            if mapper.handle_event(InputEvent(0, 0, ecodes.EV_KEY, code, 1)):
                dirty = True
    return dirty


def run(args: argparse.Namespace) -> None:
    check_uinput()
    config = load_config(Path(args.config))
    devices = open_devices()
    if not devices:
        die(
            "No readable /dev/input/event* devices.\n"
            "Add your user to the 'input' group and re-login (see README.md)."
        )

    watched = resolve_watched(devices, args.keyboard, args.mouse)

    pad: VirtualPad | None = None
    passthrough: PassthroughKBM | None = None
    stop = False
    # stay grabbed the whole session; otherwise games steal the devices
    # and toggle/quit stop working
    mapping_on = True

    def request_stop(*_args: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    try:
        pad = VirtualPad(swap_sticks=config.swap_sticks)
        if config.swap_sticks:
            print("Gamepad sticks SWAPPED (left↔right) via config.")
        passthrough = PassthroughKBM(watched)

        # flush held keys before grab eats the real key-ups
        release_all_keys(watched, passthrough, reason="before grab")
        set_grabbed(watched, True)

        print(f"Global grab ({len(watched)} device(s)):")
        for dev in watched:
            kind = []
            if is_keyboard(dev):
                kind.append("keyboard")
            if is_mouse(dev):
                kind.append("mouse")
            label = ",".join(kind) if kind else "input"
            print(f"  [{label}] {dev.path}  {dev.name}")
        print(f"Virtual pad:         {pad.device_path or '(created)'}")
        print(f"Passthrough device:  {passthrough.device_path or '(created)'}")
        print(f"Toggle mode: {_chord_label(config.toggle_keys)}")
        print(f"Quit:        {_chord_label(config.quit_keys)}")
        print(
            "MODE: controller mapping — physical KBM held globally; "
            "only the virtual pad is visible to the game."
        )

        mapper = Mapper(pad, config)
        if seed_mapper_from_active_keys(mapper, watched):
            pad.sync()

        fds = {dev.fd: dev for dev in watched}
        aim_fds = {dev.fd for dev in watched if is_aim_mouse(dev)}
        print(
            "Aim mice (right stick): "
            + (", ".join(d.name for d in watched if is_aim_mouse(d)) or "(none!)")
        )
        tick = 1.0 / MOUSE_TICK_HZ
        next_tick = time.monotonic()
        passthrough_dirty = False

        while not stop and not mapper.should_quit:
            now = time.monotonic()
            timeout = max(0.0, next_tick - now)
            readable, _, _ = select.select(list(fds.keys()), [], [], timeout)
            dirty = False
            mouse_moved = False
            for fd in readable:
                dev = fds[fd]
                try:
                    events = list(dev.read())
                except OSError:
                    die(f"Device disappeared: {dev.path}")
                    events = []

                # keyboards with REL in the same batch = firmware noise, skip for aim
                batch_has_key = any(e.type == ecodes.EV_KEY for e in events)
                allow_rel = fd in aim_fds
                if batch_has_key and is_keyboard(dev):
                    allow_rel = False

                for event in events:
                    mapped = mapper.handle_event(
                        event, allow_mouse_rel=allow_rel
                    )
                    if mapped:
                        dirty = True
                        if (
                            mapper.mapping_enabled
                            and allow_rel
                            and event.type == ecodes.EV_REL
                            and event.code in (ecodes.REL_X, ecodes.REL_Y)
                        ):
                            mouse_moved = True
                    if not mapper.mapping_enabled:
                        if event.type != ecodes.EV_SYN:
                            passthrough.write(event)
                            passthrough_dirty = True

            if mapper.should_toggle:
                mapper.should_toggle = False
                mapping_on = not mapping_on
                if mapping_on:
                    # clear stuck desktop keys before we stop forwarding kbm
                    release_all_keys(
                        watched, passthrough, reason="before controller mode"
                    )
                    mapper.mapping_enabled = True
                    mapper.reset_motion_state()
                    if seed_mapper_from_active_keys(mapper, watched):
                        pad.sync()
                    print(
                        "MODE: controller mapping — KBM blocked from game; "
                        "emitting virtual pad."
                    )
                else:
                    mapper.mapping_enabled = False
                    mapper.reset_motion_state()
                    release_all_keys(
                        watched, passthrough, reason="entering passthrough"
                    )
                    print(
                        "MODE: passthrough — KBM re-injected via virtual device; "
                        "physical grab kept (toggle/quit still work)."
                    )

            now = time.monotonic()
            # immediate tick on motion; clock tick so stick recenters when idle
            if mouse_moved or now >= next_tick:
                if mapper.tick_mouse():
                    dirty = True
                if now >= next_tick:
                    while next_tick <= now:
                        next_tick += tick

            if dirty:
                pad.sync()
            if passthrough_dirty:
                passthrough.sync()
                passthrough_dirty = False

        if mapper.should_quit:
            print("Quit hotkey pressed.")
    finally:
        if passthrough is not None:
            try:
                release_all_keys(watched, passthrough, reason="before exit")
            except Exception:
                pass
        for dev in watched:
            try:
                dev.ungrab()
            except OSError:
                pass
            try:
                dev.close()
            except OSError:
                pass
        if passthrough is not None:
            passthrough.close()
        if pad is not None:
            pad.close()
        print("Released devices. Keyboard/mouse restored.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Map keyboard/mouse to a virtual Xbox 360 controller. "
        "By default grabs ALL keyboards and mice globally."
    )
    p.add_argument(
        "-c",
        "--config",
        default=str(DEFAULT_CONFIG),
        help=f"path to config.toml (default: {DEFAULT_CONFIG})",
    )
    p.add_argument(
        "--list",
        action="store_true",
        help="list input devices and exit",
    )
    p.add_argument(
        "--keyboard",
        type=int,
        action="append",
        metavar="N",
        help="restrict to device index N from --list (repeatable). "
        "Default: all keyboards.",
    )
    p.add_argument(
        "--mouse",
        type=int,
        action="append",
        metavar="N",
        help="restrict to device index N from --list (repeatable). "
        "Default: all mice.",
    )
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.list:
        list_input_devices()
        return
    run(args)


if __name__ == "__main__":
    main()
