"use client";

import { FormEvent, useState } from "react";

type ChatResult = {
  answer: string;
  intent: string;
  data?: Record<string, unknown>;
};

const suggestions = [
  "Show open tickets by engineer",
  "Which subscriptions expire in 90 days?",
  "Summarize Contact Center performance this week",
  "Find our Duo and Webex SSO instructions",
];

export default function HomePage() {
  const [message, setMessage] = useState("");
  const [result, setResult] = useState<ChatResult | null>(null);
  const [loading, setLoading] = useState(false);

  async function submit(value?: string) {
    const finalMessage = (value ?? message).trim();
    if (!finalMessage) return;

    setLoading(true);
    setResult(null);

    try {
      const response = await fetch(
        `${process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"}/api/chat`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: finalMessage }),
        }
      );

      if (!response.ok) {
        throw new Error(`Request failed: ${response.status}`);
      }

      setResult(await response.json());
    } catch (error) {
      setResult({
        answer: error instanceof Error ? error.message : "Unable to reach the API.",
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
    <main className="shell">
      <section className="hero">
        <div className="brandRow">
          <div className="logo">BF</div>
          <div>
            <p className="eyebrow">BULLFROG GROUP</p>
            <h1>Bullfrog Intelligence</h1>
          </div>
        </div>

        <p className="subtitle">
          Ask questions across tickets, customers, Webex, renewals, and company documentation.
        </p>

        <form onSubmit={handleSubmit} className="askBox">
          <input
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            placeholder="Ask anything about Bullfrog..."
            aria-label="Ask Bullfrog Intelligence"
          />
          <button type="submit" disabled={loading}>
            {loading ? "Searching..." : "Ask"}
          </button>
          <button type="button" className="voice" title="Voice integration placeholder">
            🎤
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

      <section className="resultPanel">
        <h2>Response</h2>
        {!result && <p className="muted">Results and report previews will appear here.</p>}
        {result && (
          <>
            <p className="answer">{result.answer}</p>
            <p className="intent">Intent: {result.intent}</p>
            {result.data && (
              <pre>{JSON.stringify(result.data, null, 2)}</pre>
            )}
          </>
        )}
      </section>
    </main>
  );
}
