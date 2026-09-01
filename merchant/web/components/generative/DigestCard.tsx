// Copyright 2026 Shopify Inc.
// SPDX-License-Identifier: Apache-2.0

import { formatMoney, formatNumber } from "web-shared";
import type { DigestEntry, DigestPayload } from "@/lib/types";

const KIND_LABELS: Record<DigestEntry["kind"], string> = {
  low_stock: "Low stock",
  slow_mover: "Slow mover",
  order_issue: "Order issue",
  metric: "Metric",
  pending_change: "Pending change",
  note: "Note",
};

const KIND_STYLES: Record<DigestEntry["kind"], string> = {
  low_stock: "bg-(--accent-soft) text-(--ink)",
  slow_mover: "bg-(--well) text-(--ink)",
  order_issue: "bg-red-50 text-(--danger)",
  metric: "bg-sky-50 text-sky-800",
  pending_change: "bg-violet-50 text-violet-800",
  note: "bg-(--well) text-(--ink-soft)",
};

/** Pending changes get no chip; approval stays on the change card. */
function triagePrompt(item: DigestEntry): { label: string; prompt: string } | null {
  const listingRef = item.listing
    ? `${item.listing.title} (${item.listing.listing_id})`
    : item.ref_id;
  switch (item.kind) {
    case "low_stock":
      return listingRef
        ? { label: "Draft a restock", prompt: `Draft a restock plan for ${listingRef}.` }
        : null;
    case "slow_mover":
      return listingRef
        ? { label: "Draft a plan", prompt: `${listingRef} is moving slowly — what are my options?` }
        : null;
    case "order_issue":
      return {
        label: "Draft a reply",
        prompt: item.ref_id
          ? `Help me handle order ${item.ref_id}: ${item.headline}`
          : `Help me handle this order issue: ${item.headline}`,
      };
    case "metric":
      return { label: "Ask why", prompt: `${item.headline} — what's driving it?` };
    default:
      return null;
  }
}

function ContextLine({ item }: { item: DigestEntry }) {
  if (item.listing) {
    return (
      <div className="text-[11px] tabular-nums text-(--ink-soft)">
        {item.listing.listing_id} · {formatNumber(item.listing.stock)} in stock ·{" "}
        {formatMoney(item.listing.price)}
      </div>
    );
  }
  if (item.change) {
    return (
      <div className="text-[11px] text-(--ink-soft)">
        {item.change.change_id} · {item.change.status} · {item.change.summary}
      </div>
    );
  }
  return null;
}

export default function DigestCard({
  payload,
  onPrefill,
}: {
  payload: DigestPayload;
  onPrefill?: (text: string) => void;
}) {
  const items = payload.items ?? [];
  return (
    <section className="ac-reveal rounded-2xl border border-(--line) bg-white p-4 shadow-sm">
      <h3 className="text-[15px] font-semibold text-(--ink)">
        {payload.title ?? "Needs attention"}
      </h3>
      <div className="mt-2.5 divide-y divide-(--line)">
        {items.map((item, index) => {
          const triage = onPrefill ? triagePrompt(item) : null;
          return (
            <div key={`${item.ref_id ?? item.headline}-${index}`} className="flex gap-3 py-2.5">
              <span
                className={`mt-0.5 h-fit shrink-0 rounded px-1.5 py-0.5 text-[11px] font-bold uppercase tracking-wide ${KIND_STYLES[item.kind]}`}
              >
                {KIND_LABELS[item.kind]}
              </span>
              <div className="min-w-0 flex-1">
                <div className="text-[13px] font-medium leading-snug text-(--ink)">
                  {item.headline}
                </div>
                {item.why_it_matters ? (
                  <div className="mt-0.5 text-[13px] leading-snug text-(--ink-soft)">
                    {item.why_it_matters}
                  </div>
                ) : null}
                <ContextLine item={item} />
                {triage ? (
                  <button
                    onClick={() => onPrefill?.(triage.prompt)}
                    className="mt-1.5 rounded-full border border-(--line) bg-white px-2.5 py-0.5 text-[12px] font-semibold text-(--ink) transition hover:border-(--accent) hover:bg-(--accent-soft)"
                  >
                    {triage.label}
                  </button>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
