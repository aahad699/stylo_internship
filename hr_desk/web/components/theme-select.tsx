"use client";

import { useEffect, useId, useRef, useState } from "react";

export type ThemeOption = { value: string; label: string };

export function ThemeSelect({
  id,
  value,
  options,
  onChange,
  ariaInvalid,
  ariaDescribedBy,
}: {
  id?: string;
  value: string;
  options: ThemeOption[];
  onChange: (value: string) => void;
  ariaInvalid?: boolean;
  ariaDescribedBy?: string;
}) {
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState(-1);
  const [opensUp, setOpensUp] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const listId = useId();
  const selected = options.find((option) => option.value === value) ?? options[0];

  useEffect(() => {
    if (!open) return;
    function onPointer(event: MouseEvent) {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onPointer);
    return () => document.removeEventListener("mousedown", onPointer);
  }, [open]);

  useEffect(() => {
    if (!open || cursor < 0) return;
    root.current?.querySelector<HTMLElement>(`[data-index="${cursor}"]`)?.scrollIntoView({ block: "nearest" });
  }, [open, cursor]);

  function openMenu() {
    const box = root.current?.getBoundingClientRect();
    const roomBelow = box ? window.innerHeight - box.bottom : 240;
    setOpensUp(Boolean(box && roomBelow < 220 && box.top > roomBelow));
    setCursor(Math.max(0, options.findIndex((option) => option.value === value)));
    setOpen(true);
  }

  function choose(next: string) {
    onChange(next);
    setOpen(false);
  }

  return (
    <div className={`theme-select${open ? " is-open" : ""}${opensUp ? " opens-up" : ""}`} ref={root}>
      <button
        type="button"
        id={id}
        className="theme-select-button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-invalid={ariaInvalid || undefined}
        aria-describedby={ariaDescribedBy}
        onClick={() => (open ? setOpen(false) : openMenu())}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            setOpen(false);
            return;
          }
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            if (!open) {
              openMenu();
              return;
            }
            const step = event.key === "ArrowDown" ? 1 : -1;
            setCursor((current) => {
              const start = current < 0 ? 0 : current;
              return Math.min(options.length - 1, Math.max(0, start + step));
            });
            return;
          }
          if ((event.key === "Enter" || event.key === " ") && open && cursor >= 0 && options[cursor]) {
            event.preventDefault();
            choose(options[cursor].value);
          }
        }}
      >
        <span>{selected?.label ?? ""}</span>
        <svg className="theme-select-chevron" viewBox="0 0 20 20" aria-hidden="true">
          <path d="M5 7.5 10 12.5 15 7.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {open ? (
        <ul className="theme-select-menu" id={listId} role="listbox">
          {options.map((option, index) => (
            <li key={option.value}>
              <button
                type="button"
                role="option"
                data-index={index}
                aria-selected={option.value === value}
                className={`${option.value === value ? "is-selected" : ""} ${index === cursor ? "is-active" : ""}`.trim()}
                onMouseEnter={() => setCursor(index)}
                onClick={() => choose(option.value)}
              >
                {option.label}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
