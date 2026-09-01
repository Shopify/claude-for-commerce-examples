// Copyright 2026 Shopify Inc.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useEffect, useMemo, useState } from "react";
import {
  describeProposer,
  describeResolver,
  formatChangePct,
  formatDayMonth,
  formatMoney,
  formatNumber,
  formatRate,
  titleCase,
} from "web-shared";
import { fetchOverview } from "@/lib/api";
import type {
  HomeInsight,
  InventoryAlert,
  OrderIssue,
  OverviewResponse,
  StagedChange,
  MetricPoint,
} from "@/lib/types";

function ChangeChip({ changePct }: { changePct: number | null | undefined }) {
  if (changePct == null) return null;
  const positive = changePct >= 0;
  return (
    <span
      className={`rounded px-1.5 py-0.5 text-[11px] font-semibold tabular-nums ${
        positive ? "bg-emerald-100 text-emerald-800" : "bg-red-100 text-(--danger)"
      }`}
    >
      {formatChangePct(changePct)}
    </span>
  );
}

function TrendSparkline({ points, label }: { points: MetricPoint[]; label: string }) {
  if (points.length < 2) return null;
  const width = 120;
  const height = 26;
  const values = points.map((point) => point.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const step = width / (points.length - 1);
  const coords = points.map((point, index) => {
    const x = index * step;
    const y = height - 3 - ((point.value - min) / span) * (height - 6);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="mt-2 h-6 w-full"
      preserveAspectRatio="none"
      role="img"
      aria-label={`${label} trend over the period`}
    >
      <polyline
        points={`0,${height} ${coords.join(" ")} ${width},${height}`}
        fill="var(--well)"
        stroke="none"
      />
      <polyline
        points={coords.join(" ")}
        fill="none"
        stroke="var(--ink)"
        strokeWidth="1.5"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}

function KpiCard({
  label,
  value,
  changePct,
  trend,
  onAskAssistant,
}: {
  label: string;
  value: string;
  changePct: number | null | undefined;
  trend?: MetricPoint[];
  onAskAssistant: (text: string) => void;
}) {
  const question =
    changePct == null
      ? `How is ${label.toLowerCase()} trending, and what's behind it?`
      : `Why is ${label.toLowerCase()} ${changePct >= 0 ? "up" : "down"} ${Math.abs(
          changePct,
        ).toFixed(1)}% vs the prior week?`;
  return (
    <button
      onClick={() => onAskAssistant(question)}
      className="group rounded-xl border border-(--line) bg-white px-4 py-3 text-left shadow-sm transition hover:border-(--accent)"
      aria-label={`${label}: ${value}. Ask the assistant why.`}
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-(--ink-soft)">
          {label}
        </span>
        <span className="text-[11px] font-semibold text-(--ink) opacity-0 transition group-hover:opacity-100">
          Ask why ↗
        </span>
      </div>
      <div className="mt-1.5 flex items-baseline gap-2">
        <span className="text-xl font-bold tabular-nums text-(--ink)">{value}</span>
        <ChangeChip changePct={changePct} />
      </div>
      {trend?.length ? <TrendSparkline points={trend} label={label} /> : null}
      {trend?.length ? (
        <div className="mt-1 text-right text-[10px] text-(--ink-soft)">vs prior 7d</div>
      ) : null}
    </button>
  );
}

/** Approval itself happens on the change card. */
function ApprovalsStrip({
  changes,
  onAskAssistant,
}: {
  changes: StagedChange[];
  onAskAssistant: (text: string) => void;
}) {
  if (changes.length === 0) return null;
  return (
    <section className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-violet-200 bg-violet-50/70 px-4 py-2.5">
      <span className="text-[13px] font-bold text-violet-900">
        {changes.length} change{changes.length === 1 ? "" : "s"} awaiting your approval
      </span>
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
        {changes.map((change) => (
          <span
            key={change.change_id}
            className="max-w-72 truncate rounded-full bg-white px-2.5 py-0.5 text-[12px] text-violet-900"
            title={change.summary}
          >
            {change.summary}
          </span>
        ))}
      </div>
      <button
        onClick={() =>
          onAskAssistant(
            "Walk me through the changes awaiting my approval and what each one would do.",
          )
        }
        className="rounded-lg bg-violet-700 px-3 py-1 text-[12px] font-bold text-white transition hover:brightness-110"
      >
        Review
      </button>
    </section>
  );
}

type AttentionRow =
  | { kind: "issue"; issue: OrderIssue }
  | { kind: "inventory"; alert: InventoryAlert };

interface AttentionGroup {
  id: string;
  label: string;
  rows: AttentionRow[];
}

const GROUP_CAP = 4;

function groupAttention(data: OverviewResponse): { groups: AttentionGroup[]; hidden: number } {
  const { inventory, order_issues } = data.needs_attention;
  const lowStock = inventory
    .filter((alert) => alert.kind === "low_stock")
    .sort((a, b) => (a.days_of_cover ?? Infinity) - (b.days_of_cover ?? Infinity));
  const slowMovers = inventory.filter((alert) => alert.kind === "slow_mover");
  const groups: AttentionGroup[] = [
    {
      id: "order_issues",
      label: "Order issues",
      rows: order_issues.map((issue) => ({ kind: "issue" as const, issue })),
    },
    {
      id: "low_stock",
      label: "Low stock",
      rows: lowStock.map((alert) => ({ kind: "inventory" as const, alert })),
    },
    {
      id: "slow_movers",
      label: "Slow movers",
      rows: slowMovers.map((alert) => ({ kind: "inventory" as const, alert })),
    },
  ].filter((group) => group.rows.length > 0);
  const hidden = groups.reduce((sum, group) => sum + Math.max(0, group.rows.length - GROUP_CAP), 0);
  return {
    groups: groups.map((group) => ({ ...group, rows: group.rows.slice(0, GROUP_CAP) })),
    hidden,
  };
}

const ATTENTION_TAGS: Record<string, string> = {
  delayed: "bg-red-50 text-(--danger)",
  return_spike: "bg-red-50 text-(--danger)",
  buyer_message: "bg-sky-50 text-sky-800",
  damaged: "bg-red-50 text-(--danger)",
  low_stock: "bg-(--accent-soft) text-(--ink)",
  slow_mover: "bg-(--well) text-(--ink)",
};

function AttentionTag({ kind }: { kind: string }) {
  return (
    <span
      className={`h-fit shrink-0 rounded px-1.5 py-0.5 text-[11px] font-bold uppercase tracking-wide ${
        ATTENTION_TAGS[kind] ?? "bg-(--well) text-(--ink)"
      }`}
    >
      {titleCase(kind)}
    </span>
  );
}

function AskChip({ prompt, onAskAssistant }: { prompt: string; onAskAssistant: (t: string) => void }) {
  return (
    <button
      onClick={() => onAskAssistant(prompt)}
      className="mt-1 rounded-full border border-(--line) bg-white px-2.5 py-0.5 text-[12px] font-semibold text-(--ink) transition hover:border-(--accent) hover:bg-(--accent-soft)"
    >
      Ask assistant
    </button>
  );
}

function AttentionItem({
  row,
  onAskAssistant,
}: {
  row: AttentionRow;
  onAskAssistant: (text: string) => void;
}) {
  if (row.kind === "issue") {
    const issue = row.issue;
    return (
      <div className="flex gap-3 py-2.5">
        <AttentionTag kind={issue.kind} />
        <div className="min-w-0 flex-1">
          <div className="text-[13px] font-medium leading-snug text-(--ink)">
            {issue.summary}
          </div>
          <div className="mt-0.5 text-[11px] text-(--ink-soft)">
            Order {issue.order_id}
            {issue.opened_at ? ` · opened ${formatDayMonth(issue.opened_at)}` : ""}
          </div>
          <AskChip
            prompt={`Order ${issue.order_id}: ${issue.summary} — what are my options?`}
            onAskAssistant={onAskAssistant}
          />
        </div>
      </div>
    );
  }
  const alert = row.alert;
  const prompt =
    alert.kind === "low_stock"
      ? `Draft a restock plan for ${alert.title} (${alert.listing_id}).`
      : `${alert.title} (${alert.listing_id}) is moving slowly — what are my options?`;
  return (
    <div className="flex gap-3 py-2.5">
      <AttentionTag kind={alert.kind} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <span className="text-[13px] font-medium leading-snug text-(--ink)">
            {alert.title}
          </span>
          {alert.kind === "low_stock" && alert.stock > 0 && alert.storefront_visible ? (
            // A paused listing still alerts here but shows no chip to shoppers.
            <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold text-amber-800">
              Storefront shows “Only {formatNumber(alert.stock)} left”
            </span>
          ) : null}
        </div>
        <div className="mt-0.5 text-[11px] tabular-nums text-(--ink-soft)">
          {formatNumber(alert.stock)} in stock
          {alert.threshold != null ? ` · threshold ${formatNumber(alert.threshold)}` : ""}
          {alert.days_of_cover != null ? ` · ${alert.days_of_cover.toFixed(0)} days of cover` : ""}
          {alert.sales_last_30d != null ? ` · ${formatNumber(alert.sales_last_30d)} sold in 30d` : ""}
        </div>
        <AskChip prompt={prompt} onAskAssistant={onAskAssistant} />
      </div>
    </div>
  );
}

function InsightsCard({
  insights,
  onAskAssistant,
}: {
  insights: HomeInsight[];
  onAskAssistant: (text: string) => void;
}) {
  if (insights.length === 0) return null;
  return (
    <section className="rounded-xl border border-(--line) bg-white p-4 shadow-sm">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold text-(--ink)">From your assistant</h2>
        <span className="text-[10px] font-semibold uppercase tracking-wide text-(--ink-soft)">
          Computed from your data
        </span>
      </div>
      <div className="mt-1 divide-y divide-(--line)">
        {insights.map((insight) => (
          <div key={insight.insight_id} className="py-2.5">
            <div className="text-[13px] font-medium leading-snug text-(--ink)">
              {insight.headline}
            </div>
            {insight.detail ? (
              <div className="mt-0.5 text-[12px] leading-snug text-(--ink-soft)">
                {insight.detail}
              </div>
            ) : null}
            <button
              onClick={() => onAskAssistant(insight.prompt)}
              className="mt-1 rounded-full border border-(--line) bg-white px-2.5 py-0.5 text-[12px] font-semibold text-(--ink) transition hover:border-(--accent) hover:bg-(--accent-soft)"
            >
              Discuss
            </button>
          </div>
        ))}
      </div>
    </section>
  );
}

const CHANGE_STATUS_STYLES: Record<string, string> = {
  applied: "bg-emerald-100 text-emerald-800",
  discarded: "bg-(--well) text-(--ink-soft)",
};

function RecentChangeRow({ change }: { change: StagedChange }) {
  const applied = change.status === "applied";
  const resolution = describeResolver(change);
  const actedAt = applied ? change.applied_at : change.discarded_at;
  return (
    <div className="flex gap-3 py-2">
      <span
        className={`h-fit shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${
          CHANGE_STATUS_STYLES[change.status] ?? CHANGE_STATUS_STYLES.discarded
        }`}
      >
        {change.status}
      </span>
      <div className="min-w-0 flex-1">
        <div
          className="line-clamp-2 break-words text-[13px] font-medium leading-snug text-(--ink)"
          title={change.summary}
        >
          {change.summary}
        </div>
        <div className="mt-0.5 text-[11px] text-(--ink-soft)">
          {titleCase(change.kind)}
          {` · ${describeProposer(change)}`}
          {resolution ? ` · ${resolution}` : ""}
          {actedAt ? ` · ${formatDayMonth(actedAt)}` : ""}
        </div>
      </div>
    </div>
  );
}

const ORDER_STATUS_STYLES: Record<string, string> = {
  processing: "bg-(--well) text-(--ink)",
  shipped: "bg-sky-100 text-sky-800",
  out_for_delivery: "bg-(--accent-soft) text-(--ink)",
  delivered: "bg-emerald-100 text-emerald-800",
  delayed: "bg-orange-100 text-orange-800",
  cancelled: "bg-(--well) text-(--ink-soft)",
  return_initiated: "bg-violet-100 text-violet-800",
  refunded: "bg-emerald-100 text-emerald-800",
};

export default function OverviewView({
  refreshKey,
  onAskAssistant,
}: {
  refreshKey: number;
  /** Prefills the composer; nothing is sent. */
  onAskAssistant: (text: string) => void;
}) {
  const [data, setData] = useState<OverviewResponse | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void fetchOverview().then((response) => {
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

  const attention = useMemo(
    () => (data ? groupAttention(data) : { groups: [], hidden: 0 }),
    [data],
  );
  const pendingChanges = useMemo(
    () =>
      (data?.needs_attention.pending_changes ?? []).filter(
        (change) => change.status === "staged",
      ),
    [data],
  );

  if (failed && !data) {
    return (
      <div className="rounded-xl border border-(--line) bg-(--well)/50 p-6 text-sm text-(--ink-soft)">
        The merchant API on port 8005 isn&apos;t reachable. Start it with{" "}
        <code className="rounded bg-white px-1 font-mono text-[13px]">
          uvicorn merchant.api.main:app --port 8005
        </code>{" "}
        and reload.
      </div>
    );
  }

  if (!data) {
    return (
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[0, 1, 2, 3].map((index) => (
          <div key={index} className="ac-skeleton h-20 rounded-xl" />
        ))}
      </div>
    );
  }

  const snapshot = data.snapshot;
  // The snapshot carries no AOV delta, so derive it from the sales and orders deltas.
  const aovChangePct =
    snapshot.sales_change_pct != null && snapshot.orders_change_pct != null
      ? ((1 + snapshot.sales_change_pct / 100) / (1 + snapshot.orders_change_pct / 100) - 1) * 100
      : null;

  return (
    <div className="ac-reveal flex flex-col gap-5">
      <div className="flex items-baseline justify-between">
        <h1 className="text-lg font-bold text-(--ink)">Overview</h1>
        <div className="text-[13px] text-(--ink-soft)">
          {snapshot.period}
          {snapshot.compare_to ? ` · vs ${snapshot.compare_to}` : ""}
        </div>
      </div>

      {data.shop_domain ? (
        <div className="-mt-2 text-[11px] text-(--ink-soft)">
          {data.store_kind === "local" ? (
            <>
              Data from the local store standing in for the Shopify Admin API, as{" "}
              <span className="font-mono text-(--ink)">{data.shop_domain}</span> — no account and
              no network. An approved change really does change what this page reads.
            </>
          ) : (
            <>
              Data from the Shopify Admin API for{" "}
              <span className="font-mono text-(--ink)">{data.shop_domain}</span>.
            </>
          )}
        </div>
      ) : null}

      <ApprovalsStrip changes={pendingChanges} onAskAssistant={onAskAssistant} />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          label="Sales"
          value={formatMoney(snapshot.sales, "USD", { whole: snapshot.sales >= 1000 })}
          changePct={snapshot.sales_change_pct}
          trend={data.trends?.sales}
          onAskAssistant={onAskAssistant}
        />
        <KpiCard
          label="Orders"
          value={formatNumber(snapshot.orders)}
          changePct={snapshot.orders_change_pct}
          trend={data.trends?.orders}
          onAskAssistant={onAskAssistant}
        />
        <KpiCard
          label="Conversion"
          value={formatRate(snapshot.conversion_rate)}
          changePct={snapshot.conversion_change_pct}
          trend={data.trends?.conversion}
          onAskAssistant={onAskAssistant}
        />
        <KpiCard
          label="Avg order value"
          value={formatMoney(snapshot.average_order_value)}
          changePct={aovChangePct}
          trend={data.trends?.average_order_value}
          onAskAssistant={onAskAssistant}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-5">
        <section className="rounded-xl border border-(--line) bg-white p-4 shadow-sm xl:col-span-3">
          <h2 className="text-sm font-semibold text-(--ink)">Needs attention</h2>
          {attention.groups.length === 0 ? (
            <p className="mt-2 text-[13px] text-(--ink-soft)">Nothing is waiting on you.</p>
          ) : (
            <>
              {attention.groups.map((group) => (
                <div key={group.id} className="mt-2">
                  <div className="text-[11px] font-bold uppercase tracking-wide text-(--ink-soft)">
                    {group.label} ({group.rows.length})
                  </div>
                  <div className="divide-y divide-(--line)">
                    {group.rows.map((row, index) => (
                      <AttentionItem
                        key={`${group.id}-${index}`}
                        row={row}
                        onAskAssistant={onAskAssistant}
                      />
                    ))}
                  </div>
                </div>
              ))}
              {attention.hidden > 0 ? (
                <div className="border-t border-(--line) pt-2 text-[12px] text-(--ink-soft)">
                  +{attention.hidden} more item{attention.hidden === 1 ? "" : "s"} need
                  {attention.hidden === 1 ? "s" : ""} attention — see Catalog and Orders &amp;
                  inventory for the rest.
                </div>
              ) : null}
            </>
          )}
        </section>

        <div className="flex flex-col gap-4 xl:col-span-2">
          <InsightsCard insights={data.insights ?? []} onAskAssistant={onAskAssistant} />

          <section className="rounded-xl border border-(--line) bg-white p-4 shadow-sm">
            <h2 className="text-sm font-semibold text-(--ink)">Recent orders</h2>
            {data.recent_orders.length === 0 ? (
              <p className="mt-2 text-[13px] text-(--ink-soft)">No orders yet.</p>
            ) : (
              <table className="mt-2 w-full border-collapse text-[13px]">
                <thead>
                  <tr className="text-left text-[11px] font-bold uppercase tracking-wide text-(--ink-soft)">
                    <th className="py-1.5 pr-2">Order</th>
                    <th className="py-1.5 pr-2">Placed</th>
                    <th className="py-1.5 pr-2">Status</th>
                    <th className="py-1.5 pr-2 text-right">Items</th>
                    <th className="py-1.5 text-right">Total</th>
                  </tr>
                </thead>
                <tbody>
                  {data.recent_orders.map((order) => (
                    <tr key={order.order_id} className="border-t border-(--line)">
                      <td className="py-1.5 pr-2 font-mono text-[11px] text-(--ink)">
                        {order.order_id}
                      </td>
                      <td className="py-1.5 pr-2 text-(--ink-soft)">
                        {formatDayMonth(order.placed_at)}
                      </td>
                      <td className="py-1.5 pr-2">
                        <span
                          className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${
                            ORDER_STATUS_STYLES[order.status] ?? ORDER_STATUS_STYLES.processing
                          }`}
                        >
                          {order.status.replaceAll("_", " ")}
                        </span>
                      </td>
                      <td className="py-1.5 pr-2 text-right tabular-nums text-(--ink)">
                        {order.items}
                      </td>
                      <td className="py-1.5 text-right tabular-nums text-(--ink)">
                        {formatMoney(order.total)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          <section className="rounded-xl border border-(--line) bg-white p-4 shadow-sm">
            <h2 className="text-sm font-semibold text-(--ink)">Recent changes</h2>
            {data.recent_changes.length === 0 ? (
              <p className="mt-2 text-[13px] text-(--ink-soft)">No changes applied yet today.</p>
            ) : (
              <div className="mt-1 divide-y divide-(--line)">
                {data.recent_changes.map((change) => (
                  <RecentChangeRow key={change.change_id} change={change} />
                ))}
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
