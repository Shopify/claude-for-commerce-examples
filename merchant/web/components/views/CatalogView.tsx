// Copyright 2026 Shopify Inc.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useEffect, useState } from "react";
import {
  formatDate,
  formatMoney,
  formatNumber,
  formatRate,
  QuotedAsData,
  titleCase,
} from "web-shared";
import { fetchListingDetail, fetchListings } from "@/lib/api";
import type { Listing, ListingDetailResponse, ListingStatus } from "@/lib/types";
import { formatCategoryLabel } from "@/lib/format";

const STATUS_STYLES: Record<ListingStatus, string> = {
  active: "bg-emerald-100 text-emerald-800",
  paused: "bg-(--well) text-(--ink-soft)",
  draft: "bg-sky-50 text-sky-800",
  out_of_stock: "bg-red-50 text-(--danger)",
};

function StatusChip({ status }: { status: ListingStatus }) {
  return (
    <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${STATUS_STYLES[status]}`}>
      {status.replaceAll("_", " ")}
    </span>
  );
}

function QualityChip({ quality }: { quality: Listing["content_quality"] }) {
  if (quality === "good") {
    return <span className="text-[11px] text-(--ink-soft)">Good</span>;
  }
  if (quality !== "needs_work" && quality !== "poor") return null;
  return (
    <span
      className={`rounded px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${
        quality === "poor" ? "bg-red-50 text-(--danger)" : "bg-(--accent-soft) text-(--ink)"
      }`}
    >
      {quality === "poor" ? "Poor content" : "Needs work"}
    </span>
  );
}

function DetailRow({ label, value }: { label: string; value: React.ReactNode }) {
  if (value == null || value === "") return null;
  return (
    <div className="flex items-baseline justify-between gap-3 py-1">
      <span className="text-[11px] font-semibold uppercase tracking-wide text-(--ink-soft)">
        {label}
      </span>
      <span className="text-right text-[13px] tabular-nums text-(--ink)">{value}</span>
    </div>
  );
}

function ListingDrawer({ listingId, onClose }: { listingId: string; onClose: () => void }) {
  const [detail, setDetail] = useState<ListingDetailResponse | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setFailed(false);
    void fetchListingDetail(listingId).then((response) => {
      if (cancelled) return;
      if (response) setDetail(response);
      else setFailed(true);
    });
    return () => {
      cancelled = true;
    };
  }, [listingId]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const listing = detail?.listing;
  const pricing = detail?.pricing;

  return (
    <>
      <div onClick={onClose} aria-hidden className="fixed inset-0 z-40 bg-(--ink)/30" />
      <aside className="ac-slide-in-right fixed inset-y-0 right-0 z-50 flex w-[min(94vw,440px)] flex-col bg-white shadow-2xl">
        <div className="flex items-start justify-between gap-3 border-b border-(--line) px-4 py-3">
          <div className="min-w-0">
            <h2 className="truncate text-sm font-bold text-(--ink)">
              {listing?.title ?? "Listing"}
            </h2>
            <p className="font-mono text-[11px] text-(--ink-soft)">{listingId}</p>
          </div>
          <button
            onClick={onClose}
            aria-label="Close listing detail"
            className="rounded-md px-2 py-0.5 text-lg leading-none text-(--ink-soft) hover:text-(--ink)"
          >
            ×
          </button>
        </div>

        <div className="panel-scroll flex-1 space-y-4 overflow-y-auto p-4">
          {failed ? (
            <p className="text-[13px] text-(--ink-soft)">Couldn&apos;t load this listing.</p>
          ) : !listing ? (
            <div className="space-y-3">
              <div className="ac-skeleton h-24 rounded-xl" />
              <div className="ac-skeleton h-40 rounded-xl" />
            </div>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <StatusChip status={listing.status} />
                <QualityChip quality={listing.content_quality} />
                {listing.category ? (
                  <span className="rounded-full bg-(--well) px-2 py-0.5 text-[11px] font-medium text-(--ink)">
                    {formatCategoryLabel(listing.category)}
                  </span>
                ) : null}
              </div>

              {listing.short_description ? (
                <p className="text-[13px] leading-relaxed text-(--ink)">
                  {listing.short_description}
                </p>
              ) : null}

              <section className="rounded-xl border border-(--line) p-3">
                <h3 className="text-[13px] font-bold uppercase tracking-wide text-(--ink)">
                  Listing
                </h3>
                <div className="mt-1 divide-y divide-(--line)">
                  <DetailRow label="Price" value={formatMoney(listing.price)} />
                  <DetailRow label="Stock" value={formatNumber(listing.stock)} />
                  <DetailRow
                    label="Sales, last 30 days"
                    value={listing.sales_last_30d != null ? formatNumber(listing.sales_last_30d) : null}
                  />
                  <DetailRow
                    label="Return rate"
                    value={listing.return_rate_pct != null ? formatRate(listing.return_rate_pct) : null}
                  />
                </div>
              </section>

              {pricing ? (
                <section className="rounded-xl border border-(--line) p-3">
                  <h3 className="text-[13px] font-bold uppercase tracking-wide text-(--ink)">
                    Pricing context
                  </h3>
                  <div className="mt-1 divide-y divide-(--line)">
                    <DetailRow label="Current price" value={formatMoney(pricing.current_price)} />
                    <DetailRow
                      label="Unit cost"
                      value={pricing.unit_cost != null ? formatMoney(pricing.unit_cost) : null}
                    />
                    <DetailRow
                      label="Margin"
                      value={pricing.margin_pct != null ? formatRate(pricing.margin_pct) : null}
                    />
                    <DetailRow
                      label="Price band"
                      value={
                        pricing.min_price != null && pricing.max_price != null
                          ? `${formatMoney(pricing.min_price)} – ${formatMoney(pricing.max_price)}`
                          : null
                      }
                    />
                    <DetailRow
                      label="Demand"
                      value={pricing.demand_signal ? titleCase(pricing.demand_signal) : null}
                    />
                    <DetailRow
                      label="Last price change"
                      value={pricing.last_changed ? formatDate(pricing.last_changed) : null}
                    />
                  </div>
                </section>
              ) : null}

              {listing.missing_attributes?.length ? (
                <section className="rounded-xl border border-(--line) bg-(--accent-soft)/50 p-3">
                  <h3 className="text-[13px] font-bold uppercase tracking-wide text-(--ink)">
                    Missing attributes
                  </h3>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {listing.missing_attributes.map((attribute) => (
                      <span
                        key={attribute}
                        className="rounded-full border border-(--line) bg-white px-2 py-0.5 text-[11px] text-(--ink)"
                      >
                        {attribute}
                      </span>
                    ))}
                  </div>
                </section>
              ) : null}

              {listing.review_snippets?.length ? (
                <section className="rounded-xl border border-(--line) p-3">
                  <h3 className="text-[13px] font-bold uppercase tracking-wide text-(--ink)">
                    Buyer reviews
                  </h3>
                  <QuotedAsData subject="Customer-authored excerpts" className="mt-0.5" />
                  <div className="mt-2 space-y-2">
                    {listing.review_snippets.map((snippet, index) => (
                      <blockquote
                        key={index}
                        className="border-l-2 border-(--line) pl-2.5 text-[13px] italic leading-relaxed text-(--ink-soft)"
                      >
                        &ldquo;{snippet}&rdquo;
                      </blockquote>
                    ))}
                  </div>
                </section>
              ) : null}

              {listing.long_description ? (
                <section className="rounded-xl border border-(--line) p-3">
                  <h3 className="text-[13px] font-bold uppercase tracking-wide text-(--ink)">
                    Description
                  </h3>
                  <p className="mt-1.5 whitespace-pre-line text-[13px] leading-relaxed text-(--ink)">
                    {listing.long_description}
                  </p>
                </section>
              ) : null}
            </>
          )}
        </div>
      </aside>
    </>
  );
}

export default function CatalogView({ refreshKey }: { refreshKey: number }) {
  const [listings, setListings] = useState<Listing[] | null>(null);
  const [total, setTotal] = useState<number | null>(null);
  const [failed, setFailed] = useState(false);
  const [openListing, setOpenListing] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void fetchListings().then((response) => {
      if (cancelled) return;
      if (response) {
        setListings(response.listings);
        setTotal(response.total ?? response.listings.length);
        setFailed(false);
      } else {
        setFailed(true);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  return (
    <div className="ac-reveal flex flex-col gap-4">
      <div className="flex items-baseline justify-between">
        <h1 className="text-lg font-bold text-(--ink)">Catalog</h1>
        {listings ? (
          <div className="text-[13px] tabular-nums text-(--ink-soft)">
            {total != null && total > listings.length
              ? `${formatNumber(listings.length)} of ${formatNumber(total)} listings`
              : `${formatNumber(total ?? listings.length)} listings`}
          </div>
        ) : null}
      </div>

      {failed && !listings ? (
        <div className="rounded-xl border border-(--line) bg-(--well)/50 p-6 text-sm text-(--ink-soft)">
          The merchant API isn&apos;t reachable, so listings can&apos;t load.
        </div>
      ) : !listings ? (
        <div className="ac-skeleton h-64 rounded-xl" />
      ) : (
        <div className="panel-scroll overflow-x-auto rounded-xl border border-(--line) bg-white shadow-sm">
          <table className="w-full border-collapse text-[13px]">
            <thead>
              <tr className="bg-(--well)/70 text-left text-[11px] font-bold uppercase tracking-wide text-(--ink-soft)">
                <th className="px-3 py-2">Listing</th>
                <th className="px-3 py-2">Category</th>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2 text-right">Stock</th>
                <th className="px-3 py-2 text-right">Price</th>
                <th className="px-3 py-2">Content</th>
              </tr>
            </thead>
            <tbody>
              {listings.map((listing) => (
                <tr
                  key={listing.listing_id}
                  onClick={() => setOpenListing(listing.listing_id)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      setOpenListing(listing.listing_id);
                    }
                  }}
                  tabIndex={0}
                  aria-label={`Open ${listing.title}`}
                  className="cursor-pointer border-t border-(--line) transition hover:bg-(--well)/40 focus-visible:bg-(--well)/40"
                >
                  <td className="px-3 py-2">
                    <div className="font-medium leading-snug text-(--ink)">{listing.title}</div>
                    <div className="font-mono text-[11px] text-(--ink-soft)">
                      {listing.listing_id}
                    </div>
                  </td>
                  <td className="px-3 py-2 text-(--ink-soft)">
                    {listing.category ? formatCategoryLabel(listing.category) : "—"}
                  </td>
                  <td className="px-3 py-2">
                    <StatusChip status={listing.status} />
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-(--ink)">
                    {formatNumber(listing.stock)}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-(--ink)">
                    {formatMoney(listing.price)}
                  </td>
                  <td className="px-3 py-2">
                    <QualityChip quality={listing.content_quality} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {openListing ? (
        <ListingDrawer listingId={openListing} onClose={() => setOpenListing(null)} />
      ) : null}
    </div>
  );
}
