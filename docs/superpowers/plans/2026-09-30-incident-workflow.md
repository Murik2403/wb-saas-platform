# Incident workflow closure — 2026-09-30

Scope: preserve existing documented WB loss/compensation accounting. Add local responsible person, due date, outcome and append-only decision history. Store evidence as bounded SQLite blobs (PDF/PNG/JPEG/TXT signatures, hashes, safe download only), never external fetch or executable content. Owner/manager writes only; readers view/download.

Quarantine reserves concrete existing FIFO layer/location units in a transaction with idempotency keys, without stock/financial movements. SQL guards protect every layer decrement/reversal/delete; FIFO allocation subtracts holds and uses unheld quantities. Hold release is explicit and journalled. Loss accounting remains the existing confirmation flow after explicit hold release; quarantine itself never implies loss or compensation. Attachments and history remain in ordinary SQLite backups.

Verify invalid/oversized evidence, hashes, duplicate keys/conflicts, blocked/released FIFO, unaffected cost/loss accounting, concurrency holds, API roles and UI reader/loading/error behavior. Run relevant/full tests and build; root deploys only after integration.
