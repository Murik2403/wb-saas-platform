# Four remaining DR acceptance gates

**Goal:** Continue the accepted recovery VM on a new private disk overlay and close the four remaining gates without changing production.
**Architecture:** Inherit the stopped accepted disk as immutable backing, new bounded authorization and watchdog, same pinned SSH and native Docker identity, QEMU private-only network. Keep all historical receipts.
**Tech stack:** Ubuntu 24.04, native Docker, Python, SQLite, rclone/S3, systemd/UFW/SSH, Windows QEMU TCG.
**Spec:** Owner request: finish four DR items on separate stand; production deployment of committed landing 243fb94 is a separate explicit authorization.

- [x] S3: actual signed S3 transport, never-versioned/Object Lock policy, retained recovery proof and exact-file retention. Do not equate filesystem alias or a mocked provider with acceptance.
- [x] Drift: review differences against approved site, save a new stand baseline, unchanged PASS and injected change/alert FAIL. Production baseline remains untouched.
- [x] Heavy API: profile actual restored producer data; fix a proven issue if found; retain cold/warm latency and complete HTTP verdict. Do not merely raise the acceptance timeout.
- [x] Host: inventory captured SSH/UFW/sysctl/Zabbix, apply compatible security intent on stand, prove effective configuration and restart persistence. Do not copy source IP/netplan or call real provider monitoring.
- [x] Final: actual reboot after accepted stand changes, services/jobs checks, stop owned VM, verify backing disk unchanged and localhost ports closed; report remaining exclusions honestly.

All local gates accepted; external provider/cutover exclusions remain in the final act.
