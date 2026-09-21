#pragma once
// Copy to config.h (gitignored). Keep the working UNO firmware unchanged.
#define WIFI_SSID "YOUR_WIFI_SSID"
#define WIFI_PASSWORD "YOUR_WIFI_PASSWORD"
#define API_HOST "192.168.1.100"
#define API_PORT 5000
#define DEVICE_API_KEY "YOUR_DEVICE_API_KEY"
#define NTP_SERVER "pool.ntp.org"
// Protocol: UNO D11 TX -> divider -> D6 RX; D5 TX -> UNO D12 RX.
#define UNO_RX_PIN D6
#define UNO_TX_PIN D5
#define UNO_BAUD 9600
