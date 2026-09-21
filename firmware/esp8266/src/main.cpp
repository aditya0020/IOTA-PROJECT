#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <SoftwareSerial.h>
#include <ArduinoJson.h>
#include <time.h>
#include "config.h"
#include "protocol.h"

SoftwareSerial gateway(UNO_RX_PIN, UNO_TX_PIN);
WiFiClient client;
struct Pending { SensorPacket data; uint32_t capturedMs; String timestamp, id, json; };
constexpr size_t QUEUE_SIZE = 24;
Pending queue[QUEUE_SIZE];
size_t head = 0, queued = 0;
char line[384];
size_t lineSize = 0;
bool overflow = false;
String sessionId;
uint32_t ordinal = 0, nextAttempt = 0, responseStart = 0;
uint32_t backoff = 1000;
String responseLine;
bool awaitingResponse = false;
SensorPacket lastPacket{};
bool haveLastPacket = false;

void ack(uint32_t seq) { gateway.print("ACK|"); gateway.println(seq); }

bool samePacket(const SensorPacket& a, const SensorPacket& b) {
  return a.seq == b.seq && !strcmp(a.packageId, b.packageId) && !strcmp(a.status, b.status) &&
    a.temperature == b.temperature && a.humidity == b.humidity && a.distance == b.distance &&
    a.acceleration == b.acceleration && a.rotation == b.rotation;
}

void acceptLine() {
  SensorPacket parsed{};
  if (!parsePacket(line, parsed)) { Serial.println("Rejected malformed sensor packet"); return; }
  // A consecutive retransmission is ACKed again without creating another reading.
  if (haveLastPacket && samePacket(parsed, lastPacket)) { ack(parsed.seq); return; }
  if (queued == QUEUE_SIZE) { Serial.println("Queue full: no ACK; waiting for upload"); return; }
  Pending& item = queue[(head + queued) % QUEUE_SIZE];
  item.data = parsed;
  item.capturedMs = millis();
  item.timestamp = "";
  item.json = "";
  item.id = sessionId + "-" + String(++ordinal) + "-" + String(parsed.seq);
  ++queued;
  lastPacket = parsed;
  haveLastPacket = true;
  ack(parsed.seq); // ACK means accepted into the RAM queue, not committed by Flask.
}

void readSerial() {
  while (gateway.available()) {
    char c = gateway.read();
    if (c == '\n') {
      if (!overflow && lineSize) { line[lineSize] = 0; acceptLine(); }
      lineSize = 0; overflow = false;
    } else if (c != '\r') {
      if (lineSize < sizeof(line) - 1) line[lineSize++] = c;
      else overflow = true;
    }
  }
}

bool encode(Pending& item) {
  if (item.json.length()) return true; // Retries preserve exact ID, timestamp and body.
  time_t current = time(nullptr);
  if (current < 1704067200) return false;
  time_t captured = current - (uint32_t(millis() - item.capturedMs) / 1000);
  struct tm utc;
  gmtime_r(&captured, &utc);
  char timestamp[25];
  strftime(timestamp, sizeof(timestamp), "%Y-%m-%dT%H:%M:%SZ", &utc);
  JsonDocument doc;
  doc["package_id"] = item.data.packageId;
  doc["reading_id"] = item.id;
  doc["seq"] = item.data.seq;
  doc["timestamp"] = timestamp;
  doc["temperature"] = item.data.temperature;
  doc["humidity"] = item.data.humidity;
  doc["distance"] = item.data.distance;
  doc["accel_mps2"] = item.data.acceleration;
  doc["gyro_radps"] = item.data.rotation;
  doc["source_status"] = item.data.status;
  serializeJson(doc, item.json);
  return true;
}

void finishAttempt(int status) {
  client.stop(); awaitingResponse = false;
  if (status == 200 || status == 201) {
    Serial.printf("Stored seq=%lu HTTP=%d\n", (unsigned long)queue[head].data.seq, status);
    queue[head].json = "";
    head = (head + 1) % QUEUE_SIZE; --queued;
    backoff = 1000; nextAttempt = millis();
  } else {
    // Keep the head item, including on 4xx. Correct configuration/data before continuing.
    Serial.printf("Upload failed HTTP=%d; retained; retry in %lu ms\n", status, (unsigned long)backoff);
    nextAttempt = millis() + backoff;
    backoff = min(backoff * 2, uint32_t(30000));
  }
}

void upload() {
  if (awaitingResponse) {
    while (client.available()) {
      char c = client.read();
      if (c == '\n') {
        int status = 0;
        if (sscanf(responseLine.c_str(), "HTTP/%*s %d", &status) != 1) status = -1;
        finishAttempt(status); return;
      }
      if (responseLine.length() >= 128) { finishAttempt(-1); return; }
      responseLine += c;
    }
    if (!client.connected() || millis() - responseStart > 4000) finishAttempt(-1);
    return;
  }
  if (!queued || WiFi.status() != WL_CONNECTED || int32_t(millis() - nextAttempt) < 0) return;
  Pending& item = queue[head];
  if (!encode(item)) return;
  // Numeric LAN IP avoids blocking DNS; bound connect latency below the packet period.
  IPAddress address;
  if (!address.fromString(API_HOST)) { finishAttempt(-2); return; }
  if (!client.connect(address, API_PORT)) { finishAttempt(-1); return; }
  client.print(String("POST /api/readings HTTP/1.1\r\nHost: ") + API_HOST + ":" + API_PORT +
    "\r\nContent-Type: application/json\r\nX-API-Key: " + DEVICE_API_KEY +
    "\r\nConnection: close\r\nContent-Length: " + item.json.length() + "\r\n\r\n" + item.json);
  readSerial();
  responseLine = ""; responseStart = millis(); awaitingResponse = true;
}

void setup() {
  Serial.begin(115200);
  gateway.begin(UNO_BAUD);
  client.setTimeout(150);
  client.setNoDelay(true);
  sessionId = String(ESP.getChipId(), HEX) + "-" + String(ESP.random(), HEX);
  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  configTime(0, 0, NTP_SERVER);
  Serial.println("Gateway ready: 9600 baud; ACK on RAM queue acceptance");
}

void loop() { readSerial(); upload(); yield(); }
