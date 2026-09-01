// Copyright 2026 Shopify Inc.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useEffect, useState } from "react";
import { describeProposer, formatDate, formatMoney, humanizeField, titleCase } from "web-shared";
import { fetchListingDetail } from "@/lib/api";
import type { ChangeItem, ChangePreviewPayload, StagedChange } from "@/lib/types";
import { formatFieldValue } from "@/lib/format";

/** Characters; longer values render as stacked blocks. */
const LONG_TEXT_THRESHOLD = 48;

function isLongText(item: ChangeItem): boolean {
  return [item.before, item.after].some(
    (value) =>
      typeof value === "string" && (value.length > LONG_TEXT_THRESHOLD || value.includes("\n")),
  );
}

function LongTextDiff({ item }: { item: ChangeItem }) {
  return (
    <div className="rounded-lg border border-(--line)">
      <div className="flex items-baseline gap-2 border-b border-(--line) bg-(--well)/70 px-2.5 py-1.5">
        <span className="text-[11px] font-bold uppercase tracking-wide text-(--ink-soft)">
          {humanizeField(item.field)}
        </span>
        <span className="font-mono text-[11px] text-(--ink-soft)">{item.target}</span>
      </div>
      <div className="space-y-2 px-2.5 py-2">
        <div>
          <div className="text-[11px] font-bold uppercase tracking-wide text-(--ink-soft)">
            Before
          </div>
          <p className="mt-0.5 whitespace-pre-line break-words text-[13px] leading-snug text-(--ink-soft)">
            {formatFieldValue(item.field, item.before)}
          </p>
        </div>
        <div>
          <div className="text-[11px] font-bold uppercase tracking-wide text-(--ink)">
            After
          </div>
          <p className="mt-0.5 whitespace-pre-line break-words text-[13px] font-medium leading-snug text-(--ink)">
            {formatFieldValue(item.field, item.after)}
          </p>
        </div>
      </div>
    </div>
  );
}

/** Days of cover = new stock / (sales_last_30d / 30). */
function RestockMathRow({ item }: { item: ChangeItem }) {
  const [sales30, setSales30] = useState<number | null>(null);
  useEffect(() => {
    let cancelled = false;
    void fetchListingDetail(item.target).then((detail) => {
      if (!cancelled) setSales30(detail?.listing.sales_last_30d ?? null);
    });
    return () => {
      cancelled = true;
    };
  }, [item.target]);

  if (typeof item.before !== "number" || typeof item.after !== "number") return null;
  const added = item.after - item.before;
  if (added <= 0 || sales30 == null || sales30 <= 0) return null;
  const perDay = sales30 / 30;
  const coverDays = item.after / perDay;
  return (
    <div className="rounded-lg bg-(--well)/60 px-2.5 py-1.5 text-[12px] tabular-nums text-(--ink)">
      <span className="font-semibold">{sales30} sold/30d</span>
      {` → ${perDay.toFixed(1)}/day · ${item.before} on hand + ${added} added → `}
      <span className="font-semibold">{item.after}</span>
      {` ≈ ${coverDays.toFixed(0)} days of cover`}
    </div>
  );
}

function StatusChip({ status }: { status: StagedChange["status"] }) {
  const styles: Record<StagedChange["status"], string> = {
    staged: "bg-(--accent-soft) text-(--ink)",
    applied: "bg-emerald-100 text-emerald-800",
    discarded: "bg-(--well) text-(--ink-soft)",
  };
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${styles[status]}`}
    >
      {status}
    </span>
  );
}

/** Approve and Dismiss go through the same change gate the assistant uses. */
export default function ChangePreviewCard({
  payload,
  onAct,
}: {
  payload: ChangePreviewPayload;
  onAct?: (changeId: string, action: "apply" | "discard") => Promise<StagedChange | null>;
}) {
  // Starts from the streamed payload, is replaced by the API response when the operator
  // acts, and re-syncs when a change_update event rewrites the payload in a later turn.
  const [change, setChange] = useState<StagedChange>(payload.change);
  useEffect(() => {
    setChange(payload.change);
  }, [payload.change]);
  const [busy, setBusy] = useState<"apply" | "discard" | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const act = async (action: "apply" | "discard") => {
    if (!onAct || busy) return;
    setBusy(action);
    setActionError(null);
    const updated = await onAct(change.change_id, action);
    if (updated) {
      setChange(updated);
    } else {
      setActionError("That action did not go through. Check the API and try again.");
    }
    setBusy(null);
  };

  const resolved = change.status !== "staged";

  return (
    <section className="ac-reveal rounded-2xl border border-(--line) bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="text-[15px] font-semibold text-(--ink)">
            {payload.headline ?? "Proposed change"}
          </h3>
          <div className="mt-0.5 text-[13px] text-(--ink-soft)">
            {titleCase(change.kind)} · {describeProposer(change)} · {formatDate(change.created_at)}
          </div>
        </div>
        <StatusChip status={change.status} />
      </div>

      <p className="mt-2 text-[15px] leading-relaxed text-(--ink)">
        {change.summary}
      </p>
      {payload.note ? (
        <p className="mt-1 text-[13px] leading-snug text-(--ink-soft)">
          {payload.note}
        </p>
      ) : null}

      {change.items.length ? (
        <div className="mt-3 space-y-2">
          {change.items.some((item) => !isLongText(item)) ? (
            <div className="panel-scroll overflow-x-auto rounded-lg border border-(--line)">
              <table className="w-full border-collapse text-[13px]">
                <thead>
                  <tr className="bg-(--well)/70 text-left text-[11px] font-bold uppercase tracking-wide text-(--ink-soft)">
                    <th className="px-2.5 py-1.5">Target</th>
                    <th className="px-2.5 py-1.5">Field</th>
                    <th className="px-2.5 py-1.5">Before</th>
                    <th className="px-2.5 py-1.5">After</th>
                  </tr>
                </thead>
                <tbody>
                  {change.items
                    .filter((item) => !isLongText(item))
                    .map((item, index) => (
                      <tr
                        key={`${item.target}-${item.field}-${index}`}
                        className="border-t border-(--line)"
                      >
                        <td className="whitespace-nowrap px-2.5 py-1.5 font-mono text-[11px] text-(--ink)">
                          {item.target}
                        </td>
                        <td className="px-2.5 py-1.5 text-(--ink)">
                          {humanizeField(item.field)}
                        </td>
                        <td className="px-2.5 py-1.5 tabular-nums text-(--ink-soft)">
                          {formatFieldValue(item.field, item.before)}
                        </td>
                        <td className="px-2.5 py-1.5 font-semibold tabular-nums text-(--ink)">
                          {formatFieldValue(item.field, item.after)}
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          ) : null}
          {change.items.filter(isLongText).map((item, index) => (
            <LongTextDiff key={`${item.target}-${item.field}-long-${index}`} item={item} />
          ))}
          {change.kind === "inventory_action"
            ? change.items
                .filter((item) => item.field === "stock")
                .map((item) => <RestockMathRow key={`${item.target}-math`} item={item} />)
            : null}
        </div>
      ) : null}

      {change.margin_impact != null ? (
        <div className="mt-2 text-[13px] tabular-nums text-(--ink)">
          Margin impact:{" "}
          <span className={change.margin_impact < 0 ? "font-semibold text-(--danger)" : "font-semibold text-emerald-700"}>
            {change.margin_impact > 0 ? "+" : ""}
            {formatMoney(change.margin_impact)}
          </span>
        </div>
      ) : null}

      {change.guardrail_notes?.length ? (
        <ul className="mt-2 space-y-1 rounded-lg bg-(--accent-soft)/70 p-2.5 text-[13px] leading-snug text-(--ink)">
          {change.guardrail_notes.map((note) => (
            <li key={note} className="flex gap-1.5">
              <span aria-hidden>!</span>
              <span>{note}</span>
            </li>
          ))}
        </ul>
      ) : null}

      <div className="mt-3 flex items-center gap-2">
        {resolved ? (
          <div className="text-[13px] text-(--ink-soft)">
            {change.status === "applied"
              ? `Approved${change.applied_by ? ` by ${change.applied_by}` : ""}${
                  change.applied_at ? ` on ${formatDate(change.applied_at)}` : ""
                }.`
              : `Dismissed${
                  change.discarded_by
                    ? ` by ${change.discarded_by}${
                        change.discarded_by_kind === "agent" ? "'s assistant" : ""
                      }`
                    : ""
                } — nothing was changed.`}
          </div>
        ) : (
          <>
            <button
              onClick={() => void act("apply")}
              disabled={busy !== null || !onAct}
              className="rounded-lg bg-(--accent) px-3.5 py-1.5 text-[13px] font-bold text-(--ink) transition hover:brightness-95 disabled:opacity-50"
            >
              {busy === "apply" ? "Applying…" : "Approve"}
            </button>
            <button
              onClick={() => void act("discard")}
              disabled={busy !== null || !onAct}
              className="rounded-lg border border-(--line) bg-white px-3.5 py-1.5 text-[13px] font-semibold text-(--ink) transition hover:bg-(--well) disabled:opacity-50"
            >
              {busy === "discard" ? "Dismissing…" : "Dismiss"}
            </button>
            <span className="text-[11px] leading-snug text-(--ink-soft)">
              Nothing changes until you approve.
            </span>
          </>
        )}
      </div>
      {actionError ? <div className="mt-2 text-[13px] text-(--danger)">{actionError}</div> : null}
    </section>
  );
}
