"""uinput Xbox 360 pad."""

from __future__ import annotations

from evdev import AbsInfo, UInput, ecodes

# symmetric; -32768 makes some games unhappy
AXIS_MIN = -32767
AXIS_MAX = 32767
# flat=0 or joydev deadzone eats digital WASD
AXIS_FUZZ = 0
AXIS_FLAT = 0
TRIGGER_MAX = 255

BUTTON_CODES: dict[str, int] = {
    "A": ecodes.BTN_SOUTH,
    "B": ecodes.BTN_EAST,
    "X": ecodes.BTN_WEST,
    "Y": ecodes.BTN_NORTH,
    "LB": ecodes.BTN_TL,
    "RB": ecodes.BTN_TR,
    "BACK": ecodes.BTN_SELECT,
    "START": ecodes.BTN_START,
    "GUIDE": ecodes.BTN_MODE,
    "LS": ecodes.BTN_THUMBL,
    "RS": ecodes.BTN_THUMBR,
}

# not real EV_KEYs on a 360 pad
TRIGGER_BUTTONS = frozenset({"LT", "RT"})
DPAD_BUTTONS = frozenset({"DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT"})

AXIS_CODES: dict[str, int] = {
    "LX": ecodes.ABS_X,
    "LY": ecodes.ABS_Y,
    "RX": ecodes.ABS_RX,
    "RY": ecodes.ABS_RY,
    "LT": ecodes.ABS_Z,
    "RT": ecodes.ABS_RZ,
}

# for games that have left/right sticks backwards
AXIS_CODES_SWAPPED: dict[str, int] = {
    "LX": ecodes.ABS_RX,
    "LY": ecodes.ABS_RY,
    "RX": ecodes.ABS_X,
    "RY": ecodes.ABS_Y,
    "LT": ecodes.ABS_Z,
    "RT": ecodes.ABS_RZ,
}


def _stick_abs() -> AbsInfo:
    return AbsInfo(
        value=0,
        min=AXIS_MIN,
        max=AXIS_MAX,
        fuzz=AXIS_FUZZ,
        flat=AXIS_FLAT,
        resolution=0,
    )


def _trigger_abs() -> AbsInfo:
    return AbsInfo(
        value=0,
        min=0,
        max=TRIGGER_MAX,
        fuzz=0,
        flat=0,
        resolution=0,
    )


class VirtualPad:
    def __init__(
        self,
        name: str = "Microsoft X-Box 360 pad",
        *,
        swap_sticks: bool = False,
    ) -> None:
        self._axis_codes = AXIS_CODES_SWAPPED if swap_sticks else AXIS_CODES
        cap = {
            ecodes.EV_KEY: list(BUTTON_CODES.values()),
            ecodes.EV_ABS: [
                (ecodes.ABS_X, _stick_abs()),
                (ecodes.ABS_Y, _stick_abs()),
                (ecodes.ABS_RX, _stick_abs()),
                (ecodes.ABS_RY, _stick_abs()),
                (ecodes.ABS_Z, _trigger_abs()),
                (ecodes.ABS_RZ, _trigger_abs()),
                (
                    ecodes.ABS_HAT0X,
                    AbsInfo(value=0, min=-1, max=1, fuzz=0, flat=0, resolution=0),
                ),
                (
                    ecodes.ABS_HAT0Y,
                    AbsInfo(value=0, min=-1, max=1, fuzz=0, flat=0, resolution=0),
                ),
            ],
        }
        # pretend to be a wired 360 so games recognize us
        self._ui = UInput(
            cap,
            name=name,
            vendor=0x045E,
            product=0x028E,
            version=0x0114,
            bustype=ecodes.BUS_USB,
        )
        self._buttons: dict[str, int] = {name: 0 for name in BUTTON_CODES}
        self._dpad: dict[str, int] = {name: 0 for name in DPAD_BUTTONS}
        self._axes: dict[str, int] = {
            "LX": 0,
            "LY": 0,
            "RX": 0,
            "RY": 0,
            "LT": 0,
            "RT": 0,
        }
        self._hat_x = 0
        self._hat_y = 0

    @property
    def device_path(self) -> str | None:
        try:
            return self._ui.device.path
        except Exception:
            return None

    def set_button(self, name: str, pressed: bool) -> None:
        key = name.upper()
        value = 1 if pressed else 0

        if key in BUTTON_CODES:
            if self._buttons[key] == value:
                return
            self._buttons[key] = value
            self._ui.write(ecodes.EV_KEY, BUTTON_CODES[key], value)
            return

        if key in TRIGGER_BUTTONS:
            self._set_axis_raw(key, TRIGGER_MAX if pressed else 0)
            return

        if key in DPAD_BUTTONS:
            if self._dpad[key] == value:
                return
            self._dpad[key] = value
            self._sync_hat_from_dpad()
            return

        raise KeyError(f"Unknown button: {name}")

    def set_axis(self, name: str, value: int, *, force: bool = False) -> None:
        key = name.upper()
        if key not in self._axis_codes:
            raise KeyError(f"Unknown axis: {name}")
        if key in ("LT", "RT"):
            value = max(0, min(TRIGGER_MAX, int(value)))
        else:
            value = max(AXIS_MIN, min(AXIS_MAX, int(value)))
        self._set_axis_raw(key, value, force=force)

    def _set_axis_raw(self, key: str, value: int, *, force: bool = False) -> None:
        if not force and self._axes[key] == value:
            return
        self._axes[key] = value
        self._ui.write(ecodes.EV_ABS, self._axis_codes[key], value)

    def _sync_hat_from_dpad(self) -> None:
        hx = 0
        hy = 0
        if self._dpad["DPAD_LEFT"]:
            hx = -1
        elif self._dpad["DPAD_RIGHT"]:
            hx = 1
        if self._dpad["DPAD_UP"]:
            hy = -1
        elif self._dpad["DPAD_DOWN"]:
            hy = 1
        if hx != self._hat_x:
            self._hat_x = hx
            self._ui.write(ecodes.EV_ABS, ecodes.ABS_HAT0X, hx)
        if hy != self._hat_y:
            self._hat_y = hy
            self._ui.write(ecodes.EV_ABS, ecodes.ABS_HAT0Y, hy)

    def sync(self) -> None:
        self._ui.syn()

    def neutralize(self) -> None:
        for name in list(self._buttons):
            if self._buttons[name]:
                self.set_button(name, False)
        for name in list(self._dpad):
            if self._dpad[name]:
                self.set_button(name, False)
        for axis in ("LX", "LY", "RX", "RY", "LT", "RT"):
            self.set_axis(axis, 0)
        self.sync()

    def close(self) -> None:
        self._ui.close()

    def __enter__(self) -> VirtualPad:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
