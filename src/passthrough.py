"""Re-inject kbm while devices stay grabbed."""

from __future__ import annotations

from evdev import InputDevice, InputEvent, UInput, ecodes


class PassthroughKBM:
    def __init__(self, sources: list[InputDevice]) -> None:
        if not sources:
            raise ValueError("PassthroughKBM requires at least one source device")
        self._ui = UInput.from_device(
            *sources,
            name="KBM Emulator Passthrough",
        )

    @property
    def device_path(self) -> str | None:
        try:
            return self._ui.device.path
        except Exception:
            return None

    def write(self, event: InputEvent) -> None:
        if event.type in (ecodes.EV_KEY, ecodes.EV_REL, ecodes.EV_ABS, ecodes.EV_MSC):
            try:
                self._ui.write(event.type, event.code, event.value)
            except OSError:
                pass  # weird device, skip

    def write_key(self, code: int, value: int) -> None:
        try:
            self._ui.write(ecodes.EV_KEY, code, value)
        except OSError:
            pass

    def sync(self) -> None:
        self._ui.syn()

    def release_all_from_devices(self, devices: list[InputDevice]) -> int:
        """Synth key-ups for every code these devices can send.

        Call before grab so the desktop isn't left with stuck keys.
        """
        codes: set[int] = set()
        for dev in devices:
            try:
                codes.update(dev.active_keys())
            except OSError:
                pass
            try:
                caps = dev.capabilities(verbose=False).get(ecodes.EV_KEY, [])
            except OSError:
                caps = []
            for item in caps:
                # int or (code, absinfo) depending on event type
                if isinstance(item, (list, tuple)):
                    codes.add(int(item[0]))
                else:
                    codes.add(int(item))

        for code in sorted(codes):
            self.write_key(code, 0)
        self.sync()
        return len(codes)

    def close(self) -> None:
        self._ui.close()

    def __enter__(self) -> PassthroughKBM:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
