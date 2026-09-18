# Local visual fixture

Run the existing Vite dev command on a free loopback port, then open:

- `/qa/onboarding/index.html?case=error`
- `/qa/onboarding/index.html?case=zero`
- `/qa/onboarding/index.html?case=success`

This HTML entry is outside the production `src` entry. It imports the real
OnboardingPanel and product styles, but replaces `window.fetch` with an entirely
local synthetic transport. There is no network fallback. The warning banner
labels all data as fictional; no WB credentials, customer data or gateway
events are used. `is_demo:false` is only a fixture input for exercising the
real-user branch of the component; it does not connect to a real tenant.

Select the test product before opening the report. The separate test-cost
input lets QA switch zero/positive cost. In the failed-sync scenario, the
load-history button switches the fixture to success without any WB request.
This fixture is for visual QA, not proof of real API behavior.
