// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/** Px from the bottom within which the reader still counts as following. */
const STICK_PX = 96;

/** Scrolling up holds the position; a send or `jumpToLatest` re-engages following. */
export function useStickToBottom(
  items: readonly unknown[],
  busy: boolean,
  options: { onlyWhileBusy?: boolean } = {},
) {
  const { onlyWhileBusy = false } = options;
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  const programmaticUntil = useRef(0);
  const previousCount = useRef(0);
  const [stuck, setStuck] = useState(true);

  const scrollToBottom = useCallback((behavior: ScrollBehavior) => {
    const node = scrollRef.current;
    if (!node) return;
    // Ignore the scroll events this raises; a smooth scroll emits them well past the next frame.
    programmaticUntil.current = performance.now() + (behavior === "smooth" ? 500 : 50);
    node.scrollTo({ top: node.scrollHeight, behavior });
  }, []);

  const jumpToLatest = useCallback(() => {
    stickRef.current = true;
    setStuck(true);
    scrollToBottom("auto");
  }, [scrollToBottom]);

  // A send appends items and gets one smooth scroll; per-delta updates scroll instantly so
  // animations do not pile up.
  useEffect(() => {
    const appended = items.length !== previousCount.current;
    previousCount.current = items.length;
    if (appended) {
      stickRef.current = true;
      setStuck(true);
    }
    if (stickRef.current && (busy || !onlyWhileBusy)) scrollToBottom(appended ? "smooth" : "auto");
  }, [items, busy, onlyWhileBusy, scrollToBottom]);

  const onScroll = useCallback(() => {
    if (performance.now() < programmaticUntil.current) return;
    const node = scrollRef.current;
    if (!node) return;
    const stick = node.scrollHeight - node.scrollTop - node.clientHeight < STICK_PX;
    stickRef.current = stick;
    setStuck(stick);
  }, []);

  return { scrollRef, onScroll, showLatest: busy && !stuck, jumpToLatest };
}
