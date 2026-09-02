// Copyright 2026 Shopify Inc.
// SPDX-License-Identifier: Apache-2.0

import { AgentApi } from "web-shared";

import type { AuthStatus, Brand, CartPayload, Product, ProductDetails } from "./types";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8004";

export const api = new AgentApi(`${API_URL}/api`);

export const UNREACHABLE =
  "Couldn't reach the storefront host on port 8004. Start it with `uvicorn shopify.api.main:app --app-dir examples --port 8004` and try again.";

export function fetchCart(): Promise<CartPayload | null> {
  return api.get<CartPayload>("/cart");
}

export async function fetchProducts(): Promise<Product[] | null> {
  const data = await api.get<{ products: Product[] }>("/products", { limit: "24" });
  return data?.products ?? null;
}

export function fetchProduct(productId: string): Promise<ProductDetails | null> {
  return api.get<ProductDetails>(`/products/${encodeURIComponent(productId)}`);
}

export async function addToCart(productId: string, quantity = 1): Promise<CartPayload | null> {
  const data = await api.post<{
    cart: CartPayload;
    checkout_url?: string | null;
    cart_id?: string | null;
  }>("/cart/add", { product_id: productId, quantity });
  if (!data) return null;
  return { ...data.cart, checkout_url: data.checkout_url, cart_id: data.cart_id };
}

/** Binds the session to a cart that exists already — the storefront's `cart` cookie value
 * works as-is. Null when the shop doesn't know the id. */
export function attachCart(cartId: string): Promise<CartPayload | null> {
  return api.post<CartPayload>("/cart/attach", { cart_id: cartId });
}

export function fetchBrand(): Promise<Brand | null> {
  return api.get<Brand>("/brand");
}

export function fetchAuthStatus(): Promise<AuthStatus | null> {
  return api.get<AuthStatus>("/auth/status");
}

export function signOut(): Promise<AuthStatus | null> {
  return api.post<AuthStatus>("/auth/signout", {});
}

/** Sign in with Shop is a top-level navigation: the browser cannot attach the
 * X-Session-Id header to it, so the session rides along as a query parameter. */
export function shopSignInUrl(sessionId: string): string {
  return `${API_URL}/api/auth/shop/start?session_id=${encodeURIComponent(sessionId)}`;
}
