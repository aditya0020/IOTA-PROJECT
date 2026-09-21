#pragma once
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <errno.h>

struct SensorPacket {
  char packageId[65];
  uint32_t seq;
  double temperature, humidity, distance, acceleration, rotation;
  char status[65];
};

inline bool validIdentifier(const char* value) {
  const size_t n = strlen(value);
  if (!n || n > 64) return false;
  for (size_t i = 0; i < n; ++i) {
    char c = value[i];
    if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
          (c >= '0' && c <= '9') || c == '_' || c == '-')) return false;
  }
  return true;
}

inline bool parseNumber(const char* value, double low, double high, double& out) {
  if (!*value || *value == ' ' || *value == '\t') return false;
  char* end;
  errno = 0;
  out = strtod(value, &end);
  return !errno && end != value && !*end && isfinite(out) && out >= low && out <= high;
}

// Strictly parse the existing wire format. No change to UNO framing or fields.
inline bool parsePacket(const char* line, SensorPacket& packet) {
  if (strlen(line) >= 384) return false;
  char buffer[384];
  strcpy(buffer, line);
  size_t len = strlen(buffer);
  while (len && (buffer[len - 1] == '\n' || buffer[len - 1] == '\r')) buffer[--len] = 0;
  char* fields[8];
  fields[0] = buffer;
  int count = 1;
  for (char* p = buffer; *p; ++p) {
    if (*p == '|') {
      if (count == 8) return false;
      *p = 0;
      fields[count++] = p + 1;
    }
  }
  if (count != 8 || !validIdentifier(fields[0])) return false;
  const char* keys[] = {"seq=", "T_C=", "H_pct=", "Distance_cm=", "Accel_mps2=", "Gyro_radps=", "Status="};
  for (int i = 1; i < 8; ++i) {
    size_t length = strlen(keys[i - 1]);
    if (strncmp(fields[i], keys[i - 1], length) != 0) return false;
    fields[i] += length;
  }
  if (!*fields[1]) return false;
  uint64_t seq = 0;
  for (const char* p = fields[1]; *p; ++p) {
    if (*p < '0' || *p > '9') return false;
    seq = seq * 10 + (*p - '0');
    if (seq > UINT32_MAX) return false;
  }
  if (!parseNumber(fields[2], -40, 125, packet.temperature) ||
      !parseNumber(fields[3], 0, 100, packet.humidity) ||
      !parseNumber(fields[4], 0, 500, packet.distance) ||
      !parseNumber(fields[5], 0, 280, packet.acceleration) ||
      !parseNumber(fields[6], 0, 61, packet.rotation) || !validIdentifier(fields[7])) return false;
  strcpy(packet.packageId, fields[0]);
  strcpy(packet.status, fields[7]);
  packet.seq = static_cast<uint32_t>(seq);
  return true;
}
