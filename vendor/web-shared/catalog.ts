// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useEffect, useState } from "react";

type Loader<P> = () => Promise<P[] | null>;

const indexes = new WeakMap<object, Promise<Record<string, unknown>>>();

/** Loaded once per page, keyed on `load`; empty until then and when the API is down. */
export function useCatalogIndex<P extends { product_id: string }>(
  load: Loader<P>,
): Record<string, P> {
  const [index, setIndex] = useState<Record<string, P>>({});
  useEffect(() => {
    let promise = indexes.get(load) as Promise<Record<string, P>> | undefined;
    if (!promise) {
      promise = load().then((products) =>
        Object.fromEntries((products ?? []).map((product) => [product.product_id, product])),
      );
      indexes.set(load, promise);
    }
    let mounted = true;
    void promise.then((value) => {
      if (mounted) setIndex(value);
    });
    return () => {
      mounted = false;
    };
  }, [load]);
  return index;
}
