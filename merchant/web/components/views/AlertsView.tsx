// Copyright 2026 Shopify Inc.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useEffect, useState } from "react";
import { formatDayMonth, formatNumber, QuotedAsData, titleCase } from "web-shared";
import { fetchAlerts } from "@/lib/api";
import type { AlertsResponse, OrderIssue } from "@/lib/types";

const ISSUE_STYLES: Record<OrderIssue["kind"], string> = {
  delayed: "bg-orange-100 text-orange-800",
  return_spike: "bg-red-50 text-(--danger)",
  buyer_message: "bg-sky-50 text-sky-800",
  damaged: "bg-red-50 text-(--danger)",
};

function IssueRow({ issue }: { issue: OrderIssue }) {
  return (
    <div className="flex gap-3 py-3">
      <span
        className={`h-fit shrink-0 rounded px-1.5 py-0.5 text-[11px] font-bold uppercase tracking-wide ${ISSUE_STYLES[issue.kind]}`}
      >
        {titleCase(issue.kind)}
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-[13px] font-medium leading-snug text-(--ink)">{issue.summary}</div>
        <div className="mt-0.5 text-[11px] text-(--ink-soft)">
          Order {issue.order_id}
          {issue.listing_id ? ` · ${issue.listing_id}` : ""}
          {issue.opened_at ? ` · opened ${formatDayMonth(issue.opened_at)}` : ""}
        </div>
        {issue.buyer_message_excerpt ? (
          <>
            <blockquote className="mt-1.5 border-l-2 border-(--line) pl-2.5 text-[13px] italic leading-relaxed text-(--ink-soft)">
              Buyer wrote: &ldquo;{issue.buyer_message_excerpt}&rdquo;
            </blockquote>
            {/* Some fixture excerpts are injection attempts, so the note sits beside the quote. */}
            <QuotedAsData subject="Buyer message" className="mt-1 pl-2.5" />
          </>
        ) : null}
      </div>
    </div>
  );
}

export default function AlertsView({ refreshKey }: { refreshKey: number }) {
  const [data, setData] = useState<AlertsResponse | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void fetchAlerts().then((response) => {
      if (cancelled) return;
      if (response) {
        setData(response);
        setFailed(false);
      } else {
        setFailed(true);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  if (failed && !data) {
    return (
      <div className="rounded-xl border border-(--line) bg-(--well)/50 p-6 text-sm text-(--ink-soft)">
        The merchant API isn&apos;t reachable, so order and inventory alerts can&apos;t load.
      </div>
    );
  }

  if (!data) {
    return (
      <div className="grid gap-4 xl:grid-cols-2">
        <div className="ac-skeleton h-64 rounded-xl" />
        <div className="ac-skeleton h-64 rounded-xl" />
      </div>
    );
  }

  const lowStock = data.inventory.filter((alert) => alert.kind === "low_stock");
  const slowMovers = data.inventory.filter((alert) => alert.kind === "slow_mover");

  return (
    <div className="ac-reveal flex flex-col gap-5">
      <h1 className="text-lg font-bold text-(--ink)">Orders &amp; inventory</h1>

      <div className="grid gap-4 xl:grid-cols-2">
        <section className="rounded-xl border border-(--line) bg-white p-4 shadow-sm">
          <h2 className="text-sm font-semibold text-(--ink)">Order exceptions</h2>
          {data.order_issues.length === 0 ? (
            <p className="mt-2 text-[13px] text-(--ink-soft)">No open order issues.</p>
          ) : (
            <div className="mt-1 divide-y divide-(--line)">
              {data.order_issues.map((issue) => (
                <IssueRow key={issue.issue_id} issue={issue} />
              ))}
            </div>
          )}
        </section>

        <div className="flex flex-col gap-4">
          <section className="rounded-xl border border-(--line) bg-white p-4 shadow-sm">
            <h2 className="text-sm font-semibold text-(--ink)">Low stock</h2>
            {lowStock.length === 0 ? (
              <p className="mt-2 text-[13px] text-(--ink-soft)">No low-stock listings.</p>
            ) : (
              <table className="mt-2 w-full border-collapse text-[13px]">
                <thead>
                  <tr className="text-left text-[11px] font-bold uppercase tracking-wide text-(--ink-soft)">
                    <th className="py-1.5 pr-2">Listing</th>
                    <th className="py-1.5 pr-2 text-right">Stock</th>
                    <th className="py-1.5 pr-2 text-right">Threshold</th>
                    <th className="py-1.5 text-right">Days of cover</th>
                  </tr>
                </thead>
                <tbody>
                  {lowStock.map((alert) => (
                    <tr key={alert.listing_id} className="border-t border-(--line)">
                      <td className="py-1.5 pr-2">
                        <div className="font-medium text-(--ink)">{alert.title}</div>
                        <div className="font-mono text-[11px] text-(--ink-soft)">
                          {alert.listing_id}
                        </div>
                      </td>
                      <td className="py-1.5 pr-2 text-right tabular-nums text-(--ink)">
                        {formatNumber(alert.stock)}
                      </td>
                      <td className="py-1.5 pr-2 text-right tabular-nums text-(--ink-soft)">
                        {alert.threshold != null ? formatNumber(alert.threshold) : "—"}
                      </td>
                      <td className="py-1.5 text-right tabular-nums text-(--ink)">
                        {alert.days_of_cover != null ? alert.days_of_cover.toFixed(0) : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          <section className="rounded-xl border border-(--line) bg-white p-4 shadow-sm">
            <h2 className="text-sm font-semibold text-(--ink)">Slow movers</h2>
            {slowMovers.length === 0 ? (
              <p className="mt-2 text-[13px] text-(--ink-soft)">No slow movers.</p>
            ) : (
              <table className="mt-2 w-full border-collapse text-[13px]">
                <thead>
                  <tr className="text-left text-[11px] font-bold uppercase tracking-wide text-(--ink-soft)">
                    <th className="py-1.5 pr-2">Listing</th>
                    <th className="py-1.5 pr-2 text-right">Stock</th>
                    <th className="py-1.5 pr-2 text-right">Sold, 30d</th>
                    <th className="py-1.5 text-right">Days of cover</th>
                  </tr>
                </thead>
                <tbody>
                  {slowMovers.map((alert) => (
                    <tr key={alert.listing_id} className="border-t border-(--line)">
                      <td className="py-1.5 pr-2">
                        <div className="font-medium text-(--ink)">{alert.title}</div>
                        <div className="font-mono text-[11px] text-(--ink-soft)">
                          {alert.listing_id}
                        </div>
                      </td>
                      <td className="py-1.5 pr-2 text-right tabular-nums text-(--ink)">
                        {formatNumber(alert.stock)}
                      </td>
                      <td className="py-1.5 pr-2 text-right tabular-nums text-(--ink-soft)">
                        {alert.sales_last_30d != null ? formatNumber(alert.sales_last_30d) : "—"}
                      </td>
                      <td className="py-1.5 text-right tabular-nums text-(--ink)">
                        {alert.days_of_cover != null ? alert.days_of_cover.toFixed(0) : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
