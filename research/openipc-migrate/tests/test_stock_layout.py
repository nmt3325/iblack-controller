"""Offline tests for stock_layout.py: geometry, vendor env codec, the vendor U-Boot 2010.06 parser model,
the Linux cmdlinepart model and the probe/trial/final env builders. No hardware, no private input."""
from __future__ import annotations

import contextlib
import io
import json
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sb_fixture as fx  # noqa: E402
from sb_fixture import sl  # noqa: E402

E0 = sl.build_env(fx.E0_ENTRIES)


def cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = sl.main(list(argv))
    return rc, out.getvalue(), err.getvalue()


class Geometry(unittest.TestCase):
    def test_layouts_cover_the_chip_and_keep_uboot_read_only(self):
        p = sl.plan()
        self.assertEqual(sum(x["kib"] for x in p["final_layout"]) * 1024, sl.FLASH_BYTES)
        self.assertEqual(p["final_mtdparts"],
                         "hi_sfc:384k(boot)ro,64k(env),1856k(kernel),4992k(rootfs),896k(rootfs_data)")
        self.assertEqual(p["never_written"]["vendor_uboot"], ["0x0", "0x40000"])
        self.assertEqual(p["overlay_kib"], 896)
        self.assertEqual(p["trial_payload_limit_bytes"], 720_896)
        self.assertEqual(p["trial_kernel_slot_bytes"], 0x1D0000)
        self.assertEqual(sl.FW_OFF + sl.FW_LEN, sl.OVERLAY_OFF)
        self.assertEqual(sl.TP_OFF + sl.TP_LEN, sl.FLASH_BYTES)
        # The OpenIPC env and the trial-log mailbox live in the 0xFF gap the stock layout never uses.
        self.assertLess(sl.SENV_OFF + sl.SENV_LEN, sl.STOCK_KERNEL_OFF)
        self.assertEqual((sl.OIPC_ENV_OFF, sl.TL_OFF), (0x60000, 0x70000))
        self.assertGreaterEqual(sl.OIPC_ENV_OFF, sl.SENV_OFF + sl.SENV_LEN)

    def test_cbsize_margins(self):
        p = sl.plan()
        self.assertEqual((p["trial_bootcmd_chars"], p["probe_bootcmd_chars"], p["final_bootcmd_chars"]),
                         (417, 158, 69))
        self.assertEqual(len(sl.FINAL_BOOTARGS), 174)
        for key in ("trial_bootcmd_chars", "probe_bootcmd_chars", "final_bootcmd_chars"):
            self.assertLess(p[key], sl.CBSIZE)

    def test_weak_bit_mapping(self):
        w = sl.plan()["weak_bit"]
        self.assertEqual(w, {"abs": "0x6c4f28", "stock": "bakfs+0x144f28", "trial": "tk+0x144f28",
                             "final": "rootfs+0x484f28"})

    def test_power_fail_table(self):
        rows = sl.power_fail_table()
        self.assertEqual(len(rows), 10)
        self.assertTrue(all(set(r) == {"step", "if_interrupted"} for r in rows))
        spi = [r["step"] for r in rows if "SPI recovery" in r["if_interrupted"]]
        self.assertTrue(all(s.startswith("final") for s in spi), spi)
        self.assertTrue(any("stock boots" in r["if_interrupted"] for r in rows if r["step"].startswith("trial")))

    def test_firmware_plan(self):
        fw = fx.firmware()
        f = sl.plan(fw)["firmware"]
        self.assertEqual(f["kernel_uimage_bytes"], 64 + 1_864_668)
        self.assertEqual(f["kernel_slot_margin_bytes"], 0x1D0000 - 64 - 1_864_668)
        self.assertEqual(f["rootfs_bytes_used"], 4_000_000)
        self.assertEqual(f["rootfs_slot_margin_bytes"], sl.FW_LEN - sl.FW_KERNEL_LEN - 4_000_000)
        self.assertEqual(f["weak_bit_in_firmware_rootfs_offset"], "0x484f28")
        with self.assertRaises(sl.LayoutError):
            sl.plan(fw[:-1])
        with self.assertRaises(sl.LayoutError):
            sl.plan(b"\0" * 64 + fw[64:])
        big = fx.uimage(fx.rbytes(1, sl.FW_KERNEL_LEN))
        with self.assertRaises(sl.LayoutError):
            sl.plan((big + fw)[:sl.FW_LEN])
        no_sq = fw[:sl.FW_KERNEL_LEN] + b"\xff" * (sl.FW_LEN - sl.FW_KERNEL_LEN)
        with self.assertRaises(sl.LayoutError):
            sl.plan(no_sq)


class EnvCodec(unittest.TestCase):
    def test_roundtrip_and_crc(self):
        self.assertEqual(sl.parse_env(E0), fx.E0_ENTRIES)
        self.assertTrue(sl.env_crc_ok(E0))
        bad = bytearray(E0)
        bad[100] ^= 1
        self.assertFalse(sl.env_crc_ok(bytes(bad)))
        for img in (bytes(bad), E0[:-1], E0 + b"\0"):
            with self.assertRaises(sl.LayoutError):
                sl.parse_env(img)

    def test_rejects_malformed_entries(self):
        def raw(body: bytes) -> bytes:
            data = body + b"\0" * (sl.SENV_LEN - 4 - len(body))
            return struct.pack("<I", zlib.crc32(data) & 0xFFFFFFFF) + data
        unterminated = b"k=" + b"v" * (sl.SENV_LEN - 6)
        for body in (b"a=1\0a=2\0\0", b"novalue\0\0", b"=x\0\0", b"k=\x01\0\0", unterminated):
            with self.assertRaises(sl.LayoutError, msg=body[:12]):
                sl.parse_env(raw(body))
        with self.assertRaises(sl.LayoutError):
            sl.build_env([("k", "v" * sl.SENV_LEN)])

    def test_replace_values_keeps_order(self):
        out = sl.replace_values(fx.E0_ENTRIES, {"bootcmd": "x", "newkey": "y"})
        self.assertEqual([k for k, _ in out], [k for k, _ in fx.E0_ENTRIES] + ["newkey"])
        self.assertEqual(dict(out)["bootcmd"], "x")


class UBootParser(unittest.TestCase):
    def test_split_respects_quotes_and_escapes(self):
        self.assertEqual(sl.ub_split("setenv a 'x;y';saveenv"), ["setenv a 'x;y'", "saveenv"])
        self.assertEqual(sl.ub_split("echo a\\;b;reset"), ["echo a\\;b", "reset"])

    def test_macros_expand_outside_quotes_only(self):
        env = {"x": "1"}
        self.assertEqual(sl.ub_process_macros("setenv a ${x}", env), "setenv a 1")
        self.assertEqual(sl.ub_process_macros("setenv a '${x};y'", env), "setenv a ${x};y")
        self.assertEqual(sl.ub_process_macros("setenv a $(x)z", env), "setenv a 1z")

    def test_model_list_semantics_and_limits(self):
        m = sl.UBootModel([("a", "1"), ("b", "2")])
        m.run("setenv a 3;saveenv;setenv b;reset;setenv a 4")
        self.assertEqual(m.saved, [[("b", "2"), ("a", "3")]])
        self.assertEqual(m.env, [("a", "3")])                 # reset stops the command list
        for cmd in ("run x", "x" * 1024, "setenv " + " ".join("a" * 16), "reset now"):
            with self.assertRaises(sl.LayoutError, msg=cmd[:20]):
                sl.UBootModel([]).run(cmd)


class Cmdline(unittest.TestCase):
    def test_stock_bootargs_match_proc_mtd(self):
        parts = sl.parse_cmdline_mtdparts(dict(fx.E0_ENTRIES)["bootargs"])
        self.assertEqual([(n, o, s) for n, o, s, _ in parts], list(sl.STOCK_PARTS))
        self.assertEqual(sl.root_partition(dict(fx.E0_ENTRIES)["bootargs"], parts), "rootfs")

    def test_final_and_trial_tables(self):
        self.assertEqual(sl.check_cmdline(sl.FINAL_BOOTARGS, sl.FINAL_LAYOUT, "rootfs"), list(sl.FINAL_LAYOUT))
        parts = sl.check_cmdline(sl.TRIAL_BOOTARGS, sl.TRIAL_LAYOUT, "rootfs")
        self.assertEqual([p[0] for p in parts if not p[3]], ["tl", "senv"])
        self.assertIn(sl.TRIAL_MARK, sl.TRIAL_BOOTARGS.split())

    def test_rejects_bad_tables(self):
        for spec in ("mtdparts=hi_sfc:-(a),64k(b)", "mtdparts=hi_sfc:9M(a)", "mtdparts=hi_sfc:64k",
                     "mtdparts=other:64k(a)", "mtdparts=hi_sfc:1k(a)", "root=1f00"):
            with self.assertRaises(sl.LayoutError, msg=spec):
                sl.parse_cmdline_mtdparts(spec)
        self.assertEqual(sl.parse_cmdline_mtdparts("mtdparts=hi_sfc:64k(a),-(b)")[-1],
                         ("b", 0x10000, sl.FLASH_BYTES - 0x10000, False))
        parts = sl.parse_cmdline_mtdparts("mtdparts=hi_sfc:64k(a)")
        for cmd in ("root=/dev/mtdblock1", "root=/dev/sda1", ""):
            with self.assertRaises(sl.LayoutError, msg=cmd):
                sl.root_partition(cmd, parts)


class Builders(unittest.TestCase):
    def test_trial_env(self):
        armed, rep = sl.build_trial(E0)
        a, b = sl.parse_env(armed), fx.E0_ENTRIES
        self.assertEqual([k for k, _ in a], [k for k, _ in b])
        self.assertEqual([k for (k, v), (_, w) in zip(a, b) if v != w], ["bootcmd"])
        self.assertEqual(rep["bootcmd_chars"], 417)
        self.assertEqual(rep["bootcmd_margin_chars"], sl.CBSIZE - 1 - 417)
        self.assertEqual(rep["writable_partitions"], ["tl", "senv"])
        self.assertTrue(rep["saved_env_equals_stock"])
        saved = fx.uboot_saved(armed)                          # what U-Boot stores before the trial boot
        self.assertEqual(dict(sl.parse_env(saved)), dict(b))
        self.assertNotIn(sl.TRIAL_MARK.encode(), saved.split(b"\0\0")[0])

    def test_builders_refuse_a_non_stock_base(self):
        armed, _ = sl.build_trial(E0)
        no_args = sl.build_env([e for e in fx.E0_ENTRIES if e[0] != "bootargs"])
        marked = sl.build_env(sl.replace_values(fx.E0_ENTRIES, {"x": sl.TRIAL_MARK}))
        for base in (armed, no_args, marked):
            for fn in (sl.build_trial, sl.build_probe, sl.build_final, sl.check_restore):
                with self.assertRaises(sl.LayoutError, msg=fn.__name__):
                    fn(base)

    def test_probe_env_and_result_classification(self):
        probe, rep = sl.build_probe(E0)
        self.assertEqual((rep["bootcmd_chars"], rep["bootcmd_margin_chars"]), (158, 865))
        good = fx.uboot_saved(probe)
        self.assertEqual(sl.check_probe_result(good, E0), {"ok": True, "reason": sl.check_probe_result(good, E0)[
            "reason"]})
        bad_crc = bytearray(good)
        bad_crc[8] ^= 0xFF
        changed = sl.build_env(sl.replace_values(sl.parse_env(good), {"ipaddr": "192.0.2.11"}))
        marked = sl.build_env(sl.parse_env(good) + [("x", sl.TRIAL_MARK)])
        cases = {bytes(bad_crc): "default env", probe: "probe env still in place", E0: "not the last key",
                 changed: "differs from the base", marked: "trial marker"}
        for img, why in cases.items():
            res = sl.check_probe_result(img, E0)
            self.assertFalse(res["ok"])
            self.assertIn(why, res["reason"])
        self.assertTrue(sl.stock_equivalent(good, E0))
        self.assertFalse(sl.stock_equivalent(bytes(bad_crc), E0))

    def test_final_env(self):
        final, rep = sl.build_final(E0)
        env = dict(sl.parse_env(final))
        self.assertEqual((env["bootcmd"], env["bootargs"], env["verify"]),
                         (sl.FINAL_BOOTCMD, sl.FINAL_BOOTARGS, "y"))
        self.assertEqual(rep["changed_keys"], ["bootargs", "bootcmd", "verify"])
        self.assertEqual(rep["final_partitions"], ["boot", "env", "kernel", "rootfs", "rootfs_data"])
        self.assertEqual(sl.check_restore(E0), fx.E0_ENTRIES)


    def test_nothing_at_0x50000_triggers_the_kernel_split_layout(self):
        # The OpenIPC kernel takes its own partition table when flash 0x50000 holds a uImage header.
        rel = sl.SPLIT_PROBE_OFF - sl.SENV_OFF
        armed, _ = sl.build_trial(E0)
        final, rep = sl.build_final(E0)
        for img in (armed, fx.uboot_saved(armed), final):
            self.assertNotEqual(img[rel:rel + 4], sl.UIMAGE_MAGIC)
        self.assertEqual(rep["split_probe_0x50000"], "vendor env, no uImage header")
        # Leftovers past the terminator of the base env never reach the images that are written.
        data = bytearray(E0[4:])
        data[rel - 4:rel] = sl.UIMAGE_MAGIC
        dirty = struct.pack("<I", zlib.crc32(bytes(data)) & 0xFFFFFFFF) + bytes(data)
        self.assertEqual(sl.parse_env(dirty), fx.E0_ENTRIES)
        self.assertEqual(sl.build_trial(dirty)[0], armed)
        self.assertEqual(sl.build_final(dirty)[0], final)
        with self.assertRaisesRegex(sl.LayoutError, "0x50000"):
            sl.check_split_probe(dirty, "a dirty env")
        self.assertEqual(sl.plan()["kernel_split_probe"]["offset"], "0x50000")


class Cli(unittest.TestCase):
    def test_check_env_prints_no_values(self):
        probe, _ = sl.build_probe(E0)
        armed, _ = sl.build_trial(E0)
        final, _ = sl.build_final(E0)
        with tempfile.TemporaryDirectory() as td:
            for img, state in ((E0, "stock"), (probe, "probe"), (armed, "armed"), (final, "final")):
                p = Path(td) / "env.img"
                p.write_bytes(img)
                rc, out, _ = cli("check-env", str(p))
                self.assertEqual(rc, 0)
                self.assertEqual(json.loads(out)["bootcmd_state"], state)
                for value in ("192.0.2.10", "00:00:5e:00:53:01", "sf read"):
                    self.assertNotIn(value, out)
            p.write_bytes(E0[:-1])
            self.assertEqual(cli("check-env", str(p))[0], 2)

    def test_plan_out_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "plan.json"
            self.assertEqual(cli("plan", "--out", str(out))[0], 0)
            self.assertEqual(json.loads(out.read_text())["overlay_kib"], 896)
            self.assertEqual(cli("plan", "--out", str(out))[0], 2)


if __name__ == "__main__":
    unittest.main()
