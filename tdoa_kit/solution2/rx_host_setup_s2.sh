#!/bin/bash
# rx_host_setup_s2.sh - run ONCE on each Pi that will host a bladeRF or Pluto RECEIVER (Solution 2).
set -e
sudo apt update
sudo apt install -y git python3-numpy python3-scipy python3-pip python3-cffi chrony \
                    libiio-utils python3-libiio bladerf libbladerf-dev
pip3 install --break-system-packages pyadi-iio
# libbladeRF Python bindings (only needed on the bladeRF nodes)
cd ~ && rm -rf bladeRF && git clone --depth 1 https://github.com/Nuand/bladeRF.git
cd bladeRF/host/libraries/libbladeRF_bindings/python
pip3 install --break-system-packages .
python3 -c "from bladerf import _bladerf; print('bladeRF python bindings OK')"
sudo systemctl enable --now chrony
mkdir -p ~/tdoa/captures
echo "Done. Test:  bladeRF-cli -p   (bladeRF)   or   iio_info -s   (Pluto)"
