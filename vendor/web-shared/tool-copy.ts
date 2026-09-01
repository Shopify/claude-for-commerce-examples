// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

const TOOL_COPY: Record<string, string> = {
  search_products: "Searching the catalog…",
  get_product_details: "Reading the details…",
  search_policies: "Checking the policies…",
  get_orders: "Looking up your orders…",
  get_order_status: "Looking up your orders…",
  get_fulfillment_options: "Checking delivery and pickup options…",
  get_cart: "Checking the cart…",
  add_to_cart: "Updating the cart…",
  update_cart_item: "Updating the cart…",
  remove_from_cart: "Updating the cart…",
  checkout: "Preparing your order summary…",
  get_preferences: "Reading your profile…",
  recall_memories: "Recalling what you've told me…",
  save_memory: "Saving that for next time…",
  load_skill: "Loading…",
  web_search: "Searching the web…",
  get_business_snapshot: "Reading the business snapshot…",
  query_metrics: "Querying metrics…",
  search_listings: "Searching the listings…",
  get_listing: "Reading the listing…",
  get_inventory_alerts: "Checking inventory alerts…",
  get_order_issues: "Checking order issues…",
  get_pricing_context: "Reading the pricing context…",
  get_campaign_performance: "Reading campaign performance…",
  get_pending_changes: "Checking pending changes…",
  run_analysis: "Running the analysis…",
  apply_change: "Applying the change…",
  discard_change: "Discarding the change…",
};

const QUERY_TOOLS = new Set([
  "search_products",
  "search_policies",
  "search_listings",
  "web_search",
]);

export function describeToolCall(tool: string, input: Record<string, unknown> = {}): string {
  const copy = TOOL_COPY[tool];
  const query = typeof input.query === "string" ? input.query.trim() : "";
  if (copy && query && QUERY_TOOLS.has(tool)) {
    return `${copy.replace(/…$/, "")} · “${query}”`;
  }
  if (copy) return copy;
  if (tool.startsWith("present_")) return "Composing the answer…";
  if (tool.startsWith("stage_")) return "Staging a change for review…";
  return `${tool.replaceAll("_", " ")}…`;
}
