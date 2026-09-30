# Quarantine-aware nested procurement — 2026-09-30

Build atop integration 086853b plus incident workflow. Read active ready-location FIFO holds without creating tables during preview. Keep physical ready units as lineage metadata, supply only unheld units to the pure nested resolver. Include active holds and a compact hold/release revision in source fingerprint; both hold and release invalidate previous preview, including a hold/release cycle with restored quantity. Do not count inbound/WB holds against ready units and do not alter physical stock.

Verify complete and partial ready holds, release restoration, unrelated locations, fingerprint changes/stale confirmation with no PO writes, no double allocation or preview DB writes. Commit this fix separately after the incident workflow; parent handles integration/deployment.
