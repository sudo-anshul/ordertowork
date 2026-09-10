# Frontend acceptance checks

10 September 2026. Checked against the running Vite frontend on localhost:5173 and the real FastAPI backend/worker. The `ordertowork-secondary` browser used an isolated bakery QA workspace and development identities; the merchandise acceptance workspace was not changed.

## Passed in the browser

- Created a bakery workspace through the setup form, with sample data explicitly selected. At 320 CSS pixels the document remained 320 pixels wide.
- Opened the seeded bakery change request. The customer’s 36-cupcake Friday request showed $164; alternatives showed $144/$24 top-up for Saturday and $116/$10 top-up for the smaller Friday order. The original agreement remained revision 1.
- Created a new order for six vanilla cupcakes with blue icing and recipe V1. The backend returned a $24 total and $12 required deposit. There was no accepted revision before customer consent.
- Entered pickup at 10am in America/New_York while the browser’s timezone differed. The proposal, customer page, receipt, and production ticket all showed the intended 10am business time.
- Created a private customer review link through the API. The standalone customer page had no owner navigation and no horizontal overflow at 320px or 390px.
- Tried approval without consent: native validation blocked submission. Focusing the checkbox, pressing Space, Tab, and Enter approved the displayed revision and produced “Your order is confirmed.” The receipt correctly kept the $12 deposit outstanding.
- Recorded the received $12 QA deposit with a reference. The backend returned the updated financial record and released the order for work.
- Created a separate development operator identity and added it through the team UI. Its workspace navigation contained only Production. Its ticket contained the agreed product/specification, pickup, and resource allocations, without monetary values or customer email/messages.
- The operator’s direct request to the full orders endpoint returned HTTP 403. Starting the displayed revision through the confirmation dialog succeeded and changed the ticket to In production.
- Verified that an asynchronously loaded order focuses its H1 after the focus fix.

## Material fixes from review and QA

- Pending proposal/decision headings take precedence over readiness of the original accepted revision. The action names the **accepted** work order.
- New-order customer receipts and failure/change-request copy no longer claim that an earlier confirmed order exists.
- Production-start confirmation captures the displayed revision and sends `expected_revision`; a background refresh cannot silently substitute a newer revision.
- Workspace context lives in a stable module, preventing the observed development hot-reload context mismatch. A display-error boundary provides a reload action instead of a blank page.
- Page headings receive focus when async content mounts, rather than only during the earlier route transition.
- Business/customer/resource label limits match the backend. Development ports stay bound to loopback with strict port selection.
- Completed analysis has an owner-only evidence disclosure. It uses persisted job mode/status, actual tool event counts and results, source evidence, and missing fields. Reference mode explicitly says no AI model was called. A loading state prevents a temporary “mode not recorded” display while the job is fetched.

The root integration run independently checked the disclosure against a persisted merchandise job, including two recorded events, the $900 requested total, both resource shortages, and source evidence. Root also checked replaced customer links and the request-change path.

## Scope

These checks exercised real local persistence and API actions in reference mode. They do not establish live Bedrock execution, external email delivery, payment capture, or full WCAG conformance. Browser validation included keyboard interaction and narrow layouts; the repository’s backend tests cover the deeper permission, transaction, and revision invariants.

The final frontend production build and full npm audit passed after the fixes, with zero reported npm vulnerabilities.
