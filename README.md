# Goodix 27c6:5130 (GF_HC460SEC_APP) Linux Fingerprint Driver Guide

Working Linux fingerprint support for the **Goodix `27c6:5130`** USB fingerprint sensor (Huawei MateBook 14 AMD `KLVL-WXX9`, firmware `GF_HC460SEC_APP_12410`, Chip ID `0x2504`, ChicagoHS `80×64` / `80×88` sensor).

Tested end-to-end on **Ubuntu 26.04.1 LTS (Kernel 7.0.0)** with GNOME, lock screen, `fprintd-enroll`, `fprintd-verify`, and `sudo`.

---

## Why it didn't work before (Root Cause)

1. **Wrong driver family (`goodixmoc` vs `goodixtls`):** `27c6:5130` is **not** a Match-on-Chip (`goodixmoc`) device and **not** the eSPI `/dev/gxfp` GXFP5130 sensor found in MateBook D16 2024. It is a USB CDC-ACM **Goodix TLS (`goodixtls511`)** sensor — protocol-identical to `27c6:5110` (`GF_ST411SEC_APP_121xx`), just running on an **HC32F460** MCU (`GF_HC460SEC_APP_12410`) instead of an STM32F411.
2. **APP-mode PSK writes are rejected:** In APP mode (`GF_HC460SEC_APP_12410`), command `0xE0` (`preset_psk_write`) replies with `b0 03 00 e0 03` (ACK only, no-op) and refuses to overwrite the existing Windows-provisioned PSK.
3. **Solution:** Provision the known all-zero PSK (`PSK_WHITE_BOX`, hash `ba1a86037c1d3c71c3af344955bd69a9a9861d9e911fa24985b677e8dbd72d43`) in **IAP (bootloader) mode** (`MILAN_HC460SEC_IAP_12402`), re-flash the **exact official `GF_HC460SEC_APP_12410` firmware** extracted from Huawei's driver package, and build [`buxel/libfprint-27c6-5110`](https://github.com/buxel/libfprint-27c6-5110) (`goodixtls511`, pure-C FAST-9 + BRIEF-256 SIGFM matcher) with a 2-line patch adding PID `0x5130` and the `GF_HC460SEC_APP_` firmware prefix.

> [!CAUTION]
> - **NEVER flash `GF_ST411SEC_APP_*` (STM32) firmware onto an `HC460SEC` (HC32F460) chip!** Always use the `GF_HC460SEC_APP_*` binary extracted from your official Huawei driver package.
> - **Dual-boot warning:** Provisioning the all-zero PSK will overwrite the Windows Hello PSK stored in the sensor. If you dual-boot Windows and boot back into Windows, Windows may re-provision its own PSK (unless you disable the fingerprint device in Windows Device Manager).
> - **No copyrighted firmware is redistributed in this repository.** Use `scripts/extract_firmware.py` to extract the binary from Huawei's official driver package.

---

## Step-by-Step Setup

### 1. Download the official Huawei Fingerprint driver

Download the official **MateBook 14 AMD** Fingerprint driver `.zip` (e.g., `MateBook_14_AMD_2020_FingerPrint_1.1.124.115.zip`) from Huawei Support:
- https://consumer.huawei.com/en/support/laptops/matebook-14-amd-2020/ (or `/tr/support/laptops/matebook-14-amd-2021/`)

### 2. Extract and verify `GF_HC460SEC_APP_12410.bin`

```bash
sudo apt-get install -y unzip p7zip-full python3-venv
python3 scripts/extract_firmware.py ~/Downloads/MateBook_14_AMD_2020_FingerPrint_1.1.124.115.zip -o GF_HC460SEC_APP_12410.bin
```

Expected output for `1.1.124.115`:
- Size: `94688` bytes (`0x171e0`)
- CRC-32/MPEG-2: `0xb8a31df9`

### 3. Provision PSK in IAP mode and re-flash `GF_HC460SEC_APP_12410.bin`

Clone [`goodix-fp-linux-dev/goodix-fp-dump`](https://github.com/goodix-fp-linux-dev/goodix-fp-dump) for its `goodix.py` / `protocol.py` library:

```bash
git clone --depth 1 https://github.com/goodix-fp-linux-dev/goodix-fp-dump.git
python3 -m venv .venv
.venv/bin/pip install pyusb crcmod

sudo systemctl stop fprintd
sudo .venv/bin/python3 scripts/provision_iap_psk.py \
  --goodix-dump ./goodix-fp-dump \
  --firmware ./GF_HC460SEC_APP_12410.bin
```

**Built-in safety check:** Before erasing APP flash, `provision_iap_psk.py` runs `check_firmware(0x20000, len, crc)` against your **live sensor**. If the extracted binary does not match your sensor's current flash bit-for-bit, it **aborts immediately without touching flash**.

### 4. Build and install patched `libfprint` (`goodixtls511` + `0x5130`)

Install build dependencies (Ubuntu / Debian):

```bash
sudo apt-get install -y \
  git meson ninja-build pkg-config \
  libglib2.0-dev libgusb-dev libpixman-1-dev \
  libgnutls28-dev libgudev-1.0-dev systemd-dev
```

Clone [`buxel/libfprint-27c6-5110`](https://github.com/buxel/libfprint-27c6-5110), apply `patches/goodixtls511-add-5130.patch`, build, and install:

```bash
git clone --depth 1 https://github.com/buxel/libfprint-27c6-5110.git
cd libfprint-27c6-5110
git submodule update --init --depth 1 libfprint-fork
cd libfprint-fork

git apply ../../patches/goodixtls511-add-5130.patch

meson setup build \
  -Ddrivers=goodixtls511 \
  -Ddoc=false \
  -Dgtk-examples=false \
  -Dintrospection=false \
  -Dinstalled-tests=false \
  --prefix=/usr/local

ninja -C build
sudo ninja -C build install
sudo ldconfig
sudo systemctl restart fprintd
```

### 5. Enroll and verify!

```bash
fprintd-list "$USER"
fprintd-enroll "$USER"
fprintd-verify "$USER"
```

You can also enroll directly in **GNOME Settings → System → Users → Fingerprint Login**.

---

## Credits

- [`goodix-fp-linux-dev/goodix-fp-dump`](https://github.com/goodix-fp-linux-dev/goodix-fp-dump) & issue [#83](https://github.com/goodix-fp-linux-dev/goodix-fp-dump/issues/83) (IAP-mode provisioning & `gfspi.dll` firmware layout)
- [`buxel/libfprint-27c6-5110`](https://github.com/buxel/libfprint-27c6-5110) (pure-C `goodixtls511` + SIGFM driver on `libfprint` 1.94.9)
- [`goodix-fp-linux-dev/libfprint#33`](https://github.com/goodix-fp-linux-dev/libfprint/issues/33)
