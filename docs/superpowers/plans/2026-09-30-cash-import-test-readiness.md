# Wait for actual cash import preview/readiness

Fresh release generator full suite exposed an intermittent cash-accounts test failure: it queried/clicked confirmation before the async preview/retry busy state had settled. Production logic is unchanged. Wait for the actual confirm button and returned bank-id row, then explicitly wait for enabled consent/confirm before both attempts. Preserve the existing identical UUID assertion. No arbitrary sleeps, suppressed React warnings or application changes.

Run the three component tests repeatedly and full suite, independent review, commit two owned files. Existing failing213 suite212 pass/1 failure is retained as evidence, not relabelled green.

Verification: three actual separate focused iterations3 tests each passed; full213/36files passed. Independent focused3 passed. Test-only, runtime source and built assets unchanged.
