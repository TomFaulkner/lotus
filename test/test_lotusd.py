#!/usr/bin/env python3
import base64
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import lotusd as L  # noqa: E402


class FontTests(unittest.TestCase):
    def test_digits_are_4x7(self):
        for ch, rows in L.DIGITS.items():
            self.assertEqual(len(rows), 7, ch)
            for row in rows:
                self.assertEqual(len(row), 4, ch)

    def test_blit_pair_fits(self):
        grid = L.new_grid()
        L.blit_pair(grid, "18", 0, 200)
        lit = sum(1 for x in range(L.WIDTH) for y in range(7) if grid[x][y])
        self.assertGreater(lit, 10)
        self.assertTrue(any(grid[0][y] or grid[3][y] for y in range(7)))


class DiscoverTests(unittest.TestCase):
    def test_usb_walk_resolves_sysfs_symlinks(self):
        tty = Path("/dev/ttyACM0")
        if not tty.exists():
            self.skipTest("no ttyACM0 on this machine")
        usb = L.usb_device_dir("ttyACM0")
        self.assertIsNotNone(usb)
        self.assertTrue((usb / "idVendor").exists())


class ProtocolTests(unittest.TestCase):
    def test_greyscale_packets_are_one_command_each(self):
        grid = L.blank(7)
        packets = L.greyscale_packets(grid)
        self.assertEqual(len(packets), 10)
        for i in range(9):
            pkt = packets[i]
            self.assertEqual(len(pkt), 3 + 1 + 34)
            self.assertLess(len(pkt), 64)
            self.assertEqual(pkt[:3], bytes((0x32, 0xAC, 0x07)))
            self.assertEqual(pkt[3], i)
            self.assertEqual(pkt[4], 7)
        self.assertEqual(packets[-1], bytes((0x32, 0xAC, 0x08, 0x00)))

    def test_sleep_and_brightness_packets(self):
        self.assertEqual(L.sleep_packet(True), bytes((0x32, 0xAC, 0x03, 1)))
        self.assertEqual(L.sleep_packet(False), bytes((0x32, 0xAC, 0x03, 0)))
        self.assertEqual(L.brightness_packet(96), bytes((0x32, 0xAC, 0x00, 96)))

    def test_preview_roundtrip(self):
        grid = L.new_grid()
        grid[2][10] = 200
        grid[8][33] = 15
        back = L.decode_preview(L.encode_preview(grid))
        self.assertEqual(back, grid)
        self.assertEqual(len(base64.b64decode(L.encode_preview(grid))), 9 * 34)

    def test_preview_rejects_wrong_size(self):
        with self.assertRaises(ValueError):
            L.decode_preview(base64.b64encode(b"short").decode("ascii"))


class AutoModeTests(unittest.TestCase):
    def test_default_is_clock(self):
        self.assertEqual(L.pick_mode(L.State(), 10.0), "clock")

    def test_power_off(self):
        self.assertEqual(L.pick_mode(L.State(power=False), 10.0), "off")

    def test_notify_preempts(self):
        s = L.State(notify_until=12.0, playing=True, idle=True)
        self.assertEqual(L.pick_mode(s, 11.0), "notify")
        self.assertEqual(L.pick_mode(s, 13.0), "rain")

    def test_low_battery(self):
        s = L.State(battery=10, charging=False)
        self.assertEqual(L.pick_mode(s, 1.0), "battery")
        s.charging = True
        self.assertEqual(L.pick_mode(s, 1.0), "clock")

    def test_locked_sleeps(self):
        s = L.State(locked=True, sleep_locked=True)
        self.assertEqual(L.pick_mode(s, 1.0), "off")
        s.sleep_locked = False
        self.assertEqual(L.pick_mode(s, 1.0), "clock")

    def test_manual_mode_wins(self):
        s = L.State(mode="lotus", playing=True, idle=True)
        self.assertEqual(L.pick_mode(s, 1.0), "lotus")

    def test_apply_notify(self):
        s = L.State()
        L.apply_message(s, {"op": "notify", "duration": 2}, 5.0)
        self.assertAlmostEqual(s.notify_until, 7.0)


class RenderTests(unittest.TestCase):
    def test_clock_has_digits(self):
        grid = L.render_clock(L.State(battery=50), 0.0)
        self.assertEqual(len(grid), 9)
        self.assertEqual(len(grid[0]), 34)
        self.assertGreater(sum(grid[0][y] + grid[5][y] for y in range(9)), 0)

    def test_lotus_is_centered(self):
        grid = L.render_lotus(L.State(), 0.4)
        mid = sum(grid[4][y] for y in range(20))
        edge = sum(grid[0][y] for y in range(20))
        self.assertGreater(mid, edge)

    def test_rain_moves(self):
        rain = L.Rain(rng=lambda: 0.0)
        rain.drops[0] = L.Drop(y=0, speed=1, length=5, bright=200)
        a = rain.step(0.1)
        b = rain.step(0.1)
        self.assertNotEqual(a, b)

    def test_meter_from_cpu_reader(self):
        meter = L.Meter()
        seq = iter(
            [
                [(100, 90), (100, 90)],
                [(200, 100), (200, 180)],
            ]
        )
        meter.step(0.2, reader=lambda: next(seq))
        grid = meter.step(0.2, reader=lambda: next(seq))
        lit = sum(1 for x in range(9) for y in range(34) if grid[x][y] > 0)
        self.assertGreater(lit, 0)

    def test_spaces_marks_focus(self):
        state = L.State(
            workspaces=[
                {"id": 1, "occupied": True, "focused": True},
                {"id": 2, "occupied": True, "focused": False},
            ]
        )
        grid = L.render_spaces(state, 0.0)
        focused = grid[2][3]
        occupied = grid[1][8]
        self.assertEqual(focused, 255)
        self.assertLess(occupied, 80)
        self.assertGreater(focused - occupied, 170)

    def test_omarchy_fits_rotated(self):
        grid = L.render_word(L.State(), 0.0)
        self.assertEqual(len(grid), 9)
        self.assertEqual(len(grid[0]), 34)
        # 4px glyphs + 1px gaps, 7 letters: last column of Y is x=33
        self.assertGreater(sum(grid[x][33] for x in range(9)), 0)
        self.assertGreater(sum(grid[x][0] for x in range(9)), 0)
        # 1px padding: physical x=0 (left / bottom of letters) stays dark
        self.assertEqual(sum(grid[0]), 0)
        # Right edge is the top of the letters — padding, so x=8 is also dark
        self.assertEqual(sum(grid[8]), 0)
        body = sum(grid[x][y] for x in range(1, 8) for y in range(34))
        self.assertGreater(body, 20 * 200)

    def test_trek_walker_on_ground(self):
        a = L.render_trek(0.0, 0.0)
        b = L.render_trek(8.0, 1.0)
        self.assertEqual(len(a), 9)
        self.assertEqual(len(a[0]), 34)
        # Ground is physical x=0 (logical bottom)
        self.assertGreater(sum(a[0]), 0)
        # Walker sits above the ground
        self.assertGreater(sum(a[x][L.WALKER_X] for x in range(1, 9)), 0)
        self.assertNotEqual(a, b)

    def test_chomp_eats_ahead_of_mouth(self):
        closed = L.render_chomp(0.0, 0.0)
        opened = L.render_chomp(0.0, 2.0)
        later = L.render_chomp(6.0, 0.0)
        self.assertEqual(len(closed), 9)
        # Body sits above the empty bottom padding
        self.assertGreater(sum(closed[x][L.CHOMP_X + 3] for x in range(9)), 0)
        self.assertNotEqual(closed, opened)
        self.assertNotEqual(closed, later)


class UdevInstallTests(unittest.TestCase):
    def test_service_embeds_constant_rule_bytes(self):
        rule = (ROOT / "udev/50-framework-inputmodule.rules").read_bytes()
        qml = (ROOT / "Service.qml").read_text()
        self.assertIn(rule.hex(), qml)
        self.assertNotIn("udevRulePath", qml)
        self.assertNotIn("/bin/sh", qml)
        self.assertIn('"pkexec", "python3", "-c"', qml)
        self.assertNotIn("install -m 644", qml)
        self.assertNotIn("FileView", qml)
        self.assertIn("--load-settings", qml)
        self.assertIn("watchdogMs", qml)
        self.assertIn("previewBytes: 306", qml)


class SerialIoTests(unittest.TestCase):
    def test_write_all_times_out_on_full_pipe(self):
        r, w = os.pipe()
        os.set_blocking(w, False)
        blob = b"x" * 65536
        try:
            while True:
                os.write(w, blob)
        except BlockingIOError:
            pass
        t0 = time.monotonic()
        with self.assertRaises(TimeoutError):
            L.write_all(w, b"more", timeout=0.05)
        self.assertLess(time.monotonic() - t0, 1.0)
        os.close(r)
        os.close(w)


class SettingsIoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        os.chmod(self.dir, 0o700)
        os.environ["LOTUS_STATE_DIR"] = str(self.dir)

    def tearDown(self):
        os.environ.pop("LOTUS_STATE_DIR", None)
        self.tmp.cleanup()

    def test_roundtrip(self):
        payload = b'{"mode":"clock","power":true}\n'
        L.save_settings_bytes(payload)
        self.assertEqual(L.load_settings_bytes(), payload)

    def test_missing_is_none(self):
        self.assertIsNone(L.load_settings_bytes())

    def test_rejects_oversize(self):
        (self.dir / "lotus.json").write_bytes(b"x" * (L.MAX_SETTINGS_BYTES + 1))
        with self.assertRaises(OSError):
            L.load_settings_bytes()

    def test_rejects_fifo(self):
        os.mkfifo(self.dir / "lotus.json")
        with self.assertRaises(OSError):
            L.load_settings_bytes()

    def test_rejects_symlink(self):
        target = self.dir / "target.json"
        target.write_text("{}")
        os.symlink("target.json", self.dir / "lotus.json")
        with self.assertRaises(OSError):
            L.load_settings_bytes()

    def test_save_ignores_preexisting_tmp_fifo(self):
        fifo = self.dir / ".lotus.json.tmp"
        os.mkfifo(fifo)
        payload = b'{"mode":"clock"}\n'
        L.save_settings_bytes(payload)
        self.assertTrue(stat.S_ISFIFO(os.lstat(fifo).st_mode))
        self.assertTrue(stat.S_ISREG(os.lstat(self.dir / "lotus.json").st_mode))
        self.assertEqual(L.load_settings_bytes(), payload)

    def test_parent_symlink_rejected(self):
        real = self.dir / "real"
        real.mkdir()
        os.chmod(real, 0o700)
        link = self.dir / "link"
        link.symlink_to(real)
        os.environ["LOTUS_STATE_DIR"] = str(link / "omarchy")
        with self.assertRaises(OSError):
            L.save_settings_bytes(b"{}\n")
        with self.assertRaises(OSError):
            L.load_settings_bytes()

    def test_read_text_truncates(self):
        p = self.dir / "field"
        p.write_text("a" * 200)
        self.assertEqual(len(L.read_text(p)), L.MAX_SYSFS_FIELD)

    def test_device_cap(self):
        self.assertEqual(L.MAX_DEVICES, 4)
        self.assertEqual(L.PREVIEW_RAW_BYTES, 306)


if __name__ == "__main__":
    unittest.main()
