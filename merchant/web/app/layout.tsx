// Copyright 2026 Shopify Inc.
// SPDX-License-Identifier: Apache-2.0

import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ACME Supply Co. Merchant",
  description: "ACME Supply Co. — merchant portal for a merchant agent backed by the Shopify Admin API.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        {children}
      </body>
    </html>
  );
}
