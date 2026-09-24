# Ribbit Intelligence Phase 1 Test Plan

Use this checklist after the backend and frontend Render services finish deploying from `main`.

## 1. Executive Dashboard

Prompt:

`Show me how my company is doing`

Expected:
- One Executive Company Health report.
- KPI cards populate.
- Attention Needed remains visible.
- Collapsed detail sections include Billing, Renewals, Tickets, Projects, and Opportunities.
- No duplicate legacy report/card sections appear underneath.

## 2. Customer 360 — exact customer

Prompt:

`Show me everything about Commercial Van Interiors`

Expected:
- One `Commercial Van Interiors — Customer 360` report.
- KPI cards for Active Tickets, Active Projects, Opportunities, Renewals, Net Billing, and Contacts.
- Collapsed sections for Contacts, Tickets, Projects, Opportunities, Billing, and Renewals.

## 3. Customer 360 — partial customer name

Prompt:

`Show me everything about Commercial Van`

Expected:
- If Commercial Van Interiors is the only clear match, Ribbit resolves it and loads Customer 360.
- If multiple customers match, Ribbit asks which customer you meant.

## 4. Customer contact lookup

Prompt:

`Show me the contacts for Commercial Van`

Expected:
- Ribbit resolves Commercial Van Interiors when it is the unique match.
- Only the intended contact results display.
- A large generic customer list should not appear first.

## 5. Unknown customer

Prompt:

`Show me everything about ZZZ Test Company That Does Not Exist`

Expected:
- Ribbit says the customer could not be found.
- It must not incorrectly say the customer exists but has zero contacts/tickets/etc.

## 6. All active renewals

Prompt:

`Show me all active renewals`

Expected:
- Source is Ribbit PostgreSQL.
- US and Canada are both eligible.
- Renewal summary cards populate Total, Active, Overdue, Due in 30, Due in 31–60, Due in 61–90.
- Detail records show Subscription ID, Customer, Market, Status, Renewal Date, and Days Remaining.

## 7. Canada-only renewals

Prompt:

`Show me Canada renewals`

Expected:
- Returned records are Canada market records.
- Canada count is greater than zero when Canada data exists in PostgreSQL.
- US records are excluded.

## 8. US + Canada renewals

Prompt:

`Show me renewals for US and Canada`

Expected:
- Both markets are included.
- Summary diagnostics include US count, Canada count, markets returned, and markets available in the database.

## 9. Renewal ingestion diagnostic

Prompt:

`Show me all renewals`

Check:
- `database_us_subscriptions`
- `database_canada_subscriptions`

Interpretation:
- Canada database count > 0: Canada is stored correctly and any missing result is a query/filter problem.
- Canada database count = 0: the daily CCWRenewals ingestion job is not storing Canada and should be investigated upstream.

## 10. Billing math

Prompt:

`Show me how my company is doing`

Open Billing Details.

Expected:
- Total Charges
- Total Credits
- Net Billing Activity
- Customers with Activity
- Charge Count
- Credit Count

Check that Net Billing Activity = Total Charges − Total Credits.

## 11. 429 fallback — renewals

While the OpenAI planner is temporarily rate-limited, run:

`Show me all active renewals`

Expected:
- `/api/chat` does not return HTTP 500 merely because OpenAI returned 429.
- Ribbit uses its deterministic fallback router.
- Renewal data is still retrieved from PostgreSQL.

## 12. 429 fallback — company health

While rate-limited, run:

`Show me how my company is doing`

Expected:
- No unhandled backend 500.
- Executive Company Health still routes and loads connected business data.

## 13. 429 fallback — contacts

While rate-limited, run:

`Show me the contacts for Commercial Van`

Expected:
- No unhandled backend 500.
- The deterministic router recognizes the contact query.
- Customer resolution and contact retrieval still work.

## 14. General-chat 429 behavior

While rate-limited, run:

`Explain what Ribbit can do`

Expected:
- The backend does not crash.
- Ribbit returns the friendly temporary-rate-limit message for a request that genuinely needs the language model.

## 15. Sidebar shortcuts

1. Click `Executive Dashboard`.
2. Confirm it submits/loads the company-health request.
3. Click `Customer 360`.
4. Confirm the composer is primed with `Show me everything about `.

## Quick regression set

Run these after future backend changes:

1. `Show me how my company is doing`
2. `Show me everything about Commercial Van Interiors`
3. `Show me the contacts for Commercial Van`
4. `Show me all active renewals`
5. `Show me Canada renewals`
6. `Show me all active tickets`
7. `Show our current opportunities`
