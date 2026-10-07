#!/bin/bash
# pi_setup.sh - run ONCE on every Raspberry Pi (Raspberry Pi OS 64-bit, Bookworm or newer).
#   chmod +x pi_setup.sh && ./pi_setup.sh
set -e
sudo apt update
sudo apt install -y git cmake build-essential pkg-config libusb-1.0-0-dev \
                    python3-numpy python3-scipy python3-matplotlib chrony

# --- RTL-SDR driver (RTL-SDR Blog version: supports both V3 and V4 dongles) ---
sudo apt purge -y ^librtlsdr rtl-sdr || true
cd ~ && rm -rf rtl-sdr-blog && git clone https://github.com/rtlsdrblog/rtl-sdr-blog
cd rtl-sdr-blog && mkdir -p build && cd build
cmake ../ -DINSTALL_UDEV_RULES=ON && make -j4 && sudo make install && sudo ldconfig
# stop the TV-tuner kernel driver from grabbing the dongle
echo 'blacklist dvb_usb_rtl28xxu' | sudo tee /etc/modprobe.d/blacklist-rtl.conf

# --- Accurate clock: chrony (NTP) ---
sudo systemctl enable --now chrony

# --- Folder for recordings ---
mkdir -p ~/tdoa/captures
echo "Done. Reboot now (sudo reboot), then test with:  rtl_test -t"
