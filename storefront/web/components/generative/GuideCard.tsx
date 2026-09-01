// Copyright 2026 Shopify Inc.
// SPDX-License-Identifier: Apache-2.0

"use client";

import type { GuidePayload } from "@/lib/types";

export default function GuideCard({ payload }: { payload: GuidePayload }) {
  return (
    <section className="rounded-2xl border border-(--line) bg-(--card) p-4 shadow-sm">
      <h3 className="text-[15px] font-semibold text-(--ink)">{payload.title}</h3>
      <div className="mt-2 space-y-3">
        {(payload.sections ?? []).map((section) => (
          <div key={section.heading} className="ac-reveal">
            <div className="text-[13px] font-semibold text-(--ink)">{section.heading}</div>
            <p className="mt-0.5 text-[13px] leading-relaxed text-(--ink-soft)">{section.body}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
