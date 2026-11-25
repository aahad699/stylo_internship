"use client";

import Link from "next/link";
import { useEffect, useId, useState } from "react";
import { useSession } from "@/components/session";
import { api, formatWhen, type AdminAction, type QuestionRow } from "@/lib/api";

export function QuestionsScreen() {
  const { me } = useSession();
  const [rows, setRows] = useState<QuestionRow[] | null>(null);
  const [actions, setActions] = useState<AdminAction[]>([]);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!me?.isAdmin) return;
    api<{ questions: QuestionRow[]; actions: AdminAction[] }>("/api/questions")
      .then((data) => {
        setRows(data.questions);
        setActions(data.actions);
      })
      .catch((caught) => setError(caught instanceof Error ? caught.message : "Something went wrong. Try again."));
  }, [me?.isAdmin]);

  if (!me) {
    return (
      <section className="stack">
        <header className="page-head">
          <h1>Questions</h1>
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
          <h1>Questions</h1>
          <p className="lead">Only an admin can open this page.</p>
        </header>
        <Link href="/">Back to Ask HR</Link>
      </section>
    );
  }

  return (
    <section className="stack">
      <header className="page-head">
        <h1>Questions</h1>
        <p className="help">Questions are kept for 3 months.</p>
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
        !error ? <p role="status">Loading questions…</p> : null
      ) : rows.length === 0 ? (
        <p className="empty-state">No questions yet.</p>
      ) : (
        <ul className="plain-list">
          {rows.map((row) => (
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
      {rows !== null ? <details className="admin-log">
        <summary>Admin activity</summary>
      {actions.length === 0 ? (
        <p>No admin activity yet.</p>
      ) : (
        <ul className="plain-list">
          {actions.map((action) => (
            <li key={action.id}>
              <strong>
                {action.who} {action.action}
              </strong>
              <span className="file-name">{action.detail}</span>
              <span>{formatWhen(action.when)}</span>
            </li>
          ))}
        </ul>
      )}
      </details> : null}
    </section>
  );
}

function QuestionItem({ row, onRemoved }: { row: QuestionRow; onRemoved: (message: string) => void }) {
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const panelId = useId();
  return (
    <li>
      <p>
        <strong>{row.question || "Empty question"}</strong>
      </p>
      <p className="meta">
        {row.outcome_label} · {row.policy} · {row.asked_by} · {formatWhen(row.when)}
      </p>
      <div className="row-actions">
        <button type="button" aria-expanded={open} aria-controls={panelId} onClick={() => setOpen((value) => !value)}>
          {open ? "Hide details" : "Show details"}
        </button>
        {confirming ? (
          <>
            <span>Remove this question from the list?</span>
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
              Remove this question
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
      {open ? (
        <div id={panelId}>
          <ol>
            {row.steps.map((step) => (
              <li key={step.label}>
                <strong>{step.label}.</strong> {step.detail}
              </li>
            ))}
          </ol>
          {row.has_passages ? <QuestionPassages questionId={row.id} /> : null}
          <details>
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
