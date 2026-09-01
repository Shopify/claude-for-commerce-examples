// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

const moneyFormatters = new Map<string, Intl.NumberFormat>();

export function formatMoney(
  value: number,
  currency = "USD",
  options: { whole?: boolean } = {},
): string {
  const key = `${currency}:${options.whole ? 0 : 2}`;
  let formatter = moneyFormatters.get(key);
  if (!formatter) {
    formatter = new Intl.NumberFormat("en-US", {
      style: "currency",
      currency,
      maximumFractionDigits: options.whole ? 0 : 2,
    });
    moneyFormatters.set(key, formatter);
  }
  return formatter.format(value);
}

const plain = new Intl.NumberFormat("en-US");

export function formatNumber(value: number): string {
  return plain.format(value);
}

/** Rates arrive as percent values (3.4 means 3.4%). */
export function formatRate(value: number): string {
  return `${value.toFixed(1)}%`;
}

export function formatChangePct(value: number): string {
  return `${value > 0 ? "+" : ""}${value.toFixed(1)}%`;
}

/** Date-only strings parse as local midnight so they do not render a day early. */
function parseDate(value: string): Date {
  return new Date(/^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T00:00:00` : value);
}

const ISO_DAY = /\d{4}-\d{2}-\d{2}/g;

function dayLabel(value: string): string {
  const date = parseDate(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

/** "Jun 24, 2026"; dates inside a trailing note ("(revised from ...)") are formatted too. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return "";
  if (/^\d{4}-\d{2}-\d{2}(?!T)/.test(value)) return value.replace(ISO_DAY, dayLabel);
  return dayLabel(value);
}

/** "Jun 24" */
export function formatDayMonth(value: string | null | undefined): string {
  if (!value) return "";
  const date = parseDate(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export function titleCase(value: string): string {
  return value.replaceAll("_", " ").replace(/^\w/, (c) => c.toUpperCase());
}

/** "attributes_min_nights" as "Min nights". */
export function humanizeField(field: string): string {
  return titleCase(field.replace(/^attributes?_/, ""));
}

const ISO_RANGE = /^(\d{4}-\d{2}-\d{2})\s*\/\s*(\d{4}-\d{2}-\d{2})$/;

/** "2026-06-19/2026-06-25" as "Jun 19–25". */
export function formatPeriodLabel(value: string | null | undefined): string {
  if (!value) return "";
  const match = ISO_RANGE.exec(value.trim());
  if (!match) return value;
  const start = parseDate(match[1]);
  const end = parseDate(match[2]);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return value;
  if (start.getFullYear() !== end.getFullYear()) {
    return `${formatDate(match[1])} – ${formatDate(match[2])}`;
  }
  if (start.getMonth() !== end.getMonth()) {
    return `${formatDayMonth(match[1])} – ${formatDayMonth(match[2])}`;
  }
  return `${formatDayMonth(match[1])}–${end.getDate()}`;
}

/** "prior week"/"prior period" when the windows abut at equal length; else the window's label. */
export function formatComparisonLabel(
  period: string | null | undefined,
  compareTo: string | null | undefined,
): string {
  if (!compareTo) return "";
  const primary = ISO_RANGE.exec(period?.trim() ?? "");
  const compare = ISO_RANGE.exec(compareTo.trim());
  if (primary && compare) {
    const dayMs = 24 * 60 * 60 * 1000;
    const primaryStart = parseDate(primary[1]).getTime();
    const primaryDays = Math.round((parseDate(primary[2]).getTime() - primaryStart) / dayMs);
    const compareEnd = parseDate(compare[2]).getTime();
    const compareDays = Math.round((compareEnd - parseDate(compare[1]).getTime()) / dayMs);
    if (primaryDays === compareDays && Math.round((primaryStart - compareEnd) / dayMs) === 1) {
      return primaryDays === 6 ? "prior week" : "prior period";
    }
  }
  return formatPeriodLabel(compareTo);
}

export function describeProposer(change: {
  created_by: string;
  created_by_kind?: "operator" | "agent";
}): string {
  return change.created_by_kind === "agent"
    ? `Proposed by ${change.created_by}'s assistant`
    : `Staged by ${change.created_by}`;
}

/** Approvals are always a person. */
export function describeResolver(change: {
  status: string;
  applied_by?: string | null;
  discarded_by?: string | null;
  discarded_by_kind?: "operator" | "agent" | null;
}): string | null {
  if (change.status === "applied" && change.applied_by) return `Approved by ${change.applied_by}`;
  if (change.status === "discarded" && change.discarded_by) {
    return change.discarded_by_kind === "agent"
      ? `Discarded by ${change.discarded_by}'s assistant`
      : `Discarded by ${change.discarded_by}`;
  }
  return null;
}
