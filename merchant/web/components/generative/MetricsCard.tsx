// Copyright 2026 Shopify Inc.
// SPDX-License-Identifier: Apache-2.0

import { formatChangePct, formatMoney, formatNumber, formatRate, titleCase } from "web-shared";
import type { MetricEntry, MetricsPayload, MetricSeries } from "@/lib/types";

const CURRENCY_METRICS = new Set(["sales", "average_order_value", "revenue", "spend"]);
const RATE_METRICS = new Set(["conversion_rate", "return_rate", "click_through_rate"]);

function metricLabel(metric: string): string {
  if (metric === "average_order_value") return "Avg order value";
  return titleCase(metric);
}

function metricValue(entry: MetricEntry): string | null {
  if (entry.value == null) return null;
  if (CURRENCY_METRICS.has(entry.metric)) return formatMoney(entry.value);
  if (RATE_METRICS.has(entry.metric)) return formatRate(entry.value);
  return formatNumber(entry.value);
}

function ChangeChip({ changePct }: { changePct: number }) {
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

function Sparkline({ series }: { series: MetricSeries }) {
  const points = series.points ?? [];
  if (points.length < 2) return null;
  const width = 120;
  const height = 32;
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
      className="mt-2 h-8 w-full"
      preserveAspectRatio="none"
      role="img"
      aria-label={`${metricLabel(series.metric)} trend`}
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

export default function MetricsCard({ payload }: { payload: MetricsPayload }) {
  const metrics = payload.metrics ?? [];
  return (
    <section className="ac-reveal rounded-2xl border border-(--line) bg-white p-4 shadow-sm">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-[15px] font-semibold text-(--ink)">{payload.title ?? "Performance"}</h3>
        {payload.period ? (
          <span className="text-[13px] text-(--ink-soft)">{payload.period}</span>
        ) : null}
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2.5">
        {metrics.map((entry, index) => {
          const value = metricValue(entry);
          return (
            <div
              key={`${entry.metric}-${index}`}
              className="rounded-xl border border-(--line) bg-(--well)/40 px-3 py-2.5"
            >
              <div className="text-[11px] font-semibold uppercase tracking-wide text-(--ink-soft)">
                {metricLabel(entry.metric)}
              </div>
              <div className="mt-1 flex items-baseline gap-2">
                {value != null ? (
                  <span className="text-lg font-bold tabular-nums text-(--ink)">{value}</span>
                ) : null}
                {entry.change_pct != null ? <ChangeChip changePct={entry.change_pct} /> : null}
              </div>
              {entry.series ? <Sparkline series={entry.series} /> : null}
              {entry.note ? (
                <div className="mt-1 text-[11px] leading-snug text-(--ink-soft)">{entry.note}</div>
              ) : null}
            </div>
          );
        })}
      </div>
    </section>
  );
}
