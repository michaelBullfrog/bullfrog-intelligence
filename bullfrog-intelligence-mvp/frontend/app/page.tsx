"use client";

import { FormEvent, useMemo, useState } from "react";

type Ticket = {
  ticket_id: string;
  subject: string;
  customer_name: string;
  status: string;
  priority?: string | null;
  severity?: string | null;
  ticket_type?: string | null;
  assigned_engineer?: string | null;
  created_at?: string | null;
  modified_at?: string | null;
  age_days?: number | null;
};

type EntityRecord = Record<string, unknown>;

type ChatResult = {
  answer: string;
  intent: string;
  conversation_id?: string;
  download_url?: string | null;
  download_name?: string | null;
  data?: {
    tickets?: Ticket[];
    projects?: EntityRecord[];
    contacts?: EntityRecord[];
    opportunities?: EntityRecord[];
    invoices?: EntityRecord[];
    ledger_entries?: EntityRecord[];
    ledger_summary?: EntityRecord;
    customer?: EntityRecord;
    customers?: EntityRecord[];
    customer_matches?: EntityRecord[];
    customer_name?: string;
    opportunity?: EntityRecord;
    activity?: EntityRecord | EntityRecord[];
    project_id?: string | number;
    report?: Record<string, unknown>;
    source?: string;
    [key: string]: unknown;
  };
};

type ChatMessage = {
  id: string;
  role: "assistant" | "user";
  text: string;
  result?: ChatResult;
};

const starterPrompts = [
  "Show me all active tickets",
  "Show me all projects",
  "Find contacts for Shamrock Chimney",
  "Show Shamrock Chimney's billing ledger",
  "Create a PDF of the previous results",
  "Show our current opportunities",
];

function createId() {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random()}`;
}

function formatDate(value?: unknown) {
  if (!value) return "Not available";

  const date = new Date(String(value));
  if (Number.isNaN(date.getTime())) return String(value);

  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

function formatCurrency(value?: unknown) {
  if (value === undefined || value === null || value === "") {
    return "Not available";
  }

  const numeric =
    typeof value === "number"
      ? value
      : Number(String(value).replace(/[$,]/g, ""));

  if (Number.isNaN(numeric)) return String(value);

  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
  }).format(numeric);
}

function badgeClass(value?: unknown) {
  return String(value ?? "unknown")
    .toLowerCase()
    .replaceAll(" ", "-")
    .replaceAll("_", "-");
}

function pick(
  record: EntityRecord | undefined,
  keys: string[],
  fallback = "Not available"
): string {
  if (!record) return fallback;

  for (const key of keys) {
    const value = record[key];
    if (value !== undefined && value !== null && value !== "") {
      if (typeof value === "object") {
        return JSON.stringify(value);
      }
      return String(value);
    }
  }

  return fallback;
}

function pickRaw(record: EntityRecord | undefined, keys: string[]): unknown {
  if (!record) return undefined;

  for (const key of keys) {
    const value = record[key];
    if (value !== undefined && value !== null && value !== "") {
      return value;
    }
  }

  return undefined;
}

function normalizeRecords(value: unknown): EntityRecord[] {
  if (Array.isArray(value)) {
    return value.filter(
      (item): item is EntityRecord => typeof item === "object" && item !== null
    );
  }

  if (value && typeof value === "object") {
    const record = value as EntityRecord;

    for (const key of ["items", "records", "results", "entries", "activities", "data"]) {
      const nested = record[key];
      if (Array.isArray(nested)) {
        return nested.filter(
          (item): item is EntityRecord => typeof item === "object" && item !== null
        );
      }
    }

    return [record];
  }

  return [];
}

function formatAnswerText(answer: string): string[] {
  return answer
    .replace(/\s+(?=\d+\.\s+)/g, "\n")
    .replace(/\s+(?=(Summary|Total|Recently|Important|If you'd))/g, "\n")
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function getProjectStatus(project: EntityRecord): { name: string; color?: string } {
  const rawStatus = project.projectStatus;

  if (rawStatus && typeof rawStatus === "object") {
    const statusRecord = rawStatus as EntityRecord;
    const name = pick(
      statusRecord,
      ["projectStatusName", "statusName", "name"],
      "Unknown"
    );
    const color = pick(statusRecord, ["projectStatusColor", "statusColor", "color"], "");

    return {
      name,
      color: color || undefined,
    };
  }

  return {
    name: pick(project, ["projectStatusName", "statusName", "status", "state"], "Unknown"),
    color: pick(project, ["projectStatusColor", "statusColor"], "") || undefined,
  };
}

function normalizeHexColor(value?: string) {
  if (!value) return undefined;
  const cleaned = value.trim();

  if (/^#[0-9a-fA-F]{6}$/.test(cleaned)) return cleaned;
  if (/^[0-9a-fA-F]{6}$/.test(cleaned)) return `#${cleaned}`;
  return undefined;
}

function MiniMetric({
  label,
  value,
}: {
  label: string;
  value: string | number;
}) {
  return (
    <article className="miniMetric">
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function ResultCard({
  eyebrow,
  title,
  subtitle,
  status,
  statusColor,
  fields,
  footer,
}: {
  eyebrow: string;
  title: string;
  subtitle?: string;
  status?: string;
  statusColor?: string;
  fields: Array<{ label: string; value: string }>;
  footer?: string[];
}) {
  return (
    <article className="resultCard">
      <div className="resultCardTop">
        <div>
          <p className="cardEyebrow">{eyebrow}</p>
          <h4>{title}</h4>
        </div>

        {status && (
          <span
            className={`statusBadge ${badgeClass(status)}`}
            style={
              normalizeHexColor(statusColor)
                ? {
                    backgroundColor: `${normalizeHexColor(statusColor)}20`,
                    borderColor: normalizeHexColor(statusColor),
                    color: normalizeHexColor(statusColor),
                  }
                : undefined
            }
          >
            {status}
          </span>
        )}
      </div>

      {subtitle && <p className="cardSubtitle">{subtitle}</p>}

      <div className="resultCardMeta">
        {fields.map((field) => (
          <div key={`${field.label}-${field.value}`}>
            <span>{field.label}</span>
            <strong>{field.value}</strong>
          </div>
        ))}
      </div>

      {footer && footer.length > 0 && (
        <div className="resultCardFooter">
          {footer.map((item) => (
            <span key={item}>{item}</span>
          ))}
        </div>
      )}
    </article>
  );
}

function StructuredResponse({
  result,
  apiBase,
}: {
  result: ChatResult;
  apiBase: string;
}) {
  const tickets = result?.data?.tickets ?? [];
  const projects = result?.data?.projects ?? [];
  const contacts = result?.data?.contacts ?? [];
  const opportunities = result?.data?.opportunities ?? [];
  const invoices = result?.data?.invoices ?? [];
  const ledgerEntries = result?.data?.ledger_entries ?? [];
  const ledgerSummary = result?.data?.ledger_summary;
  const customer = result?.data?.customer;
  const customers = result?.data?.customers ?? result?.data?.customer_matches ?? [];
  const opportunity = result?.data?.opportunity;
  const activity = normalizeRecords(result?.data?.activity);

  const ticketSummary = useMemo(() => {
    const byStatus: Record<string, number> = {};
    const byEngineer: Record<string, number> = {};

    for (const ticket of tickets) {
      byStatus[ticket.status] = (byStatus[ticket.status] ?? 0) + 1;
      const engineer = ticket.assigned_engineer || "Unassigned";
      byEngineer[engineer] = (byEngineer[engineer] ?? 0) + 1;
    }

    return {
      byStatus,
      byEngineer,
      oldest: tickets.reduce((max, ticket) => Math.max(max, ticket.age_days ?? 0), 0),
    };
  }, [tickets]);

  const projectsActive = projects.filter((project) =>
    ["active", "open", "in progress"].includes(
      getProjectStatus(project).name.toLowerCase()
    )
  ).length;

  const uniqueCustomers = new Set(
    projects.map((project) =>
      pick(project, ["customerName", "customer_name", "companyName", "accountName"])
    )
  ).size;

  const uniqueOwners = new Set(
    projects.map((project) =>
      pick(project, ["projectManager", "projectManagerName", "ownerName", "managerName"])
    )
  ).size;

  const hasStructuredResults =
    tickets.length > 0 ||
    projects.length > 0 ||
    contacts.length > 0 ||
    opportunities.length > 0 ||
    invoices.length > 0 ||
    ledgerEntries.length > 0 ||
    Boolean(customer) ||
    customers.length > 0 ||
    Boolean(opportunity) ||
    activity.length > 0;

  if (!hasStructuredResults && !result.download_url) {
    return null;
  }

  return (
    <div className="structuredResponse">
      {result.download_url && (
        <div className="downloadBanner">
          <div>
            <span>Report ready</span>
            <strong>{result.download_name ?? "Ribbit Report.pdf"}</strong>
          </div>
          <a
            href={`${apiBase}${result.download_url}`}
            target="_blank"
            rel="noreferrer"
            className="downloadButton"
          >
            Download PDF
          </a>
        </div>
      )}

      {tickets.length > 0 && (
        <section className="resultSection">
          <div className="sectionHeader">
            <div>
              <p className="sectionKicker">Tickets</p>
              <h3>Ticket overview</h3>
            </div>
            <span>{tickets.length} results</span>
          </div>

          <div className="metricsRow">
            <MiniMetric label="Active tickets" value={tickets.length} />
            <MiniMetric label="Engineers" value={Object.keys(ticketSummary.byEngineer).length} />
            <MiniMetric label="Statuses" value={Object.keys(ticketSummary.byStatus).length} />
            <MiniMetric label="Oldest age" value={`${ticketSummary.oldest}d`} />
          </div>

          <div className="resultGrid">
            {tickets.map((ticket) => (
              <ResultCard
                key={ticket.ticket_id}
                eyebrow={`Ticket #${ticket.ticket_id}`}
                title={ticket.subject}
                subtitle={ticket.customer_name}
                status={ticket.status}
                fields={[
                  {
                    label: "Engineer",
                    value: ticket.assigned_engineer || "Unassigned",
                  },
                  {
                    label: "Priority",
                    value: ticket.priority || "Not set",
                  },
                  {
                    label: "Type",
                    value: ticket.ticket_type || "Not set",
                  },
                  {
                    label: "Age",
                    value: `${ticket.age_days ?? 0} days`,
                  },
                ]}
                footer={[
                  `Created ${formatDate(ticket.created_at)}`,
                  ticket.modified_at
                    ? `Updated ${formatDate(ticket.modified_at)}`
                    : "No recent update",
                ]}
              />
            ))}
          </div>
        </section>
      )}

      {projects.length > 0 && (
        <section className="resultSection">
          <div className="sectionHeader">
            <div>
              <p className="sectionKicker">Projects</p>
              <h3>Project details</h3>
            </div>
            <span>{projects.length} results</span>
          </div>

          <div className="metricsRow">
            <MiniMetric label="Projects returned" value={projects.length} />
            <MiniMetric label="Active" value={projectsActive} />
            <MiniMetric label="Customers" value={uniqueCustomers} />
            <MiniMetric label="Owners" value={uniqueOwners} />
          </div>

          <div className="resultGrid">
            {projects.map((project, index) => {
              const status = getProjectStatus(project);
              const id = pick(project, ["projectId", "id", "project_id"], `${index + 1}`);
              return (
                <ResultCard
                  key={`${id}-${index}`}
                  eyebrow={`Project #${id}`}
                  title={pick(project, ["projectName", "name"], "Unnamed project")}
                  subtitle={pick(
                    project,
                    ["customerName", "customer_name", "companyName", "accountName"],
                    "Customer not listed"
                  )}
                  status={status.name}
                  statusColor={status.color}
                  fields={[
                    {
                      label: "Start",
                      value: pick(project, ["projectStartDate", "startDate"], "Not available"),
                    },
                    {
                      label: "End",
                      value: pick(project, ["projectEndDate", "endDate"], "Not available"),
                    },
                    {
                      label: "Manager",
                      value: pick(
                        project,
                        ["projectManager", "projectManagerName", "ownerName", "managerName"],
                        "Not available"
                      ),
                    },
                    {
                      label: "Budget hours",
                      value: pick(project, ["projectBudgetHours", "budgetHours"], "Not available"),
                    },
                  ]}
                />
              );
            })}
          </div>
        </section>
      )}

      {customers.length > 0 && (
        <section className="resultSection">
          <div className="sectionHeader">
            <div>
              <p className="sectionKicker">Customers</p>
              <h3>Customer results</h3>
            </div>
            <span>{customers.length} results</span>
          </div>

          <div className="resultGrid">
            {customers.map((item, index) => (
              <ResultCard
                key={`customer-${index}`}
                eyebrow={`Customer #${pick(item, ["customerId", "CustomerId", "id"], `${index + 1}`)}`}
                title={pick(
                  item,
                  ["customerName", "name", "companyName", "Name", "DisplayName"],
                  "Unnamed customer"
                )}
                subtitle={pick(item, ["email", "Email"], "Email not available")}
                status={pick(item, ["status", "Status"], "Unknown")}
                fields={[
                  {
                    label: "Phone",
                    value: pick(item, ["phone", "Phone"], "Not available"),
                  },
                  {
                    label: "Owner",
                    value: pick(item, ["ownerName", "OwnerName"], "Not available"),
                  },
                  {
                    label: "Account",
                    value: pick(item, ["accountNumber", "AccountNumber"], "Not available"),
                  },
                  {
                    label: "Source",
                    value: pick(item, ["source", "Source"], "Rev.io"),
                  },
                ]}
              />
            ))}
          </div>
        </section>
      )}

      {customer && (
        <section className="resultSection">
          <div className="sectionHeader">
            <div>
              <p className="sectionKicker">Customer</p>
              <h3>Customer detail</h3>
            </div>
          </div>

          <div className="resultGrid singleGrid">
            <ResultCard
              eyebrow={`Customer #${pick(customer, ["customerId", "CustomerId", "id"], "N/A")}`}
              title={pick(
                customer,
                ["customerName", "name", "companyName", "Name", "DisplayName"],
                "Unnamed customer"
              )}
              subtitle={pick(customer, ["email", "Email"], "Email not available")}
              status={pick(customer, ["status", "Status"], "Unknown")}
              fields={[
                { label: "Phone", value: pick(customer, ["phone", "Phone"], "Not available") },
                { label: "Account", value: pick(customer, ["accountNumber", "AccountNumber"], "Not available") },
                { label: "Owner", value: pick(customer, ["ownerName", "OwnerName"], "Not available") },
                { label: "City", value: pick(customer, ["city", "City"], "Not available") },
              ]}
            />
          </div>
        </section>
      )}

      {contacts.length > 0 && (
        <section className="resultSection">
          <div className="sectionHeader">
            <div>
              <p className="sectionKicker">Contacts</p>
              <h3>Contact results</h3>
            </div>
            <span>{contacts.length} results</span>
          </div>

          <div className="resultGrid">
            {contacts.map((contact, index) => (
              <ResultCard
                key={`contact-${index}`}
                eyebrow={`Contact #${pick(contact, ["contactId", "ContactId", "id"], `${index + 1}`)}`}
                title={pick(contact, ["name", "fullName", "DisplayName"], "Unnamed contact")}
                subtitle={pick(contact, ["email", "Email"], "Email not available")}
                status={pick(contact, ["status", "Status"], "Unknown")}
                fields={[
                  { label: "Phone", value: pick(contact, ["phone", "Phone"], "Not available") },
                  {
                    label: "Customer",
                    value: pick(contact, ["customerName", "CustomerName"], "Not available"),
                  },
                  { label: "Title", value: pick(contact, ["title", "Title"], "Not available") },
                  { label: "Type", value: pick(contact, ["type", "Type"], "Not available") },
                ]}
              />
            ))}
          </div>
        </section>
      )}

      {(opportunities.length > 0 || opportunity) && (
        <section className="resultSection">
          <div className="sectionHeader">
            <div>
              <p className="sectionKicker">Opportunities</p>
              <h3>Opportunity pipeline</h3>
            </div>
            <span>{opportunities.length + (opportunity ? 1 : 0)} results</span>
          </div>

          <div className="resultGrid">
            {opportunities.map((item, index) => (
              <ResultCard
                key={`opp-${index}`}
                eyebrow={`Opportunity #${pick(item, ["opportunityId", "id"], `${index + 1}`)}`}
                title={pick(item, ["opportunityName", "name", "title"], "Untitled opportunity")}
                subtitle={pick(item, ["customerName", "companyName"], "Customer not listed")}
                status={pick(item, ["status", "stageName", "opportunityStatus"], "Unknown")}
                fields={[
                  { label: "Amount", value: formatCurrency(pickRaw(item, ["amount", "Amount"])) },
                  { label: "Owner", value: pick(item, ["ownerName", "salesRepName"], "Not available") },
                  { label: "Stage", value: pick(item, ["stageName", "salesStage"], "Not available") },
                  { label: "Close date", value: pick(item, ["closeDate", "expectedCloseDate"], "Not available") },
                ]}
              />
            ))}

            {opportunity && (
              <ResultCard
                eyebrow={`Opportunity #${pick(opportunity, ["opportunityId", "id"], "1")}`}
                title={pick(opportunity, ["opportunityName", "name", "title"], "Opportunity detail")}
                subtitle={pick(opportunity, ["customerName", "companyName"], "Customer not listed")}
                status={pick(opportunity, ["status", "stageName", "opportunityStatus"], "Unknown")}
                fields={[
                  { label: "Amount", value: formatCurrency(pickRaw(opportunity, ["amount", "Amount"])) },
                  { label: "Owner", value: pick(opportunity, ["ownerName", "salesRepName"], "Not available") },
                  { label: "Stage", value: pick(opportunity, ["stageName", "salesStage"], "Not available") },
                  { label: "Close date", value: pick(opportunity, ["closeDate", "expectedCloseDate"], "Not available") },
                ]}
              />
            )}
          </div>
        </section>
      )}

      {invoices.length > 0 && (
        <section className="resultSection">
          <div className="sectionHeader">
            <div>
              <p className="sectionKicker">Invoices</p>
              <h3>Invoice results</h3>
            </div>
            <span>{invoices.length} results</span>
          </div>

          <div className="resultGrid">
            {invoices.map((invoice, index) => (
              <ResultCard
                key={`invoice-${index}`}
                eyebrow={`Invoice #${pick(invoice, ["invoiceNumber", "InvoiceNumber", "id"], `${index + 1}`)}`}
                title={pick(invoice, ["customerName", "CustomerName"], "Invoice record")}
                subtitle={pick(invoice, ["invoiceDate", "InvoiceDate"], "Date not available")}
                status={pick(invoice, ["status", "Status"], "Unknown")}
                fields={[
                  { label: "Amount", value: formatCurrency(pickRaw(invoice, ["amount", "Amount", "total"])) },
                  { label: "Balance", value: formatCurrency(pickRaw(invoice, ["balance", "Balance"])) },
                  { label: "Due date", value: pick(invoice, ["dueDate", "DueDate"], "Not available") },
                  { label: "Customer ID", value: pick(invoice, ["customerId", "CustomerId"], "Not available") },
                ]}
              />
            ))}
          </div>
        </section>
      )}

      {ledgerEntries.length > 0 && (
        <section className="resultSection">
          <div className="sectionHeader">
            <div>
              <p className="sectionKicker">Billing ledger</p>
              <h3>
                {result.data?.customer_name
                  ? `${String(result.data.customer_name)} ledger`
                  : "Charge and credit ledger"}
              </h3>
            </div>
            <span>{ledgerEntries.length} entries</span>
          </div>

          <div className="metricsRow">
            <MiniMetric label="Entries" value={ledgerEntries.length} />
            <MiniMetric
              label="Total charges"
              value={formatCurrency(pickRaw(ledgerSummary, ["total_charges"]))}
            />
            <MiniMetric
              label="Total credits"
              value={formatCurrency(pickRaw(ledgerSummary, ["total_credits"]))}
            />
            <MiniMetric
              label="Net"
              value={formatCurrency(
                pickRaw(ledgerSummary, ["net_charges_less_credits"])
              )}
            />
          </div>

          <div className="inlineNotice">
            This ledger currently includes charges and credits. Payment receipt
            and payment-application records are not included yet.
          </div>

          <div className="resultGrid">
            {ledgerEntries.map((entry, index) => {
              const entryType = pick(entry, ["entry_type"], "TRANSACTION");
              const transactionId = pick(entry, ["transaction_id"], `${index + 1}`);

              return (
                <ResultCard
                  key={`ledger-${entryType}-${transactionId}-${index}`}
                  eyebrow={`${entryType} #${transactionId}`}
                  title={pick(entry, ["description"], "Billing transaction")}
                  subtitle={`Bill/Statement #${pick(entry, ["bill_id"], "Not assigned")}`}
                  status={entryType}
                  fields={[
                    {
                      label: "Amount",
                      value: formatCurrency(pickRaw(entry, ["amount"])),
                    },
                    {
                      label: "Quantity",
                      value: pick(entry, ["quantity"], "Not available"),
                    },
                    {
                      label: "Service ID",
                      value: pick(entry, ["service_id"], "Not available"),
                    },
                    {
                      label: "Product ID",
                      value: pick(entry, ["product_id"], "Not available"),
                    },
                    {
                      label: "Rate",
                      value: formatCurrency(pickRaw(entry, ["rate"])),
                    },
                    {
                      label: "Running total",
                      value: formatCurrency(
                        pickRaw(entry, ["running_charge_credit_balance"])
                      ),
                    },
                  ]}
                  footer={[
                    `Created ${formatDate(pickRaw(entry, ["created_date"]))}`,
                    `Period ${formatDate(pickRaw(entry, ["start_date"]))} – ${formatDate(
                      pickRaw(entry, ["end_date"])
                    )}`,
                  ]}
                />
              );
            })}
          </div>
        </section>
      )}

      {activity.length > 0 && (
        <section className="resultSection">
          <div className="sectionHeader">
            <div>
              <p className="sectionKicker">Activity</p>
              <h3>Project activity</h3>
            </div>
            <span>{activity.length} records</span>
          </div>

          <div className="resultGrid">
            {activity.map((item, index) => (
              <ResultCard
                key={`activity-${index}`}
                eyebrow={`Activity #${index + 1}`}
                title={pick(item, ["description", "activityDescription", "title"], "Activity")}
                subtitle={pick(item, ["projectName", "customerName"], "Project activity")}
                status={pick(item, ["status", "activityStatus"], "Logged")}
                fields={[
                  { label: "Date", value: pick(item, ["activityDate", "date"], "Not available") },
                  { label: "Resource", value: pick(item, ["resourceName", "ownerName"], "Not available") },
                  { label: "Hours", value: pick(item, ["hours", "duration"], "Not available") },
                  { label: "Type", value: pick(item, ["activityType", "type"], "Not available") },
                ]}
              />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

export default function HomePage() {
  const welcomeText =
    "Hi, I’m Ribbit. Ask me about tickets, customers, projects, invoices, opportunities, or billing data across your connected Rev.io systems.";

  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const [conversationId, setConversationId] = useState<string>(() => createId());
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: createId(),
      role: "assistant",
      text: welcomeText,
    },
  ]);

  const apiBase =
    process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

  const latestAssistant = [...messages]
    .reverse()
    .find((item) => item.role === "assistant" && item.result);

  const connectedSystems = [
    "Rev.io PSA",
    "Rev.io Billing",
    "PDF Reports",
  ];

  async function submit(value?: string) {
    const finalMessage = (value ?? message).trim();
    if (!finalMessage || loading) return;

    const userMessage: ChatMessage = {
      id: createId(),
      role: "user",
      text: finalMessage,
    };

    setMessages((current) => [...current, userMessage]);
    setLoading(true);
    setMessage("");

    try {
      const response = await fetch(`${apiBase}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: finalMessage,
          conversation_id: conversationId,
        }),
      });

      if (!response.ok) {
        const body = await response.text();
        throw new Error(body || `Request failed: ${response.status}`);
      }

      const payload: ChatResult = await response.json();

      setMessages((current) => [
        ...current,
        {
          id: createId(),
          role: "assistant",
          text: payload.answer,
          result: payload,
        },
      ]);

      if (payload.conversation_id) {
        setConversationId(payload.conversation_id);
      }
    } catch (error) {
      setMessages((current) => [
        ...current,
        {
          id: createId(),
          role: "assistant",
          text: error instanceof Error ? error.message : "Unable to reach the API.",
          result: {
            answer:
              error instanceof Error ? error.message : "Unable to reach the API.",
            intent: "error",
          },
        },
      ]);
    } finally {
      setLoading(false);
    }
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    void submit();
  }

  function startNewConversation() {
    setConversationId(createId());
    setMessages([
      {
        id: createId(),
        role: "assistant",
        text: welcomeText,
      },
    ]);
    setMessage("");
    setLoading(false);
  }

  return (
    <main className="ribbitShell">
      <aside className="ribbitSidebar">
        <div className="sidebarTop">
          <button
            type="button"
            className="newChatButton"
            onClick={startNewConversation}
          >
            + New chat
          </button>

          <div className="brandPanel compactBrandPanel">
            <img
              src="/frog-logo.png"
              alt="Ribbit frog logo"
              className="brandLogo frogBrandLogo"
            />
            <div>
              <h1>Ribbit</h1>
            </div>
          </div>

          <div className="sidebarInfoCard">
            <p className="sidebarLabel">Connected systems</p>
            <div className="connectedList">
              {connectedSystems.map((item) => (
                <span key={item} className="connectedPill">
                  {item}
                </span>
              ))}
            </div>
          </div>
        </div>

        <div className="sidebarBottom">
          <p className="sidebarLabel">Try asking</p>
          <div className="sidebarPrompts">
            {starterPrompts.slice(0, 4).map((prompt) => (
              <button
                key={prompt}
                type="button"
                className="sidebarPromptButton"
                onClick={() => void submit(prompt)}
              >
                {prompt}
              </button>
            ))}
          </div>

          <div className="sidebarStatus">
            <span className="statusDot" />
            Live systems connected
          </div>
        </div>
      </aside>

      <section className="chatWorkspace">
        <header className="workspaceHeader">
          <div>
            <p className="workspaceOverline">Ribbit workspace</p>
            <h2>Ask anything about your business data</h2>
          </div>
          {latestAssistant?.result?.intent && (
            <span className="intentPill">
              {latestAssistant.result.intent}
            </span>
          )}
        </header>

        <div className="chatStream">
          {messages.length === 1 && (
            <section className="starterPanel">
              <div className="starterText">
                <h3>Start with a quick question</h3>
                <p>
                  Ribbit is designed to feel conversational while still pulling
                  live data from your connected Rev.io systems.
                </p>
              </div>

              <div className="starterGrid">
                {starterPrompts.map((prompt) => (
                  <button
                    key={prompt}
                    type="button"
                    className="starterCard"
                    onClick={() => void submit(prompt)}
                  >
                    <span>Ask Ribbit</span>
                    <strong>{prompt}</strong>
                  </button>
                ))}
              </div>
            </section>
          )}

          {messages.map((item) => (
            <div
              key={item.id}
              className={`chatRow ${item.role === "user" ? "userRow" : "assistantRow"}`}
            >
              {item.role === "assistant" && (
                <div className="avatar assistantAvatar imageAvatar">
                  <img src="/frog-logo.png" alt="Ribbit" className="avatarIcon" />
                </div>
              )}

              <div className={`messageBubble ${item.role}`}>
                <div className="messageContent">
                  {formatAnswerText(item.text).map((line, index) => (
                    <p key={`${item.id}-${index}`}>{line}</p>
                  ))}
                </div>

                {item.role === "assistant" && item.result && (
                  <StructuredResponse result={item.result} apiBase={apiBase} />
                )}
              </div>

              {item.role === "user" && <div className="avatar userAvatar">MG</div>}
            </div>
          ))}

          {loading && (
            <div className="chatRow assistantRow">
              <div className="avatar assistantAvatar imageAvatar">
                <img src="/frog-logo.png" alt="Ribbit" className="avatarIcon" />
              </div>
              <div className="messageBubble assistant loadingBubble">
                <div className="typingDots">
                  <span />
                  <span />
                  <span />
                </div>
                <p>Ribbit is thinking…</p>
              </div>
            </div>
          )}
        </div>

        <footer className="composerWrap">
          <form onSubmit={handleSubmit} className="composer">
            <input
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              placeholder="Message Ribbit..."
              aria-label="Message Ribbit"
            />
            <button type="submit" disabled={loading || !message.trim()}>
              {loading ? "Working..." : "Send"}
            </button>
          </form>
          <p className="composerHint">
            Ribbit can search tickets, projects, customers, contacts,
            opportunities, invoices, and billing ledgers.
          </p>
        </footer>
      </section>
    </main>
  );
}
