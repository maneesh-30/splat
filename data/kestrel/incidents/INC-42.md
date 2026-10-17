---
id: INC-42
author: priya.rao@kestrel.example
started: 2026-08-17
resolved: 2026-09-07
metric: api_p95_latency_ms
---

# Degraded storage volume

Timeline: 08:40 p95 latency alerts. 09:10 traced to a degraded storage volume under the primary database. Mitigated by failing over to a replica on 2026-09-07 after the vendor confirmed the volume fault. Latency stayed elevated for three weeks.
