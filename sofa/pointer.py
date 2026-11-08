# SPDX-License-Identifier: GPL-3.0-only
"""
The remote's D-pad as a mouse-like pointer, drawn over the page.

Holding a direction accelerates the pointer (0.05 px/frame gained per ms held, scaled to a 1920 px wide window,
capped at width/25 px per 60 Hz frame); releasing stops it at once. Pushing against an edge scrolls whatever is
under the pointer. OK clicks at the pointer, OK held while moving drags, and OK held still for a second opens the
menu of a link or image under the pointer, or else switches to scroll mode. The pointer hides after five seconds
without use; the first OK after that only reveals it. A game pad's left stick aims and its right stick scrolls.
"""

import math
import time

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

HIDE_AFTER_S = 5.0
HOLD_S = 1.0
NUDGE = 6.0

LEFT, RIGHT, UP, DOWN = "Left", "Right", "Up", "Down"
DIRS = {"Left": (-1, 0), "Right": (1, 0), "Up": (0, -1), "Down": (0, 1),
        "KP_Left": (-1, 0), "KP_Right": (1, 0), "KP_Up": (0, -1), "KP_Down": (0, 1)}
OK_KEYS = {"Return", "KP_Enter", "ISO_Enter", "Select", "KP_Begin"}


class Pointer(Gtk.DrawingArea):
    """
    [page] provides: zoom (float), hover(x, y, buttons), click/dbl/down/up(x, y), scroll(x, y, dx, dy, done) and
    hit(x, y, cb).
    """

    def __init__(self, page, on_hold_menu, on_scroll_mode):
        super().__init__()
        self.page = page
        self.on_hold_menu = on_hold_menu
        self.on_scroll_mode = on_scroll_mode
        self.set_can_target(False)
        self.set_can_focus(False)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_draw_func(self._draw)
        self.speed_level = 2
        self.size_factor = 1.0  # set from Settings → Mouse pointer size
        self.enabled = True
        self.scroll_mode = False
        self.x = self.y = -1.0
        self.held: dict[str, float] = {}
        self.last_activity = time.monotonic() - HIDE_AFTER_S
        self.ok_held = False
        self.ok_reveal_only = False
        self.ok_start = 0.0
        self.dragging = False
        self.hold_timer = 0
        self.aim = (0.0, 0.0)
        self.wheel = (0.0, 0.0)
        self._last_hover = 0.0
        self._last_frame = None
        self._tick = 0
        self._was_visible = False
        self._scroll_acc = [0.0, 0.0]
        self._scroll_busy = False

    # Geometry
    @property
    def w(self):
        return max(self.get_width(), 1)

    @property
    def h(self):
        return max(self.get_height(), 1)

    @property
    def unit(self):
        return self.w / 1920

    @property
    def visible(self):
        return self.enabled and time.monotonic() - self.last_activity < HIDE_AFTER_S

    def _css(self):
        z = self.page.zoom or 1.0
        return self.x / z, self.y / z

    def _ensure_pos(self):
        if self.x < 0:
            self.x, self.y = self.w / 2, self.h / 2
        self.x = min(max(self.x, 0), self.w - 1)
        self.y = min(max(self.y, 0), self.h - 1)

    def poke(self):
        self.last_activity = time.monotonic()
        self._start()
        self.queue_draw()

    def hide_now(self):
        self.last_activity = 0
        self.queue_draw()

    def reset(self):
        self.held.clear()
        self.ok_held = False
        self.dragging = False
        if self.hold_timer:
            GLib.source_remove(self.hold_timer)
            self.hold_timer = 0
        self.set_scroll_mode(False)

    def set_scroll_mode(self, on):
        if self.scroll_mode != on:
            self.scroll_mode = on
            self.on_scroll_mode(on)
            self.queue_draw()

    # Keys
    def key_pressed(self, name: str) -> bool:
        if name in DIRS:
            self._ensure_pos()
            self.last_activity = time.monotonic()
            if name not in self.held:
                self.held[name] = time.monotonic()
                dx, dy = DIRS[name]
                if self.scroll_mode:
                    self._scroll(dx * 120, dy * 120)
                else:
                    self.x = min(max(self.x + dx * NUDGE * self.unit, 0), self.w - 1)
                    self.y = min(max(self.y + dy * NUDGE * self.unit, 0), self.h - 1)
                    self._moved()
            self._start()
            return True
        if name in OK_KEYS:
            self._ok_down()
            return True
        return False

    def key_released(self, name: str) -> bool:
        if name in DIRS:
            self.held.pop(name, None)
            if not self.held and self.dragging and not self.ok_held:
                self._end_drag()
            self.queue_draw()
            return True
        if name in OK_KEYS:
            self._ok_up()
            return True
        return False

    def _ok_down(self):
        if self.ok_held:
            return  # key repeat
        self._ensure_pos()
        if self.scroll_mode:
            self.set_scroll_mode(False)
            self.ok_reveal_only = True
            return
        if not self.visible:
            self.ok_reveal_only = True
            self.poke()
            return
        self.ok_held = True
        self.ok_start = time.monotonic()
        self.last_activity = self.ok_start
        self.hold_timer = GLib.timeout_add(int(HOLD_S * 1000), self._hold)
        self.queue_draw()

    def _ok_up(self):
        if self.hold_timer:
            GLib.source_remove(self.hold_timer)
            self.hold_timer = 0
        self.last_activity = time.monotonic()
        if self.ok_reveal_only:
            self.ok_reveal_only = False
            return
        if not self.ok_held:
            return
        self.ok_held = False
        if self.dragging:
            self._end_drag()
        else:
            self.page.click(*self._css())
        self.queue_draw()

    def _hold(self):
        self.hold_timer = 0
        if not self.ok_held or self.dragging:
            return False
        self.ok_held = False
        self.ok_reveal_only = True  # the key-up is swallowed

        def answered(info):
            if info and (info.get("link") or info.get("image") or info.get("video")):
                self.on_hold_menu(info)
            else:
                self.set_scroll_mode(True)

        self.page.hit(*self._css(), answered)
        return False

    def _end_drag(self):
        self.dragging = False
        self.page.up(*self._css())

    # Motion
    def _moved(self):
        if self.ok_held and not self.dragging:
            # OK held while moving: a drag from where OK went down.
            if self.hold_timer:
                GLib.source_remove(self.hold_timer)
                self.hold_timer = 0
            self.dragging = True
            self.page.down(*self._css())
        now = time.monotonic()
        if now - self._last_hover > 0.05:
            self._last_hover = now
            self.page.hover(*self._css(), 1 if self.dragging else 0)
        self.queue_draw()

    def _start(self):
        # A plain 60 Hz timer rather than a frame-clock tick: it keeps time on every GDK backend.
        if not self._tick:
            self._last_frame = None
            self._tick = GLib.timeout_add(16, self._frame)

    def _frame(self):
        now = time.monotonic()
        dt = 1.0 if self._last_frame is None else min((now - self._last_frame) / (1 / 60), 4.0)
        self._last_frame = now
        factor = {1: 0.6, 3: 1.5}.get(self.speed_level, 1.0)
        max_speed = self.w / 25 * factor
        vx = vy = 0.0
        if self.held:
            held_ms = (now - min(self.held.values())) * 1000
            speed = min(max_speed, (2 + 0.05 * held_ms) * self.unit * factor)
            for name in self.held:
                dx, dy = DIRS[name]
                vx += dx * speed
                vy += dy * speed
            if vx and vy:
                vx, vy = vx / math.sqrt(2), vy / math.sqrt(2)
        ax, ay = self.aim
        if ax or ay:
            vx += ax * max_speed * 0.8
            vy += ay * max_speed * 0.8
            self.last_activity = now
        if vx or vy:
            self._ensure_pos()
            if self.scroll_mode:
                self._scroll(vx * dt * 1.5, vy * dt * 1.5)
            else:
                nx, ny = self.x + vx * dt, self.y + vy * dt
                over_x = nx - min(max(nx, 0), self.w - 1)
                over_y = ny - min(max(ny, 0), self.h - 1)
                self.x, self.y = min(max(nx, 0), self.w - 1), min(max(ny, 0), self.h - 1)
                edge = self.w / 15
                sx = vx * dt if (over_x or (vx < 0 and self.x < edge * 0.2) or (vx > 0 and self.x > self.w - edge * 0.2)) else 0
                sy = vy * dt if (over_y or (vy < 0 and self.y < edge * 0.2) or (vy > 0 and self.y > self.h - edge * 0.2)) else 0
                if sx or sy:
                    self._scroll(sx, sy)
                self._moved()
        wx, wy = self.wheel
        if wx or wy:
            self._ensure_pos()
            self.last_activity = now
            self._scroll(wx * max_speed * dt, wy * max_speed * dt)
        vis = self.visible
        if vis != self._was_visible:
            self._was_visible = vis
        self.queue_draw()
        idle = not (self.held or ax or ay or wx or wy)
        if idle and not vis:
            self._tick = 0
            self.queue_draw()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _scroll(self, dx, dy):
        # Coalesced: one call in flight at a time, the rest added up.
        z = self.page.zoom or 1.0
        self._scroll_acc[0] += dx / z
        self._scroll_acc[1] += dy / z
        if self._scroll_busy:
            return
        sx, sy = self._scroll_acc
        self._scroll_acc = [0.0, 0.0]
        if abs(sx) < 0.5 and abs(sy) < 0.5:
            return
        self._scroll_busy = True

        def done():
            self._scroll_busy = False

        cx, cy = self._css()
        self.page.scroll(cx, cy, round(sx), round(sy), done)

    # The phone's trackpad: moves arrive as distances, in window pixels.
    def css_pos(self):
        self._ensure_pos()
        return self._css()

    def move_by(self, dx, dy):
        self._ensure_pos()
        nx, ny = self.x + dx, self.y + dy
        self.x, self.y = min(max(nx, 0), self.w - 1), min(max(ny, 0), self.h - 1)
        over_x, over_y = nx - self.x, ny - self.y
        if (over_x or over_y) and not self.dragging:
            self._scroll(over_x * 2, over_y * 2)  # pushing past an edge scrolls, as with the arrows
        self.last_activity = time.monotonic()
        self._moved()
        self.poke()

    def step(self, name, px):
        """A phone arrow: a fixed step, as no key is held."""
        dx, dy = DIRS[name]
        if self.scroll_mode:
            self._scroll(dx * 120, dy * 120)
            self.poke()
        else:
            self.move_by(dx * px * self.unit, dy * px * self.unit)

    def tap(self):
        self.poke()
        if self.scroll_mode:
            self.set_scroll_mode(False)
            return
        self.page.click(*self.css_pos())

    def double_tap(self):
        self.poke()
        self.page.dbl(*self.css_pos())

    def press(self):
        if self.dragging:
            return
        self.poke()
        self.dragging = True
        self.page.down(*self.css_pos())
        self.queue_draw()

    def release(self):
        if self.dragging:
            self._end_drag()
            self.queue_draw()

    # Game pad sticks, already through the dead zone: -1..1 each.
    def set_aim(self, x, y):
        self.aim = (x, y)
        if x or y:
            self.poke()

    def set_wheel(self, x, y):
        self.wheel = (x, y)
        if x or y:
            self.poke()

    def _draw(self, _area, cr, width, height):
        if not self.visible or self.x < 0:
            return
        k = self.size_factor
        r = max(width / 132, 6) * k
        if self.ok_held or self.dragging:
            r += 4 * k
        if self.scroll_mode:
            # Scroll mode: arrows above and below a ring.
            cr.set_source_rgba(1, 1, 1, 0.95)
            cr.set_line_width(4 * k)
            cr.arc(self.x, self.y, r * 1.6, 0, 2 * math.pi)
            cr.stroke()
            for s in (-1, 1):
                cr.move_to(self.x - r, self.y + s * r * 2.2)
                cr.line_to(self.x, self.y + s * r * 3.2)
                cr.line_to(self.x + r, self.y + s * r * 2.2)
                cr.close_path()
                cr.fill()
            return
        cr.arc(self.x, self.y, r, 0, 2 * math.pi)
        cr.set_source_rgba(0x57 / 255, 0x7F / 255, 1, 0.95)
        cr.fill_preserve()
        cr.set_source_rgba(0, 0, 0, 0.6)
        cr.set_line_width(6 * k)
        cr.stroke_preserve()
        cr.set_source_rgba(1, 1, 1, 1)
        cr.set_line_width(3 * k)
        cr.stroke()
