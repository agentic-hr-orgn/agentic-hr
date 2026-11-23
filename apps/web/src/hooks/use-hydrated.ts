"use client";

import { useSyncExternalStore } from "react";

const emptySubscribe = () => () => {};

/**
 * Returns `false` during SSR and the initial React `hydrateRoot` pass,
 * then `true` immediately after hydration completes (and synchronously `true`
 * during client SPA navigation and jsdom/Vitest client renders).
 */
export function useHydrated(): boolean {
  return useSyncExternalStore(
    emptySubscribe,
    () => true,
    () => false
  );
}
