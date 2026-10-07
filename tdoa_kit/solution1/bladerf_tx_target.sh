#!/bin/bash
# bladerf_tx_target.sh  -  Solution 1: make the bladeRF transmit the TARGET signal.
#
# Run on the Pi/laptop the bladeRF is plugged into, at the target position.
#     ./bladerf_tx_target.sh            (uses gain 0)
#     ./bladerf_tx_target.sh 10         (uses gain 10)
# Stop with Ctrl+C.
#
# Written for bladeRF 2.0 micro (xA4 / xA9).  For an OLD bladeRF x40/x115 replace the
# three "tx1" lines with:   set frequency tx 434090000 ; set samplerate tx 2000000 ;
#                           set bandwidth tx 1500000 ; set txvga1 -35 ; set txvga2 0
GAIN=${1:-0}
FILE=$(dirname "$0")/target_bladerf_sc16q11.bin

bladeRF-cli -e "set frequency tx1 434090000; \
                set samplerate tx1 2000000; \
                set bandwidth tx1 1500000; \
                set gain tx1 ${GAIN}; \
                tx config file=${FILE} format=bin repeat=0 delay=0; \
                tx start; \
                tx wait"
# repeat=0 means "repeat forever".  Signal centre = 434.090 + 0.400 = 434.490 MHz.
