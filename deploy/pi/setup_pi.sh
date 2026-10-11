#!/bin/bash
# setup_pi.sh - Provision a Raspberry Pi 5 receiver station for TDOA operations.
# Run on each Pi once: chmod +x setup_pi.sh && ./setup_pi.sh <station_id>
# Example: ./setup_pi.sh node1
set -e

STATION_ID=${1:-node1}
echo "=== Provisioning Raspberry Pi Station: $STATION_ID ==="

sudo apt update
sudo apt install -y git cmake build-essential pkg-config libusb-1.0-0-dev \
                    python3-numpy python3-scipy chrony

# 1. Compile rtl-sdr-blog driver (supports both V3 and V4 dongles)
sudo apt purge -y ^librtlsdr rtl-sdr || true
cd /tmp
rm -rf rtl-sdr-blog
git clone https://github.com/rtlsdrblog/rtl-sdr-blog
cd rtl-sdr-blog
mkdir -p build && cd build
cmake ../ -DINSTALL_UDEV_RULES=ON
make -j4
sudo make install
sudo ldconfig

# 2. Blacklist default kernel DVB-T tuner driver
echo 'blacklist dvb_usb_rtl28xxu' | sudo tee /etc/modprobe.d/blacklist-rtl.conf

# 3. Enable and synchronize chrony
sudo systemctl enable --now chrony

# 4. Prepare local station workspace
mkdir -p "$HOME/tdoa/captures"
cp station_daemon.py health_check.py "$HOME/tdoa/" 2>/dev/null || true
chmod +x "$HOME/tdoa/"*.py 2>/dev/null || true

echo "=== Provisioning Complete ==="
echo "Next steps:"
echo "1. Copy the appropriate station config to $HOME/tdoa/station_config.json"
echo "2. Install systemd service: sudo cp tdoa-recorder.service /etc/systemd/system/ && sudo systemctl daemon-reload"
echo "3. Reboot the Pi (sudo reboot) and verify with: python3 $HOME/tdoa/health_check.py"
