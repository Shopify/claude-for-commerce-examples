// Copyright 2026 Shopify Inc.
// SPDX-License-Identifier: Apache-2.0

/** One entry per shopping presentation tool. Every card renders its streamed payload
 * as-is — the chat surface never fetches catalog data on its own; a product click
 * navigates to the product page instead. */

import { type GenerativeBlockProps, UnknownBlock } from "web-shared";
import type {
  CheckoutPayload,
  ComparisonPayload,
  GuidePayload,
  OrderStatusPayload,
  PlanPayload,
  Product,
  ProductsPayload,
} from "@/lib/types";
import CheckoutCard from "./CheckoutCard";
import ComparisonCard from "./ComparisonCard";
import GuideCard from "./GuideCard";
import OrderStatusCard from "./OrderStatusCard";
import PlanCard from "./PlanCard";
import ProductStrip from "./ProductStrip";

export default function GenerativeBlock({
  block,
  status,
  onAdd,
  checkoutUrl,
}: GenerativeBlockProps & {
  onAdd?: (product: Product) => boolean | void | Promise<boolean | void>;
  checkoutUrl?: string | null;
}) {
  const partial = status !== "final";
  switch (block.component) {
    case "products":
      return <ProductStrip payload={block.payload as ProductsPayload} onAdd={onAdd} partial={partial} />;
    case "comparison":
      return <ComparisonCard payload={block.payload as ComparisonPayload} onAdd={onAdd} partial={partial} />;
    case "plan":
      return <PlanCard payload={block.payload as PlanPayload} onAdd={onAdd} partial={partial} />;
    case "guide":
      return <GuideCard payload={block.payload as GuidePayload} />;
    case "order_status":
      return <OrderStatusCard payload={block.payload as OrderStatusPayload} />;
    case "checkout":
      return <CheckoutCard payload={block.payload as CheckoutPayload} checkoutUrl={checkoutUrl} />;
    default:
      return partial ? null : <UnknownBlock component={block.component} />;
  }
}
