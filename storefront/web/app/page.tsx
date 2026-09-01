// Copyright 2026 Shopify Inc.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { fetchProducts } from "@/lib/api";
import type { Product } from "@/lib/types";
import ProductTile from "@/components/ProductTile";
import { useStore } from "@/components/StoreShell";

export default function GridPage() {
  const { brand, chat, addProduct, askAssistant } = useStore();
  const router = useRouter();
  const [products, setProducts] = useState<Product[] | null>(null);

  // The host's catalog fills lazily from what the agent surfaces, so the grid
  // re-reads it after every settled turn.
  useEffect(() => {
    let cancelled = false;
    void fetchProducts().then((value) => {
      if (!cancelled && value) setProducts(value);
    });
    return () => {
      cancelled = true;
    };
  }, [chat.turnCount]);

  const open = (product: Product) => router.push(`/products/${encodeURIComponent(product.product_id)}`);

  return (
    <div className="mx-auto max-w-6xl px-5 py-6">
      {brand?.cover_image_url || brand?.tagline ? (
        <div className="relative mb-6 overflow-hidden rounded-2xl bg-(--brand)">
          {brand.cover_image_url ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={brand.cover_image_url}
              alt=""
              className="absolute inset-0 h-full w-full object-cover opacity-40"
            />
          ) : null}
          <div className="relative px-6 py-10">
            <h1 className="text-2xl font-extrabold text-(--brand-contrast)">{brand.name}</h1>
            {brand.tagline ? <p className="mt-1 text-[15px] text-(--brand-contrast)/90">{brand.tagline}</p> : null}
          </div>
        </div>
      ) : null}

      {products === null ? (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-4">
          {Array.from({ length: 8 }, (_, slot) => (
            <div key={slot} className="ac-skeleton h-64 rounded-xl" />
          ))}
        </div>
      ) : products.length === 0 ? (
        <div className="mx-auto mt-16 max-w-md text-center">
          <h2 className="text-lg font-semibold text-(--ink)">Nothing here yet</h2>
          <p className="mt-2 text-[15px] text-(--ink-soft)">
            The grid fills with what the assistant finds in the live shop. Ask it for something to get started.
          </p>
          <button
            type="button"
            onClick={() => askAssistant("What's in the catalog?")}
            className="btn-primary mt-4"
          >
            Ask the assistant
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-4">
          {products.map((product) => (
            <ProductTile
              key={product.product_id}
              product={product}
              onAdd={(item) => addProduct(item.product_id)}
              onOpen={open}
            />
          ))}
        </div>
      )}
    </div>
  );
}
