// Copyright 2026 Shopify Inc.
// SPDX-License-Identifier: Apache-2.0

"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ActivityButton } from "web-shared";
import { shopSignInUrl } from "@/lib/api";
import type { Brand } from "@/lib/types";

/** The button leaves the app: sign-in is a top-level navigation through the host. */
function ShopSignIn({
  sessionId,
  signedIn,
  onSignOut,
}: {
  sessionId: string | null;
  signedIn: boolean;
  onSignOut: () => void;
}) {
  if (signedIn) {
    return (
      <span className="flex items-center gap-1.5 rounded-full bg-(--accent-soft) py-1 pl-3 pr-1.5 text-[13px] font-semibold text-(--ink)">
        Shop ✓
        <button
          type="button"
          onClick={onSignOut}
          className="rounded-full px-1.5 py-0.5 text-[11px] font-medium text-(--ink-soft) hover:text-(--ink)"
        >
          Sign out
        </button>
      </span>
    );
  }
  return (
    <a
      href={sessionId ? shopSignInUrl(sessionId) : undefined}
      aria-disabled={!sessionId}
      className={`rounded-full bg-(--brand) px-3.5 py-1.5 text-[13px] font-semibold text-(--brand-contrast) transition hover:brightness-110 ${
        sessionId ? "" : "pointer-events-none opacity-50"
      }`}
    >
      Sign in with Shop
    </a>
  );
}

export default function Header({
  brand,
  sessionId,
  signedIn,
  onSignOut,
  cartCount,
  onOpenCart,
  assistantOpen,
  onToggleAssistant,
  streaming,
  newMemoryCount,
  onOpenActivity,
}: {
  brand: Brand | null;
  sessionId: string | null;
  signedIn: boolean;
  onSignOut: () => void;
  cartCount: number;
  onOpenCart: () => void;
  assistantOpen: boolean;
  onToggleAssistant: () => void;
  streaming: boolean;
  newMemoryCount: number;
  onOpenActivity: () => void;
}) {
  // Pulse the counter when the cart grows; it is the only visible cue for mid-turn agent adds.
  const previousCountRef = useRef(cartCount);
  const [pulsing, setPulsing] = useState(false);
  useEffect(() => {
    if (cartCount > previousCountRef.current) {
      setPulsing(true);
      const timer = window.setTimeout(() => setPulsing(false), 600);
      previousCountRef.current = cartCount;
      return () => window.clearTimeout(timer);
    }
    previousCountRef.current = cartCount;
  }, [cartCount]);

  return (
    <header className="flex items-center justify-between gap-3 border-b border-(--line) bg-(--card) px-5 py-3">
      <Link href="/" className="flex min-w-0 items-center gap-2.5">
        {brand?.logo_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={brand.logo_url} alt="" className="h-8 w-8 shrink-0 rounded-md object-contain" />
        ) : null}
        <span className="truncate text-lg font-extrabold tracking-tight text-(--ink)">
          {brand?.name ?? "Storefront"}
        </span>
        {brand?.slogan ? (
          <span className="hidden truncate text-[13px] text-(--ink-soft)/80 md:inline">{brand.slogan}</span>
        ) : null}
      </Link>
      <div className="flex shrink-0 items-center gap-2">
        <ShopSignIn sessionId={sessionId} signedIn={signedIn} onSignOut={onSignOut} />
        <ActivityButton streaming={streaming} newMemoryCount={newMemoryCount} onClick={onOpenActivity} />
        <button
          type="button"
          onClick={onOpenCart}
          aria-label={`Open cart, ${cartCount} item${cartCount === 1 ? "" : "s"}`}
          className="flex items-center gap-1.5 rounded-full border border-(--line) bg-(--card) px-3 py-1 text-[13px] font-semibold text-(--ink) transition hover:border-(--brand)"
        >
          Cart
          <span
            data-cart-target
            className={`rounded-full bg-(--accent-soft) px-1.5 py-0.5 text-[11px] font-bold text-(--ink) ${
              pulsing ? "ac-pop" : ""
            }`}
          >
            {cartCount}
          </span>
        </button>
        <button
          type="button"
          onClick={onToggleAssistant}
          aria-pressed={assistantOpen}
          className={`rounded-full border px-3 py-1 text-[13px] font-semibold transition ${
            assistantOpen
              ? "border-(--brand) bg-(--accent-soft) text-(--ink)"
              : "border-(--line) bg-(--card) text-(--ink) hover:border-(--brand)"
          }`}
        >
          Assistant
        </button>
      </div>
    </header>
  );
}
