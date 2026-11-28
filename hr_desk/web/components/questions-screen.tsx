"use client";

import Link from "next/link";
import { useEffect, useId, useState } from "react";
import { useSession } from "@/components/session";
import { ThemeSelect } from "@/components/theme-select";
import { api, type AdminAction, type QuestionRow } from "@/lib/api";

export function QuestionsScreen() {
  const { me } = useSession();
  const [rows, setRows] = useState<QuestionRow[] | null>(null);
  const [actions, setActions] = useState<AdminAction[]>([]);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [logQuery, setLogQuery] = useState("");
  const [logOutcome, setLogOutcome] = useState("All");
  const [logPerson, setLogPerson] = useState("All");
  const [logFrom, setLogFrom] = useState("");
  const [logTo, setLogTo] = useState("");

  useEffect(() => {
    if (!me?.isAdmin) return;
    api<{ questions: QuestionRow[]; actions: AdminAction[] }>("/api/questions")
      .then((data) => {
        setRows(data.questions);
        setActions(data.actions);
      })
      .catch((caught) => setError(caught instanceof Error ? caught.message : "Something went wrong. Try again."));
  }, [me?.isAdmin]);

  const outcomes = ["All", ...Array.from(new Set((rows ?? []).map((row) => row.outcome_label).filter(Boolean))).sort()];
  const people = Array.from(new Set((rows ?? []).map((row) => row.asked_by).filter(Boolean))).sort((left, right) => left.localeCompare(right));
  const visibleRows = (rows ?? []).filter((row) => {
    const query = logQuery.trim().toLowerCase();
    const haystack = `${row.question} ${row.asked_by} ${row.email} ${row.policy}`.toLowerCase();
    const day = logDay(row.when);
    const fromOk = !logFrom || (day !== "" && day >= logFrom);
    const toOk = !logTo || (day !== "" && day <= logTo);
    return (!query || haystack.includes(query))
      && (logOutcome === "All" || row.outcome_label === logOutcome)
      && (logPerson === "All" || row.asked_by === logPerson)
      && fromOk && toOk;
  });
  const logFiltersOn = logQuery.trim() !== "" || logOutcome !== "All" || logPerson !== "All" || logFrom !== "" || logTo !== "";

  if (!me) {
    return (
      <section className="stack">
        <header className="page-head">
          <h1>Logs</h1>
          <p className="lead">Sign in to continue.</p>
        </header>
        <div className="actions">
          <Link className="primary" href="/sign-in">
            Sign in
          </Link>
        </div>
      </section>
    );
  }
  if (!me.isAdmin) {
    return (
      <section className="stack">
        <header className="page-head">
          <h1>Logs</h1>
          <p className="lead">Only an admin can open this page.</p>
        </header>
        <Link href="/">Back to Ask HR</Link>
      </section>
    );
  }

  return (
    <section className="stack">
      <header className="page-head">
        <h1>Logs</h1>
        <p className="help">Logs are kept for 3 months.</p>
      </header>
      {error ? (
        <p className="form-error" role="alert">
          {error}
        </p>
      ) : null}
      {message ? (
        <p role="status" className="success">
          {message}
        </p>
      ) : null}
      {rows === null ? (
        !error ? <p role="status">Loading logs…</p> : null
      ) : rows.length === 0 ? (
        <p className="empty-state">No logs yet.</p>
      ) : (
        <>
        <div className="list-filters">
          <label>
            Search
            <input
              type="search"
              value={logQuery}
              placeholder="Question, person, or policy"
              onChange={(event) => setLogQuery(event.target.value)}
            />
          </label>
          <label>
            Outcome
            <ThemeSelect
              value={logOutcome}
              onChange={setLogOutcome}
              options={outcomes.map((outcome) => ({ value: outcome, label: outcome }))}
            />
          </label>
          <label>
            Person
            <ThemeSelect
              value={logPerson}
              onChange={setLogPerson}
              options={[{ value: "All", label: "All" }, ...people.map((person) => ({ value: person, label: person }))]}
            />
          </label>
          <label>
            From
            <input type="date" value={logFrom} onChange={(event) => setLogFrom(event.target.value)} />
          </label>
          <label>
            To
            <input type="date" value={logTo} onChange={(event) => setLogTo(event.target.value)} />
          </label>
          {logFiltersOn ? (
            <button type="button" className="clear-filters" onClick={() => {
              setLogQuery("");
              setLogOutcome("All");
              setLogPerson("All");
              setLogFrom("");
              setLogTo("");
            }}>Clear filters</button>
          ) : null}
        </div>
        <p className="list-filter-count">
          {visibleRows.length === rows.length
            ? `${rows.length} ${rows.length === 1 ? "log" : "logs"}`
            : `${visibleRows.length} of ${rows.length} logs`}
        </p>
        {visibleRows.length === 0 ? (
          <p className="empty-state">No logs match.</p>
        ) : (
        <ul className="plain-list log-list">
          {visibleRows.map((row) => (
            <QuestionItem
              key={row.id}
              row={row}
              onRemoved={(message) => {
                setError("");
                setMessage(message);
                api<{ questions: QuestionRow[]; actions: AdminAction[] }>("/api/questions")
                  .then((data) => {
                    setRows(data.questions);
                    setActions(data.actions);
                  })
                  .catch((caught) =>
                    setError(caught instanceof Error ? caught.message : "Something went wrong. Try again."),
                  );
              }}
            />
          ))}
        </ul>
        )}
        </>
      )}
      {rows !== null ? <details className="admin-log">
        <summary>Admin activity</summary>
      {actions.length === 0 ? (
        <p>No admin activity yet.</p>
      ) : (
        <ul className="plain-list">
          {actions.map((action) => (
            <li key={action.id}>
              <strong>{action.who}</strong>
              <span className="file-name">{action.action}</span>
              <div className="policy-facts">
                <span>{logWhen(action.when)}</span>
              </div>
              {action.detail ? <p className="person-hint">{action.detail}</p> : null}
            </li>
          ))}
        </ul>
      )}
      </details> : null}
    </section>
  );
}

function logDay(iso: string) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

function logWhen(iso: string) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const day = date.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
  const time = date.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  return `${day}, ${time}`;
}

function outcomeClass(label: string) {
  if (label === "Answered") return "is-active";
  if (label === "Not allowed") return "is-refused";
  return "is-miss";
}

function QuestionItem({ row, onRemoved }: { row: QuestionRow; onRemoved: (message: string) => void }) {
  const [answerOpen, setAnswerOpen] = useState(false);
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const panelId = useId();
  const answerId = useId();
  return (
    <li>
      <p className="log-question">{row.question || "Empty question"}</p>
      <p className="file-name">
        {row.asked_by}
        {row.email ? ` · ${row.email}` : ""}
      </p>
      <div className="policy-facts">
        <span className={outcomeClass(row.outcome_label)}>{row.outcome_label}</span>
        <span>{logWhen(row.when)}</span>
      </div>
      {row.policy && row.policy !== "Not answered" ? <p className="person-hint">Taken from {row.policy}</p> : null}
      <div className="row-actions">
        {row.answer ? (
          <button type="button" aria-expanded={answerOpen} aria-controls={answerId} onClick={() => setAnswerOpen((value) => !value)}>
            {answerOpen ? "Hide the answer" : "Read the answer"}
          </button>
        ) : null}
        <button type="button" aria-expanded={open} aria-controls={panelId} onClick={() => setOpen((value) => !value)}>
          {open ? "Hide how this was answered" : "How this was answered"}
        </button>
        {confirming ? (
          <>
            <span>Remove this log?</span>
            <button
              type="button"
              className="danger"
              disabled={busy}
              onClick={() => {
                setBusy(true);
                api<{ message: string }>(`/api/questions/${row.id}`, { method: "DELETE" })
                  .then((result) => onRemoved(result.message))
                  .catch((caught) =>
                    setError(caught instanceof Error ? caught.message : "Something went wrong. Try again."),
                  )
                  .finally(() => setBusy(false));
              }}
            >
              Remove this log
            </button>
            <button type="button" disabled={busy} onClick={() => setConfirming(false)}>
              Cancel
            </button>
          </>
        ) : (
          <button type="button" className="danger" onClick={() => setConfirming(true)}>
            Remove
          </button>
        )}
      </div>
      {error ? (
        <p className="form-error" role="alert">
          {error}
        </p>
      ) : null}
      {answerOpen && row.answer ? (
        <div id={answerId} className="log-answer">
          <span className="whisper">Answer</span>
          <p>{row.answer}</p>
        </div>
      ) : null}
      {open ? (
        <div id={panelId} className="log-how">
          <ol className="log-steps">
            {row.steps.map((step) => (
              <li key={step.label}>
                <strong>{step.label}.</strong> {step.detail}
              </li>
            ))}
          </ol>
          {row.has_passages ? <QuestionPassages questionId={row.id} /> : null}
          <details className="log-technical">
            <summary>Details for IT</summary>
            <pre>{JSON.stringify(row.technical, null, 2)}</pre>
          </details>
        </div>
      ) : null}
    </li>
  );
}

function QuestionPassages({ questionId }: { questionId: string }) {
  const [passages, setPassages] = useState<{ title: string; label: string; text: string }[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  return (
    <div>
      {passages === null ? (
        <button type="button" disabled={loading} onClick={() => {
          setLoading(true);
          setError("");
          api<{ passages: { title: string; label: string; text: string }[] }>(`/api/questions/${questionId}/passages`)
            .then((result) => setPassages(result.passages))
            .catch(() => setError("Policy text could not be loaded. Try again."))
            .finally(() => setLoading(false));
        }}>
          {loading ? "Loading policy text…" : "Show policy text"}
        </button>
      ) : passages.length ? (
        <ul>
          {passages.map((passage, index) => (
            <li key={`${passage.label}-${index}`}>
              <strong>{passage.label || passage.title}</strong>
              <p style={{ whiteSpace: "pre-wrap" }}>{passage.text}</p>
            </li>
          ))}
        </ul>
      ) : <p role="status">This policy text is no longer available.</p>}
      {error ? <p className="form-error" role="alert">{error}</p> : null}
    </div>
  );
}
