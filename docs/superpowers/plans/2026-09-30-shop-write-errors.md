# Selected shop write feedback plan

Goal: preserve the actual backend rejection reason and normalize only absent optional string metadata when editing selected-shop current cost estimates.

Evidence: frozen86 local actual API accepts GET cost rows with null supplier_article/note, but strict write DTO requires strings (idle synthetic PATCH returned422, revision1 unchanged). Browser first rejection409 may be fail-fast busy; it is not attributed without reproduction.

- [ ] Add focused error formatter for structured domain reason and422 validation shape, with safe generic fallback; never print input/private payloads.
- [ ] Use formatter in existing PATCH transport; no automatic retry and no scope/role/revision changes.
- [ ] Normalize optional supplier_article/note null only to empty strings; retain nm_id and numeric cost unchanged.
- [ ] Test failed exact retry, null metadata, structured409 and malformed errors; run frontend focused/full/build.
- [ ] Actual private HTTP cold-read+write confirms fail-fast409/no write; idle valid patch confirms success and other-shop isolation. Root repeats actual UI after reviewed commit/matching assets.
