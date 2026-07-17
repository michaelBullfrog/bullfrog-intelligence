# Bullfrog Intelligence Architecture

## Request flow

1. Employee signs in through Microsoft Entra ID.
2. Frontend sends the question and user identity to FastAPI.
3. Backend checks group-based permissions.
4. Orchestrator determines the request intent.
5. An approved connector queries the correct platform.
6. The backend normalizes the records.
7. The AI generates a grounded answer using only returned data.
8. The response includes source references.
9. Report requests query approved warehouse views and create downloadable files.
10. Every request and write action is logged.

## Data sources

### Live connectors
- Rev.io PSA
- Webex Control Hub
- Webex Contact Center
- Cisco CCW-R
- Zoho
- Meraki
- Clerk Chat
- Microsoft Graph

### Knowledge sources
- SharePoint
- Google Drive
- GitHub Help Center
- Internal PDFs and Word documents
- TAC case notes
- Implementation procedures

### Reporting warehouse
- Customers
- Customer platform mappings
- Tickets
- Ticket activity
- Contact Center intervals
- Subscription renewals
- Meetings and calls
- Customer health metrics

## Security boundaries

- Credentials stay in Key Vault or environment secrets.
- The model cannot make arbitrary HTTP requests.
- SQL access is restricted to approved views.
- All platform actions are read-only initially.
- Write actions require explicit human confirmation.
- Permissions are enforced before retrieving data.
- Customer-specific content can be restricted by Entra group or database entitlement.
