# IERMU iBlack — SPI NOR firmware dump static analysis

Date: 2026-11-13
Source file: iblack_1.bin
Image size: 8,388,608 bytes (8 MiB)
SHA-256: b3e0ff253ed30696af53ecadb4ccf4e4220c6d6e38ce6730f4fe86e4a80da0

## Verified partition layout (U-Boot `mtdparts=hi_sfc`)

| Offset start | Offset end (exclusive) | Size | Name | Evidence |
|---|---|---|---|---|
| 0x000000 | 0x200000 | 2048 KiB | boot | U-Boot at 0x0, uImage at 0x80000; partition also includes U-Boot environment at 0x40000 |
| 0x200000 | 0x2C0000 | 768 KiB | rootfs | SquashFS v4, xz compressed, 179 inodes |
| 0x2C0000 | 0x540000 | 2560 KiB | appfs | SquashFS v4, xz compressed, 58 inodes |
| 0x540000 | 0x560000 | 128 KiB | mdat1 | zlib-compressed configuration data |
| 0x560000 | 0x580000 | 128 KiB | mdat2 | zlib-compressed configuration data; decompresses to same bytes as mdat1 |
| 0x580000 | 0x800000 | 2560 KiB | bakfs | 8-byte header followed by appfs image; exact bit-for-bit match for image |

## Platform

- Bootloader: U-Boot 2010.06 (built 2015-05-06 14:41:18)
- Linux kernel: 3.0.8 (uImage at 0x80000; CRCs verified)
- Architecture: ARM, 32-bit, little endian, EABI5
- C runtime: uClibc 0.9.32.1
- SoC indicators: Hi3518 family
- Wi-Fi driver modules: mt7601Usta.ko / mt7601Uap.ko
- Startup-selected imaging sensor: AR0141 (`load3518 -i ar0141`)
- Main executable: `/app/cmsapp/cmsMain.out` (3,806,272 bytes)

## Boot sequence

`/etc/inittab` -> `/etc/init.d/rcS` -> `S10mpp` -> `/etc/cmssysrun.sh` -> mount `/dev/mtdblock2` at `/app` -> `/app/runapp.sh` -> `/app/komod/load3518 -i ar0141` -> start `cmsMain.out`.

UART findings:
- U-Boot environment reports baudrate=115200 and stdin/stdout/stderr=serial; bootdelay=0.
- The `getty` line in `/etc/inittab` is commented out.
- `/etc/cmssysrun.sh` explicitly starts `getty -L ttyS000 115200 vt100 -t 5&`.
- This alone does NOT establish that a GPIO/test pad exposes a functioning UART.

## Application and networking (static binary string evidence)

- P2P and cloud transport (P2P message handlers, RTMP upload)
- HTTP server and legacy browser ActiveX UI (`cmsapp/webroot/index.html`, refers to webvideo.cab)
- UPnP/DLNA identification string `cmsIermu DLNADOC/1.50 UPnP/1.0 SakaueDLNA/1.1.0`
- NFS and SMB/CIFS mount code, possibly NAS recording
- API/cloud destination strings: api.iermu.com, pcs.baidu.com, openapi.baidu.com, api.topvdn.com, upload.qiniu.com, www.dvrnat.com
- No explicit RTSP or ONVIF string was found in `cmsMain.out`. Their absence in strings is NOT proof the protocols are unavailable.

## Security considerations

- `/etc/passwd` includes a hashed root password.
- Nonvolatile settings contain user/account strings and apparent fixed credentials; handle this firmware dump as sensitive.
- Avoid running this legacy firmware on a network that can reach the internet.

## Files

The `base_rootfs` directory has files from the first SquashFS; `camera_modules` has files from the second. Symbolic link targets are represented as `.symlink` text files to avoid host-side execution/path effects. Extracted using a custom read-only SquashFS v4 XZ parser; files decompress without reported errors.

## Remaining uncertainties

- Whether RTSP/ONVIF is offered in the running system, or only private streaming protocols
- HTTP/TCP command/data ports and authentication semantics
- Physical UART GPIO and test-pad assignment
- Reliability of the single uploaded dump: verify against second flash read when available
- Whether embedded credentials are currently active after runtime provisioning
