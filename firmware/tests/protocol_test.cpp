#include <assert.h>
#include <stdio.h>
#include "../esp8266/include/protocol.h"
int main() {
  SensorPacket p{};
  const char* valid = "PKG001|seq=156|T_C=27.50|H_pct=61.00|Distance_cm=14.20|Accel_mps2=9.81|Gyro_radps=0.04|Status=NORMAL\r\n";
  assert(parsePacket(valid, p));
  assert(!strcmp(p.packageId, "PKG001") && p.seq == 156);
  assert(p.temperature == 27.5 && p.humidity == 61 && p.distance == 14.2);
  assert(p.acceleration == 9.81 && p.rotation == .04 && !strcmp(p.status, "NORMAL"));
  const char* invalid[] = {
    "ACK|156", "", "PKG001||seq=156",
    "PKG001|seq=-1|T_C=27|H_pct=61|Distance_cm=14|Accel_mps2=9.81|Gyro_radps=.04|Status=NORMAL",
    "PKG001|seq=4294967296|T_C=27|H_pct=61|Distance_cm=14|Accel_mps2=9.81|Gyro_radps=.04|Status=NORMAL",
    "PKG001|seq=1|T_C=nan|H_pct=61|Distance_cm=14|Accel_mps2=9.81|Gyro_radps=.04|Status=NORMAL",
    "PKG001|seq=1|T_C=27|H_pct=101|Distance_cm=14|Accel_mps2=9.81|Gyro_radps=.04|Status=NORMAL",
    "PKG001|seq=1|T_C=27|H_pct=61|Distance_cm=14|Accel_mps2=9.81|Gyro_radps=.04|Status=NORMAL|extra=1",
    "PKG001|seq=1|T_C=27|H_pct=61|Distance_cm=14|Accel_mps2=9.81|Gyro_radps=.04|Status=",
    "PKG001|seq=1|T_C=27oops|H_pct=61|Distance_cm=14|Accel_mps2=9.81|Gyro_radps=.04|Status=NORMAL"
  };
  for (const char* line : invalid) assert(!parsePacket(line, p));
  puts("Protocol parser: exact hardware fixture and 10 rejection cases passed");
}
