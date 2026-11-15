# Trial-only WPA2 authenticator

This is not a replacement firmware or a permanent migration. The stock-userland
trial must authenticate with the MT7601 driver before trying DHCP; merely loading
the driver and supplying `MT7601USTA.dat` did not establish an authenticated
connection in the second physical trial.

## Offline build

Download the archives and clone the public repository named in `sources.json`
to private storage outside Git. Verify the toolchain's published `.sha256`
file as well as the committed pins. Check out the exact libnl-tiny revision
and leave that checkout clean. Host prerequisites: Python 3.11+, tar support,
Git, Make, and patch. The verified Bootlin archive supplies the cross-compiler.

```sh
ionice -c3 nice -n 10 python3 build_trial_auth.py \
  --toolchain-tar "$DOWNLOADS/armv5-eabi--musl--stable-2024.05-1.tar.xz" \
  --wpa-tar "$DOWNLOADS/wpa_supplicant-2.11.tar.gz" \
  --libnl-source "$PUBLIC_LIBNL_CHECKOUT" \
  --workdir "$PRIVATE_NEW_BUILD" --out "$PRIVATE_NEW_AUTH_RUNTIME"
```

The work and output directories must be new and outside any Git checkout.
Compilation is serial for a 2 GiB/no-swap host. libnl-tiny is built without
LTO because its external-inline API did not link reliably under cross-unit
LTO; the supplicant uses size optimization, section garbage collection and LTO.
No downloads, credentials, camera access, flash operations or reboots occur.
Do not commit the resulting executable or generated runtime directory.

Pass the private runtime directory to both `build_stockboot.py trial` and
`build_stockboot.py plan` using `--auth-runtime`.

## Scope and evidence

- Static ARM ELF32, ARMv5, EABI5 soft-float; no interpreter or dynamic library.
  The host builder checks the ELF headers, load bounds and aeabi CPU attribute.
- A network configuration uses hex-encoded SSID, a host-derived 256-bit PSK,
  RSN/WPA2-PSK and CCMP only. A derived key remains a credential: keep the
  whole payload, bakfs image, state and configuration private.
- The default random pool remains enabled. No cryptographic authentication,
  key validation or entropy checks are bypassed.
- `event-only.patch` changes diagnostic output only. With
  `CONFIG_NO_STDOUT_DEBUG` and the trial-only macro, `wpa_msg` emits only five
  fixed event names, never format arguments, addresses, SSIDs or keys.
  The header patch explicitly retains these messages despite upstream's
  `CONFIG_NO_WPA_MSG` optimization, and a third hunk keeps the one
  `CTRL-EVENT-CONNECTED` call site that upstream compiles only with a control
  interface or stdout debugging. Without it the fourth physical trial's
  supplicant completed WPA2 (the host access point logged a completed
  four-way handshake about 4 s into the nl80211 attempt and again into the
  wext attempt) but never reported it, so the trial waited out both attempts
  and never started DHCP. The builder refuses
  executables in which the fixed names have been stripped or in which an event
  has no call site (its name followed by a space, the start of an upstream
  format string), and ARM validation links the logging object from the actual
  supplicant build, not a differently configured copy.
  No control socket is compiled in. This patch is inappropriate for a build
  that needs a control interface.
- The trial checks the pinned executable again with stock `sha1sum`, starts
  one supplicant with `-D nl80211` as its owned background child and waits at
  most 90 polling intervals for a real `CTRL-EVENT-CONNECTED`. Only once that
  process is confirmed gone (it exited, or it was stopped after the wait) does
  it make a single `-D wext` attempt of at most 60 intervals, so two
  supplicants never run at once. Each attempt's outcome and exit status are
  recorded as `auth`/`auth_rc` and `auth_wext`/`auth_wext_rc` (255: the
  supplicant's own initialization failure; 128+N: killed by signal N).
  DHCP does not run after a failed authentication gate.
- The generated configuration holds only fields this build parses: `ap_scan`
  and one RSN/WPA2-PSK/CCMP network block. `wpa.config` sets
  `CONFIG_NO_CONFIG_WRITE`, which removes `update_config` from the parser; an
  unknown global field makes the supplicant exit with 255 before any socket or
  driver call, silently under `CONFIG_NO_STDOUT_DEBUG`. That ended the third
  physical trial. A unit test pins the field list. With `OM_AUTH_RUNTIME` (the
  private runtime directory) and `OM_QEMU_ARM` (a qemu-arm launcher for
  `-cpu arm926`) set, the tests also start the real executable on the
  generated configuration in a network namespace that has only `lo`.
- The child's PID and original `/proc` start time gate cleanup. It stays alive
  through result upload, then only that process is terminated.
- The existing 70-second post-host stability window and independent 900-second
  stock-return fallback remain. Authentication must still be connected at the
  end of that window.
- The existing 704 KiB payload boundary is unchanged and still enforced.
  No extra flash region is made writable; neither vendor U-Boot nor permanent
  firmware is installed by this build tool.

Run the executable under `qemu-arm -cpu arm926 -v` and `-h`, test the fixed-event
hook on ARM, start it on the generated configuration, and run the full
stock-BusyBox QEMU chain before physical use. qemu-user cannot pass nl80211 or
wireless-extension requests to a driver, so these are compatibility checks,
not proof of hardware Wi-Fi interoperability.

The event path itself was checked on a Linux host (root, `mac80211_hwsim`): a
native build of the same source, patch and configuration against hostapd
WPA2-PSK/CCMP on two simulated radios created inside private network
namespaces (`HWSIM_CMD_NEW_RADIO` issued there, so the host's own network
manager never sees them), then BusyBox `udhcpc` with `trial_dhcp.sh` and a ping
over the simulated medium. With the fourth trial's patch the access point
completed the four-way handshake for both backends while the supplicant printed
no `CTRL-EVENT-CONNECTED`, reproducing that trial; with the current patch both
backends reported it within seconds and DHCP and ping succeeded. This covers
the supplicant and the kernel's cfg80211 paths, not the vendor MT7601 driver.
