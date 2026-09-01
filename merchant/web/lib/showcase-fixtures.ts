// Copyright 2026 Shopify Inc.
// SPDX-License-Identifier: Apache-2.0

/** Adapted from the retail merchant fixtures under data/; restyled for ACME Supply Co. on Shopify. */

import type { ChangePreviewPayload, DigestPayload } from "./types";

const digest: DigestPayload = {
  title: "Morning digest",
  items: [
    {
      kind: "low_stock",
      ref_id: "ACME-4410",
      headline: "4 units left of the canvas tool apron (ACME-4410) — under two days of cover",
      why_it_matters: "Selling roughly 60 units a month on the Shopify storefront; at that pace the listing goes dark this week.",
      listing: {
        listing_id: "ACME-4410",
        title: "ACME Supply Co. Waxed Canvas Tool Apron",
        status: "active",
        price: 38.0,
        stock: 4,
      },
    },
    {
      kind: "order_issue",
      ref_id: "ACME-90244",
      headline: "Five returns on the folding step stool this week, all citing a wobbly hinge",
      why_it_matters: "A return spike on a top seller usually means a listing-content or quality-control gap.",
    },
    {
      kind: "metric",
      headline: "Outdoor & camping sales are up 18% week-over-week",
      why_it_matters: "Most of the lift traces to the collapsible cookware line.",
    },
  ],
  suggestions: ["Draft a restock plan", "Look at the step stool returns"],
};

const change_preview: ChangePreviewPayload = {
  change_id: "chg-7042",
  headline: "Refill ACME-4410 before it sells out",
  note: "Puts about a month of cover back on the shelf at the trailing sales pace, synced back to Shopify on approval.",
  change: {
    change_id: "chg-7042",
    kind: "inventory_action",
    status: "staged",
    summary: "Add 60 units of ACME Supply Co. Waxed Canvas Tool Apron (ACME-4410)",
    items: [{ target: "ACME-4410", field: "stock", before: 4, after: 64 }],
    created_at: "2026-07-09",
    created_by: "Avery",
    created_by_kind: "agent",
  },
  suggestions: ["Show the slow movers too", "What else is running low?"],
};

export const SHOWCASE = { digest, change_preview };
