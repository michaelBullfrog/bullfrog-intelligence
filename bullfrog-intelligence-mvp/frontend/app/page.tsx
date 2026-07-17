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
  data?: {
    tickets?: Ticket[];
    projects?: EntityRecord[];
    contacts?: EntityRecord[];
    opportunities?: EntityRecord[];
    customer?: EntityRecord;
    opportunity?: EntityRecord;
    activity?: EntityRecord | EntityRecord[];
    project_id?: string | number;
    report?: Record<string, unknown>;
    [key: string]: unknown;
  };
};

const suggestions = [
  "Show me all active tickets",
  "Show me all projects",
  "Show me our current opportunities",
  "Find contacts for a customer",
];

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

function pickRaw(
  record: EntityRecord | undefined,
  keys: string[]
): unknown {
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
      (item): item is EntityRecord =>
        typeof item === "object" && item !== null
    );
  }

  if (value && typeof value === "object") {
    const record = value as EntityRecord;

    for (const key of [
      "items",
      "records",
      "results",
      "entries",
      "activities",
      "data",
    ]) {
      const nested = record[key];
      if (Array.isArray(nested)) {
        return nested.filter(
          (item): item is EntityRecord =>
            typeof item === "object" && item !== null
        );
      }
    }

    return [record];
  }

  return [];
}


function formatAnswerText(answer: string): string[] {
  return answer
    .replace(/\s+(?=\d+\.\s+Project ID:)/g, "\n")
    .replace(/\s+(?=Summary:)/g, "\n\n")
    .replace(/\s+(?=Total projects listed:)/g, "\n")
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function getProjectStatus(project: EntityRecord): {
  name: string;
  color?: string;
} {
  const rawStatus = project.projectStatus;

  if (rawStatus && typeof rawStatus === "object") {
    const statusRecord = rawStatus as EntityRecord;
    const name = pick(
      statusRecord,
      ["projectStatusName", "statusName", "name"],
      "Unknown"
    );
    const color = pick(
      statusRecord,
      ["projectStatusColor", "statusColor", "color"],
      ""
    );

    return {
      name,
      color: color || undefined,
    };
  }

  return {
    name: pick(
      project,
      ["projectStatusName", "statusName", "status", "state"],
      "Unknown"
    ),
    color:
      pick(
        project,
        ["projectStatusColor", "statusColor"],
        ""
      ) || undefined,
  };
}

function normalizeHexColor(value?: string): string | undefined {
  if (!value) return undefined;
  const cleaned = value.trim();

  if (/^#[0-9a-fA-F]{6}$/.test(cleaned)) return cleaned;
  if (/^[0-9a-fA-F]{6}$/.test(cleaned)) return `#${cleaned}`;

  return undefined;
}

function EntityMetric({
  label,
  value,
}: {
  label: string;
  value: string | number;
}) {
  return (
    <article className="metricCard">
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function DetailCard({
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
    <article className="ticketCard entityCard">
      <div className="ticketTop">
        <div>
          <p className="ticketNumber">{eyebrow}</p>
          <h4>{title}</h4>
        </div>

        {status && (
          <span
            className={`badge ${badgeClass(status)}`}
            style={
              normalizeHexColor(statusColor)
                ? {
                    backgroundColor: `${normalizeHexColor(statusColor)}26`,
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

      {subtitle && <p className="customerName">{subtitle}</p>}

      <div className="ticketMeta entityMeta">
        {fields.map((field) => (
          <div key={`${field.label}-${field.value}`}>
            <span>{field.label}</span>
            <strong>{field.value}</strong>
          </div>
        ))}
      </div>

      {footer && footer.length > 0 && (
        <div className="ticketFooter entityFooter">
          {footer.map((item) => (
            <span key={item}>{item}</span>
          ))}
        </div>
      )}
    </article>
  );
}

export default function HomePage() {
  const [message, setMessage] = useState("");
  const [result, setResult] = useState<ChatResult | null>(null);
  const [loading, setLoading] = useState(false);

  const tickets = result?.data?.tickets ?? [];
  const projects = result?.data?.projects ?? [];
  const contacts = result?.data?.contacts ?? [];
  const opportunities = result?.data?.opportunities ?? [];
  const customer = result?.data?.customer;
  const opportunity = result?.data?.opportunity;
  const activity = normalizeRecords(result?.data?.activity);

  const hasFormattedResults =
    tickets.length > 0 ||
    projects.length > 0 ||
    contacts.length > 0 ||
    opportunities.length > 0 ||
    Boolean(customer) ||
    Boolean(opportunity) ||
    activity.length > 0;

  const summary = useMemo(() => {
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
      oldest: tickets.reduce(
        (max, ticket) => Math.max(max, ticket.age_days ?? 0),
        0
      ),
    };
  }, [tickets]);

  async function submit(value?: string) {
    const finalMessage = (value ?? message).trim();
    if (!finalMessage) return;

    setLoading(true);
    setResult(null);

    try {
      const apiBase =
        process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

      const response = await fetch(`${apiBase}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: finalMessage }),
      });

      if (!response.ok) {
        const body = await response.text();
        throw new Error(body || `Request failed: ${response.status}`);
      }

      setResult(await response.json());
    } catch (error) {
      setResult({
        answer:
          error instanceof Error ? error.message : "Unable to reach the API.",
        intent: "error",
      });
    } finally {
      setLoading(false);
    }
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    void submit();
  }

  return (
    <main className="pageShell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brandMark">BF</div>
          <div>
            <p className="brandEyebrow">BULLFROG GROUP</p>
            <h1>Bullfrog Intelligence</h1>
          </div>
        </div>

        <nav className="nav">
          <button className="navItem active">Ask Intelligence</button>
          <button className="navItem">Tickets</button>
          <button className="navItem">Projects</button>
          <button className="navItem">Customers</button>
          <button className="navItem">Opportunities</button>
        </nav>

        <div className="sidebarFooter">
          <span className="statusDot" />
          Rev.io connected
        </div>
      </aside>

      <section className="content">
        <header className="topbar">
          <div>
            <p className="sectionEyebrow">AI OPERATIONS PORTAL</p>
            <h2>What would you like to know?</h2>
          </div>
          <div className="profile">MG</div>
        </header>

        <section className="askPanel">
          <form onSubmit={handleSubmit} className="askBox">
            <input
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              placeholder="Ask about tickets, projects, customers, contacts, or opportunities..."
              aria-label="Ask Bullfrog Intelligence"
            />
            <button type="submit" disabled={loading}>
              {loading ? "Searching..." : "Ask"}
            </button>
          </form>

          <div className="suggestions">
            {suggestions.map((suggestion) => (
              <button key={suggestion} onClick={() => void submit(suggestion)}>
                {suggestion}
              </button>
            ))}
          </div>
        </section>

        {!result && !loading && (
          <section className="emptyState">
            <div className="emptyIcon">AI</div>
            <h3>Your company knowledge, in one place</h3>
            <p>
              Ask Bullfrog Intelligence to retrieve live Rev.io tickets,
              projects, customers, contacts, and sales opportunities.
            </p>
          </section>
        )}

        {loading && (
          <section className="loadingCard">
            <div className="spinner" />
            <div>
              <h3>Searching Bullfrog systems</h3>
              <p>Retrieving and organizing the latest information.</p>
            </div>
          </section>
        )}

        {result && (
          <section className="results">
            <div className="responseHeader">
              <div>
                <p className="sectionEyebrow">RESPONSE</p>
                <div className="responseAnswer">
                  {formatAnswerText(result.answer).map((line, index) => (
                    <p key={`${line}-${index}`}>{line}</p>
                  ))}
                </div>
              </div>
              <span className="intentBadge">{result.intent}</span>
            </div>

            {tickets.length > 0 && (
              <>
                <section className="metricsGrid">
                  <EntityMetric label="Active tickets" value={tickets.length} />
                  <EntityMetric
                    label="Engineers"
                    value={Object.keys(summary.byEngineer).length}
                  />
                  <EntityMetric
                    label="Statuses"
                    value={Object.keys(summary.byStatus).length}
                  />
                  <EntityMetric label="Oldest age" value={`${summary.oldest}d`} />
                </section>

                <section className="summaryGrid">
                  <article className="summaryCard">
                    <h4>Status breakdown</h4>
                    <div className="summaryRows">
                      {Object.entries(summary.byStatus).map(([status, count]) => (
                        <div className="summaryRow" key={status}>
                          <span className={`badge ${badgeClass(status)}`}>
                            {status}
                          </span>
                          <strong>{count}</strong>
                        </div>
                      ))}
                    </div>
                  </article>

                  <article className="summaryCard">
                    <h4>Engineer workload</h4>
                    <div className="summaryRows">
                      {Object.entries(summary.byEngineer)
                        .sort((a, b) => b[1] - a[1])
                        .map(([engineer, count]) => (
                          <div className="summaryRow" key={engineer}>
                            <span>{engineer}</span>
                            <strong>{count}</strong>
                          </div>
                        ))}
                    </div>
                  </article>
                </section>

                <div className="ticketListHeader">
                  <h3>Ticket details</h3>
                  <span>{tickets.length} results</span>
                </div>

                <section className="ticketGrid">
                  {tickets.map((ticket) => (
                    <article className="ticketCard" key={ticket.ticket_id}>
                      <div className="ticketTop">
                        <div>
                          <p className="ticketNumber">
                            Ticket #{ticket.ticket_id}
                          </p>
                          <h4>{ticket.subject}</h4>
                        </div>
                        <span className={`badge ${badgeClass(ticket.status)}`}>
                          {ticket.status}
                        </span>
                      </div>

                      <p className="customerName">{ticket.customer_name}</p>

                      <div className="ticketMeta">
                        <div>
                          <span>Engineer</span>
                          <strong>
                            {ticket.assigned_engineer || "Unassigned"}
                          </strong>
                        </div>
                        <div>
                          <span>Priority</span>
                          <strong>{ticket.priority || "Not set"}</strong>
                        </div>
                        <div>
                          <span>Type</span>
                          <strong>{ticket.ticket_type || "Not set"}</strong>
                        </div>
                        <div>
                          <span>Age</span>
                          <strong>{ticket.age_days ?? 0} days</strong>
                        </div>
                      </div>

                      <div className="ticketFooter">
                        <span>Created {formatDate(ticket.created_at)}</span>
                        {ticket.modified_at && (
                          <span>Updated {formatDate(ticket.modified_at)}</span>
                        )}
                      </div>
                    </article>
                  ))}
                </section>
              </>
            )}

            {projects.length > 0 && (
              <>
                <section className="metricsGrid entityMetrics">
                  <EntityMetric label="Projects returned" value={projects.length} />
                  <EntityMetric
                    label="Active"
                    value={
                      projects.filter((project) =>
                        ["active", "open", "in progress"].includes(
                          getProjectStatus(project).name.toLowerCase()
                        )
                      ).length
                    }
                  />
                  <EntityMetric
                    label="Customers"
                    value={
                      new Set(
                        projects.map((project) =>
                          pick(project, [
                            "customerName",
                            "customer_name",
                            "companyName",
                            "accountName",
                          ])
                        )
                      ).size
                    }
                  />
                  <EntityMetric
                    label="Owners"
                    value={
                      new Set(
                        projects.map((project) =>
                          pick(project, [
                            "projectManager",
                            "projectManagerName",
                            "ownerName",
                            "managerName",
                          ])
                        )
                      ).size
                    }
                  />
                </section>

                <div className="ticketListHeader">
                  <h3>Project details</h3>
                  <span>{projects.length} results</span>
                </div>

                <section className="ticketGrid">
                  {projects.map((project, index) => {
                    const id = pick(project, [
                      "projectId",
                      "id",
                      "project_id",
                    ], String(index + 1));
                    const projectStatus = getProjectStatus(project);

                    return (
                      <DetailCard
                        key={`project-${id}-${index}`}
                        eyebrow={`Project #${id}`}
                        title={pick(project, [
                          "projectName",
                          "name",
                          "title",
                          "description",
                        ], "Untitled project")}
                        subtitle={pick(project, [
                          "customerName",
                          "customer_name",
                          "companyName",
                          "accountName",
                        ], "Customer not listed")}
                        status={projectStatus.name}
                        statusColor={projectStatus.color}
                        fields={[
                          {
                            label: "Project manager",
                            value: pick(project, [
                              "projectManager",
                              "projectManagerName",
                              "ownerName",
                              "managerName",
                            ]),
                          },
                          {
                            label: "Type",
                            value: pick(project, [
                              "projectType",
                              "type",
                              "category",
                            ]),
                          },
                          {
                            label: "Start date",
                            value: formatDate(
                              pickRaw(project, [
                                "startDate",
                                "start_date",
                                "createdDate",
                              ])
                            ),
                          },
                          {
                            label: "Due date",
                            value: formatDate(
                              pickRaw(project, [
                                "dueDate",
                                "endDate",
                                "targetDate",
                                "completionDate",
                              ])
                            ),
                          },
                        ]}
                        footer={[
                          `Created ${formatDate(
                            pickRaw(project, ["createdDate", "created_at"])
                          )}`,
                          `Updated ${formatDate(
                            pickRaw(project, ["modifiedDate", "updatedDate", "updated_at"])
                          )}`,
                        ]}
                      />
                    );
                  })}
                </section>
              </>
            )}

            {contacts.length > 0 && (
              <>
                <section className="metricsGrid entityMetrics">
                  <EntityMetric label="Contacts returned" value={contacts.length} />
                  <EntityMetric
                    label="Companies"
                    value={
                      new Set(
                        contacts.map((contact) =>
                          pick(contact, [
                            "customerName",
                            "companyName",
                            "accountName",
                          ])
                        )
                      ).size
                    }
                  />
                  <EntityMetric
                    label="With email"
                    value={
                      contacts.filter((contact) =>
                        Boolean(pickRaw(contact, ["email", "emailAddress"]))
                      ).length
                    }
                  />
                  <EntityMetric
                    label="With phone"
                    value={
                      contacts.filter((contact) =>
                        Boolean(
                          pickRaw(contact, [
                            "phone",
                            "phoneNumber",
                            "mobilePhone",
                          ])
                        )
                      ).length
                    }
                  />
                </section>

                <div className="ticketListHeader">
                  <h3>Contact details</h3>
                  <span>{contacts.length} results</span>
                </div>

                <section className="ticketGrid">
                  {contacts.map((contact, index) => {
                    const id = pick(contact, [
                      "contactId",
                      "id",
                      "contact_id",
                    ], String(index + 1));

                    const fullName =
                      pick(contact, ["fullName", "displayName", "name"], "") ||
                      `${pick(contact, ["firstName"], "")} ${pick(
                        contact,
                        ["lastName"],
                        ""
                      )}`.trim() ||
                      "Unnamed contact";

                    return (
                      <DetailCard
                        key={`contact-${id}-${index}`}
                        eyebrow={`Contact #${id}`}
                        title={fullName}
                        subtitle={pick(contact, [
                          "customerName",
                          "companyName",
                          "accountName",
                        ], "Company not listed")}
                        status={pick(contact, [
                          "status",
                          "contactStatus",
                          "active",
                        ], "")}
                        fields={[
                          {
                            label: "Email",
                            value: pick(contact, ["email", "emailAddress"]),
                          },
                          {
                            label: "Phone",
                            value: pick(contact, [
                              "phone",
                              "phoneNumber",
                              "businessPhone",
                            ]),
                          },
                          {
                            label: "Mobile",
                            value: pick(contact, [
                              "mobilePhone",
                              "cellPhone",
                              "mobile",
                            ]),
                          },
                          {
                            label: "Title",
                            value: pick(contact, [
                              "jobTitle",
                              "title",
                              "position",
                            ]),
                          },
                        ]}
                      />
                    );
                  })}
                </section>
              </>
            )}

            {opportunities.length > 0 && (
              <>
                <section className="metricsGrid entityMetrics">
                  <EntityMetric
                    label="Opportunities"
                    value={opportunities.length}
                  />
                  <EntityMetric
                    label="Open"
                    value={
                      opportunities.filter((item) =>
                        ["open", "active", "pending"].includes(
                          pick(item, ["status", "stage", "state"], "").toLowerCase()
                        )
                      ).length
                    }
                  />
                  <EntityMetric
                    label="Customers"
                    value={
                      new Set(
                        opportunities.map((item) =>
                          pick(item, [
                            "customerName",
                            "companyName",
                            "accountName",
                          ])
                        )
                      ).size
                    }
                  />
                  <EntityMetric
                    label="Owners"
                    value={
                      new Set(
                        opportunities.map((item) =>
                          pick(item, [
                            "ownerName",
                            "salesPerson",
                            "assignedTo",
                            "createdByName",
                          ])
                        )
                      ).size
                    }
                  />
                </section>

                <div className="ticketListHeader">
                  <h3>Opportunity details</h3>
                  <span>{opportunities.length} results</span>
                </div>

                <section className="ticketGrid">
                  {opportunities.map((item, index) => {
                    const id = pick(item, [
                      "opportunityId",
                      "id",
                      "opportunity_id",
                    ], String(index + 1));

                    return (
                      <DetailCard
                        key={`opportunity-${id}-${index}`}
                        eyebrow={`Opportunity #${id}`}
                        title={pick(item, [
                          "opportunityName",
                          "name",
                          "title",
                          "description",
                        ], "Untitled opportunity")}
                        subtitle={pick(item, [
                          "customerName",
                          "companyName",
                          "accountName",
                        ], "Customer not listed")}
                        status={pick(item, ["status", "stage", "state"], "Unknown")}
                        fields={[
                          {
                            label: "Owner",
                            value: pick(item, [
                              "ownerName",
                              "salesPerson",
                              "assignedTo",
                              "createdByName",
                            ]),
                          },
                          {
                            label: "Value",
                            value: pick(item, [
                              "amount",
                              "value",
                              "estimatedValue",
                              "total",
                            ]),
                          },
                          {
                            label: "Probability",
                            value: pick(item, [
                              "probability",
                              "winProbability",
                              "confidence",
                            ]),
                          },
                          {
                            label: "Close date",
                            value: formatDate(
                              pickRaw(item, [
                                "expectedCloseDate",
                                "closeDate",
                                "estimatedCloseDate",
                              ])
                            ),
                          },
                        ]}
                        footer={[
                          `Created ${formatDate(
                            pickRaw(item, ["createdDate", "created_at"])
                          )}`,
                          `Updated ${formatDate(
                            pickRaw(item, ["modifiedDate", "updatedDate", "updated_at"])
                          )}`,
                        ]}
                      />
                    );
                  })}
                </section>
              </>
            )}

            {customer && (
              <>
                <div className="ticketListHeader">
                  <h3>Customer details</h3>
                  <span>1 result</span>
                </div>

                <section className="ticketGrid">
                  <DetailCard
                    eyebrow={`Customer #${pick(customer, [
                      "customerId",
                      "id",
                      "customer_id",
                    ])}`}
                    title={pick(customer, [
                      "customerName",
                      "companyName",
                      "name",
                      "accountName",
                    ], "Unnamed customer")}
                    subtitle={pick(customer, [
                      "legalName",
                      "displayName",
                      "website",
                    ], "")}
                    status={pick(customer, [
                      "status",
                      "customerStatus",
                      "state",
                    ], "Unknown")}
                    fields={[
                      {
                        label: "Primary contact",
                        value: pick(customer, [
                          "primaryContactName",
                          "contactName",
                          "mainContact",
                        ]),
                      },
                      {
                        label: "Email",
                        value: pick(customer, [
                          "email",
                          "emailAddress",
                          "primaryEmail",
                        ]),
                      },
                      {
                        label: "Phone",
                        value: pick(customer, [
                          "phone",
                          "phoneNumber",
                          "primaryPhone",
                        ]),
                      },
                      {
                        label: "Account owner",
                        value: pick(customer, [
                          "accountManager",
                          "accountManagerName",
                          "ownerName",
                        ]),
                      },
                    ]}
                    footer={[
                      `Created ${formatDate(
                        pickRaw(customer, ["createdDate", "created_at"])
                      )}`,
                      `Updated ${formatDate(
                        pickRaw(customer, ["modifiedDate", "updatedDate", "updated_at"])
                      )}`,
                    ]}
                  />
                </section>
              </>
            )}

            {opportunity && (
              <>
                <div className="ticketListHeader">
                  <h3>Opportunity details</h3>
                  <span>1 result</span>
                </div>

                <section className="ticketGrid">
                  <DetailCard
                    eyebrow={`Opportunity #${pick(opportunity, [
                      "opportunityId",
                      "id",
                      "opportunity_id",
                    ])}`}
                    title={pick(opportunity, [
                      "opportunityName",
                      "name",
                      "title",
                      "description",
                    ], "Untitled opportunity")}
                    subtitle={pick(opportunity, [
                      "customerName",
                      "companyName",
                      "accountName",
                    ], "Customer not listed")}
                    status={pick(opportunity, [
                      "status",
                      "stage",
                      "state",
                    ], "Unknown")}
                    fields={[
                      {
                        label: "Owner",
                        value: pick(opportunity, [
                          "ownerName",
                          "salesPerson",
                          "assignedTo",
                        ]),
                      },
                      {
                        label: "Value",
                        value: pick(opportunity, [
                          "amount",
                          "value",
                          "estimatedValue",
                        ]),
                      },
                      {
                        label: "Probability",
                        value: pick(opportunity, [
                          "probability",
                          "winProbability",
                        ]),
                      },
                      {
                        label: "Close date",
                        value: formatDate(
                          pickRaw(opportunity, [
                            "expectedCloseDate",
                            "closeDate",
                          ])
                        ),
                      },
                    ]}
                    footer={[
                      `Created ${formatDate(
                        pickRaw(opportunity, ["createdDate", "created_at"])
                      )}`,
                      `Updated ${formatDate(
                        pickRaw(opportunity, [
                          "modifiedDate",
                          "updatedDate",
                          "updated_at",
                        ])
                      )}`,
                    ]}
                  />
                </section>
              </>
            )}

            {activity.length > 0 && (
              <>
                <section className="metricsGrid entityMetrics">
                  <EntityMetric label="Activity entries" value={activity.length} />
                  <EntityMetric
                    label="Project ID"
                    value={String(result.data?.project_id ?? "Not listed")}
                  />
                  <EntityMetric
                    label="Event types"
                    value={
                      new Set(
                        activity.map((item) =>
                          pick(item, ["eventType", "type", "activityType"])
                        )
                      ).size
                    }
                  />
                  <EntityMetric
                    label="Contributors"
                    value={
                      new Set(
                        activity.map((item) =>
                          pick(item, [
                            "performedBy",
                            "performedByName",
                            "userName",
                            "createdBy",
                          ])
                        )
                      ).size
                    }
                  />
                </section>

                <div className="ticketListHeader">
                  <h3>Project activity</h3>
                  <span>{activity.length} entries</span>
                </div>

                <section className="ticketGrid">
                  {activity.map((item, index) => (
                    <DetailCard
                      key={`activity-${index}`}
                      eyebrow={`Activity ${index + 1}`}
                      title={pick(item, [
                        "description",
                        "message",
                        "summary",
                        "title",
                        "eventType",
                      ], "Project activity")}
                      subtitle={pick(item, [
                        "projectName",
                        "customerName",
                        "entityName",
                      ], "")}
                      status={pick(item, [
                        "eventType",
                        "type",
                        "activityType",
                      ], "")}
                      fields={[
                        {
                          label: "Performed by",
                          value: pick(item, [
                            "performedBy",
                            "performedByName",
                            "userName",
                            "createdBy",
                          ]),
                        },
                        {
                          label: "Entity",
                          value: pick(item, [
                            "entityType",
                            "subjectType",
                            "category",
                          ]),
                        },
                        {
                          label: "Action",
                          value: pick(item, [
                            "action",
                            "operation",
                            "eventName",
                          ]),
                        },
                        {
                          label: "Date",
                          value: formatDate(
                            pickRaw(item, [
                              "createdDate",
                              "timestamp",
                              "eventDate",
                              "date",
                            ])
                          ),
                        },
                      ]}
                    />
                  ))}
                </section>
              </>
            )}

            {!hasFormattedResults && result.data && (
              <pre className="rawData">
                {JSON.stringify(result.data, null, 2)}
              </pre>
            )}
          </section>
        )}
      </section>
    </main>
  );
}
