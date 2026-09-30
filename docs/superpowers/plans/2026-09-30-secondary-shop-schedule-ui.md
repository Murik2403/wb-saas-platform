# Selected secondary-shop schedules UI

Base: 9e407d5. Backend contract: separate shop-schedules worktree; final registry parity will be updated only against the accepted backend commit.

1. Add typed read/configure transport for selected additional shops only. Keep exact target identity and immutable within-page UUID/body replay, expected generation, and existing scoped response guards. No primary fallback or automatic retry.
2. Mount a separate Settings panel. Server can_configure plus current owner and exact allowed action authorize controls. Defaults stay disabled. Sync and PDF cadence/period have distinct validated limits; PDF metrics are selected from server-supported values, at most eight.
3. Show next due UTC, last attempt and explicit failures without claiming delivery. Local PDF downloads use the scoped transport, never a direct href. Preserve form and receipt on failed save; refresh after accepted configuration.
4. Test primary no-fetch, viewer/ACL denial, disabled defaults, explicit opt-in, same-body retry key retention, changed payload rotation, generation conflicts, safe local downloads and malformed responses. Run relevant and full UI gates/build; independent review and native/browser deployment gates remain root-owned.

No scheduling engine, remote delivery, membership grants, marketplace writes or production changes in this UI worktree. Reload does not persist an ambiguous pending actor receipt; advise checking last saved schedule before making a new attempt.
