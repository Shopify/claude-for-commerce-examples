// Copyright 2026 Shopify Inc.
// SPDX-License-Identifier: Apache-2.0

"use client";

import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import { type AgentEvent, type AgentTurn, Inspector, useAgentTurn } from "web-shared";
import { addToCart, api, fetchBrand, fetchCart, UNREACHABLE } from "@/lib/api";
import { useStoreSession } from "@/lib/session";
import type { Brand, CartPayload } from "@/lib/types";
import Assistant from "./Assistant";
import CartDrawer from "./CartDrawer";
import Header from "./Header";

interface StoreContextValue {
  sessionId: string | null;
  signedIn: boolean;
  signOut: () => Promise<void>;
  brand: Brand | null;
  cart: CartPayload | null;
  chat: AgentTurn;
  /** Adds via the host's direct-add route; false means the server refused. */
  addProduct: (productId: string) => Promise<boolean>;
  openCart: () => void;
  /** Opens the rail and, when given a message, sends it. */
  askAssistant: (message?: string) => void;
}

const StoreContext = createContext<StoreContextValue | null>(null);

export function useStore(): StoreContextValue {
  const value = useContext(StoreContext);
  if (!value) throw new Error("useStore must be used inside StoreShell");
  return value;
}

export default function StoreShell({ children }: { children: ReactNode }) {
  const { sessionId, signedIn, refreshAuth, signOut } = useStoreSession();
  const [brand, setBrand] = useState<Brand | null>(null);
  const [cart, setCart] = useState<CartPayload | null>(null);
  const [cartOpen, setCartOpen] = useState(false);
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [activityOpen, setActivityOpen] = useState(false);
  const [signInFlag, setSignInFlag] = useState<"ok" | "error" | null>(null);

  // The brand's primary color pair drives the theme; the CSS defaults hold until it
  // lands — and for good, when the host's contrast guard dropped the pair.
  useEffect(() => {
    void fetchBrand().then((value) => {
      if (!value) return;
      setBrand(value);
      if (!value.colors) return;
      const root = document.documentElement;
      root.style.setProperty("--brand", value.colors.background);
      root.style.setProperty("--brand-contrast", value.colors.foreground);
    });
  }, []);

  const refetchCart = useCallback(async () => {
    const next = await fetchCart();
    if (next) setCart(next);
  }, []);

  // The cart_update event omits checkout_url, so the event's cart lands immediately and a
  // refetch of /api/cart fills the extras in.
  const onEvent = useCallback(
    (event: AgentEvent) => {
      if (event.type !== "cart_update") return;
      setCart((current) => ({ ...(event.data.cart as CartPayload), checkout_url: current?.checkout_url }));
      void refetchCart();
    },
    [refetchCart],
  );

  const chat = useAgentTurn(api, { sessionId, unreachable: UNREACHABLE, onEvent });

  useEffect(() => {
    if (sessionId) void refetchCart();
  }, [sessionId, refetchCart]);

  // The rail is part of the default layout on wide screens; narrow screens open it on demand.
  useEffect(() => {
    if (window.matchMedia("(min-width: 1024px)").matches) setAssistantOpen(true);
  }, []);

  // The Shop sign-in callback returns here with ?shop_signin=ok|error on a fresh page load.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const flag = params.get("shop_signin");
    if (flag !== "ok" && flag !== "error") return;
    setSignInFlag(flag);
    params.delete("shop_signin");
    const query = params.toString();
    window.history.replaceState(null, "", `${window.location.pathname}${query ? `?${query}` : ""}`);
    const timer = window.setTimeout(() => setSignInFlag(null), 6000);
    return () => window.clearTimeout(timer);
  }, []);
  useEffect(() => {
    if (signInFlag === "ok" && sessionId) void refreshAuth();
  }, [signInFlag, sessionId, refreshAuth]);

  const addProduct = useCallback(async (productId: string) => {
    const next = await addToCart(productId);
    if (!next) return false;
    setCart(next);
    return true;
  }, []);

  const askAssistant = useCallback(
    (message?: string) => {
      setAssistantOpen(true);
      if (message) void chat.send(message);
    },
    [chat],
  );

  const value: StoreContextValue = {
    sessionId,
    signedIn,
    signOut,
    brand,
    cart,
    chat,
    addProduct,
    openCart: () => setCartOpen(true),
    askAssistant,
  };

  return (
    <StoreContext.Provider value={value}>
      <div className="flex h-dvh flex-col">
        <Header
          brand={brand}
          sessionId={sessionId}
          signedIn={signedIn}
          onSignOut={() => void signOut()}
          cartCount={cart?.item_count ?? 0}
          onOpenCart={() => setCartOpen(true)}
          assistantOpen={assistantOpen}
          onToggleAssistant={() => setAssistantOpen((open) => !open)}
          streaming={chat.streaming}
          newMemoryCount={chat.newMemoryKeys.size}
          onOpenActivity={() => setActivityOpen(true)}
        />
        {signInFlag ? (
          <div
            role="status"
            className={`px-5 py-2 text-center text-[13px] font-medium ${
              signInFlag === "ok" ? "bg-emerald-50 text-emerald-800" : "bg-(--danger-soft) text-(--danger)"
            }`}
          >
            {signInFlag === "ok"
              ? "Signed in with Shop. Results now reflect your profile."
              : "Shop sign-in didn't complete. You can keep browsing as a guest and try again."}
            <button
              type="button"
              onClick={() => setSignInFlag(null)}
              aria-label="Dismiss"
              className="ml-3 font-bold"
            >
              ×
            </button>
          </div>
        ) : null}
        <main className="flex min-h-0 flex-1">
          <div className="panel-scroll min-w-0 flex-1 overflow-y-auto">{children}</div>
          <Assistant
            open={assistantOpen}
            chat={chat}
            onClose={() => setAssistantOpen(false)}
            onAdd={addProduct}
            checkoutUrl={cart?.checkout_url}
          />
        </main>
        <CartDrawer
          open={cartOpen}
          cart={cart}
          onClose={() => setCartOpen(false)}
          onAction={(message) => {
            setCartOpen(false);
            askAssistant(message);
          }}
        />
        {activityOpen ? (
          <Inspector
            turnCount={chat.turnCount}
            streaming={chat.streaming}
            trace={chat.trace}
            memory={chat.memory}
            newMemoryKeys={chat.newMemoryKeys}
            onClose={() => setActivityOpen(false)}
          />
        ) : null}
      </div>
    </StoreContext.Provider>
  );
}
