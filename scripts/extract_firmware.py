#!/usr/bin/env python3
"""
Extract GF_HC460SEC_APP_* firmware binary from an official Huawei MateBook
Fingerprint driver .zip package (or directly from gfspi.dll).

Does NOT redistribute any copyrighted firmware; extracts from your own local download.
"""

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile


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


def find_gfspi_dll(input_path: str, workdir: str) -> str:
    if input_path.lower().endswith(".dll"):
        return input_path

    # Unpack outer zip -> inner zip -> outer NSIS exe -> inner NSIS exe
    step1 = os.path.join(workdir, "step1")
    subprocess.run(["unzip", "-o", "-q", input_path, "-d", step1], check=True)

    # Check if there's an inner zip
    inner_zips = []
    for root, _, files in os.walk(step1):
        for f in files:
            if f.lower().endswith(".zip"):
                inner_zips.append(os.path.join(root, f))

    search_root = step1
    if inner_zips:
        step2 = os.path.join(workdir, "step2")
        subprocess.run(["unzip", "-o", "-q", inner_zips[0], "-d", step2], check=True)
        search_root = step2

    # Recursively extract NSIS .exe installers with 7z (up to 3 levels)
    for depth in range(3):
        for root, _, files in os.walk(search_root):
            for f in files:
                if f.lower() == "gfspi.dll":
                    return os.path.join(root, f)
        exes = []
        for root, _, files in os.walk(search_root):
            for f in files:
                if f.lower().endswith(".exe") and "fingerprint" in f.lower():
                    exes.append(os.path.join(root, f))
        if not exes:
            break
        next_dir = os.path.join(workdir, f"exe_{depth}")
        subprocess.run(["7z", "x", "-y", f"-o{next_dir}", exes[0]],
                       stdout=subprocess.DEVNULL, check=True)
        search_root = next_dir

    for root, _, files in os.walk(search_root):
        for f in files:
            if f.lower() == "gfspi.dll":
                return os.path.join(root, f)

    raise FileNotFoundError("Could not find gfspi.dll inside the archive.")


def extract_hc460_fw(dll_path: str) -> tuple[str, bytes]:
    data = open(dll_path, "rb").read()

    # Match Goodix descriptor header: \x00\x00\x15GF_HC460SEC_APP_XXXXX followed by ARM Cortex-M vector table
    pattern = re.compile(rb"\x00\x00\x15(GF_HC460SEC_APP_\d{5})")
    matches = list(pattern.finditer(data))
    if not matches:
        raise RuntimeError("No GF_HC460SEC_APP_* firmware header found in gfspi.dll")

    # Use first two copies to determine exact end boundary if multiple copies exist
    label = matches[0].group(1).decode("ascii")
    s1 = matches[0].end()

    # Sanity-check ARM Cortex-M4 vector table (initial SP in SRAM 0x20000000..0x20030000)
    sp = int.from_bytes(data[s1 : s1 + 4], "little")
    reset_vec = int.from_bytes(data[s1 + 4 : s1 + 8], "little")
    if not (0x20000000 <= sp <= 0x20040000 and 0x00020000 <= reset_vec <= 0x00040000):
        raise RuntimeError(f"Unexpected ARM vector table at 0x{s1:x}: SP=0x{sp:08x} Reset=0x{reset_vec:08x}")

    if len(matches) >= 2:
        s2 = matches[1].end()
        diff = None
        for i in range(0x14000, 0x20000):
            if data[s1 + i] != data[s2 + i]:
                diff = i
                break
        if diff is None:
            raise RuntimeError("Could not locate end boundary between firmware copies")
        # Strip trailing alignment zeros + 4-byte descriptor trailer before next .rdata symbol
        end = diff
        while end > 0 and data[s1 + end - 1] == 0:
            end -= 1
        # The 4-byte trailer before trailing zeros is not flashed to 0x20000; align down to 16B
        fw_len = (end - 4) & ~0xF
    else:
        raise RuntimeError("Expected multiple firmware copies in gfspi.dll to determine exact length")

    return label, data[s1 : s1 + fw_len]


def main():
    ap = argparse.ArgumentParser(description="Extract GF_HC460SEC_APP firmware from Huawei driver package")
    ap.add_argument("input", help="Path to Huawei Fingerprint .zip or gfspi.dll")
    ap.add_argument("-o", "--output", default="GF_HC460SEC_APP.bin", help="Output .bin path")
    args = ap.parse_args()

    workdir = tempfile.mkdtemp(prefix="hc460_extract_")
    try:
        dll_path = find_gfspi_dll(args.input, workdir)
        print(f"Found gfspi.dll: {dll_path}")
        label, fw = extract_hc460_fw(dll_path)
        crc = crc32_mpeg2(fw)
        sha = hashlib.sha256(fw).hexdigest()
        with open(args.output, "wb") as f:
            f.write(fw)
        print(f"Extracted: {label}")
        print(f"  Output:        {args.output}")
        print(f"  Size:          {len(fw)} bytes (0x{len(fw):x})")
        print(f"  CRC-32/MPEG-2: 0x{crc:08x}")
        print(f"  SHA-256:       {sha}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
