#!/usr/bin/env python3
"""
Provision all-zero TLS PSK (PSK_WHITE_BOX) on Goodix 27c6:5130 (GF_HC460SEC_APP)
via IAP mode, then re-flash the verified official GF_HC460SEC_APP firmware.

Safety: Before erasing APP flash, verifies the supplied firmware .bin CRC-32/MPEG-2
against the live sensor's flash at 0x20000. Aborts without modifying flash on mismatch.
"""

import argparse
import contextlib
import io
import os
import sys
import time
import types
import usb.core

PSK_WHITE_BOX = bytes.fromhex(
    "ec35ae3abb45ed3f12c4751f1e5c2cc05b3c5452e9104d9f2a3118644f37a04b"
    "6fd66b1d97cf80f1345f76c84f03ff30bb51bf308f2a9875c41e6592cd2a2f9e"
    "60809b17b5316037b69bb2fa5d4c8ac31edb3394046ec06bbdacc57da6a756c5"
)
PMK_HASH = bytes.fromhex(
    "ba1a86037c1d3c71c3af344955bd69a9a9861d9e911fa24985b677e8dbd72d43"
)


def crc32_mpeg2(buf: bytes) -> int:
    crc = 0xFFFFFFFF
    for b in buf:
        crc ^= (b << 24)
        for _ in range(8):
            if crc & 0x80000000:
                crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF
            else:
                crc = (crc << 1) & 0xFFFFFFFF
    return crc


def reset_usb(pid: int):
    raw = usb.core.find(idVendor=0x27C6, idProduct=pid)
    if raw is not None:
        try:
            raw.reset()
            time.sleep(0.5)
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser(description="IAP-mode PSK provisioning for Goodix 27c6:5130")
    ap.add_argument("--goodix-dump", required=True, help="Path to cloned goodix-fp-dump repository")
    ap.add_argument("--firmware", required=True, help="Path to extracted GF_HC460SEC_APP_*.bin")
    ap.add_argument("--pid", default="0x5130", help="USB Product ID (default: 0x5130)")
    ap.add_argument("--skip-live-check", action="store_true",
                    help="Skip live flash CRC match check (only if already in IAP mode)")
    args = ap.parse_args()

    pid = int(args.pid, 0)
    fw_bytes = open(args.firmware, "rb").read()
    fw_crc = crc32_mpeg2(fw_bytes)

    sys.path.insert(0, os.path.abspath(args.goodix_dump))
    sys.modules["periphery"] = types.ModuleType("periphery")
    sys.modules["spidev"] = types.ModuleType("spidev")
    import goodix
    import protocol

    def open_dev():
        dev = goodix.Device(pid, protocol.USBProtocol)
        dev.nop()
        time.sleep(0.3)
        return dev

    reset_usb(pid)
    dev = open_dev()
    fw_ver = dev.firmware_version()
    print(f"[STEP 0] Current MCU firmware: {fw_ver}")

    if "IAP" not in fw_ver:
        if not args.skip_live_check:
            ok = dev.check_firmware(0x20000, len(fw_bytes), fw_crc)
            print(f"[STEP 0] Live flash check (0x20000, len={len(fw_bytes)}, crc=0x{fw_crc:08x}): {ok}")
            if not ok:
                print("[ABORT] Firmware binary does NOT match live chip flash at 0x20000! No changes made.")
                sys.exit(1)
            print("[OK] Firmware matches live chip bit-for-bit.")

        print("[STEP 1] Erasing APP to enter IAP mode...")
        dev.mcu_erase_app(0, False)
        usb.util.dispose_resources(dev.protocol.device)
        time.sleep(1.5)

        dev = open_dev()
        fw_ver = dev.firmware_version()
        print(f"[STEP 1] Reconnected in IAP mode: {fw_ver}")
        if "IAP" not in fw_ver:
            print(f"[ABORT] Expected IAP mode, got: {fw_ver}")
            sys.exit(1)

    print("[STEP 2] Writing 96-byte PSK_WHITE_BOX (all-zero PSK) in IAP mode...")
    w_ok = dev.preset_psk_write(0xBB010003, PSK_WHITE_BOX)
    print(f"  preset_psk_write: {w_ok}")
    r_ok, fl, h = dev.preset_psk_read(0xBB020003)
    print(f"  preset_psk_read(0xbb020003): ok={r_ok} hash={h.hex() if h else None}")
    if h == PMK_HASH:
        print("[OK] IAP-mode PSK provisioning VERIFIED!")
    else:
        print("[WARN] PSK hash mismatch — still re-flashing APP firmware so device remains healthy.")

    print(f"[STEP 3] Flashing {len(fw_bytes)} bytes (CRC 0x{fw_crc:08x})...")
    with contextlib.redirect_stdout(io.StringIO()):
        for i in range(0, len(fw_bytes), 1008):
            if not dev.write_firmware(i, fw_bytes[i : i + 1008]):
                raise RuntimeError(f"write_firmware failed at offset {i}")
            if (i // 1008) % 10 == 0 or i + 1008 >= len(fw_bytes):
                sys.stderr.write(f"\r  Flashed {min(i + 1008, len(fw_bytes))}/{len(fw_bytes)} bytes")
                sys.stderr.flush()
    sys.stderr.write("\n")

    chk = dev.check_firmware(0, len(fw_bytes), fw_crc)
    print(f"[STEP 3] MCU check_firmware(0, {len(fw_bytes)}, 0x{fw_crc:08x}) = {chk}")
    if not chk:
        raise RuntimeError("MCU rejected flashed firmware CRC!")

    print("[STEP 3] Resetting MCU into APP mode...")
    dev.reset(False, True, 20)
    usb.util.dispose_resources(dev.protocol.device)
    time.sleep(1.5)

    dev = open_dev()
    fw_after = dev.firmware_version()
    r_ok2, _, h2 = dev.preset_psk_read(0xBB020003)
    usb.util.dispose_resources(dev.protocol.device)
    print(f"[DONE] APP firmware: {fw_after}")
    print(f"[DONE] PSK hash:     {h2.hex() if h2 else None} (matches PMK_HASH={h2 == PMK_HASH})")


if __name__ == "__main__":
    main()
