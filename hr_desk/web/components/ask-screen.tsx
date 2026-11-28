"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";
import { useSession } from "@/components/session";
import { api, type AskResponse } from "@/lib/api";
import { formatAnswer } from "@/lib/format-answer";
import { DESIGNATIONS } from "@/lib/policy-roles";

function dayGreeting(name: string) {
  const hour = new Date().getHours();
  const part = hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
  const first = name.trim().split(/\s+/)[0];
  return first ? `${part}, ${first}` : part;
}

const GENERAL_SUGGESTIONS = ["What is in the code of conduct?", "What is the corrective action policy?"];
const ADDITIONAL_SUGGESTIONS: Record<string, string[]> = {
  employee: ["What does the medical takaful policy cover?"],
  manager: ["How do I request approval for business travel?"],
  executive: ["How do I file a travel claim?"],
  hr: ["How does a provident fund loan work?"],
};

type Chat = { id: string; title: string; updatedAt?: string; pinned?: boolean };

type ConversationMessage = {
  role: "user" | "assistant";
  text: string;
  at?: string | null;
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

function SidebarIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
      <rect x="2.2" y="3" width="13.6" height="12" rx="2.2" />
      <path d="M7.1 3.2v11.6" />
    </svg>
  );
}

function RailIcon({ name }: { name: "sidebar" | "new" | "search" }) {
  const common = {
    width: 18,
    height: 18,
    viewBox: "0 0 18 18",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.6,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true as const,
  };
  if (name === "sidebar") {
    return <SidebarIcon />;
  }
  if (name === "new") {
    return (
      <svg {...common}>
        <path d="M10.2 3.6 14.4 7.8 7.1 15.1 2.8 15.2 2.9 10.9z" />
        <path d="M8.8 5 13 9.2" />
      </svg>
    );
  }
  return (
    <svg {...common}>
      <circle cx="7.6" cy="7.6" r="4.15" />
      <path d="M10.7 10.7 15 15" />
    </svg>
  );
}

function RailButton({
  label,
  icon,
  disabled,
  pressed,
  onClick,
}: {
  label: string;
  icon: "sidebar" | "new" | "search";
  disabled?: boolean;
  pressed?: boolean;
  onClick: () => void;
}) {
  return (
    <button className={`rail-button${pressed ? " is-open" : ""}`} type="button" aria-label={label}
      aria-expanded={pressed} disabled={disabled} onClick={onClick}>
      <RailIcon name={icon} />
      <span className="rail-hint" aria-hidden="true">{label}</span>
    </button>
  );
}

export function AskScreen() {
  const { me, farewell, loadError, retry } = useSession();
  const questionId = useId();
  const helpId = useId();
  const errorId = useId();
  const historyId = useId();
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [chats, setChats] = useState<Chat[]>([]);
  const [chatId, setChatId] = useState("");
  const [deletingId, setDeletingId] = useState("");
  const [menuId, setMenuId] = useState("");
  const [renamingId, setRenamingId] = useState("");
  const [renameDraft, setRenameDraft] = useState("");
  const [limits, setLimits] = useState<ChatList | null>(null);
  const [followUpsClosed, setFollowUpsClosed] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [historyOpen, setHistoryOpen] = useState(true);
  const [chatQuery, setChatQuery] = useState("");
  const [focusSearch, setFocusSearch] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [closedSuggestions, setClosedSuggestions] = useState<string[]>([]);
  const [sending, setSending] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);
  const nextTurnId = useRef(0);
  const threadEnd = useRef<HTMLDivElement | null>(null);
  const composer = useRef<HTMLTextAreaElement | null>(null);
  const searchBox = useRef<HTMLInputElement | null>(null);
  const searchPopover = useRef<HTMLDivElement | null>(null);
  const searchToggle = useRef<HTMLSpanElement | null>(null);
  const renameBox = useRef<HTMLInputElement | null>(null);
  const chatMenu = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (turns.length) threadEnd.current?.scrollIntoView({ block: "end" });
  }, [turns, sending]);

  useEffect(() => {
    const box = composer.current;
    if (!box) return;
    box.style.height = "auto";
    box.style.height = `${box.scrollHeight}px`;
  }, [question, followUpsClosed, turns.length]);

  useEffect(() => {
    if (historyOpen && focusSearch) {
      searchBox.current?.focus();
      setFocusSearch(false);
    }
  }, [historyOpen, focusSearch]);

  useEffect(() => {
    if (historyOpen) setSearchOpen(false);
  }, [historyOpen]);

  useEffect(() => {
    if (!searchOpen) return;
    return () => setChatQuery("");
  }, [searchOpen]);

  useEffect(() => {
    if (!searchOpen) return;
    searchPopover.current?.querySelector("input")?.focus();
    function onPointer(event: MouseEvent) {
      const target = event.target as Node;
      if (searchPopover.current?.contains(target) || searchToggle.current?.contains(target)) return;
      setSearchOpen(false);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setSearchOpen(false);
    }
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [searchOpen]);

  useEffect(() => {
    if (!menuId) return;
    function onPointer(event: MouseEvent) {
      if (chatMenu.current?.contains(event.target as Node)) return;
      setMenuId("");
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setMenuId("");
    }
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuId]);

  useEffect(() => {
    if (renamingId) renameBox.current?.focus();
  }, [renamingId]);

  useEffect(() => {
    request.current?.abort();
    setTurns([]);
    setChatId("");
    setFollowUpsClosed(false);
    setLimits(null);
    setChats([]);
    setMenuId("");
    setRenamingId("");
    setDeletingId("");
    setQuestion("");
    setError("");
    setHistoryError("");
    setHistoryOpen(window.matchMedia("(min-width: 721px)").matches);
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
    setDeletingId("");
    setMenuId("");
    setRenamingId("");
    setError("");
    try {
      const result = id
        ? await api<{ messages: ConversationMessage[]; followUpsClosed: boolean }>(`/api/conversations/${id}`)
        : { messages: [], followUpsClosed: false };
      setChatId(id);
      setFollowUpsClosed(result.followUpsClosed);
      setTurns(result.messages.map((item) => ({ ...item, id: ++nextTurnId.current })));
      setQuestion("");
      if (window.matchMedia("(max-width: 720px)").matches) setHistoryOpen(false);
      if (!result.followUpsClosed) composer.current?.focus({ preventScroll: true });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not open that chat.");
    } finally {
      setBusy(false);
    }
  }

  async function deleteChat(id: string) {
    setBusy(true);
    setError("");
    try {
      await api(`/api/conversations/${id}`, { method: "DELETE" });
      setChats((items) => items.filter((item) => item.id !== id));
      if (chatId === id) {
        setChatId("");
        setFollowUpsClosed(false);
        setTurns([]);
        setQuestion("");
      }
      setDeletingId("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not delete that chat.");
    } finally {
      setBusy(false);
    }
  }

  function startRename(chat: Chat) {
    setMenuId("");
    setDeletingId("");
    setRenamingId(chat.id);
    setRenameDraft(chat.title);
  }

  async function saveRename(id: string) {
    const title = renameDraft.trim().replace(/\s+/g, " ");
    if (!title) {
      setError("Type a name for this chat.");
      return;
    }
    if (title.length > 70) {
      setError("Use a name of 70 characters or fewer.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const saved = await api<Chat>(`/api/conversations/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ title }),
      });
      setChats((items) => items.map((item) => item.id === id ? { ...item, title: saved.title } : item));
      setRenamingId("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not rename that chat.");
    } finally {
      setBusy(false);
    }
  }

  async function togglePin(chat: Chat) {
    setMenuId("");
    setBusy(true);
    setError("");
    const pinned = !chat.pinned;
    try {
      const saved = await api<Chat>(`/api/conversations/${chat.id}`, {
        method: "PATCH",
        body: JSON.stringify({ pinned }),
      });
      setChats((items) => {
        const rest = items.filter((item) => item.id !== chat.id);
        const next = { ...saved, updatedAt: saved.updatedAt || chat.updatedAt };
        const pinnedRows = rest.filter((item) => item.pinned);
        const openRows = rest.filter((item) => !item.pinned);
        return pinned ? [next, ...pinnedRows, ...openRows] : [...pinnedRows, next, ...openRows];
      });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not update that chat.");
    } finally {
      setBusy(false);
    }
  }

  function stop() {
    request.current?.abort();
  }

  async function submit(text: string) {
    const trimmed = text.trim();
    if (busy || request.current || followUpsClosed) return;
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
        setChats((items) => {
          const pinnedRows = items.filter((item) => item.pinned);
          const openRows = [{ id, title: trimmed.slice(0, 70), pinned: false }, ...items.filter((item) => !item.pinned)];
          return [...pinnedRows, ...openRows.slice(0, 20)];
        });
      }
      const result = await api<AskResponse>("/api/ask", {
        method: "POST",
        body: JSON.stringify({ question: trimmed, conversation_id: id }),
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      const answer: Turn = { id: ++nextTurnId.current, role: "assistant", text: result.answer, at: result.at };
      setTurns((current) => [...current.map((turn) => turn.id === userTurn.id ? { ...turn, at: result.at } : turn), answer]
        .slice(-(limits?.messageLimit ?? 40)));
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

  const visibleChats = chats.filter((chat) =>
    chat.title.toLowerCase().includes(chatQuery.trim().toLowerCase()),
  );

  if (!me) {
    return (
      <section className="stack auth">
        <header className="page-head">
          <h1>{farewell ? `Goodbye, ${farewell}` : "Ask about a policy"}</h1>
          <p className="lead">{farewell ? "See you next time." : "Sign in to ask about Stylo policies."}</p>
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
    <section className={`chat-layout${historyOpen ? "" : " sidebar-closed"}`}>
      <aside id={historyId} className={`chat-sidebar${historyOpen ? " is-open" : ""}`} aria-label="Chat history">
        <div className="sidebar-rail">
          <RailButton label="Open conversations" icon="sidebar" onClick={() => setHistoryOpen(true)} />
          <RailButton label="New chat" icon="new" disabled={busy} onClick={() => void openChat("")} />
          <span ref={searchToggle}>
            <RailButton label="Search chats" icon="search" pressed={searchOpen} onClick={() => setSearchOpen((open) => !open)} />
          </span>
          {searchOpen ? (
            <div className="chat-search-popover" ref={searchPopover} role="dialog" aria-label="Search chats">
              <div className="chat-search-bar">
                <input type="search" value={chatQuery} placeholder="Search..."
                  onChange={(event) => setChatQuery(event.target.value)} />
                <button type="button" aria-label="Close search" onClick={() => setSearchOpen(false)}>×</button>
              </div>
              <p>Recent chats</p>
              {historyError ? <p className="chat-sidebar-note">{historyError}</p> : null}
              {!historyError && visibleChats.length === 0 ? (
                <p className="chat-sidebar-note">{chats.length === 0 ? "Your chats will appear here." : "No chats match that search."}</p>
              ) : null}
              <ul className="chat-search-results">
                {visibleChats.map((chat) => (
                  <li key={chat.id}>
                    <button type="button" disabled={busy} onClick={() => { setSearchOpen(false); void openChat(chat.id); }}>
                      <span className="chat-search-mark" aria-hidden="true" />
                      <span className="chat-row-copy">
                        <span className="chat-row-title">{chat.pinned ? <PinMark /> : null}{chat.title}</span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
        <div className="sidebar-body" hidden={!historyOpen}>
          <div className="sidebar-heading">
            <button className="primary new-chat" type="button" disabled={busy} onClick={() => void openChat("")}><span aria-hidden="true">＋</span> New chat</button>
            <button className="pane-toggle" type="button" aria-expanded="true" aria-controls={historyId}
              onClick={() => setHistoryOpen(false)}>
              <SidebarIcon />
              <span className="rail-hint" aria-hidden="true">Close conversations</span>
              <span className="sr-only">Close conversations</span>
            </button>
          </div>
          <label className="chat-search">
            <span className="sr-only">Search chats</span>
            <input ref={searchBox} type="search" value={chatQuery} placeholder="Search chats"
              onChange={(event) => setChatQuery(event.target.value)} />
          </label>
          <h2>Saved chats</h2>
          {historyError ? <p className="banner" role="status">{historyError}</p> : null}
          {!historyError && chats.length === 0 ? <p className="chat-sidebar-note">Your chats will appear here.</p> : null}
          {!historyError && chats.length > 0 && visibleChats.length === 0 ? (
            <p className="chat-sidebar-note">No chats match that search.</p>
          ) : null}
          <ul className="chat-list">
            {visibleChats.map((chat) => (
              <li key={chat.id} className={chat.pinned ? "is-pinned" : undefined}>
                {renamingId === chat.id ? (
                  <form className="chat-rename" onSubmit={(event) => { event.preventDefault(); void saveRename(chat.id); }}>
                    <label>
                      <span className="sr-only">Chat name</span>
                      <input
                        ref={renameBox}
                        value={renameDraft}
                        maxLength={70}
                        disabled={busy}
                        onChange={(event) => setRenameDraft(event.target.value)}
                        onKeyDown={(event) => { if (event.key === "Escape") setRenamingId(""); }}
                      />
                    </label>
                    <button className="primary" type="submit" disabled={busy}>Save</button>
                    <button type="button" disabled={busy} onClick={() => setRenamingId("")}>Cancel</button>
                  </form>
                ) : deletingId === chat.id ? (
                  <div className="chat-delete-confirm" role="group" aria-label="Confirm chat deletion">
                    <span>Delete this chat?</span>
                    <button className="danger" type="button" disabled={busy} onClick={() => void deleteChat(chat.id)}>Delete</button>
                    <button type="button" disabled={busy} onClick={() => setDeletingId("")}>Cancel</button>
                  </div>
                ) : (
                  <div ref={menuId === chat.id ? chatMenu : undefined}>
                    <div className="chat-row">
                      <button type="button" aria-current={chat.id === chatId ? "true" : undefined} disabled={busy}
                        onClick={() => void openChat(chat.id)} title={chat.title}>
                        <span className="chat-row-title">{chat.pinned ? <PinMark /> : null}{chat.title}</span>
                      </button>
                      <button className="chat-row-more" type="button" aria-label={`Options for ${chat.title}`}
                        aria-haspopup="menu" aria-expanded={menuId === chat.id} disabled={busy}
                        onClick={() => {
                          setDeletingId("");
                          setMenuId((current) => current === chat.id ? "" : chat.id);
                        }}>
                        <DotsIcon />
                      </button>
                    </div>
                    {menuId === chat.id ? (
                      <div className="chat-menu" role="menu">
                        <button type="button" role="menuitem" onClick={() => startRename(chat)}>Rename</button>
                        <button type="button" role="menuitem" onClick={() => void togglePin(chat)}>{chat.pinned ? "Unpin" : "Pin"}</button>
                        <button type="button" role="menuitem" onClick={() => { setMenuId(""); setDeletingId(chat.id); }}>Delete</button>
                      </div>
                    ) : null}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      </aside>
      <div className={`stack employee-chat${turns.length ? "" : " is-welcome"}`}>
      <header className="page-head chat-heading">
        {historyOpen ? null : (
          <button className="pane-toggle reopen-pane" type="button" aria-expanded="false" aria-controls={historyId}
            onClick={() => setHistoryOpen(true)}>
            <SidebarIcon />
            <span className="sr-only">Open conversations</span>
          </button>
        )}
        <div className="chat-intro">
          <h1>{chats.find((chat) => chat.id === chatId)?.title || "New chat"}</h1>
        </div>
      </header>
      <div className="chat-transcript">
      {turns.length > 0 ? (
        <ol className="thread" aria-label="Conversation">
          {turns.map((turn, index) => (
            <li key={turn.id} className={turn.role}>
              {turn.role === "user" ? (
                <p>
                  <span className="whisper">You asked</span>
                  {turn.text}
                  <MessageTime at={turn.at} />
                </p>
              ) : (
                <Answer
                  text={turn.text}
                  at={turn.at}
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
          <p className="chat-greeting">{dayGreeting(me.name)}</p>
          <h2>How can we help today?</h2>
        </div>
      )}
      <div ref={threadEnd} />
      </div>
      <div className="chat-composer">
      {followUpsClosed ? (
        <p className="follow-up-note" role="status">
          You can read this chat, but you can't ask a follow-up here. A policy or who can see it changed after these answers. Start a new chat to ask again.
        </p>
      ) : (
      <form
        className={`ask-form${turns.length ? "" : " welcome-form"}`}
        aria-busy={busy}
        onSubmit={(event) => {
          event.preventDefault();
          void submit(question);
        }}
      >
        <div className="field">
          <label htmlFor={questionId} className="sr-only">Your question</label>
          <textarea
            ref={composer}
            id={questionId}
            name="question"
            value={question}
            rows={1}
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
      )}
      {turns.length ? null : (
        <Suggestions
          items={[...GENERAL_SUGGESTIONS, ...(ADDITIONAL_SUGGESTIONS[me.designation] || []), "What is my fuel entitlement?"]}
          closed={closedSuggestions}
          busy={busy || followUpsClosed}
          onAsk={(item) => void submit(item)}
          onClose={(item) => setClosedSuggestions((current) => current.includes(item) ? current : [...current, item])}
        />
      )}
      <p className="composer-note">Pro tip: In confusion, consult HR.</p>
      </div>
      </div>
    </section>
  );
}

function DotsIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <circle cx="3.2" cy="8" r="1.15" />
      <circle cx="8" cy="8" r="1.15" />
      <circle cx="12.8" cy="8" r="1.15" />
    </svg>
  );
}

function PinMark() {
  return (
    <svg className="chat-pin" width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
      <path d="M6.2 2.6h3.6l-.6 3.2 2.3 1.5v1.2H4.5V7.3l2.3-1.5z" strokeLinejoin="round" />
      <path d="M8 8.5v5" strokeLinecap="round" />
    </svg>
  );
}

function Suggestions({
  items,
  closed,
  busy,
  onAsk,
  onClose,
}: {
  items: string[];
  closed: string[];
  busy: boolean;
  onAsk: (item: string) => void;
  onClose: (item: string) => void;
}) {
  const visible = items.filter((item) => !closed.includes(item));
  if (visible.length === 0) return null;
  return (
    <div className="welcome-suggestions">
      <h3>Suggestions</h3>
      <ul>
        {visible.map((item) => (
          <li key={item}>
            <button type="button" disabled={busy} onClick={() => onAsk(item)}>{item}</button>
            <button type="button" aria-label={`Dismiss ${item}`} onClick={() => onClose(item)}>
              <span aria-hidden="true">×</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function messageWhen(iso: string) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const day = date.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
  const time = date.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  return `${day}, ${time}`;
}

function MessageTime({ at }: { at?: string | null }) {
  if (!at) return null;
  return <time className="message-time" dateTime={at}>{messageWhen(at)}</time>;
}

function Answer({ text, at, live }: { text: string; at?: string | null; live: boolean }) {
  return (
    <div className="answer" aria-live={live ? "polite" : undefined}>
      <span className="whisper">Answer</span>
      <Formatted text={text} />
      <MessageTime at={at} />
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
  const { me } = useSession();
  const roleLabel = DESIGNATIONS.find(([code]) => code === me?.designation)?.[1] ?? "";
  const blocks = formatAnswer(text, labels, roleLabel);
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
