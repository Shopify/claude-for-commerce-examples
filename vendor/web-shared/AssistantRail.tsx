// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import { type CSSProperties, type PointerEvent, type ReactNode, useCallback, useEffect, useRef, useState } from "react";

const DEFAULT_WIDTH = 420;
const MIN_WIDTH = 360;

const clampWidth = (width: number) => Math.max(MIN_WIDTH, Math.min(width, Math.round(Math.min(window.innerWidth * 0.7, 960))));

export interface RailControls {
  fullscreen: boolean;
  onToggleFullscreen: () => void;
  onClose: () => void;
}

/** Closing resets full screen: below `lg` there is no Escape or toggle to leave it with. */
export function AssistantRail({
  open,
  storageKey,
  onClose,
  children,
}: {
  open: boolean;
  storageKey: string;
  onClose: () => void;
  children: (rail: RailControls) => ReactNode;
}) {
  const [width, setWidth] = useState(DEFAULT_WIDTH);
  const [fullscreen, setFullscreen] = useState(false);
  const widthRef = useRef(DEFAULT_WIDTH);
  const railRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const saved = Number(window.localStorage.getItem(storageKey));
    if (saved > 0) {
      widthRef.current = clampWidth(saved);
      setWidth(widthRef.current);
    }
    const onResize = () => {
      widthRef.current = clampWidth(widthRef.current);
      setWidth(widthRef.current);
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [storageKey]);

  useEffect(() => {
    if (!fullscreen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setFullscreen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullscreen]);

  const beginResize = useCallback(
    (event: PointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      // The drag writes the CSS variable directly; state (and the dashboard re-render) updates
      // once, on release.
      const onMove = (move: globalThis.PointerEvent) => {
        widthRef.current = clampWidth(window.innerWidth - move.clientX);
        railRef.current?.style.setProperty("--rail-w", `${widthRef.current}px`);
      };
      const finish = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", finish);
        window.removeEventListener("pointercancel", finish);
        setWidth(widthRef.current);
        window.localStorage.setItem(storageKey, String(widthRef.current));
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", finish);
      window.addEventListener("pointercancel", finish);
    },
    [storageKey],
  );

  if (!open) return null;
  const controls: RailControls = {
    fullscreen,
    onToggleFullscreen: () => setFullscreen((value) => !value),
    onClose: () => {
      setFullscreen(false);
      onClose();
    },
  };
  return (
    <div
      ref={railRef}
      className={
        fullscreen
          ? "fixed inset-0 z-40"
          : "fixed inset-y-0 right-0 z-30 w-[min(94vw,420px)] shadow-2xl lg:relative lg:z-auto lg:w-(--rail-w) lg:shrink-0 lg:shadow-none"
      }
      style={fullscreen ? undefined : ({ "--rail-w": `${width}px` } as CSSProperties)}
    >
      {fullscreen ? null : (
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize assistant panel"
          onPointerDown={beginResize}
          className="absolute inset-y-0 left-0 z-10 hidden w-1.5 cursor-col-resize transition-colors hover:bg-(--line) lg:block"
        />
      )}
      {children(controls)}
    </div>
  );
}
