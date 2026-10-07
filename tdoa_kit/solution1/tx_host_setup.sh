#!/bin/bash
# tx_host_setup.sh - run ONCE on each Pi/laptop that drives a TRANSMITTER (Pluto or bladeRF).
set -e
sudo apt update
sudo apt install -y python3-numpy python3-pip libiio-utils python3-libiio bladerf
pip3 install --break-system-packages pyadi-iio
echo "Pluto check:   iio_info -s          (should list the PlutoSDR)"
echo "bladeRF check: bladeRF-cli -p       (should list the bladeRF)"
echo "bladeRF FPGA:  download the 'hosted' FPGA image for your model from nuand.com/fpga"
echo "               then load it with:  bladeRF-cli -l hostedxA4-latest.rbf   (xA9: hostedxA9-latest.rbf)"
