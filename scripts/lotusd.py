#!/usr/bin/env python3
"""Drive Framework 16 LED matrix modules and emit a matching 9x34 preview.

Talks to the Omarchy plugin over newline-delimited JSON on stdin/stdout.
No third-party Python packages — stdlib only.
"""

from __future__ import annotations

import argparse
import array
import base64
import fcntl
import json
import math
import os
import select
import stat
import sys
import termios
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

WIDTH = 9
HEIGHT = 34
PREVIEW_RAW_BYTES = WIDTH * HEIGHT
MAGIC = bytes((0x32, 0xAC))
CMD_BRIGHTNESS = 0x00
CMD_PATTERN = 0x01
CMD_SLEEP = 0x03
CMD_ANIMATE = 0x04
CMD_STAGE_COL = 0x07
CMD_FLUSH_COLS = 0x08
VID = "32ac"
PID = "0020"
FPS = 20
PREVIEW_HZ = 10
KEEPALIVE_S = 25.0
DISCOVER_S = 2.0
VERSION = 1
MAX_DEVICES = 4
MAX_SYSFS_FIELD = 64
MAX_LINE_BYTES = 8192
MAX_STDIN_BUF = 16384
MAX_SETTINGS_BYTES = 4096
SERIAL_IO_S = 0.4
SETTINGS_NAME = "lotus.json"

MODES = (
    "auto",
    "clock",
    "battery",
    "spaces",
    "lotus",
    "word",
    "trek",
    "chomp",
    "rain",
    "meter",
    "breathe",
    "off",
)

# 4x7 glyphs. Seven letters at 4px + 1px gaps fill the 34-long edge exactly.
DIGITS = {
    "0": ("0110", "1001", "1001", "1001", "1001", "1001", "0110"),
    "1": ("0010", "0110", "0010", "0010", "0010", "0010", "0111"),
    "2": ("0110", "1001", "0001", "0010", "0100", "1000", "1111"),
    "3": ("1110", "0001", "0001", "0110", "0001", "0001", "1110"),
    "4": ("0001", "0011", "0101", "1001", "1111", "0001", "0001"),
    "5": ("1111", "1000", "1110", "0001", "0001", "1001", "0110"),
    "6": ("0110", "1000", "1000", "1110", "1001", "1001", "0110"),
    "7": ("1111", "0001", "0010", "0010", "0100", "0100", "0100"),
    "8": ("0110", "1001", "1001", "0110", "1001", "1001", "0110"),
    "9": ("0110", "1001", "1001", "0111", "0001", "0001", "0110"),
    " ": ("0000", "0000", "0000", "0000", "0000", "0000", "0000"),
    "-": ("0000", "0000", "0000", "1111", "0000", "0000", "0000"),
    "A": ("0110", "1001", "1001", "1111", "1001", "1001", "1001"),
    "C": ("0110", "1001", "1000", "1000", "1000", "1001", "0110"),
    "H": ("1001", "1001", "1001", "1111", "1001", "1001", "1001"),
    "M": ("1001", "1101", "1011", "1001", "1001", "1001", "1001"),
    "O": ("0110", "1001", "1001", "1001", "1001", "1001", "0110"),
    "R": ("1110", "1001", "1001", "1110", "1010", "1001", "1001"),
    "Y": ("1001", "1001", "1001", "0110", "0010", "0010", "0010"),
}


def blank(value: int = 0) -> list[list[int]]:
    return [[int(value) for _ in range(HEIGHT)] for _ in range(WIDTH)]


def clamp(n: float, lo: float, hi: float) -> float:
    return lo if n < lo else hi if n > hi else n


def new_grid() -> list[list[int]]:
    return blank(0)


def set_px(grid: list[list[int]], x: int, y: int, value: int) -> None:
    if 0 <= x < WIDTH and 0 <= y < HEIGHT:
        grid[x][y] = int(clamp(value, 0, 255))


def add_px(grid: list[list[int]], x: int, y: int, value: int) -> None:
    if 0 <= x < WIDTH and 0 <= y < HEIGHT:
        grid[x][y] = int(clamp(grid[x][y] + value, 0, 255))


def blit_digit(grid: list[list[int]], ch: str, ox: int, oy: int, value: int = 220) -> None:
    rows = DIGITS.get(ch, DIGITS[" "])
    for dy, row in enumerate(rows):
        for dx, bit in enumerate(row):
            if bit == "1":
                set_px(grid, ox + dx, oy + dy, value)


def blit_pair(grid: list[list[int]], text: str, oy: int, value: int = 220) -> None:
    text = (text + "  ")[:2]
    blit_digit(grid, text[0], 0, oy, value)
    blit_digit(grid, text[1], 5, oy, value)


def blit_rotated(grid: list[list[int]], lx: int, ly: int, value: int) -> None:
    """Logical 34×9 (lx along the long edge, ly=0 at the top of the letters).

    The module is 9×34 portrait; treating the right edge as top maps
    logical (lx, ly) → physical (8 - ly, lx).
    """
    set_px(grid, (WIDTH - 1) - ly, lx, value)


# 5×8 walkers, top row first. ly=0 is the top of the sprite (module right).
WALK_A = (
    ".##..",
    "####.",
    ".##..",
    "#####",
    ".#.#.",
    ".##..",
    "#..#.",
    "##..#",
)
WALK_B = (
    ".##..",
    "####.",
    ".##..",
    "#####",
    ".#.#.",
    ".##..",
    ".#..#",
    ".#.##",
)
WALK_TONE = (240, 210, 190, 175, 160, 150, 145, 140)
GROUND_TONE = 70
HILL_TONE = 115
# Looping skyline: 1 = floor, 2 = step. Length 34 so one screen is one loop.
TREK_WORLD = (
    1, 1, 1, 1, 1, 1, 1, 2, 2, 2,
    1, 1, 1, 1, 2, 2, 1, 1, 1, 1,
    1, 1, 2, 2, 2, 2, 1, 1, 1, 1,
    1, 1, 1, 1,
)
WALKER_X = 6

# 7×7 disc, facing +lx, mouth on the right. Closed / half / open.
CHOMP_FRAMES = (
    (
        "..###..",
        ".#####.",
        "#######",
        "#######",
        "#######",
        ".#####.",
        "..###..",
    ),
    (
        "..###..",
        ".#####.",
        "######.",
        "#####..",
        "######.",
        ".#####.",
        "..###..",
    ),
    (
        "..###..",
        ".#####.",
        "#####..",
        "###....",
        "#####..",
        ".#####.",
        "..###..",
    ),
)
CHOMP_X = 3
CHOMP_TOP = 1
CHOMP_BODY = 230
PELLET = 90
POWER_PELLET = 220
DOT_LANE = 4


def blit_word(grid: list[list[int]], text: str, value: int = 230) -> None:
    """Draw a 4×7 caps word along the long edge. 7 letters + 6 gaps = 34."""
    text = text.upper()
    pad = (WIDTH - 7) // 2
    for i, ch in enumerate(text):
        rows = DIGITS.get(ch, DIGITS[" "])
        ox = i * 5
        for dy, row in enumerate(rows):
            for dx, bit in enumerate(row):
                if bit == "1":
                    blit_rotated(grid, ox + dx, pad + dy, value)


def encode_preview(grid: list[list[int]]) -> str:
    raw = bytes(grid[x][y] for y in range(HEIGHT) for x in range(WIDTH))
    return base64.b64encode(raw).decode("ascii")


def decode_preview(b64: str) -> list[list[int]]:
    raw = base64.b64decode(b64, validate=True)
    if len(raw) != PREVIEW_RAW_BYTES:
        raise ValueError("preview must be exactly 9x34 bytes")
    grid = new_grid()
    i = 0
    for y in range(HEIGHT):
        for x in range(WIDTH):
            grid[x][y] = raw[i]
            i += 1
    return grid


def packet(command: int, params: bytes | list[int] = b"") -> bytes:
    return MAGIC + bytes((command,)) + bytes(params)


def brightness_packet(level: int) -> bytes:
    return packet(CMD_BRIGHTNESS, [int(clamp(level, 0, 255))])


def sleep_packet(sleeping: bool) -> bytes:
    return packet(CMD_SLEEP, [1 if sleeping else 0])


def animate_packet(on: bool) -> bytes:
    return packet(CMD_ANIMATE, [1 if on else 0])


def greyscale_packets(grid: list[list[int]]) -> list[bytes]:
    """One USB packet per command.

    The firmware reads at most 64 bytes per poll and only parses a command
    if it starts at byte 0 of that read. A concatenated 346-byte frame is
    split on 64-byte boundaries, so columns 1–8 never land.
    """
    packets: list[bytes] = []
    for x in range(WIDTH):
        col = [int(clamp(v, 0, 255)) for v in grid[x]]
        packets.append(packet(CMD_STAGE_COL, [x] + col))
    packets.append(packet(CMD_FLUSH_COLS, [0x00]))
    return packets


def local_now(ts: float | None = None):
    return time.localtime(time.time() if ts is None else ts)


# --- modes -----------------------------------------------------------------

def render_clock(state: "State", ts: float) -> list[list[int]]:
    grid = new_grid()
    lt = local_now(ts)
    blit_pair(grid, f"{lt.tm_hour:02d}", 2, 230)
    # colon
    on = (lt.tm_sec % 2) == 0
    colon = 200 if on else 40
    set_px(grid, 4, 11, colon)
    set_px(grid, 4, 13, colon)
    blit_pair(grid, f"{lt.tm_min:02d}", 16, 230)
    # seconds as a 9-wide bar
    sec_w = int(round((lt.tm_sec / 59.0) * (WIDTH - 1))) if lt.tm_sec else 0
    for x in range(WIDTH):
        set_px(grid, x, 25, 80 if x <= sec_w else 18)
    # battery ribbon
    batt = state.battery
    if batt is not None:
        fill = int(round((clamp(batt, 0, 100) / 100.0) * WIDTH))
        glow = 160 if not state.charging else 110 + int(70 * (0.5 + 0.5 * math.sin(ts * 3)))
        for x in range(WIDTH):
            set_px(grid, x, 32, glow if x < fill else 22)
    return grid


def render_battery(state: "State", ts: float) -> list[list[int]]:
    grid = new_grid()
    batt = 0 if state.battery is None else int(clamp(state.battery, 0, 100))
    blit_pair(grid, f"{batt:02d}" if batt < 100 else "99", 1, 220)
    # body
    level = batt / 100.0
    inner_top, inner_bot = 11, 31
    span = inner_bot - inner_top
    filled = int(round(level * span))
    shimmer = 0
    if state.charging:
        shimmer = int(40 * (0.5 + 0.5 * math.sin(ts * 4)))
    for y in range(inner_top, inner_bot + 1):
        from_bottom = inner_bot - y
        lit = from_bottom < filled
        for x in range(2, 7):
            if x in (2, 6) or y in (inner_top, inner_bot):
                set_px(grid, x, y, 70)
            elif lit:
                set_px(grid, x, y, 200 + shimmer)
    # cap
    for x in range(3, 6):
        set_px(grid, x, 10, 90)
    return grid


def render_spaces(state: "State", ts: float) -> list[list[int]]:
    grid = new_grid()
    spaces = state.workspaces or [{"id": i, "occupied": False, "focused": i == 1} for i in range(1, 6)]
    spaces = [w for w in spaces if int(w.get("id", 0)) > 0][:10]
    if not spaces:
        return render_lotus(state, ts)
    # two columns of up to 5, each cell 4 tall
    for i, ws in enumerate(spaces):
        col = 1 if i < 5 else 5
        row = (i % 5) * 6 + 2
        focused = bool(ws.get("focused"))
        occupied = bool(ws.get("occupied"))
        if focused:
            fill, border, halo = 255, 255, 70
        elif occupied:
            fill, border, halo = 0, 55, 0
        else:
            fill, border, halo = 0, 18, 0
        for x in range(col, col + 3):
            for y in range(row, row + 4):
                edge = x in (col, col + 2) or y in (row, row + 3)
                set_px(grid, x, y, border if edge else fill)
        if halo:
            for x in range(col - 1, col + 4):
                set_px(grid, x, row - 1, halo)
                set_px(grid, x, row + 4, halo)
            for y in range(row, row + 4):
                set_px(grid, col - 1, y, halo)
                set_px(grid, col + 3, y, halo)
    return grid


def render_word(_state: "State", ts: float) -> list[list[int]]:
    grid = new_grid()
    breath = 0.82 + 0.18 * math.sin(ts * 1.15)
    blit_word(grid, "OMARCHY", int(230 * breath))
    return grid


def render_lotus(state: "State", ts: float) -> list[list[int]]:
    grid = new_grid()
    breath = 0.55 + 0.45 * math.sin(ts * 1.3)
    cx, cy = 4.0, 11.0
    for y in range(HEIGHT):
        for x in range(WIDTH):
            dx = x - cx
            dy = (y - cy) * 0.72
            r = math.hypot(dx, dy)
            ang = math.atan2(dy, dx)
            petals = 0.5 + 0.5 * math.cos(ang * 5)
            bloom = math.exp(-((r - (1.6 + 1.1 * petals)) ** 2) / 1.35)
            core = math.exp(-(r ** 2) / 1.8)
            stem = 0.0
            if abs(dx) < 0.8 and y > 14:
                stem = max(0.0, 1.0 - abs(dx) * 1.6) * max(0.0, 1.0 - (y - 14) / 20.0)
            v = (bloom * 210 + core * 80 + stem * 90) * breath
            if v > 8:
                set_px(grid, x, y, int(clamp(v, 0, 255)))
    return grid


def render_breathe(_state: "State", ts: float) -> list[list[int]]:
    grid = new_grid()
    wave = 0.15 + 0.85 * (0.5 + 0.5 * math.sin(ts * 1.1))
    for y in range(HEIGHT):
        row = 255 * wave * (0.35 + 0.65 * (1.0 - y / (HEIGHT - 1)))
        for x in range(WIDTH):
            set_px(grid, x, y, int(row))
    return grid


def render_notify(state: "State", ts: float) -> list[list[int]]:
    grid = new_grid()
    remaining = max(0.0, state.notify_until - ts)
    flash = 0.5 + 0.5 * math.sin(ts * 14)
    level = int(255 * flash * min(1.0, remaining / 0.4))
    for x in range(WIDTH):
        for y in range(HEIGHT):
            set_px(grid, x, y, level)
    return grid


@dataclass
class Drop:
    y: float
    speed: float
    length: int
    bright: int


class Trek:
    def __init__(self) -> None:
        self.scroll = 0.0
        self.phase = 0.0

    def step(self, dt: float) -> list[list[int]]:
        self.scroll = (self.scroll + dt * 10.0) % len(TREK_WORLD)
        self.phase += dt * 7.0
        return render_trek(self.scroll, self.phase)


def terrain_at(scroll: float, lx: int) -> int:
    i = (int(scroll) + lx) % len(TREK_WORLD)
    return TREK_WORLD[i]


def render_trek(scroll: float, phase: float) -> list[list[int]]:
    grid = new_grid()
    for lx in range(HEIGHT):
        height = terrain_at(scroll, lx)
        blit_rotated(grid, lx, 8, GROUND_TONE)
        if height >= 2:
            blit_rotated(grid, lx, 7, HILL_TONE)
    stand = terrain_at(scroll, WALKER_X)
    foot = 8 - stand
    frames = WALK_A if int(phase) % 2 == 0 else WALK_B
    for dy, row in enumerate(frames):
        ly = foot - (len(frames) - 1 - dy)
        tone = WALK_TONE[dy] if dy < len(WALK_TONE) else 160
        for dx, bit in enumerate(row):
            if bit == "#":
                blit_rotated(grid, WALKER_X + dx, ly, tone)
    return grid


class Chomp:
    def __init__(self) -> None:
        self.scroll = 0.0
        self.phase = 0.0

    def step(self, dt: float) -> list[list[int]]:
        self.scroll = (self.scroll + dt * 12.0) % 24.0
        self.phase += dt * 10.0
        return render_chomp(self.scroll, self.phase)


def render_chomp(scroll: float, phase: float) -> list[list[int]]:
    grid = new_grid()
    mouth = CHOMP_X + 6
    for lx in range(mouth + 1, HEIGHT):
        world = int(scroll) + lx
        if world % 12 == 0:
            blit_rotated(grid, lx, DOT_LANE, POWER_PELLET)
            blit_rotated(grid, lx, DOT_LANE - 1, POWER_PELLET)
            blit_rotated(grid, lx, DOT_LANE + 1, POWER_PELLET)
        elif world % 3 == 0:
            blit_rotated(grid, lx, DOT_LANE, PELLET)
    # 0,1,2,1,0,1,2… so the mouth snaps shut
    cycle = int(phase) % 4
    frame = (0, 1, 2, 1)[cycle]
    for dy, row in enumerate(CHOMP_FRAMES[frame]):
        for dx, bit in enumerate(row):
            if bit == "#":
                blit_rotated(grid, CHOMP_X + dx, CHOMP_TOP + dy, CHOMP_BODY)
    return grid


class Rain:
    def __init__(self, rng: Callable[[], float] | None = None) -> None:
        self.rng = rng or (lambda: os.urandom(1)[0] / 255.0)
        self.drops: list[Drop | None] = [None] * WIDTH

    def _spawn(self) -> Drop:
        r = self.rng
        return Drop(
            y=-(r() * 12),
            speed=0.35 + r() * 0.85,
            length=4 + int(r() * 8),
            bright=160 + int(r() * 95),
        )

    def step(self, dt: float) -> list[list[int]]:
        grid = new_grid()
        for x in range(WIDTH):
            drop = self.drops[x]
            if drop is None:
                if self.rng() < 0.08:
                    drop = self._spawn()
                    self.drops[x] = drop
                else:
                    continue
            drop.y += drop.speed * dt * 18
            head = drop.y
            for i in range(drop.length):
                yy = int(head) - i
                fade = 1.0 - i / max(1, drop.length)
                set_px(grid, x, yy, int(drop.bright * fade))
            if head - drop.length > HEIGHT + 2:
                self.drops[x] = None if self.rng() < 0.7 else self._spawn()
        return grid


def read_cpu_times() -> list[tuple[int, int]] | None:
    try:
        lines = Path("/proc/stat").read_text().splitlines()
    except OSError:
        return None
    cores: list[tuple[int, int]] = []
    for line in lines:
        if not line.startswith("cpu") or line.startswith("cpu "):
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        nums = [int(p) for p in parts[1:]]
        total = sum(nums)
        idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
        cores.append((total, idle))
        if len(cores) >= 16:
            break
    return cores or None


class Meter:
    def __init__(self) -> None:
        self.prev: list[tuple[int, int]] | None = None
        self.levels = [0.0] * WIDTH
        self.peaks = [0.0] * WIDTH

    def step(self, dt: float, reader: Callable[[], list[tuple[int, int]] | None] | None = None) -> list[list[int]]:
        reader = reader or read_cpu_times
        cores = reader()
        target = [0.12] * WIDTH
        if cores and self.prev and len(cores) == len(self.prev):
            loads: list[float] = []
            for (t1, i1), (t0, i0) in zip(cores, self.prev):
                dtot = t1 - t0
                didle = i1 - i0
                loads.append(0.0 if dtot <= 0 else clamp(1.0 - didle / dtot, 0.0, 1.0))
            if loads:
                for x in range(WIDTH):
                    src = loads[int(x * len(loads) / WIDTH)]
                    target[x] = src
        self.prev = cores
        grid = new_grid()
        for x in range(WIDTH):
            self.levels[x] += (target[x] - self.levels[x]) * min(1.0, dt * 8)
            if self.levels[x] > self.peaks[x]:
                self.peaks[x] = self.levels[x]
            else:
                self.peaks[x] = max(0.0, self.peaks[x] - dt * 0.35)
            h = int(round(self.levels[x] * (HEIGHT - 1)))
            peak_y = HEIGHT - 1 - int(round(self.peaks[x] * (HEIGHT - 1)))
            for y in range(HEIGHT):
                from_bottom = HEIGHT - 1 - y
                if from_bottom <= h:
                    frac = from_bottom / max(1, h)
                    set_px(grid, x, y, int(40 + 200 * frac))
            set_px(grid, x, peak_y, 255)
        return grid


# --- state / engine --------------------------------------------------------

@dataclass
class State:
    mode: str = "auto"
    power: bool = True
    brightness: int = 96
    idle: bool = False
    locked: bool = False
    battery: int | None = None
    charging: bool = False
    workspaces: list[dict] = field(default_factory=list)
    notify_until: float = 0.0
    playing: bool = False
    sleep_locked: bool = True
    idle_art: str = "rain"
    low_battery: int = 15


def pick_mode(state: State, ts: float) -> str:
    if not state.power or state.mode == "off":
        return "off"
    if state.mode != "auto":
        return state.mode if state.mode in MODES else "clock"
    if ts < state.notify_until:
        return "notify"
    if state.locked and state.sleep_locked:
        return "off"
    if state.idle:
        return state.idle_art if state.idle_art in MODES else "rain"
    if state.battery is not None and state.battery <= state.low_battery and not state.charging:
        return "battery"
    if state.playing:
        return "meter"
    return "clock"


def apply_message(state: State, msg: dict, ts: float) -> State:
    op = msg.get("op") or msg.get("cmd") or "state"
    if op not in ("state", "notify", "ping"):
        return state
    if "mode" in msg and isinstance(msg["mode"], str):
        mode = msg["mode"].strip().lower()
        if mode in MODES:
            state.mode = mode
    if "power" in msg:
        state.power = bool(msg["power"])
    if "brightness" in msg:
        state.brightness = int(clamp(int(msg["brightness"]), 10, 255))
    if "idle" in msg:
        state.idle = bool(msg["idle"])
    if "locked" in msg:
        state.locked = bool(msg["locked"])
    if "battery" in msg:
        b = msg["battery"]
        state.battery = None if b is None else int(clamp(int(b), 0, 100))
    if "charging" in msg:
        state.charging = bool(msg["charging"])
    if "workspaces" in msg and isinstance(msg["workspaces"], list):
        cleaned = []
        for item in msg["workspaces"]:
            if not isinstance(item, dict):
                continue
            wid = int(item.get("id") or 0)
            if wid < 1 or wid > 10:
                continue
            cleaned.append(
                {
                    "id": wid,
                    "occupied": bool(item.get("occupied")),
                    "focused": bool(item.get("focused")),
                }
            )
        state.workspaces = cleaned[:10]
    if "playing" in msg:
        state.playing = bool(msg["playing"])
    if "sleepLocked" in msg:
        state.sleep_locked = bool(msg["sleepLocked"])
    if "idleArt" in msg and isinstance(msg["idleArt"], str) and msg["idleArt"] in MODES:
        state.idle_art = msg["idleArt"]
    if "lowBattery" in msg:
        state.low_battery = int(clamp(int(msg["lowBattery"]), 1, 50))
    if op == "notify" or msg.get("notify"):
        dur = float(msg.get("duration", 1.6))
        state.notify_until = ts + max(0.2, dur)
    return state


def render(
    state: State,
    ts: float,
    rain: Rain,
    meter: Meter,
    trek: Trek,
    chomp: Chomp,
    dt: float,
) -> tuple[str, list[list[int]]]:
    mode = pick_mode(state, ts)
    if mode == "off":
        return mode, new_grid()
    if mode == "clock":
        return mode, render_clock(state, ts)
    if mode == "battery":
        return mode, render_battery(state, ts)
    if mode == "spaces":
        return mode, render_spaces(state, ts)
    if mode == "lotus":
        return mode, render_lotus(state, ts)
    if mode == "word":
        return mode, render_word(state, ts)
    if mode == "trek":
        return mode, trek.step(dt)
    if mode == "chomp":
        return mode, chomp.step(dt)
    if mode == "rain":
        return mode, rain.step(dt)
    if mode == "meter":
        return mode, meter.step(dt)
    if mode == "breathe":
        return mode, render_breathe(state, ts)
    if mode == "notify":
        return mode, render_notify(state, ts)
    return "clock", render_clock(state, ts)


# --- devices ---------------------------------------------------------------

@dataclass
class Device:
    path: str
    panel: str
    serial: str
    fd: int | None = None
    last_brightness: int | None = None
    sleeping: bool | None = None
    last_frame: tuple[bytes, ...] | None = None
    last_write: float = 0.0

    def as_dict(self) -> dict:
        return {"path": self.path, "panel": self.panel, "serial": self.serial}


def read_text(path: Path, limit: int = MAX_SYSFS_FIELD) -> str:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError:
        return ""
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return ""
        data = os.read(fd, limit)
        return data.decode("utf-8", "replace").strip()[:limit]
    except OSError:
        return ""
    finally:
        os.close(fd)


def usb_device_dir(tty_name: str) -> Path | None:
    start = Path(f"/sys/class/tty/{tty_name}/device")
    try:
        p = start.resolve()
    except OSError:
        return None
    for _ in range(8):
        if not p.exists() or p == p.parent:
            return None
        if (p / "idVendor").exists() and (p / "idProduct").exists():
            return p
        p = p.parent
    return None


def discover_devices() -> list[dict]:
    found: list[dict] = []
    dev = Path("/dev")
    if not dev.exists():
        return found
    for entry in sorted(dev.iterdir()):
        name = entry.name
        if not name.startswith("ttyACM"):
            continue
        usb = usb_device_dir(name)
        if usb is None:
            continue
        if read_text(usb / "idVendor").lower() != VID:
            continue
        if read_text(usb / "idProduct").lower() != PID:
            continue
        panel = read_text(usb / "physical_location" / "panel") or "unknown"
        serial = read_text(usb / "serial")
        found.append(
            {
                "path": str(entry)[:MAX_SYSFS_FIELD],
                "panel": panel[:MAX_SYSFS_FIELD],
                "serial": serial[:MAX_SYSFS_FIELD],
            }
        )
        if len(found) >= MAX_DEVICES:
            break
    return found


def configure_serial(fd: int) -> None:
    attrs = termios.tcgetattr(fd)
    attrs[0] = 0
    attrs[1] = 0
    attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    attrs[3] = 0
    attrs[4] = termios.B115200
    attrs[5] = termios.B115200
    cc = list(attrs[6])
    cc[termios.VMIN] = 0
    cc[termios.VTIME] = 0
    attrs[6] = cc
    termios.tcsetattr(fd, termios.TCSANOW, attrs)


def open_device(path: str) -> int:
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        configure_serial(fd)
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        return fd
    except OSError:
        os.close(fd)
        raise


def _deadline_remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("serial I/O timeout")
    return remaining


def drain_output(fd: int, deadline: float) -> None:
    queued = array.array("i", [0])
    while True:
        remaining = _deadline_remaining(deadline)
        try:
            fcntl.ioctl(fd, termios.TIOCOUTQ, queued, True)
        except OSError:
            return
        if queued[0] <= 0:
            return
        select.select([], [fd], [], min(0.02, remaining))


def write_all(fd: int, data: bytes, timeout: float = SERIAL_IO_S) -> None:
    deadline = time.monotonic() + timeout
    view = memoryview(data)
    while view:
        remaining = _deadline_remaining(deadline)
        _, writable, _ = select.select([], [fd], [], remaining)
        if not writable:
            raise TimeoutError("serial write timeout")
        try:
            n = os.write(fd, view)
        except BlockingIOError:
            continue
        if n <= 0:
            raise OSError("short write")
        view = view[n:]
    drain_output(fd, deadline)


def state_dir() -> Path:
    override = os.environ.get("LOTUS_STATE_DIR")
    if override:
        return Path(override)
    home = os.environ.get("HOME") or ""
    base = os.environ.get("XDG_STATE_HOME") or str(Path(home) / ".local" / "state")
    return Path(base) / "omarchy"


def ensure_state_dir() -> Path:
    path = state_dir()
    uid = os.getuid()
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode) or st.st_uid != uid:
        raise OSError("untrusted state directory")
    os.chmod(path, 0o700)
    return path


def _open_state_dir() -> int:
    path = ensure_state_dir()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    st = os.fstat(fd)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        os.close(fd)
        raise OSError("untrusted state directory")
    return fd


def _read_bounded(fd: int, max_bytes: int, timeout: float = 0.2) -> bytes:
    deadline = time.monotonic() + timeout
    data = bytearray()
    while len(data) <= max_bytes:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        readable, _, _ = select.select([fd], [], [], remaining)
        if not readable:
            break
        try:
            chunk = os.read(fd, min(1024, max_bytes + 1 - len(data)))
        except BlockingIOError:
            continue
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > max_bytes:
            raise OSError("settings file too large")
    return bytes(data)


def load_settings_bytes() -> bytes | None:
    dirfd = _open_state_dir()
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(SETTINGS_NAME, flags, dir_fd=dirfd)
    except FileNotFoundError:
        os.close(dirfd)
        return None
    except OSError:
        os.close(dirfd)
        raise
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
            raise OSError("untrusted settings file")
        if st.st_size > MAX_SETTINGS_BYTES:
            raise OSError("settings file too large")
        return _read_bounded(fd, MAX_SETTINGS_BYTES)
    finally:
        os.close(fd)
        os.close(dirfd)


def save_settings_bytes(data: bytes) -> None:
    if len(data) > MAX_SETTINGS_BYTES:
        raise OSError("settings payload too large")
    dirfd = _open_state_dir()
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    tmp = "." + SETTINGS_NAME + ".tmp"
    try:
        fd = os.open(tmp, flags, 0o600, dir_fd=dirfd)
        try:
            view = memoryview(data)
            while view:
                n = os.write(fd, view)
                if n <= 0:
                    raise OSError("short write")
                view = view[n:]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, SETTINGS_NAME, src_dir_fd=dirfd, dst_dir_fd=dirfd)
    finally:
        os.close(dirfd)


def read_stdin_line(max_bytes: int = MAX_SETTINGS_BYTES, timeout: float = 2.0) -> bytes:
    fd = sys.stdin.fileno()
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    deadline = time.monotonic() + timeout
    buf = bytearray()
    while len(buf) <= max_bytes:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        readable, _, _ = select.select([fd], [], [], remaining)
        if not readable:
            break
        chunk = os.read(fd, 1024)
        if not chunk:
            break
        buf.extend(chunk)
        if b"\n" in buf:
            break
    if len(buf) > max_bytes:
        raise OSError("settings payload too large")
    return bytes(buf).split(b"\n", 1)[0]


def cmd_load_settings() -> int:
    try:
        data = load_settings_bytes()
    except OSError:
        return 1
    if data:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
    return 0


def cmd_save_settings() -> int:
    try:
        data = read_stdin_line()
        save_settings_bytes(data)
    except OSError:
        return 1
    return 0


class Hardware:
    def __init__(self) -> None:
        self.devices: list[Device] = []
        self.permission_error = False
        self.last_discover = 0.0

    def refresh(self, ts: float, force: bool = False) -> bool:
        if not force and ts - self.last_discover < DISCOVER_S:
            return False
        self.last_discover = ts
        wanted = discover_devices()
        changed = [d.as_dict() for d in self.devices] != wanted
        keep_by_path = {d.path: d for d in self.devices}
        next_devs: list[Device] = []
        self.permission_error = False
        for info in wanted:
            existing = keep_by_path.get(info["path"])
            if existing and existing.fd is not None:
                existing.panel = info["panel"]
                existing.serial = info["serial"]
                next_devs.append(existing)
                continue
            if existing:
                self._close(existing)
            dev = Device(path=info["path"], panel=info["panel"], serial=info["serial"])
            try:
                dev.fd = open_device(dev.path)
            except PermissionError:
                self.permission_error = True
                next_devs.append(dev)
            except OSError:
                next_devs.append(dev)
            else:
                try:
                    write_all(dev.fd, sleep_packet(False))
                    write_all(dev.fd, animate_packet(False))
                    dev.sleeping = False
                except OSError:
                    self._close(dev)
                next_devs.append(dev)
        for old in self.devices:
            if old.path not in {d["path"] for d in wanted}:
                self._close(old)
        self.devices = next_devs
        if any(d.fd is None and Path(d.path).exists() for d in self.devices):
            # present but unopened — likely permissions
            if not any(d.fd is not None for d in self.devices):
                self.permission_error = True
        return changed

    def _close(self, dev: Device) -> None:
        if dev.fd is not None:
            try:
                os.close(dev.fd)
            except OSError:
                pass
            dev.fd = None

    def close_all(self) -> None:
        for d in self.devices:
            self._close(d)
        self.devices = []

    def push(self, grid: list[list[int]], brightness: int, sleeping: bool, ts: float) -> None:
        frame = () if sleeping else tuple(greyscale_packets(grid))
        for dev in self.devices:
            if dev.fd is None:
                continue
            try:
                if sleeping:
                    if dev.sleeping is not True:
                        write_all(dev.fd, sleep_packet(True))
                        dev.sleeping = True
                        dev.last_write = ts
                    continue
                if dev.sleeping is not False:
                    write_all(dev.fd, sleep_packet(False))
                    dev.sleeping = False
                    dev.last_brightness = None
                    dev.last_frame = None
                if dev.last_brightness != brightness:
                    write_all(dev.fd, brightness_packet(brightness))
                    dev.last_brightness = brightness
                if frame != dev.last_frame or ts - dev.last_write > KEEPALIVE_S:
                    for pkt in frame:
                        write_all(dev.fd, pkt)
                    dev.last_frame = frame
                    dev.last_write = ts
            except OSError:
                self._close(dev)


# --- daemon loop -----------------------------------------------------------

def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def status_payload(hw: Hardware, mode: str, state: State) -> dict:
    return {
        "type": "status",
        "version": VERSION,
        "mode": mode,
        "wanted": state.mode,
        "power": state.power,
        "brightness": state.brightness,
        "permission": not hw.permission_error,
        "devices": [d.as_dict() | {"open": d.fd is not None} for d in hw.devices],
    }


def run_daemon() -> int:
    state = State()
    rain = Rain()
    meter = Meter()
    trek = Trek()
    chomp = Chomp()
    hw = Hardware()
    buf = ""
    last_tick = time.time()
    last_preview = 0.0
    last_mode = ""
    emit({"type": "hello", "version": VERSION, "modes": list(MODES)})
    try:
        while True:
            timeout = 1.0 / FPS
            ready, _, _ = select.select([sys.stdin], [], [], timeout)
            ts = time.time()
            if ready:
                chunk = os.read(sys.stdin.fileno(), 4096)
                if not chunk:
                    break
                buf += chunk.decode("utf-8", "replace")
                if len(buf) > MAX_STDIN_BUF:
                    nl = buf.find("\n")
                    buf = buf[nl + 1 :] if nl >= 0 else ""
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    if len(line) > MAX_LINE_BYTES:
                        emit({"type": "error", "message": "line too long"})
                        continue
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        emit({"type": "error", "message": "bad json"})
                        continue
                    if not isinstance(msg, dict):
                        continue
                    op = msg.get("op") or msg.get("cmd") or "state"
                    if op == "quit":
                        return 0
                    apply_message(state, msg, ts)
                    hw.refresh(ts, force=True)
                    mode, grid = render(state, ts, rain, meter, trek, chomp, 0.05)
                    emit(status_payload(hw, mode, state))
                    emit({"type": "preview", "w": WIDTH, "h": HEIGHT, "px": encode_preview(grid)})
                    last_preview = ts
            dt = max(0.001, ts - last_tick)
            last_tick = ts
            hw.refresh(ts)
            mode, grid = render(state, ts, rain, meter, trek, chomp, dt)
            sleeping = mode == "off"
            hw.push(grid, state.brightness, sleeping, ts)
            if mode != last_mode:
                last_mode = mode
                emit(status_payload(hw, mode, state))
            if ts - last_preview >= 1.0 / PREVIEW_HZ:
                emit({"type": "preview", "w": WIDTH, "h": HEIGHT, "px": encode_preview(grid)})
                last_preview = ts
    except KeyboardInterrupt:
        return 0
    finally:
        hw.close_all()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Framework 16 LED matrix daemon for Lotus")
    parser.add_argument("--list", action="store_true", help="list LED matrix devices and exit")
    parser.add_argument("--render", choices=MODES, help="print one ASCII frame of a mode")
    parser.add_argument("--load-settings", action="store_true", help="print lotus.json from the private state dir")
    parser.add_argument("--save-settings", action="store_true", help="write one stdin line to lotus.json")
    args = parser.parse_args(argv)
    if args.load_settings:
        return cmd_load_settings()
    if args.save_settings:
        return cmd_save_settings()
    if args.list:
        for d in discover_devices():
            print(f"{d['path']}\tpanel={d['panel']}\tserial={d['serial']}")
        return 0
    if args.render:
        state = State(mode=args.render, battery=67, charging=False, workspaces=[
            {"id": 1, "occupied": True, "focused": True},
            {"id": 2, "occupied": True, "focused": False},
            {"id": 3, "occupied": False, "focused": False},
        ])
        rain = Rain(rng=lambda: 0.3)
        meter = Meter()
        trek = Trek()
        chomp = Chomp()
        mode, grid = render(state, 1.0, rain, meter, trek, chomp, 0.05)
        print(mode)
        for y in range(HEIGHT):
            print("".join(".#"[grid[x][y] > 40] for x in range(WIDTH)))
        return 0
    return run_daemon()


if __name__ == "__main__":
    sys.exit(main())
