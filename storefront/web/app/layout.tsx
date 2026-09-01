// Copyright 2026 Shopify Inc.
// SPDX-License-Identifier: Apache-2.0

import type { Metadata } from "next";
import StoreShell from "@/components/StoreShell";
import "./globals.css";

export const metadata: Metadata = {
  title: "Storefront",
  description: "A live Shopify shop browsed through the shopping agent.",
};

// The shell lives in the layout so the session, cart, and conversation survive
// navigation between the grid and product pages.
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <StoreShell>{children}</StoreShell>
      </body>
    </html>
  );
}
