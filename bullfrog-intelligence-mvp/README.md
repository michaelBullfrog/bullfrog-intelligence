# Bullfrog Intelligence — Customer Invoices

Replace:

- backend/app/connectors/revio.py
- backend/app/orchestrator.py
- frontend/app/page.tsx
- frontend/app/styles.css

The invoice operation requires a numeric customer ID because the Rev.io route is:

GET /billing/api/v1/customers/{customerId}/invoices

Example questions:

- Show invoices for customer 123
- Show unpaid invoices for customer 123
- Summarize the invoice balance for customer 123
- Are any invoices overdue for customer 123?
