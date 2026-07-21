"use client";

import { FormEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";

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


type StandardReportColumn = {
  key: string;
  label: string;
  type?: string;
};

type StandardReport = {
  report_type?: string;
  title?: string;
  source?: string;
  summary?: string;
  period?: string | null;
  generated_at?: string | null;
  last_refreshed?: unknown;
  kpis?: Array<{
    label?: string;
    value?: unknown;
    format?: string;
  }>;
  attention_items?: EntityRecord[];
  table?: {
    title?: string;
    columns?: StandardReportColumn[];
    rows?: EntityRecord[];
  };
  detail_sections?: Array<{
    title?: string;
    columns?: StandardReportColumn[];
    rows?: EntityRecord[];
  }>;
};

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
    ccwr_renewals?: EntityRecord[];
    renewal_summary?: EntityRecord;
    company_health?: EntityRecord;
    support_health?: EntityRecord;
    project_health?: EntityRecord;
    sales_health?: EntityRecord;
    billing_health?: EntityRecord;
    renewal_health?: EntityRecord;
    attention_items?: EntityRecord[];
    standard_reports?: StandardReport[];
    customer?: EntityRecord;
    customers?: EntityRecord[];
    customer_matches?: EntityRecord[];
    customer_name?: string;
    opportunity?: EntityRecord;
    activity?: EntityRecord | EntityRecord[];
    project_id?: string | number;
    report?: Record<string, unknown>;
    source?: string;
    dataset_id?: string;
    dataset_title?: string;
    dataset_type?: string;
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
  "Show me how my company is doing",
  "Show active Cisco renewals due in the next 90 days",
  "Show overdue Cisco renewals",
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

function renderInlineFormatting(text: string): ReactNode[] {
  const labelMatch = text.match(
    /^(Total active tickets|Total tickets|Ticket ID|Subject|Customer|Status|Priority|Engineer|Assigned Engineer|Assigned To|Technician|Owner|Created|Modified|Closed|Queue|Agent|Subscription ID|Renewal Date|Renewal Bucket|Risk|Reseller|Bill To|Market|Source):\s*(.*)$/i
  );

  if (labelMatch) {
    return [
      <strong key="label">{labelMatch[1]}:</strong>,
      <span key="value">
        {labelMatch[2] ? ` ${labelMatch[2]}` : ""}
      </span>,
    ];
  }

  const parts = text.split(/(\*\*[^*]+\*\*)/g);

  return parts
    .filter(Boolean)
    .map((part, index) => {
      if (part.startsWith("**") && part.endsWith("**")) {
        return (
          <strong key={`bold-${index}`}>
            {part.slice(2, -2)}
          </strong>
        );
      }

      return <span key={`text-${index}`}>{part}</span>;
    });
}

function formatAnswerBlocks(answer: string): ReactNode[] {
  const normalized = answer
    .replace(/\r\n/g, "\n")
    .replace(/\s+(?=\d+\.\s+)/g, "\n")
    .replace(/\s+(?=(Summary|Total|Recently|Important|Next steps|What this means):)/gi, "\n");

  const lines = normalized
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean);

  const blocks: ReactNode[] = [];
  let bulletItems: string[] = [];
  let numberedItems: string[] = [];

  function flushBullets() {
    if (!bulletItems.length) return;

    blocks.push(
      <ul className="messageList" key={`bullets-${blocks.length}`}>
        {bulletItems.map((item, index) => (
          <li key={`bullet-${index}`}>
            {renderInlineFormatting(item)}
          </li>
        ))}
      </ul>
    );
    bulletItems = [];
  }

  function flushNumbers() {
    if (!numberedItems.length) return;

    blocks.push(
      <ol className="messageList numberedList" key={`numbers-${blocks.length}`}>
        {numberedItems.map((item, index) => (
          <li key={`number-${index}`}>
            {renderInlineFormatting(item)}
          </li>
        ))}
      </ol>
    );
    numberedItems = [];
  }

  lines.forEach((line) => {
    const bulletMatch = line.match(/^[-•]\s+(.+)$/);
    const numberedMatch = line.match(/^\d+\.\s+(.+)$/);
    const headingMatch = line.match(/^#{1,3}\s+(.+)$/);
    const boldHeadingMatch = line.match(/^\*\*([^*]+)\*\*:?\s*$/);

    if (bulletMatch) {
      flushNumbers();
      bulletItems.push(bulletMatch[1]);
      return;
    }

    if (numberedMatch) {
      flushBullets();
      numberedItems.push(numberedMatch[1]);
      return;
    }

    flushBullets();
    flushNumbers();

    if (headingMatch) {
      blocks.push(
        <h4 className="messageHeading" key={`heading-${blocks.length}`}>
          {renderInlineFormatting(headingMatch[1])}
        </h4>
      );
      return;
    }

    if (boldHeadingMatch) {
      blocks.push(
        <h4 className="messageHeading" key={`bold-heading-${blocks.length}`}>
          {boldHeadingMatch[1]}
        </h4>
      );
      return;
    }

    if (/^(Ticket ID|Subscription ID):\s*/i.test(line)) {
      blocks.push(
        <div
          className="messageRecordHeading"
          key={`record-heading-${blocks.length}`}
        >
          {renderInlineFormatting(line)}
        </div>
      );
      return;
    }

    blocks.push(
      <p key={`paragraph-${blocks.length}`}>
        {renderInlineFormatting(line)}
      </p>
    );
  });

  flushBullets();
  flushNumbers();

  return blocks;
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


function formatReportValue(value: unknown, format?: string) {
  if (value === null || value === undefined || value === "") return "—";
  if (format === "currency") return formatCurrency(value);
  if (format === "date") return formatDate(value);

  const numeric = Number(value);
  if (format === "days" && Number.isFinite(numeric)) {
    return `${numeric.toLocaleString(undefined, {
      maximumFractionDigits: 1,
    })} days`;
  }
  if (format === "score" && Number.isFinite(numeric)) {
    return `${numeric.toLocaleString(undefined, {
      maximumFractionDigits: 0,
    })}/100`;
  }
  if (format === "number" && Number.isFinite(numeric)) {
    return numeric.toLocaleString();
  }

  return String(value);
}

function StandardReportView({ report }: { report: StandardReport }) {
  const kpis = report.kpis ?? [];
  const attention = report.attention_items ?? [];
  const columns = report.table?.columns ?? [];
  const rows = report.table?.rows ?? [];
  const detailSections = report.detail_sections ?? [];

  const formatDetailCell = (
    row: EntityRecord,
    column: StandardReportColumn
  ) => {
    const rowFormat =
      column.key === "value" && typeof row.value_type === "string"
        ? row.value_type
        : column.type;

    return formatReportValue(row[column.key], rowFormat);
  };

  return (
    <section className="standardReport">
      <header className="standardReportHeader">
        <div>
          <p className="sectionKicker">{report.source ?? "Ribbit Intelligence"}</p>
          <h3>{report.title ?? "Report"}</h3>
          {report.summary && <p>{report.summary}</p>}
        </div>
        <div className="standardReportMeta">
          {report.period && <span>{report.period}</span>}
          {report.last_refreshed ? (
            <span>Refreshed {formatDate(report.last_refreshed)}</span>
          ) : report.generated_at ? (
            <span>Generated {formatDate(report.generated_at)}</span>
          ) : null}
        </div>
      </header>

      {kpis.length > 0 && (
        <div className="standardReportKpis">
          {kpis.map((kpi, index) => (
            <article className="standardReportKpi" key={`${kpi.label}-${index}`}>
              <span>{kpi.label ?? "Metric"}</span>
              <strong>{formatReportValue(kpi.value, kpi.format)}</strong>
            </article>
          ))}
        </div>
      )}

      {attention.length > 0 && (
        <div className="standardReportAttention">
          <h4>Attention Needed</h4>
          {attention.map((item, index) => (
            <article key={`attention-${index}`}>
              <span>{String(item.severity ?? "Review")}</span>
              <div>
                <strong>{String(item.title ?? "Review item")}</strong>
                {item.detail !== null &&
                item.detail !== undefined &&
                item.detail !== "" ? (
                  <p>{String(item.detail)}</p>
                ) : null}
              </div>
            </article>
          ))}
        </div>
      )}

      {detailSections.map((section, sectionIndex) => {
        const sectionColumns = section.columns ?? [];
        const sectionRows = section.rows ?? [];

        if (sectionColumns.length === 0 || sectionRows.length === 0) {
          return null;
        }

        return (
          <details
            className="standardReportDetails"
            key={`${section.title ?? "details"}-${sectionIndex}`}
          >
            <summary>
              {section.title ?? "View details"} ({sectionRows.length})
            </summary>
            <div className="standardReportTableWrap">
              <table className="standardReportTable">
                <thead>
                  <tr>
                    {sectionColumns.map((column) => (
                      <th key={column.key}>{column.label}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sectionRows.map((row, rowIndex) => (
                    <tr key={`section-${sectionIndex}-row-${rowIndex}`}>
                      {sectionColumns.map((column) => (
                        <td key={column.key}>
                          {formatDetailCell(row, column)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        );
      })}

      {columns.length > 0 && rows.length > 0 && (
        <details className="standardReportDetails">
          <summary>
            {report.table?.title ?? "View details"} ({rows.length})
          </summary>
          <div className="standardReportTableWrap">
            <table className="standardReportTable">
              <thead>
                <tr>
                  {columns.map((column) => (
                    <th key={column.key}>{column.label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row, rowIndex) => (
                  <tr key={`row-${rowIndex}`}>
                    {columns.map((column) => (
                      <td key={column.key}>
                        {formatDetailCell(row, column)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </section>
  );
}

function StructuredResponse({
  result,
  apiBase,
  onCreateReport,
}: {
  result: ChatResult;
  apiBase: string;
  onCreateReport: (datasetId?: string, datasetTitle?: string) => void;
}) {
  const tickets = result?.data?.tickets ?? [];
  const projects = result?.data?.projects ?? [];
  const contacts = result?.data?.contacts ?? [];
  const opportunities = result?.data?.opportunities ?? [];
  const invoices = result?.data?.invoices ?? [];
  const ledgerEntries = result?.data?.ledger_entries ?? [];
  const ledgerSummary = result?.data?.ledger_summary;
  const ccwrRenewals = result?.data?.ccwr_renewals ?? [];
  const renewalSummary = result?.data?.renewal_summary;
  const companyHealth = result?.data?.company_health;
  const supportHealth = result?.data?.support_health;
  const projectHealth = result?.data?.project_health;
  const salesHealth = result?.data?.sales_health;
  const billingHealth = result?.data?.billing_health;
  const renewalHealth = result?.data?.renewal_health;
  const attentionItems = result?.data?.attention_items ?? [];
  const standardReports = result?.data?.standard_reports ?? [];
  const contactsOnly =
    result?.data?.presentation_mode === "contacts_only";
  const hasExecutiveCompanyReport = standardReports.some(
    (report) => report.report_type === "company_health"
  );
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
    ccwrRenewals.length > 0 ||
    standardReports.length > 0 ||
    Boolean(companyHealth) ||
    Boolean(customer) ||
    customers.length > 0 ||
    Boolean(opportunity) ||
    activity.length > 0;

  if (!hasStructuredResults && !result.download_url) {
    return null;
  }

  return (
    <div className="structuredResponse">
      {result.data?.dataset_id && (
        <div className="datasetActionBar">
          <div>
            <span>Saved dataset</span>
            <strong>
              {String(result.data.dataset_title ?? "Ribbit results")}
            </strong>
          </div>
          <button
            type="button"
            onClick={() =>
              onCreateReport(
                String(result.data?.dataset_id ?? ""),
                String(result.data?.dataset_title ?? "Ribbit Report")
              )
            }
          >
            Create report
          </button>
        </div>
      )}
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

      {standardReports.length > 0 && (
        <div className="standardReportStack">
          {standardReports.map((report, index) => (
            <StandardReportView
              key={`${report.report_type ?? "report"}-${index}`}
              report={report}
            />
          ))}
        </div>
      )}

      {standardReports.length === 0 && companyHealth && (
        <section className="resultSection companyHealthSection">
          <div className="sectionHeader">
            <div>
              <p className="sectionKicker">Executive snapshot</p>
              <h3>Company health overview</h3>
            </div>
            <span
              className={`companyHealthBadge ${String(
                pick(companyHealth, ["rating"], "Unknown")
              )
                .toLowerCase()
                .replace(/\s+/g, "-")}`}
            >
              {pick(companyHealth, ["rating"], "Unknown")}
            </span>
          </div>

          <div className="companyHealthHero">
            <div className="healthScoreRing">
              <strong>{pick(companyHealth, ["score"], "0")}</strong>
              <span>out of 100</span>
            </div>
            <div>
              <p>Overall company health</p>
              <h4>{pick(companyHealth, ["rating"], "Unknown")}</h4>
              <span>
                {pick(companyHealth, ["systems_available"], "0")} of{" "}
                {pick(companyHealth, ["systems_expected"], "5")} data areas available
              </span>
            </div>
          </div>

          <div className="companySnapshotGrid">
            <article className="companySnapshotCard">
              <span>Support</span>
              <strong>
                {pick(supportHealth, ["active_tickets"], "0")} active
              </strong>
              <p>
                {pick(supportHealth, ["needs_review"], "0")} need review · oldest{" "}
                {pick(supportHealth, ["oldest_age_days"], "0")} days
              </p>
            </article>

            <article className="companySnapshotCard">
              <span>Projects</span>
              <strong>
                {pick(projectHealth, ["active_projects"], "0")} active
              </strong>
              <p>{pick(projectHealth, ["total_projects"], "0")} total projects</p>
            </article>

            <article className="companySnapshotCard">
              <span>Sales</span>
              <strong>
                {pick(salesHealth, ["open_opportunities"], "0")} opportunities
              </strong>
              <p>
                {formatCurrency(pickRaw(salesHealth, ["pipeline_value"]))} pipeline
              </p>
            </article>

            <article className="companySnapshotCard">
              <span>Billing</span>
              <strong>
                {formatCurrency(
                  pickRaw(
                    billingHealth,
                    [
                      "net_billing_activity",
                      "net_charges_less_credits"
                    ]
                  ) ??
                    (Number(
                      pickRaw(
                        billingHealth,
                        ["total_charges"]
                      ) ?? 0
                    ) -
                      Number(
                        pickRaw(
                          billingHealth,
                          ["total_credits"]
                        ) ?? 0
                      ))
                )}{" "}
                net activity
              </strong>
              <p>
                {formatCurrency(
                  pickRaw(billingHealth, ["total_charges"])
                )} charges ·{" "}
                {formatCurrency(
                  pickRaw(billingHealth, ["total_credits"])
                )} credits
                <br />
                {pick(
                  billingHealth,
                  ["customers_with_activity"],
                  "0"
                )} customers with activity ·{" "}
                {pick(billingHealth, ["period_days"], "30")} days
              </p>
            </article>

            <article className="companySnapshotCard">
              <span>Renewals</span>
              <strong>
                {pick(renewalHealth, ["due_next_90"], "0")} due in 90 days
              </strong>
              <p>
                US {pick(renewalHealth, ["us_subscriptions"], "0")} · Canada{" "}
                {pick(renewalHealth, ["canada_subscriptions"], "0")} ·{" "}
                {pick(renewalHealth, ["total_subscriptions"], "0")} total ·{" "}
                {pick(renewalHealth, ["actionable_overdue"], "0")} overdue
                <br />
                Database refreshed{" "}
                {formatDate(pickRaw(renewalHealth, ["last_refreshed"]))}
              </p>
            </article>
          </div>

          {attentionItems.length > 0 && (
            <div className="attentionPanel">
              <div className="sectionHeader compactHeader">
                <div>
                  <p className="sectionKicker">Attention needed</p>
                  <h3>Priority actions</h3>
                </div>
                <span>{attentionItems.length} items</span>
              </div>

              <div className="attentionList">
                {attentionItems.map((item, index) => (
                  <article
                    className={`attentionItem ${pick(
                      item,
                      ["severity"],
                      "Review"
                    ).toLowerCase()}`}
                    key={`attention-${index}`}
                  >
                    <span>
                      {pick(item, ["area"], "Operations")} ·{" "}
                      {pick(item, ["severity"], "Review")}
                    </span>
                    <strong>
                      {pick(item, ["title"], "Review required")}
                    </strong>
                    <p>{pick(item, ["detail"], "No additional detail.")}</p>
                  </article>
                ))}
              </div>
            </div>
          )}
        </section>
      )}

      {standardReports.length === 0 && !companyHealth && tickets.length > 0 && (
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

      {standardReports.length === 0 && !companyHealth && projects.length > 0 && (
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

      {standardReports.length === 0 && !companyHealth && customers.length > 0 && (
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

      {!contactsOnly && standardReports.length === 0 && customer && (
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

      {standardReports.length === 0 && !companyHealth && contacts.length > 0 && (
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

      {standardReports.length === 0 &&
        !companyHealth &&
        (opportunities.length > 0 || opportunity) && (
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

      {standardReports.length === 0 && !companyHealth && invoices.length > 0 && (
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

      {standardReports.length === 0 && !companyHealth && ccwrRenewals.length > 0 && (
        <section className="resultSection">
          <div className="sectionHeader">
            <div>
              <p className="sectionKicker">Cisco CCW-R</p>
              <h3>
                {result.data?.customer_name
                  ? `${String(result.data.customer_name)} renewals`
                  : "Subscription renewal results"}
              </h3>
            </div>
            <span>{ccwrRenewals.length} subscriptions</span>
          </div>

          <div className="metricsRow renewalMetrics">
            <MiniMetric
              label="Total"
              value={pick(renewalSummary, ["total_subscriptions"], "0")}
            />
            <MiniMetric
              label="Active"
              value={pick(renewalSummary, ["active"], "0")}
            />
            <MiniMetric
              label="Overdue"
              value={pick(renewalSummary, ["actionable_overdue"], "0")}
            />
            <MiniMetric
              label="Due in 30"
              value={pick(renewalSummary, ["due_0_30"], "0")}
            />
            <MiniMetric
              label="Due in 31–60"
              value={pick(renewalSummary, ["due_31_60"], "0")}
            />
            <MiniMetric
              label="Due in 61–90"
              value={pick(renewalSummary, ["due_61_90"], "0")}
            />
            <MiniMetric
              label="Closed"
              value={pick(renewalSummary, ["closed"], "0")}
            />
          </div>

          <div className="resultGrid">
            {ccwrRenewals.map((renewal, index) => {
              const risk = pick(renewal, ["risk_level"], "Unknown");
              const daysRaw = pickRaw(renewal, ["days_until_renewal"]);
              const days =
                typeof daysRaw === "number"
                  ? daysRaw < 0
                    ? `${Math.abs(daysRaw)} days overdue`
                    : `${daysRaw} days remaining`
                  : "Date unavailable";

              return (
                <ResultCard
                  key={`ccwr-${pick(renewal, ["subscription_id"], `${index}`)}`}
                  eyebrow={`Subscription ${pick(
                    renewal,
                    ["subscription_id"],
                    `${index + 1}`
                  )}`}
                  title={pick(
                    renewal,
                    ["end_customer_name"],
                    "Customer not available"
                  )}
                  subtitle={`${pick(
                    renewal,
                    ["market"],
                    "Market unavailable"
                  )} · ${days}`}
                  status={risk}
                  fields={[
                    {
                      label: "Subscription status",
                      value: pick(renewal, ["status"], "Unknown"),
                    },
                    {
                      label: "Renewal date",
                      value: pick(
                        renewal,
                        ["effective_renewal_date", "renewal_date"],
                        "Not available"
                      ),
                    },
                    {
                      label: "Renewal bucket",
                      value: pick(
                        renewal,
                        ["renewal_bucket"],
                        "Unknown"
                      ),
                    },
                    {
                      label: "Auto renewal",
                      value:
                        pick(renewal, ["has_auto_renewal"], "false") === "true"
                          ? "Yes"
                          : "No",
                    },
                    {
                      label: "Reseller",
                      value: pick(
                        renewal,
                        ["reseller_name"],
                        "Not available"
                      ),
                    },
                    {
                      label: "Bill To",
                      value: pick(
                        renewal,
                        ["bill_to_name"],
                        "Not available"
                      ),
                    },
                  ]}
                  footer={[
                    `Term ${pick(
                      renewal,
                      ["initial_term_measurement"],
                      "?"
                    )} ${pick(renewal, ["initial_term_unit"], "")}`,
                    `Billing: ${pick(
                      renewal,
                      ["billing_model"],
                      "Not available"
                    )}`,
                  ]}
                />
              );
            })}
          </div>
        </section>
      )}

      {standardReports.length === 0 && !companyHealth && ledgerEntries.length > 0 && (
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


function reportTemplateDescription(template: string) {
  const descriptions: Record<string, string> = {
    executive:
      "Dashboard view: renewal totals, status and renewal-bucket donut charts, bucket cards, and a complete subscription table.",
    detailed:
      "Operations view: complete records with useful IDs, dates, statuses, amounts, and source fields.",
    customer_facing:
      "Customer-ready dashboard: visual KPIs and charts with internal IDs, API names, queries, and technical metadata removed.",
    audit:
      "Evidence view: full records plus dataset IDs, original query, source, intent, timestamps, and raw payload details.",
  };

  return descriptions[template] ?? "";
}

export default function HomePage() {
  const welcomeText =
    "Hi, I’m Ribbit. Ask me about tickets, customers, projects, invoices, billing data, or Cisco subscription renewals across your connected systems.";

  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
  const [reportDatasetId, setReportDatasetId] = useState("");
  const [reportTitle, setReportTitle] = useState("Ribbit Report");
  const [reportFormat, setReportFormat] = useState("pdf");
  const [reportTemplate, setReportTemplate] = useState("detailed");
  const [reportScope, setReportScope] = useState("selected");
  const [reportLoading, setReportLoading] = useState(false);
  const [reportError, setReportError] = useState("");
  const [conversationId, setConversationId] = useState<string>(() => createId());
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: createId(),
      role: "assistant",
      text: welcomeText,
    },
  ]);
  const chatStreamRef = useRef<HTMLDivElement | null>(null);
  const chatBottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      chatBottomRef.current?.scrollIntoView({
        behavior: "smooth",
        block: "end",
      });
    });

    return () => window.cancelAnimationFrame(frame);
  }, [messages, loading]);

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

  function openReportBuilder(datasetId?: string, datasetTitle?: string) {
    setReportDatasetId(datasetId ?? "");
    setReportTitle(datasetTitle || "Ribbit Report");
    setReportScope(datasetId ? "selected" : "conversation");
    setReportError("");
    setReportOpen(true);
  }

  async function generateReport() {
    setReportLoading(true);
    setReportError("");

    try {
      const response = await fetch(`${apiBase}/api/reports/build`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          conversation_id: conversationId,
          dataset_ids:
            reportScope === "selected" && reportDatasetId
              ? [reportDatasetId]
              : [],
          scope: reportScope,
          title: reportTitle.trim() || "Ribbit Report",
          format: reportFormat,
          template: reportTemplate,
          include_summary: true,
          include_raw_records: true,
        }),
      });

      const payload = await response.json();
      if (!response.ok || payload.status === "error") {
        throw new Error(payload.message || "Unable to create report.");
      }

      window.open(`${apiBase}${payload.download_url}`, "_blank", "noopener,noreferrer");
      setReportOpen(false);
    } catch (error) {
      setReportError(
        error instanceof Error ? error.message : "Unable to create report."
      );
    } finally {
      setReportLoading(false);
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

          <button
            type="button"
            className="conversationReportButton"
            onClick={() => openReportBuilder()}
          >
            Create conversation report
          </button>

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

        <div className="chatStream" ref={chatStreamRef}>
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
                {item.role === "assistant" && item.result && (
                  <StructuredResponse
                    result={item.result}
                    apiBase={apiBase}
                    onCreateReport={openReportBuilder}
                  />
                )}

                {!(
                  item.role === "assistant" &&
                  (
                    item.result?.data?.presentation_mode === "contacts_only" ||
                    item.result?.data?.standard_reports?.some(
                      (report) => report.report_type === "company_health"
                    )
                  )
                ) && (
                  <div
                    className={`messageContent ${
                      item.role === "assistant" && item.result
                        ? "messageContentAfterOverview"
                        : ""
                    }`}
                  >
                    {formatAnswerBlocks(item.text)}
                  </div>
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

          <div
            ref={chatBottomRef}
            className="chatBottomAnchor"
            aria-hidden="true"
          />
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
            opportunities, invoices, billing ledgers, and Cisco renewals.
          </p>
        </footer>
      </section>

      {reportOpen && (
        <div className="modalBackdrop" onClick={() => setReportOpen(false)}>
          <section
            className="reportModal"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="reportModalHeader">
              <div>
                <p>Report builder</p>
                <h3>Create a report</h3>
              </div>
              <button
                type="button"
                className="modalClose"
                onClick={() => setReportOpen(false)}
              >
                ×
              </button>
            </div>

            <label className="reportField">
              <span>Report title</span>
              <input
                value={reportTitle}
                onChange={(event) => setReportTitle(event.target.value)}
              />
            </label>

            <div className="reportFieldGrid">
              <label className="reportField">
                <span>Format</span>
                <select
                  value={reportFormat}
                  onChange={(event) => setReportFormat(event.target.value)}
                >
                  <option value="pdf">PDF</option>
                  <option value="xlsx">Excel</option>
                  <option value="csv">CSV</option>
                </select>
              </label>

              <label className="reportField">
                <span>Template</span>
                <select
                  value={reportTemplate}
                  onChange={(event) => setReportTemplate(event.target.value)}
                >
                  <option value="executive">Executive summary</option>
                  <option value="detailed">Detailed operational report</option>
                  <option value="customer_facing">Customer-facing report</option>
                  <option value="audit">Audit and evidence report</option>
                </select>
              </label>
            </div>

            <div className={`templateExplanation ${reportTemplate}`}>
              <strong>
                {reportTemplate.replace("_", " ").replace(/\b\w/g, (letter) =>
                  letter.toUpperCase()
                )}
              </strong>
              <p>{reportTemplateDescription(reportTemplate)}</p>
            </div>

            <label className="reportField">
              <span>Data to include</span>
              <select
                value={reportScope}
                onChange={(event) => setReportScope(event.target.value)}
              >
                {reportDatasetId && (
                  <option value="selected">This result only</option>
                )}
                <option value="conversation">
                  All datasets in this conversation
                </option>
              </select>
            </label>

            {reportError && <p className="reportError">{reportError}</p>}

            <div className="reportModalActions">
              <button
                type="button"
                className="secondaryButton"
                onClick={() => setReportOpen(false)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="primaryButton"
                onClick={() => void generateReport()}
                disabled={reportLoading}
              >
                {reportLoading ? "Building..." : "Generate report"}
              </button>
            </div>
          </section>
        </div>
      )}
    </main>
  );
}
