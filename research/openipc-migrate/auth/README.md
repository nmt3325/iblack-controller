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
  `CONFIG_NO_WPA_MSG` optimization. The builder refuses executables in which
  the fixed events have been stripped, and ARM validation links the logging
  object from the actual supplicant build, not a differently configured copy.
  No control socket is compiled in. This patch is inappropriate for a build
  that needs a control interface.
- The trial launches one foreground supplicant as its owned background child,
  checks the pinned executable again with stock `sha1sum`, and waits at most
  90 polling intervals for a real `CTRL-EVENT-CONNECTED`.
  DHCP does not run after a failed authentication gate.
- The child's PID and original `/proc` start time gate cleanup. It stays alive
  through result upload, then only that process is terminated.
- The existing 70-second post-host stability window and independent 900-second
  stock-return fallback remain. Authentication must still be connected at the
  end of that window.
- The existing 704 KiB payload boundary is unchanged and still enforced.
  No extra flash region is made writable; neither vendor U-Boot nor permanent
  firmware is installed by this build tool.

Run the executable under `qemu-arm -cpu arm926 -v` and `-h`, test the fixed-event
hook on ARM, and run the full stock-BusyBox QEMU chain before physical use.
Those are compatibility checks, not proof of hardware Wi-Fi interoperability.