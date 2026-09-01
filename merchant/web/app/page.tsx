// Copyright 2026 Shopify Inc.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useCallback, useEffect, useState } from "react";
import { AssistantRail, Inspector, type Prefill, useMerchantChat, useSession } from "web-shared";
import AssistantPanel from "@/components/AssistantPanel";
import Header, { MobileViewTabs, type PortalView } from "@/components/Header";
import AlertsView from "@/components/views/AlertsView";
import CatalogView from "@/components/views/CatalogView";
import OverviewView from "@/components/views/OverviewView";
import { api, UNREACHABLE } from "@/lib/api";
import type { StagedChange } from "@/lib/types";


export default function PortalPage() {
  const session = useSession(api);
  const [view, setView] = useState<PortalView>("overview");
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [activityOpen, setActivityOpen] = useState(false);
  const [prefill, setPrefill] = useState<Prefill | null>(null);
  // Bumped whenever a staged change moves, so every widget re-reads the store the agent wrote.
  const [refreshKey, setRefreshKey] = useState(0);
  const refreshPortal = useCallback(() => setRefreshKey((value) => value + 1), []);

  const chat = useMerchantChat<StagedChange>(api, {
    ...session,
    unreachable: UNREACHABLE,
    onPortalRefresh: refreshPortal,
  });

  // The rail is part of the default layout on wide screens; narrow screens open it on demand.
  useEffect(() => {
    setAssistantOpen(window.innerWidth >= 1024);
  }, []);

  const askAssistant = useCallback((text: string) => {
    setAssistantOpen(true);
    setPrefill({ text, nonce: Date.now() });
  }, []);

  return (
    <div className="flex h-dvh flex-col">
      <Header
        view={view}
        onViewChange={setView}
        assistantOpen={assistantOpen}
        onToggleAssistant={() => setAssistantOpen((open) => !open)}
      />
      <MobileViewTabs view={view} onViewChange={setView} />
      <main className="flex min-h-0 flex-1">
        <div className="panel-scroll min-w-0 flex-1 overflow-y-auto bg-(--well)/30">
          <div className="mx-auto flex max-w-5xl flex-col gap-6 px-4 py-5 sm:px-6">
            {session.sessionId ? (
              <>
                {view === "overview" ? (
                  <OverviewView refreshKey={refreshKey} onAskAssistant={askAssistant} />
                ) : null}
                {view === "catalog" ? <CatalogView refreshKey={refreshKey} /> : null}
                {view === "orders" ? <AlertsView refreshKey={refreshKey} /> : null}
              </>
            ) : null}
            <footer className="mt-2 border-t border-(--line) pt-3 text-[11px] text-(--ink-soft)/80">
              ACME Supply Co. Seller Operations · catalog, orders, pricing, and campaigns over
              the Shopify Admin API.
            </footer>
          </div>
        </div>
        <AssistantRail
          open={assistantOpen}
          storageKey="acme-shopify-merchant-panel-width"
          onClose={() => setAssistantOpen(false)}
        >
          {(rail) => (
            <AssistantPanel
              chat={chat}
              prefill={prefill}
              onPrefill={askAssistant}
              newMemoryCount={chat.newMemoryKeys.size}
              onOpenActivity={() => setActivityOpen(true)}
              {...rail}
            />
          )}
        </AssistantRail>
      </main>
      {activityOpen ? (
        <Inspector
          turnCount={chat.turnCount}
          streaming={chat.streaming}
          trace={chat.trace}
          memory={chat.memory}
          newMemoryKeys={chat.newMemoryKeys}
          memoryTitle="Business memory"
          onClose={() => setActivityOpen(false)}
        />
      ) : null}
    </div>
  );
}
