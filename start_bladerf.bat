@echo off
echo ===================================================
echo   Starting bladeRF 2.0 Target Transmitter @ 434.490 MHz
echo ===================================================
bladeRF-cli -e "set frequency tx1 434090000; set samplerate tx1 2000000; set bandwidth tx1 1500000; set gain tx1 42; tx config file=d:\NanoSat_TDOA\tdoa_kit\solution1\target_bladerf_sc16q11.bin format=bin repeat=0 delay=0; tx start; tx wait"
pause
