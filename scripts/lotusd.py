#!/usr/bin/env python3
"""Drive Framework 16 LED matrix modules and emit a matching 9x34 preview.

Talks to the Omarchy plugin over newline-delimited JSON on stdin/stdout.
No third-party Python packages — stdlib only.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import select
import sys
import termios
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

WIDTH = 9
HEIGHT = 34
MAGIC = bytes((0x32, 0xAC))
CMD_BRIGHTNESS = 0x00
CMD_PATTERN = 0x01
CMD_SLEEP = 0x03
CMD_STAGE_COL = 0x07
CMD_FLUSH_COLS = 0x08
VID = "32ac"
PID = "0020"
FPS = 20
PREVIEW_HZ = 10
KEEPALIVE_S = 25.0
DISCOVER_S = 2.0
VERSION = 1

MODES = (
    "auto",
    "clock",
    "battery",
    "spaces",
    "lotus",
    "rain",
    "meter",
    "breathe",
    "off",
)

# 4x7 digits. Two of them plus a 1-column gap fill the 9-wide matrix.
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


def encode_preview(grid: list[list[int]]) -> str:
    raw = bytes(grid[x][y] for y in range(HEIGHT) for x in range(WIDTH))
    return base64.b64encode(raw).decode("ascii")


def decode_preview(b64: str) -> list[list[int]]:
    raw = base64.b64decode(b64)
    grid = new_grid()
    i = 0
    for y in range(HEIGHT):
        for x in range(WIDTH):
            if i < len(raw):
                grid[x][y] = raw[i]
                i += 1
    return grid


def packet(command: int, params: bytes | list[int] = b"") -> bytes:
    return MAGIC + bytes((command,)) + bytes(params)


def brightness_packet(level: int) -> bytes:
    return packet(CMD_BRIGHTNESS, [int(clamp(level, 0, 255))])


def sleep_packet(sleeping: bool) -> bytes:
    return packet(CMD_SLEEP, [1 if sleeping else 0])


def greyscale_frame(grid: list[list[int]]) -> bytes:
    chunks: list[bytes] = []
    for x in range(WIDTH):
        col = [int(clamp(v, 0, 255)) for v in grid[x]]
        chunks.append(packet(CMD_STAGE_COL, [x] + col))
    chunks.append(packet(CMD_FLUSH_COLS, [0x00]))
    return b"".join(chunks)


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
            fill = 230
            border = 230
        elif occupied:
            fill = 40
            border = 160
        else:
            fill = 0
            border = 45
        for x in range(col, col + 3):
            for y in range(row, row + 4):
                edge = x in (col, col + 2) or y in (row, row + 3)
                set_px(grid, x, y, border if edge else fill)
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
        state.brightness = int(clamp(int(msg["brightness"]), 0, 255))
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
            cleaned.append(
                {
                    "id": int(item.get("id") or 0),
                    "occupied": bool(item.get("occupied")),
                    "focused": bool(item.get("focused")),
                }
            )
        state.workspaces = cleaned
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


def render(state: State, ts: float, rain: Rain, meter: Meter, dt: float) -> tuple[str, list[list[int]]]:
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
    last_frame: bytes | None = None
    last_write: float = 0.0

    def as_dict(self) -> dict:
        return {"path": self.path, "panel": self.panel, "serial": self.serial}


def read_text(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


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
        found.append({"path": str(entry), "panel": panel, "serial": serial})
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
    import fcntl

    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    configure_serial(fd)
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)
    return fd


def write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        if n <= 0:
            raise OSError("short write")
        view = view[n:]


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
        frame = b"" if sleeping else greyscale_frame(grid)
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
                    write_all(dev.fd, frame)
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
                chunk = os.read(sys.stdin.fileno(), 65536)
                if not chunk:
                    break
                buf += chunk.decode("utf-8", "replace")
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
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
                    mode, grid = render(state, ts, rain, meter, 0.05)
                    emit(status_payload(hw, mode, state))
                    emit({"type": "preview", "w": WIDTH, "h": HEIGHT, "px": encode_preview(grid)})
                    last_preview = ts
            dt = max(0.001, ts - last_tick)
            last_tick = ts
            hw.refresh(ts)
            mode, grid = render(state, ts, rain, meter, dt)
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
    args = parser.parse_args(argv)
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
        mode, grid = render(state, 1.0, rain, meter, 0.05)
        print(mode)
        for y in range(HEIGHT):
            print("".join(".#"[grid[x][y] > 40] for x in range(WIDTH)))
        return 0
    return run_daemon()


if __name__ == "__main__":
    sys.exit(main())
