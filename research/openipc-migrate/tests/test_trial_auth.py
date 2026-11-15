"""Offline checks for trial-only WPA2 configuration and the ARM runtime contract."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import auth_fixture as af
import trial_auth as ta

ROOT = Path(__file__).resolve().parents[1]
SSID, PASS = "dummy-ssid-for-test", "dummy-pass-for-test"
# Optional: the private pinned runtime directory (as given to build_stockboot.py --auth-runtime) and a
# qemu-arm launcher for an ARMv5 CPU, to run the real executable on the configuration it is shipped with.
RUNTIME = os.environ.get("OM_AUTH_RUNTIME")
QEMU_ARM = os.environ.get("OM_QEMU_ARM")


class Authentication(unittest.TestCase):
    def test_known_pbkdf2_vector_and_no_plaintext_credentials(self):
        data = ta.wpa_config("IEEE", "password")
        self.assertIn(b"ssid=49454545", data)
        self.assertIn(b"psk=f42c6fc52df0ebef9ebb4b90b38a5f902e83fe1b135a70e23aed762e9710a12e", data)
        self.assertNotIn(b"password", data)
        self.assertIn(b"proto=RSN\n", data)
        self.assertIn(b"pairwise=CCMP\n", data)

    def test_hex_psk_is_not_rederived(self):
        self.assertIn(b"psk=" + b"ab" * 32 + b"\n", ta.wpa_config("x", "AB" * 32))

    def test_special_characters_cannot_inject_configuration(self):
        ssid = 'x"\\#={}'
        data = ta.wpa_config(ssid, 'secret"\\#pass')
        self.assertIn(("ssid=" + ssid.encode().hex()).encode(), data)
        self.assertEqual(data.count(b"network={"), 1)
        self.assertNotIn(b"secret", data)

    def test_invalid_credentials_are_rejected_without_echoing_them(self):
        for ssid, psk in [("", "passphrase"), ("x" * 33, "passphrase"), ("a\nb", "passphrase"),
                          ("x", "short"), ("x", "g" * 64), ("x", "a" * 65), ("日本", "passphrase"),
                          ("x", "password\n")]:
            with self.subTest(ssid_length=len(ssid), psk_length=len(psk)):
                with self.assertRaises(ta.AuthError):
                    ta.wpa_config(ssid, psk)

    def test_config_only_uses_fields_of_the_runtime_profile(self):
        # The pinned build sets CONFIG_NO_CONFIG_WRITE, which removes update_config from its parser: that one
        # unknown global made the third hardware trial's supplicant exit (255) before any driver call.
        self.assertIn("CONFIG_NO_CONFIG_WRITE=y", (ROOT / "auth" / "wpa.config").read_text().splitlines())
        lines = ta.wpa_config(SSID, PASS).decode("ascii").splitlines()
        self.assertEqual(lines[:2], ["ap_scan=1", "network={"])
        self.assertEqual([line.strip().split("=", 1)[0] for line in lines[2:-1]],
                         ["ssid", "scan_ssid", "psk", "proto", "key_mgmt", "pairwise", "group", "ieee80211w"])
        self.assertEqual(lines[-1], "}")
        self.assertNotIn("update_config", "\n".join(lines))

    def test_maximal_credentials(self):
        self.assertIn(b"ssid=" + b"78" * 32, ta.wpa_config("x" * 32, "p" * 63))

    def test_static_armv5_fixture(self):
        ta.validate_elf(af.elf())

    def test_newer_cpu_is_rejected(self):
        for arch in (6, 10, 14):
            with self.subTest(arch=arch), self.assertRaisesRegex(ta.AuthError, "ARMv5"):
                ta.validate_elf(af.elf(arch))

    def test_bad_elf_headers_are_rejected(self):
        for offset, value in [(4, 2), (5, 2), (16, 3), (18, 62), (39, 4), (42, 31)]:
            data = bytearray(af.elf())
            data[offset] = value
            with self.subTest(offset=offset), self.assertRaises(ta.AuthError):
                ta.validate_elf(bytes(data))

    def test_hard_float_is_rejected(self):
        data = bytearray(af.elf())
        struct.pack_into("<I", data, 36, 0x05000400)
        with self.assertRaisesRegex(ta.AuthError, "soft-float"):
            ta.validate_elf(bytes(data))

    def test_dynamic_or_interpreted_binary_is_rejected(self):
        for typ in (2, 3):
            data = bytearray(af.elf())
            struct.pack_into("<I", data, 52, typ)
            with self.subTest(typ=typ), self.assertRaisesRegex(ta.AuthError, "forbidden"):
                ta.validate_elf(bytes(data))

    def test_corrupt_segments_and_missing_attributes_are_rejected(self):
        for offset, value in [(28, 500), (68, 900), (76, 0), (296 + 20, 999999)]:
            data = bytearray(af.elf())
            struct.pack_into("<I", data, offset, value)
            with self.subTest(offset=offset), self.assertRaises(ta.AuthError):
                ta.validate_elf(bytes(data))

    def test_runtime_contract_and_tamper_rejection(self):
        with tempfile.TemporaryDirectory(prefix="om-auth-") as tmp:
            p = af.runtime(Path(tmp) / "runtime")
            files = ta.runtime_files(p)
            self.assertEqual(set(files), {"wpa_supplicant", "auth-runtime.json", "auth.sha1"})
            self.assertEqual(files["auth.sha1"].strip().decode(), hashlib.sha1(af.elf()).hexdigest())
            binary = p / "wpa_supplicant"
            binary.write_bytes(binary.read_bytes() + b"tamper")
            with self.assertRaisesRegex(ta.AuthError, "differs"):
                ta.runtime_files(p)

    def test_runtime_rejects_wrong_manifest_or_permissions(self):
        with tempfile.TemporaryDirectory(prefix="om-auth-") as tmp:
            p = af.runtime(Path(tmp) / "runtime")
            meta = json.loads((p / "runtime.json").read_text())
            for key, value in [("profile", "unknown"), ("version", "2.9"), ("capabilities", [])]:
                af.runtime(p)
                bad = dict(meta, **{key: value})
                (p / "runtime.json").write_text(json.dumps(bad))
                with self.subTest(key=key), self.assertRaises(ta.AuthError):
                    ta.runtime_files(p)
            af.runtime(p)
            (p / "runtime.json").chmod(0o644)
            with self.assertRaisesRegex(ta.AuthError, "permissions"):
                ta.runtime_files(p)

    def test_runtime_requires_events_not_stripped_by_upstream_makefile(self):
        with tempfile.TemporaryDirectory(prefix="om-auth-") as tmp:
            p = af.runtime(Path(tmp) / "runtime")
            data = (p / "wpa_supplicant").read_bytes().replace(b"CTRL-EVENT-CONNECTED", b"XTRL-EVENT-CONNECTED")
            (p / "wpa_supplicant").write_bytes(data)
            meta = json.loads((p / "runtime.json").read_text())
            meta["sha256"] = hashlib.sha256(data).hexdigest()
            (p / "runtime.json").write_text(json.dumps(meta))
            with self.assertRaisesRegex(ta.AuthError, "events were stripped"):
                ta.runtime_files(p)

    def test_runtime_requires_a_call_site_for_each_event(self):
        # The fourth hardware trial's runtime held the patch's name table but no CONNECTED format string:
        # upstream compiles that call site out without a control interface, so success was never reported.
        with tempfile.TemporaryDirectory(prefix="om-auth-") as tmp:
            p = af.runtime(Path(tmp) / "runtime")
            data = (p / "wpa_supplicant").read_bytes().replace(b"CTRL-EVENT-CONNECTED \0", b"CTRL-EVENT-CONNECTEDX\0")
            self.assertIn(b"CTRL-EVENT-CONNECTED\0", data)
            (p / "wpa_supplicant").write_bytes(data)
            meta = json.loads((p / "runtime.json").read_text())
            meta["sha256"] = hashlib.sha256(data).hexdigest()
            (p / "runtime.json").write_text(json.dumps(meta))
            with self.assertRaisesRegex(ta.AuthError, "call site compiled out.*: CTRL-EVENT-CONNECTED$"):
                ta.runtime_files(p)

    def test_event_patch_keeps_the_connected_call_site(self):
        self.assertIn("CONFIG_NO_STDOUT_DEBUG=y", (ROOT / "auth" / "wpa.config").read_text().splitlines())
        patch = (ROOT / "auth" / "event-only.patch").read_text()
        self.assertEqual(patch.count("+++ b/wpa_supplicant/wpa_supplicant.c\n"), 1)
        hunk = patch.split("+++ b/wpa_supplicant/wpa_supplicant.c\n", 1)[1]
        self.assertIn("-#if defined(CONFIG_CTRL_IFACE) || !defined(CONFIG_NO_STDOUT_DEBUG)\n", hunk)
        self.assertIn("+\tdefined(CONFIG_OMT_AUTH_EVENT_ONLY)\n", hunk)
        self.assertIn(' \t\twpa_msg(wpa_s, MSG_INFO, WPA_EVENT_CONNECTED "- Connection to "\n', hunk)

    def test_runtime_rejects_symlinks(self):
        with tempfile.TemporaryDirectory(prefix="om-auth-") as tmp:
            p = af.runtime(Path(tmp) / "runtime")
            binary = p / "wpa_supplicant"
            binary.rename(p / "real")
            os.symlink("real", binary)
            with self.assertRaises(ta.AuthError):
                ta.runtime_files(p)


@unittest.skipUnless(RUNTIME and QEMU_ARM and shutil.which("unshare"),
                     "needs OM_AUTH_RUNTIME (private runtime), OM_QEMU_ARM (ARMv5 launcher) and unshare")
class RealRuntimeReadsTheTrialConfig(unittest.TestCase):
    """The pinned executable under qemu-arm, in a user+network namespace that has only lo. qemu-user does not
    emulate nl80211 or the wireless-extension ioctls, so this checks the configuration and the start, not the
    driver: an accepted configuration keeps it running, a rejected one ends it with 255 at once."""

    def run_supplicant(self, config: bytes, seconds: float) -> tuple:
        files = ta.runtime_files(Path(RUNTIME))  # verified exactly as for a payload
        with tempfile.TemporaryDirectory(prefix="om-auth-qemu-") as tmp:
            binary, conf = Path(tmp) / "wpa_supplicant", Path(tmp) / "wpa.conf"
            binary.write_bytes(files["wpa_supplicant"])
            binary.chmod(0o700)
            conf.write_bytes(config)
            proc = subprocess.Popen(["unshare", "-rn", QEMU_ARM, str(binary), "-i", "lo", "-D", "wext",
                                     "-c", str(conf)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, start_new_session=True)
            try:
                return False, proc.wait(timeout=seconds)
            except subprocess.TimeoutExpired:
                return True, None
            finally:
                for sig in (signal.SIGTERM, signal.SIGKILL):
                    try:
                        os.killpg(proc.pid, sig)
                        proc.wait(timeout=10)
                        break
                    except ProcessLookupError:
                        break
                    except subprocess.TimeoutExpired:
                        continue

    def test_generated_config_keeps_the_real_supplicant_running(self):
        self.assertEqual(self.run_supplicant(ta.wpa_config(SSID, PASS), 3), (True, None))

    def test_the_third_trial_config_still_ends_it_with_255(self):
        # The same fields plus update_config=0, as the third hardware trial shipped them.
        self.assertEqual(self.run_supplicant(b"update_config=0\n" + ta.wpa_config(SSID, PASS), 20), (False, 255))


if __name__ == "__main__":
    unittest.main()