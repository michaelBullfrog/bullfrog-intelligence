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

type ChatResult = {
  answer: string;
  intent: string;
  data?: {
    tickets?: Ticket[];
    report?: Record<string, unknown>;
    [key: string]: unknown;
  };
};

const suggestions = [
  "Show me all active tickets",
  "Show me tickets assigned to Michael",
  "Show me Needs Reviewed tickets",
  "Show me the engineer workload",
];

function formatDate(value?: string | null) {
  if (!value) return "Not available";
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

function badgeClass(value?: string | null) {
  return (value ?? "unknown")
    .toLowerCase()
    .replaceAll(" ", "-")
    .replaceAll("_", "-");
}

export default function HomePage() {
  const [message, setMessage] = useState("");
  const [result, setResult] = useState<ChatResult | null>(null);
  const [loading, setLoading] = useState(false);

  const tickets = result?.data?.tickets ?? [];

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
          <button className="navItem">Reports</button>
          <button className="navItem">Customers</button>
          <button className="navItem">Knowledge</button>
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
              placeholder="Ask about tickets, customers, engineers, or reports..."
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
              summarize workload, or prepare operational reports.
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
                <h3>{result.answer}</h3>
              </div>
              <span className="intentBadge">{result.intent}</span>
            </div>

            {tickets.length > 0 && (
              <>
                <section className="metricsGrid">
                  <article className="metricCard">
                    <span>Active tickets</span>
                    <strong>{tickets.length}</strong>
                  </article>
                  <article className="metricCard">
                    <span>Engineers</span>
                    <strong>{Object.keys(summary.byEngineer).length}</strong>
                  </article>
                  <article className="metricCard">
                    <span>Statuses</span>
                    <strong>{Object.keys(summary.byStatus).length}</strong>
                  </article>
                  <article className="metricCard">
                    <span>Oldest age</span>
                    <strong>{summary.oldest}d</strong>
                  </article>
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
                        <span
                          className={`badge ${badgeClass(ticket.status)}`}
                        >
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

            {tickets.length === 0 && result.data && (
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
