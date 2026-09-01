// Copyright 2026 Shopify Inc.
// SPDX-License-Identifier: Apache-2.0

"use client";

export type PortalView = "overview" | "catalog" | "orders";

const TABS: { id: PortalView; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "catalog", label: "Catalog" },
  { id: "orders", label: "Orders & inventory" },
];

export default function Header({
  view,
  onViewChange,
  assistantOpen,
  onToggleAssistant,
}: {
  view: PortalView;
  onViewChange: (view: PortalView) => void;
  assistantOpen: boolean;
  onToggleAssistant: () => void;
}) {
  return (
    <header className="flex items-center justify-between gap-4 border-b border-(--line) bg-(--ink) px-5 py-2.5 text-white">
      <div className="flex items-center gap-6">
        <div className="flex items-baseline gap-1.5">
          <span className="text-lg font-extrabold tracking-tight">ACME</span>
          <span className="text-lg font-light tracking-tight text-white/85">Supply Co.</span>
          <span className="ml-1.5 hidden rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-white/70 ring-1 ring-inset ring-white/20 sm:inline">
            Shopify Admin API
          </span>
        </div>
        <nav className="hidden items-center gap-1 sm:flex" aria-label="Portal views">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => onViewChange(tab.id)}
              className={`rounded-lg px-3 py-1.5 text-[13px] font-medium transition ${
                view === tab.id
                  ? "bg-white/15 text-white"
                  : "text-white/70 hover:bg-white/10 hover:text-white"
              }`}
              aria-current={view === tab.id ? "page" : undefined}
            >
              {tab.label}
            </button>
          ))}
        </nav>
      </div>
      <div className="flex items-center gap-3">
        <span className="hidden text-[11px] text-white/60 lg:inline">Store operations</span>
        <button
          onClick={onToggleAssistant}
          className={`rounded-lg px-3 py-1.5 text-[13px] font-bold transition ${
            assistantOpen
              ? "bg-white/15 text-white hover:bg-white/20"
              : "bg-(--accent) text-(--ink) hover:brightness-95"
          }`}
        >
          {assistantOpen ? "Hide assistant" : "Assistant"}
        </button>
      </div>
    </header>
  );
}

export function MobileViewTabs({
  view,
  onViewChange,
}: {
  view: PortalView;
  onViewChange: (view: PortalView) => void;
}) {
  return (
    <nav className="flex gap-1 border-b border-(--line) bg-white px-3 py-2 sm:hidden" aria-label="Portal views">
      {TABS.map((tab) => (
        <button
          key={tab.id}
          onClick={() => onViewChange(tab.id)}
          className={`rounded-lg px-2.5 py-1 text-[13px] font-medium transition ${
            view === tab.id
              ? "bg-(--well) text-(--ink)"
              : "text-(--ink-soft) hover:bg-(--well)/60"
          }`}
        >
          {tab.label}
        </button>
      ))}
    </nav>
  );
}
