# SPDX-License-Identifier: GPL-3.0-only
"""
Game pads (Xbox, PlayStation, 8BitDo and others, over USB or Bluetooth) through libmanette, laid out as a console
browser lays them out: the left stick aims the pointer, the right stick scrolls, A selects, B goes back, X is
Watch, Y is Search, Start the menu, Select Home, L1/R1 back and forward, L2/R2 a page up and down. Optional: without
libmanette (gir1.2-manette-0.2) Sofa runs without game pad support.
"""

import math

try:
    import gi

    gi.require_version("Manette", "0.2")
    from gi.repository import Manette
except (ImportError, ValueError):
    Manette = None


# Linux input codes, as libmanette reports them after its mapping (the standard game pad layout).
BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_WEST = 304, 305, 307, 308
BTN_TL, BTN_TR, BTN_TL2, BTN_TR2 = 310, 311, 312, 313
BTN_SELECT, BTN_START, BTN_MODE = 314, 315, 316
BTN_DPAD_UP, BTN_DPAD_DOWN, BTN_DPAD_LEFT, BTN_DPAD_RIGHT = 544, 545, 546, 547
ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ, ABS_HAT0X, ABS_HAT0Y = 0, 1, 2, 3, 4, 5, 16, 17

ACTIONS = {
    BTN_SOUTH: "select", BTN_EAST: "back", BTN_WEST: "watch", BTN_NORTH: "search",
    BTN_START: "menu", BTN_SELECT: "home", BTN_MODE: "home", BTN_TL: "history_back", BTN_TR: "history_forward",
    BTN_TL2: "page_up", BTN_TR2: "page_down",
    BTN_DPAD_UP: "up", BTN_DPAD_DOWN: "down", BTN_DPAD_LEFT: "left", BTN_DPAD_RIGHT: "right",
}
DEAD_ZONE = 0.18
CURVE = 1.8


def stick(x: float, y: float) -> tuple[float, float]:
    """A stick's position after the dead zone, scaled back to 0..1 and curved: small pushes aim finely."""
    m = math.hypot(x, y)
    if m <= DEAD_ZONE:
        return 0.0, 0.0
    scaled = ((min(m, 1.0) - DEAD_ZONE) / (1 - DEAD_ZONE)) ** CURVE
    return x / m * scaled, y / m * scaled


class GamePads:
    """Calls [on_action](name, pressed) for buttons and [on_sticks](aim, wheel) when the sticks move."""

    def __init__(self, on_action, on_sticks, on_connected=None):
        self.on_action = on_action
        self.on_sticks = on_sticks
        self.on_connected = on_connected
        self.axes: dict[int, float] = {}
        self.triggers = {ABS_Z: False, ABS_RZ: False}
        self.hat = {ABS_HAT0X: 0, ABS_HAT0Y: 0}
        self.devices = []
        self.monitor = None
        if Manette is None:
            return
        try:
            self.monitor = Manette.Monitor.new()
        except Exception:  # noqa: BLE001 - no game pad support on this system
            self.monitor = None
            return
        self.monitor.connect("device-connected", lambda _m, d: self._add(d, announce=True))
        self.monitor.connect("device-disconnected", lambda _m, d: self._remove(d))
        it = self.monitor.iterate()
        while True:
            ok, dev = it.next()
            if not ok:
                break
            self._add(dev)

    @property
    def available(self):
        return self.monitor is not None

    def _add(self, dev, announce=False):
        if dev in self.devices:
            return
        self.devices.append(dev)
        dev.connect("button-press-event", lambda d, e: self._button(e, True))
        dev.connect("button-release-event", lambda d, e: self._button(e, False))
        dev.connect("absolute-axis-event", lambda d, e: self._axis(e))
        dev.connect("hat-axis-event", lambda d, e: self._hat(e))
        if announce and self.on_connected:
            self.on_connected(dev.get_name())

    def _remove(self, dev):
        if dev in self.devices:
            self.devices.remove(dev)
        self.axes.clear()
        self.on_sticks((0.0, 0.0), (0.0, 0.0))

    def _button(self, event, pressed):
        ok, code = event.get_button()
        if ok and code in ACTIONS:
            self.on_action(ACTIONS[code], pressed)

    def _hat(self, event):
        ok, axis, value = event.get_hat()
        if not ok or axis not in self.hat:
            return
        old = self.hat[axis]
        self.hat[axis] = value
        names = ("left", "right") if axis == ABS_HAT0X else ("up", "down")
        if old:
            self.on_action(names[0] if old < 0 else names[1], False)
        if value:
            self.on_action(names[0] if value < 0 else names[1], True)

    def _axis(self, event):
        ok, axis, value = event.get_absolute()
        if not ok:
            return
        if axis in (ABS_Z, ABS_RZ):
            on = value > 0.5
            if on != self.triggers[axis]:
                self.triggers[axis] = on
                self.on_action("page_up" if axis == ABS_Z else "page_down", on)
            return
        self.axes[axis] = value
        aim = stick(self.axes.get(ABS_X, 0.0), self.axes.get(ABS_Y, 0.0))
        wheel = stick(self.axes.get(ABS_RX, 0.0), self.axes.get(ABS_RY, 0.0))
        self.on_sticks(aim, wheel)
