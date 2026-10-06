TDOA demonstrator software kit  (see TDOA_Build_Guide.docx for full instructions)

common/      shared functions (tdoa_lib.py) and quicklook.py (check one recording)
solution1/   RTL-SDR receivers, Pluto reference, bladeRF target at 433 MHz
solution2/   bladeRF + Pluto receivers, ESP32 Wi-Fi reference and target at 2.4 GHz

First thing to run (laptop, needs Python 3 + numpy scipy matplotlib):
  cd solution1
  python3 simulate_captures.py
  python3 tdoa_process.py --config config_sim.json --dir captures_sim
