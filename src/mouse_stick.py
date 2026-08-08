"""Mouse deltas → right stick."""

from __future__ import annotations

from dataclasses import dataclass

from .virtual_pad import AXIS_MAX, AXIS_MIN


@dataclass
class MouseStickConfig:
    sensitivity_x: float = 9000.0  # stick units per pixel this tick
    sensitivity_y: float = 9000.0
    smoothing: float = 0.05  # 0 = raw, ~0.2–0.4 if jittery
    release: float = 0.85  # how hard we pull back to center when idle
    deadzone: int = 0
    invert_y: bool = False
    max_delta: float = 200.0  # clamp flicks so they don't saturate forever


class MouseStick:
    """Velocity-matched look. Move → deflect; stop → snap back."""

    def __init__(self, config: MouseStickConfig | None = None) -> None:
        self.config = config or MouseStickConfig()
        self._dx = 0.0
        self._dy = 0.0
        self._out_x = 0.0
        self._out_y = 0.0

    def add_delta(self, dx: int, dy: int) -> None:
        self._dx += dx
        self._dy += dy

    def tick(self) -> tuple[int, int]:
        cfg = self.config
        cap = cfg.max_delta
        dx = max(-cap, min(cap, self._dx))
        dy = max(-cap, min(cap, self._dy))
        self._dx = 0.0
        self._dy = 0.0

        if cfg.invert_y:
            dy = -dy

        target_x = dx * cfg.sensitivity_x
        target_y = dy * cfg.sensitivity_y

        moved = dx != 0.0 or dy != 0.0
        if moved:
            smooth = max(0.0, min(0.95, cfg.smoothing))
            self._out_x = self._out_x * smooth + target_x * (1.0 - smooth)
            self._out_y = self._out_y * smooth + target_y * (1.0 - smooth)
        else:
            release = max(0.0, min(1.0, cfg.release))
            self._out_x *= 1.0 - release
            self._out_y *= 1.0 - release
            if abs(self._out_x) < 1.0:
                self._out_x = 0.0
            if abs(self._out_y) < 1.0:
                self._out_y = 0.0

        rx = int(max(AXIS_MIN, min(AXIS_MAX, self._out_x)))
        ry = int(max(AXIS_MIN, min(AXIS_MAX, self._out_y)))

        dead = cfg.deadzone
        if abs(rx) < dead:
            rx = 0
        if abs(ry) < dead:
            ry = 0
        return rx, ry

    def reset(self) -> None:
        self._dx = 0.0
        self._dy = 0.0
        self._out_x = 0.0
        self._out_y = 0.0
