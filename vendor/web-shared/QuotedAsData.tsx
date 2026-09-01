// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

/** Label under quoted third-party text. */
export function QuotedAsData({ subject, className = "" }: { subject: string; className?: string }) {
  return (
    <div className={`flex items-center gap-1.5 text-[11px] leading-tight text-(--ink-soft) ${className}`}>
      <svg
        viewBox="0 0 16 16"
        className="h-3 w-3 shrink-0"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
        aria-hidden
      >
        <path d="M8 1.5 13.5 3.5v4c0 3.2-2.3 5.8-5.5 7-3.2-1.2-5.5-3.8-5.5-7v-4L8 1.5Z" />
        <path d="m5.8 7.8 1.6 1.6 2.8-3" strokeLinecap="round" />
      </svg>
      <span>{subject}, shown as written.</span>
    </div>
  );
}
