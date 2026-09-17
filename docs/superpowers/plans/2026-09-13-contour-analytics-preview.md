# Contour analytics local preview

**Goal:** Implement the first three approved chart blocks as a local React preview.
**Architecture:** A separate HTML entry loads a scoped React feature. Typed illustrative datasets remain separate from rendering; existing application and API behavior remain intact.
**Tech Stack:** Existing React, TypeScript, Vite, lucide-react; SVG/CSS charts, no new dependencies.
**Spec:** User-approved Contour / Precision landing palette and latest three-chart mockup in this task.

## Constraints
- Warm paper #f4f2ed, graphite #252721, terracotta #df563a; 2–3px corners.
- Clearly label illustrative data; never imply a live store connection.
- Three blocks: revenue trend, unit expense breakdown, warehouse versus WB inventory.
- Keyboard-accessible points, visible values, accessible data tables, responsive layout.

## Execution
- [x] Add typed preview data and chart components in src/contour/.
- [x] Add isolated contour.html entry and scoped stylesheet.
- [x] Build; verify period, tooltip, comparison toggle and data-table interaction in browser.
- [x] Check desktop and mobile layout, then open the local preview.

