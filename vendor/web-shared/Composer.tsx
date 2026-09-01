// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useEffect, useRef, useState } from "react";

export interface Prefill {
  text: string;
  /** Changes on every request so the same text can be offered twice. */
  nonce: number;
}

/** A prefill only fills the draft. screenshot_tour.py waits on the "Working…" placeholder. */
export function Composer({
  send,
  ready,
  busy,
  label,
  placeholder,
  prefill,
  className = "",
}: {
  send: (text: string) => void;
  ready: boolean;
  busy: boolean;
  label: string;
  placeholder: string;
  prefill?: Prefill | null;
  className?: string;
}) {
  const [draft, setDraft] = useState("");
  const boxRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!prefill) return;
    setDraft(prefill.text);
    boxRef.current?.focus();
  }, [prefill]);

  const submit = () => {
    if (!draft.trim() || busy || !ready) return;
    send(draft);
    setDraft("");
  };

  return (
    <form
      className={`flex items-end gap-2 ${className}`}
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <textarea
        ref={boxRef}
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            submit();
          }
        }}
        rows={1}
        aria-label={label}
        placeholder={busy ? "Working…" : placeholder}
        className="max-h-40 min-w-0 flex-1 resize-none rounded-(--radius) border border-(--line) bg-(--card) px-3.5 py-2 text-[16px] text-(--ink) outline-none transition placeholder:text-(--ink-soft)/70 focus:border-(--accent)"
      />
      <button type="submit" disabled={busy || !ready || !draft.trim()} className="btn-primary">
        Send
      </button>
    </form>
  );
}
