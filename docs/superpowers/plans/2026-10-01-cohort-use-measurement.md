# Cohort useful use measurement implementation plan

Goal: close F201 missing D7, connection/result shares and time-to-first-result collection without inventing a customer cohort.
Architecture: preserve first milestones; append one trusted verified-report use event per UTC day through existing tenant outbox and scoped Gateway endpoint. D7 is use on the seventh UTC calendar day after first verified result, eligible only after that day ends; old activations without first-day activity remain unobserved.
Constraints: no contact, production customer writes, payment or advertising changes. No arbitrary client tracking event endpoint or personal event properties.
- [x] Add regression tests for repeated day delivery, auth/key validation, D7 maturity and unavailable history.
- [x] Extend outbox, internal receiver and growth summary, render explicit definitions in admin.
- [x] Run Gateway and tenant growth suites; update Graphify and commit.
