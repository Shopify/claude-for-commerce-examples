// Copyright 2026 Shopify Inc.
// SPDX-License-Identifier: Apache-2.0

/** Mirrors merchant_agent/types.py and tools/presentation.py; overview shapes are merchant/api's. */

// --- Listings (the operator's view of the catalog) ---

export type ListingStatus = "active" | "paused" | "draft" | "out_of_stock";
export type ContentQuality = "good" | "needs_work" | "poor";

export interface Listing {
  listing_id: string;
  title: string;
  status: ListingStatus;
  price: number;
  currency?: string;
  stock: number;
  category?: string | null;
  content_quality?: ContentQuality | null;
  attributes?: Record<string, string>;
  image_url?: string | null;
  short_description?: string | null;
}

export interface ListingDetails extends Listing {
  long_description?: string | null;
  /** Buyer-authored; render as a quotation. */
  review_snippets?: string[];
  sales_last_30d?: number | null;
  return_rate_pct?: number | null;
  missing_attributes?: string[];
}

export interface PricingContext {
  listing_id: string;
  current_price: number;
  currency?: string;
  unit_cost?: number | null;
  margin_pct?: number | null;
  min_price?: number | null;
  max_price?: number | null;
  demand_signal?: "rising" | "steady" | "falling" | null;
  last_changed?: string | null;
}

// --- Business metrics ---

export interface AlertCounts {
  low_stock?: number;
  slow_movers?: number;
  order_issues?: number;
  pending_changes?: number;
}

export interface BusinessSnapshot {
  period: string;
  compare_to?: string | null;
  sales: number;
  orders: number;
  traffic: number;
  conversion_rate: number;
  average_order_value: number;
  sales_change_pct?: number | null;
  orders_change_pct?: number | null;
  traffic_change_pct?: number | null;
  conversion_change_pct?: number | null;
  currency?: string;
  alerts?: AlertCounts;
}

export interface MetricPoint {
  date: string;
  value: number;
}

export interface MetricSeries {
  metric: string;
  unit?: string | null;
  granularity?: "day" | "week" | "month";
  period?: string | null;
  segment?: string | null;
  points: MetricPoint[];
}

// --- Inventory and order health ---

export interface InventoryAlert {
  listing_id: string;
  title: string;
  kind: "low_stock" | "slow_mover";
  stock: number;
  threshold?: number | null;
  days_of_cover?: number | null;
  sales_last_30d?: number | null;
  storefront_visible?: boolean | null;
}

export interface OrderIssue {
  issue_id: string;
  order_id: string;
  kind: "delayed" | "return_spike" | "buyer_message" | "damaged";
  summary: string;
  listing_id?: string | null;
  /** Buyer-authored; render as a quotation. */
  buyer_message_excerpt?: string | null;
  opened_at?: string | null;
}

// --- Staged changes (propose → preview → approve → apply) ---

export type ChangeKind =
  | "listing_update"
  | "price_update"
  | "inventory_action"
  | "promotion"
  | "campaign";

export type ChangeStatus = "staged" | "applied" | "discarded";

export interface ChangeItem {
  target: string;
  field: string;
  before?: unknown;
  after?: unknown;
}

export interface StagedChange {
  change_id: string;
  kind: ChangeKind;
  status: ChangeStatus;
  summary: string;
  items: ChangeItem[];
  created_at: string;
  created_by: string;
  created_by_kind?: "operator" | "agent";
  applied_at?: string | null;
  applied_by?: string | null;
  discarded_at?: string | null;
  discarded_by?: string | null;
  discarded_by_kind?: "operator" | "agent" | null;
  guardrail_notes?: string[];
  currency?: string | null;
  margin_impact?: number | null;
  /** Set only on single-listing price moves. */
  margin_before_pct?: number | null;
  margin_after_pct?: number | null;
}

// --- Portal data-plane responses ---

export interface RecentOrder {
  order_id: string;
  status: string;
  placed_at: string;
  total: number;
  items: number;
}

export interface HomeInsight {
  insight_id: string;
  kind: string;
  headline: string;
  detail?: string | null;
  prompt: string;
}

export interface OverviewResponse {
  snapshot: BusinessSnapshot;
  needs_attention: {
    inventory: InventoryAlert[];
    order_issues: OrderIssue[];
    pending_changes: StagedChange[];
  };
  recent_orders: RecentOrder[];
  /** Newest first. */
  recent_changes: StagedChange[];
  trends?: Record<string, MetricPoint[]>;
  insights?: HomeInsight[];
  /** The Shopify store this data came from, e.g. "acme-supply.myshopify.com"; omitted if unknown. */
  shop_domain?: string | null;
  /** Which store answered: a Shopify store, or `api/local_store.py` standing in for one. */
  store_kind?: "shopify" | "local" | null;
}

export interface ListingsResponse {
  /** Count before paging. */
  total?: number;
  listings: Listing[];
}

export interface ListingDetailResponse {
  listing: ListingDetails;
  pricing: PricingContext | null;
}

export interface AlertsResponse {
  inventory: InventoryAlert[];
  order_issues: OrderIssue[];
}

// --- Presentation payloads, as streamed after server enrichment ---

export interface MetricEntry {
  metric: string;
  value?: number | null;
  change_pct?: number | null;
  currency?: string | null;
  note?: string | null;
  series?: MetricSeries | null;
}

export interface MetricsPayload {
  title?: string | null;
  period?: string | null;
  metrics: MetricEntry[];
  suggestions?: string[];
}

export interface DigestEntry {
  kind: "low_stock" | "slow_mover" | "order_issue" | "metric" | "pending_change" | "note";
  ref_id?: string | null;
  headline: string;
  why_it_matters?: string | null;
  listing?: Listing | null;
  change?: StagedChange | null;
}

export interface DigestPayload {
  title?: string | null;
  items: DigestEntry[];
  suggestions?: string[];
}

export interface ChangePreviewPayload {
  change_id: string;
  headline?: string | null;
  note?: string | null;
  change: StagedChange;
  suggestions?: string[];
}
