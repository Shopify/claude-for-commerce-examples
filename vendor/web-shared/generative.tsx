// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

import type { UIBlock, UISlotStatus } from "./protocol";

/** Base props of each app's `components/generative/index.tsx` registry; apps add callbacks. */
export interface GenerativeBlockProps {
  block: UIBlock;
  status: UISlotStatus;
  onSuggestion?: (text: string) => void;
}

export function UnknownBlock({ component }: { component: string }) {
  return (
    <p className="rounded-(--radius) border border-(--line) bg-(--card) px-4 py-3 text-[13px] text-(--ink-soft)">
      This page has no view for “{component}” yet.
    </p>
  );
}
