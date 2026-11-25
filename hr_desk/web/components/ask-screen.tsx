"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";
import { useSession } from "@/components/session";
import { api, type AskResponse } from "@/lib/api";
import { formatAnswer } from "@/lib/format-answer";

const GENERAL_SUGGESTIONS = ["What is in the code of conduct?", "What is the corrective action policy?"];
const ADDITIONAL_SUGGESTIONS: Record<string, string[]> = {
  employee: ["What does the medical takaful policy cover?"],
  manager: ["How do I request approval for business travel?"],
  executive: ["How do I file a travel claim?"],
  hr: ["How does a provident fund loan work?"],
};

type Chat = { id: string; title: string };

type ConversationMessage = {
  role: "user" | "assistant";
  text: string;
};

type Turn = ConversationMessage & {
  id: number;
};

type ChatList = {
  conversations: Chat[];
  messageLimit: number;
  retentionDays: number;
  questionLimit: number;
};

export function AskScreen() {
  const { me, loadError, retry } = useSession();
  const questionId = useId();
  const helpId = useId();
  const errorId = useId();
  const historyId = useId();
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [chats, setChats] = useState<Chat[]>([]);
  const [chatId, setChatId] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [limits, setLimits] = useState<ChatList | null>(null);
  const [hiddenMessages, setHiddenMessages] = useState(0);
  const [historyError, setHistoryError] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [sending, setSending] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);
  const nextTurnId = useRef(0);
  const threadEnd = useRef<HTMLDivElement | null>(null);
  const composer = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (turns.length) threadEnd.current?.scrollIntoView({ block: "end" });
  }, [turns, sending]);

  useEffect(() => {
    request.current?.abort();
    setTurns([]);
    setChatId("");
    setHiddenMessages(0);
    setLimits(null);
    setChats([]);
    setQuestion("");
    setError("");
    setHistoryError("");
    setHistoryOpen(false);
    if (!me) return;
    let active = true;
    api<ChatList>("/api/conversations")
      .then((result) => {
        if (!active) return;
        setChats(result.conversations);
        setLimits(result);
      })
      .catch(() => { if (active) setHistoryError("Could not load saved chats."); });
    return () => { active = false; };
  }, [me?.email]);

  async function openChat(id: string) {
    if (busy) return;
    setBusy(true);
    setDeleting(false);
    setError("");
    try {
      const result = id
        ? await api<{ messages: ConversationMessage[]; hiddenMessages: number }>(`/api/conversations/${id}`)
        : { messages: [], hiddenMessages: 0 };
      setChatId(id);
      setHiddenMessages(result.hiddenMessages);
      setTurns(result.messages.map((item) => ({ ...item, id: ++nextTurnId.current })));
      setQuestion("");
      setHistoryOpen(false);
      composer.current?.focus({ preventScroll: true });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not open that chat.");
    } finally {
      setBusy(false);
    }
  }

  async function deleteChat() {
    setBusy(true);
    setError("");
    try {
      await api(`/api/conversations/${chatId}`, { method: "DELETE" });
      setChats((items) => items.filter((item) => item.id !== chatId));
      setChatId("");
      setHiddenMessages(0);
      setTurns([]);
      setQuestion("");
      setDeleting(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not delete that chat.");
    } finally {
      setBusy(false);
    }
  }

  function stop() {
    request.current?.abort();
  }

  async function submit(text: string) {
    const trimmed = text.trim();
    if (busy || request.current) return;
    if (!trimmed) {
      setError("Type your question first.");
      return;
    }
    if (limits && trimmed.length > limits.questionLimit) {
      setError(`Use ${limits.questionLimit} characters or fewer.`);
      return;
    }
    const controller = new AbortController();
    const userTurn: Turn = { id: ++nextTurnId.current, role: "user", text: trimmed };
    request.current = controller;
    setError("");
    setBusy(true);
    setSending(true);
    setQuestion("");
    setTurns((current) => [...current, userTurn]);
    try {
      let id = chatId;
      if (!id) {
        const chat = await api<{ id: string }>("/api/conversations", { method: "POST", signal: controller.signal });
        if (controller.signal.aborted) return;
        id = chat.id;
        setChatId(id);
        setChats((items) => [{ id, title: trimmed.slice(0, 70) }, ...items].slice(0, 20));
      }
      const result = await api<AskResponse>("/api/ask", {
        method: "POST",
        body: JSON.stringify({ question: trimmed, conversation_id: id }),
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      const answer: Turn = { id: ++nextTurnId.current, role: "assistant", text: result.answer };
      setTurns((current) => [...current, answer].slice(-(limits?.messageLimit ?? 40)));
    } catch (caught) {
      setTurns((current) => current.filter((turn) => turn.id !== userTurn.id));
      setQuestion(trimmed);
      if (!controller.signal.aborted) {
        setError(caught instanceof Error ? caught.message : "The assistant is temporarily unavailable.");
      }
    } finally {
      if (request.current === controller) {
        request.current = null;
        setSending(false);
        setBusy(false);
      }
    }
  }

  if (!me) {
    return (
      <section className="stack auth">
        <header className="page-head">
          <h1>Ask about a policy</h1>
          <p className="lead">Sign in to ask about Stylo policies.</p>
        </header>
        <div className="actions">
          <Link className="primary" href="/sign-in">
            Sign in
          </Link>
          <Link className="secondary" href="/sign-up">
            Set up your account
          </Link>
        </div>
        {loadError ? (
          <p className="form-error" role="alert">
            {loadError}{" "}
            <button type="button" onClick={() => void retry()}>
              Try again
            </button>
          </p>
        ) : null}
      </section>
    );
  }

  return (
    <section className="chat-layout">
      <button className="chat-history-toggle" type="button" aria-expanded={historyOpen} aria-controls={historyId}
        onClick={() => setHistoryOpen((open) => !open)}>
        {historyOpen ? "Hide chat history" : "Show chat history"}
      </button>
      <aside id={historyId} className={`chat-sidebar${historyOpen ? " is-open" : ""}`} aria-label="Chat history">
        <div className="sidebar-heading"><span className="eyebrow">YOUR WORKSPACE</span><p>Conversations</p><span className="sidebar-subtitle">Your questions, all in one place.</span></div>
        <button className="primary new-chat" type="button" disabled={busy} onClick={() => void openChat("")}><span aria-hidden="true">＋</span> New chat</button>
        <h2>Saved chats</h2>
        {historyError ? <p className="banner" role="status">{historyError}</p> : null}
        {!historyError && chats.length === 0 ? <p className="chat-sidebar-note">Your chats will appear here.</p> : null}
        <ul className="chat-list">
          {chats.map((chat) => (
            <li key={chat.id}>
              <button type="button" aria-current={chat.id === chatId ? "true" : undefined} disabled={busy}
                onClick={() => void openChat(chat.id)} title={chat.title}>{chat.title}</button>
            </li>
          ))}
        </ul>
        {limits ? (
          <details className="chat-details"><summary>Chat storage</summary>
            <p>Up to {limits.messageLimit} messages per chat, kept for {limits.retentionDays} days after your last activity.</p>
          </details>
        ) : null}
      </aside>
      <div className="stack employee-chat">
      <header className="page-head chat-heading">
        <div className="chat-intro">
          <span className="eyebrow"><span className="status-dot" aria-hidden="true" /> YOUR HR DESK</span>
          <h1>Ask about a policy</h1>
          <p className="lead">A little clarity for your workday. Ask a question to get started.</p>
        </div>
        {chatId && !deleting ? <button type="button" disabled={busy} onClick={() => setDeleting(true)}>Delete chat</button> : null}
        {deleting ? (
          <div className="row-actions confirm" role="group" aria-label="Confirm chat deletion">
            <span>Delete this chat?</span>
            <button className="danger" type="button" disabled={busy} onClick={() => void deleteChat()}>Delete</button>
            <button type="button" disabled={busy} onClick={() => setDeleting(false)}>Cancel</button>
          </div>
        ) : null}
      </header>
      <div className="chat-transcript">
      {hiddenMessages > 0 ? (
        <p className="banner" role="status">Earlier messages were hidden after a policy or access change.</p>
      ) : null}
      {turns.length > 0 ? (
        <ol className="thread" aria-label="Conversation">
          {turns.map((turn, index) => (
            <li key={turn.id} className={turn.role}>
              {turn.role === "user" ? (
                <p>
                  <span className="whisper">You asked</span>
                  {turn.text}
                </p>
              ) : (
                <Answer
                  text={turn.text}
                  live={index === turns.length - 1}
                />
              )}
            </li>
          ))}
          {sending ? (
            <li className="assistant">
              <div className="answer">
                <span className="whisper">Answer</span>
                <p role="status">Checking policies…</p>
              </div>
            </li>
          ) : null}
        </ol>
      ) : (
        <div className="chat-welcome">
          <div className="welcome-mark" aria-hidden="true">✦</div>
          <h2>How can we help today?</h2>
          <p>Find your way through workplace policies, one question at a time.</p>
          <div className="suggestions">
            <h3>Start with a question</h3>
            <ul className="people">
              {[...GENERAL_SUGGESTIONS, ...(ADDITIONAL_SUGGESTIONS[me.designation] || []), "What is my fuel entitlement?"].map((item) => (
                <li key={item}>
                  <button type="button" disabled={busy} onClick={() => void submit(item)}>
                    <span>{item}</span><span className="suggestion-arrow" aria-hidden="true">↗</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
      <div ref={threadEnd} />
      </div>
      <div className="chat-composer">
      <form
        className="ask-form"
        aria-busy={busy}
        onSubmit={(event) => {
          event.preventDefault();
          void submit(question);
        }}
      >
        <div className="field">
          <label htmlFor={questionId}>Your question</label>
          <textarea
            ref={composer}
            id={questionId}
            name="question"
            value={question}
            rows={3}
            maxLength={limits?.questionLimit}
            aria-describedby={error ? `${helpId} ${errorId}` : helpId}
            placeholder="Ask about leave, travel, or another policy"
            aria-invalid={error ? true : undefined}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && window.matchMedia("(pointer: fine)").matches) {
                event.preventDefault();
                void submit(question);
              }
            }}
          />
          <span id={helpId} className="sr-only">On a desktop keyboard, press Enter to ask or Shift+Enter for a new line.</span>
        </div>
        {error ? (
          <p id={errorId} className="form-error" role="alert">
            {error}
          </p>
        ) : null}
        {sending ? (
          <button className="stop" type="button" onClick={stop}>
            Stop
          </button>
        ) : (
          <button className="primary" type="submit" disabled={busy}>
            Ask
          </button>
        )}
      </form>
      <p className="composer-note">Answers are based on the policies available to you. When in doubt, ask HR.</p>
      </div>
      </div>
    </section>
  );
}

function Answer({ text, live }: { text: string; live: boolean }) {
  return (
    <div className="answer" aria-live={live ? "polite" : undefined}>
      <span className="whisper">Answer</span>
      <Formatted text={text} />
    </div>
  );
}

function LinkText({ text }: { text: string }) {
  const parts = text.split(/(https?:\/\/[^\s)]+)/g);
  return parts.map((part, index) => {
    if (!part.startsWith("http")) return <span key={`t-${index}`}>{part}</span>;
    const href = part.replace(/[.,]$/, "");
    return (
      <a key={`u-${index}`} href={href} target="_blank" rel="noopener noreferrer">
        {href}
      </a>
    );
  });
}

function Formatted({ text, labels = [] }: { text: string; labels?: string[] }) {
  const blocks = formatAnswer(text, labels);
  return (
    <div className="answer-copy">
      {blocks.map((block, index) => {
        if (block.type === "heading") {
          return <h2 className="answer-topic" key={`heading-${index}`}>{block.text}</h2>;
        }
        if (block.type === "table") {
          return (
            <div className="table-wrap" key={`table-${index}`}>
              {block.caption ? <p className="table-caption">{block.caption}</p> : null}
              <table className="answer-table">
                <thead>
                  <tr>
                    {block.headers.map((header, column) => (
                      <th key={column} scope="col">
                        {header}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {block.rows.map((row, rowIndex) => (
                    <tr key={`row-${rowIndex}`}>
                      {row.map((cell, cellIndex) =>
                        block.headers[0] === "Item" && cellIndex === 0 ? (
                          <th key={`cell-${cellIndex}`} scope="row">
                            <LinkText text={cell} />
                          </th>
                        ) : (
                          <td key={`cell-${cellIndex}`}>
                            <LinkText text={cell} />
                          </td>
                        ),
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        }
        if (block.type === "list") {
          return (
            <ul key={`list-${index}`}>
              {block.items.map((item, itemIndex) => (
                <li key={`item-${itemIndex}`}>
                  <LinkText text={item} />
                </li>
              ))}
            </ul>
          );
        }
        return (
          <p key={`line-${index}`}>
            <LinkText text={block.text} />
          </p>
        );
      })}
    </div>
  );
}
