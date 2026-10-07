/*
  esp32_beacon.ino  -  Solution 2: turns a ~$5 ESP32 board into a Wi-Fi test transmitter.

  Flash TWO boards with this sketch (Arduino IDE, board "ESP32 Dev Module"):
    * Board 1 = REFERENCE  : keep the line  #define ROLE_REFERENCE
    * Board 2 = TARGET     : put // in front of that line (comment it out)

  Each board sends a short Wi-Fi "beacon" packet every PERIOD_MS milliseconds on CHANNEL.
  The REFERENCE packet is short (~0.64 ms), the TARGET packet is long (~1.8 ms),
  so the software can tell them apart just by their length.
  Packets are sent at 1 Mbit/s (802.11b, DSSS): a ~20 MHz wide, noise-like signal
  that gives a sharp correlation peak.
*/
#include <WiFi.h>
#include "esp_wifi.h"

#define ROLE_REFERENCE            // <-- comment out for the TARGET board

const uint8_t  CHANNEL   = 6;     // pick the quietest channel at your site (1..13)
const uint32_t PERIOD_MS = 10;    // one packet every 10 ms

#ifdef ROLE_REFERENCE
const char*   SSID     = "TDOA-REF";
const uint8_t PAD_LEN  = 0;                                  // no padding -> short packet
uint8_t       MAC[6]   = {0x02, 0x54, 0x44, 0x4F, 0x41, 0x01};
#else
const char*   SSID     = "TDOA-TARGET-0123456789ABCDEFGHI";  // long name
const uint8_t PAD_LEN  = 120;                                // extra bytes -> long packet
uint8_t       MAC[6]   = {0x02, 0x54, 0x44, 0x4F, 0x41, 0x02};
#endif

uint8_t pkt[400];
int     pkt_len = 0;

void build_beacon() {
  int i = 0;
  pkt[i++] = 0x80; pkt[i++] = 0x00;                    // frame type: beacon
  pkt[i++] = 0x00; pkt[i++] = 0x00;                    // duration
  for (int k = 0; k < 6; k++) pkt[i++] = 0xFF;         // to: everyone
  for (int k = 0; k < 6; k++) pkt[i++] = MAC[k];       // from
  for (int k = 0; k < 6; k++) pkt[i++] = MAC[k];       // network id (BSSID)
  pkt[i++] = 0x00; pkt[i++] = 0x00;                    // sequence (filled in by the ESP32)
  for (int k = 0; k < 8; k++) pkt[i++] = 0x00;         // timestamp
  pkt[i++] = 0x64; pkt[i++] = 0x00;                    // beacon interval
  pkt[i++] = 0x01; pkt[i++] = 0x00;                    // capabilities (long preamble)
  uint8_t n = strlen(SSID);
  pkt[i++] = 0x00; pkt[i++] = n;                       // network name
  memcpy(&pkt[i], SSID, n); i += n;
  pkt[i++] = 0x01; pkt[i++] = 0x01; pkt[i++] = 0x82;   // supported rate: 1 Mbit/s
  pkt[i++] = 0x03; pkt[i++] = 0x01; pkt[i++] = CHANNEL;// channel number
  if (PAD_LEN > 0) {                                   // vendor element used as padding
    pkt[i++] = 0xDD; pkt[i++] = PAD_LEN;
    for (int k = 0; k < PAD_LEN; k++) pkt[i++] = (uint8_t)(k * 37 + 11);
  }
  pkt_len = i;
}

void setup() {
  Serial.begin(115200);
  WiFi.mode(WIFI_STA);                                  // radio on, not connected anywhere
  esp_wifi_set_promiscuous(true);
  esp_wifi_set_channel(CHANNEL, WIFI_SECOND_CHAN_NONE);
  esp_wifi_set_max_tx_power(8);                         // units of 0.25 dBm -> 2 dBm (keep it low)
  // Force 1 Mbit/s long-preamble DSSS. If your Arduino-ESP32 version does not know this
  // function, delete the line - the beacons are usually sent at 1 Mbit/s anyway.
  esp_wifi_config_80211_tx_rate(WIFI_IF_STA, WIFI_PHY_RATE_1M_L);
  build_beacon();
  Serial.printf("TDOA beacon '%s' on channel %d, %d bytes every %lu ms\n",
                SSID, CHANNEL, pkt_len, (unsigned long)PERIOD_MS);
}

void loop() {
  esp_wifi_80211_tx(WIFI_IF_STA, pkt, pkt_len, true);
  delay(PERIOD_MS);
}
