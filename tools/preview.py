#!/usr/bin/env python3
"""Write preview.png — a still of the 9x34 clock on a dark plate."""

import struct
import sys
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import lotusd as L  # noqa: E402


def png_rgb(width: int, height: int, rgb: bytes) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    raw = b"".join(b"\x00" + rgb[y * width * 3 : (y + 1) * width * 3] for y in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def main() -> None:
    state = L.State(mode="clock", battery=72, charging=False)
    grid = L.render_clock(state, time.mktime((2026, 9, 1, 14, 32, 0, 0, 0, -1)))
    cell, gap, pad = 14, 4, 48
    cols, rows = L.WIDTH, L.HEIGHT
    w = pad * 2 + cols * cell + (cols - 1) * gap
    h = pad * 2 + rows * cell + (rows - 1) * gap
    bg = (18, 18, 20)
    well = (36, 35, 32)
    led = (244, 241, 234)
    pixels = bytearray(bg * (w * h))

    def put(x: int, y: int, color: tuple[int, int, int]) -> None:
        if 0 <= x < w and 0 <= y < h:
            i = (y * w + x) * 3
            pixels[i : i + 3] = bytes(color)

    for y in range(rows):
        for x in range(cols):
            v = grid[x][y] / 255.0
            ox = pad + x * (cell + gap)
            oy = pad + y * (cell + gap)
            r = int(well[0] + (led[0] - well[0]) * v)
            g = int(well[1] + (led[1] - well[1]) * v)
            b = int(well[2] + (led[2] - well[2]) * v)
            for yy in range(cell):
                for xx in range(cell):
                    put(ox + xx, oy + yy, (r, g, b))

    out = ROOT / "preview.png"
    out.write_bytes(png_rgb(w, h, bytes(pixels)))
    print(out, w, h)


if __name__ == "__main__":
    main()
