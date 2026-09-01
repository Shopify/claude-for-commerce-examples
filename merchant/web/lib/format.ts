// Copyright 2026 Shopify Inc.
// SPDX-License-Identifier: Apache-2.0

/** ACME Supply Co. catalog labels on top of web-shared's formatters. */

import { formatMoney, formatNumber, formatRate, titleCase } from "web-shared";

const CATEGORY_LABELS: Record<string, string> = {
  "beauty-personal-care": "Beauty & personal care",
  fitness: "Fitness",
  "furniture-bedroom": "Furniture & bedroom",
  grocery: "Grocery",
  "home-kitchen": "Home & kitchen",
  "kids-room": "Kids' room",
  "office-electronics": "Office & electronics",
  "outdoor-camping": "Outdoor & camping",
  "pet-supplies": "Pet supplies",
  "toys-games": "Toys & games",
  travel: "Travel",
};

export function formatCategoryLabel(slug: string): string {
  return CATEGORY_LABELS[slug] ?? titleCase(slug.replaceAll("-", "_"));
}

// Diff fields are typed by name: a listing update can carry a price and a promotion a budget.
const CURRENCY_FIELD = /(^|_)(price|cost|budget|spend|revenue|amount)(_|$)/;
const PERCENT_FIELD = /(^|_)(pct|percent|rate|margin)(_|$)/;
const COUNT_FIELD = /(^|_)(stock|quantity|units|count)(_|$)/;

export function formatFieldValue(field: string, value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  // Numeric fields sometimes arrive as strings; other strings (ids like "0012") stay verbatim.
  if (
    typeof value === "string" &&
    /^-?\d+(\.\d+)?$/.test(value.trim()) &&
    (CURRENCY_FIELD.test(field) || PERCENT_FIELD.test(field) || COUNT_FIELD.test(field))
  ) {
    value = Number(value);
  }
  if (typeof value === "number") {
    if (CURRENCY_FIELD.test(field)) return formatMoney(value);
    if (PERCENT_FIELD.test(field)) return formatRate(value);
    if (COUNT_FIELD.test(field)) return formatNumber(value);
    return Number.isInteger(value) ? formatNumber(value) : value.toFixed(2);
  }
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
