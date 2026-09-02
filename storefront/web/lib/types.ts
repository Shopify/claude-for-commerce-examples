// Copyright 2026 Shopify Inc.
// SPDX-License-Identifier: Apache-2.0

/** Mirrors shopping_agent/types.py and tools/presentation.py; brand is shopify/api's. */

export interface Product {
  product_id: string;
  title: string;
  brand?: string | null;
  price: number;
  currency?: string;
  rating?: number | null;
  review_count?: number | null;
  image_url?: string | null;
  category?: string | null;
  labels?: string[];
  attributes?: Record<string, string>;
  in_stock?: boolean;
  short_description?: string | null;
}

export interface ProductDetails extends Product {
  long_description?: string | null;
  specs?: Record<string, string>;
  review_highlights?: string[];
  variants?: Product[];
}

export interface CartItem {
  product_id: string;
  title: string;
  price: number;
  quantity: number;
  image_url?: string | null;
  line_total: number;
}

export interface CartPayload {
  items: CartItem[];
  item_count: number;
  subtotal: number;
  currency: string;
  /** Shopify's hosted checkout for this cart; absent until the cart exists. */
  checkout_url?: string | null;
  /** The Shopify cart id (`gid://shopify/Cart/<token>?key=<key>`); absent until the cart exists. */
  cart_id?: string | null;
}

export interface AuthStatus {
  signed_in: boolean;
}

/** Served by /api/brand from the shop's own brand settings, with static fallbacks. */
export interface Brand {
  name: string;
  slogan?: string | null;
  /** The hero's line: the shop's slogan when set, else the host's static tagline. */
  tagline?: string | null;
  short_description?: string | null;
  /** Omitted when the shop's pair fails the host's contrast guard; CSS defaults hold. */
  colors?: { background: string; foreground: string };
  logo_url?: string | null;
  cover_image_url?: string | null;
}

// --- Presentation payloads, as streamed after server enrichment ---

export interface ProductsPayload {
  title?: string;
  layout?: "carousel" | "grid" | "list";
  items: { product: Product; reason?: string | null }[];
  suggestions?: string[];
}

export interface ComparisonPayload {
  title?: string;
  entries: {
    product_id: string;
    product: Product;
    pros?: string[];
    cons?: string[];
    best_for?: string | null;
  }[];
  dimensions?: string[];
  recommended_product_id?: string | null;
  suggestions?: string[];
}

export interface PlanPayload {
  title: string;
  intro?: string;
  steps: { label: string; detail?: string | null; products: Product[] }[];
  suggestions?: string[];
}

export interface GuidePayload {
  title: string;
  sections: { heading: string; body: string }[];
  related_products?: Product[];
  sources?: string[];
  suggestions?: string[];
}

export interface OrderStatusPayload {
  order_id: string;
  summary: string;
  next_step?: string;
  suggestions?: string[];
  order?: {
    order_id: string;
    status: string;
    placed_at: string;
    items: { product_id: string; title: string; quantity: number; price: number }[];
    total: number;
    currency?: string;
    estimated_delivery?: string;
    tracking_url?: string;
  };
}

export interface CheckoutPayload {
  note?: string;
  fulfillment_method?: "delivery" | "pickup" | "shipping";
  cart: CartPayload;
  suggestions?: string[];
}
