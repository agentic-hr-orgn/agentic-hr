import { describe, it, expect, beforeEach } from "vitest";
import { useUIStore } from "@/stores/ui-store";

describe("Zustand useUIStore", () => {
  beforeEach(() => {
    useUIStore.setState({ isSidebarOpen: false });
  });

  it("should initialize with isSidebarOpen as false", () => {
    expect(useUIStore.getState().isSidebarOpen).toBe(false);
  });

  it("should toggle sidebar state with toggleSidebar()", () => {
    useUIStore.getState().toggleSidebar();
    expect(useUIStore.getState().isSidebarOpen).toBe(true);

    useUIStore.getState().toggleSidebar();
    expect(useUIStore.getState().isSidebarOpen).toBe(false);
  });

  it("should explicitly set sidebar state with setSidebarOpen()", () => {
    useUIStore.getState().setSidebarOpen(true);
    expect(useUIStore.getState().isSidebarOpen).toBe(true);

    useUIStore.getState().setSidebarOpen(false);
    expect(useUIStore.getState().isSidebarOpen).toBe(false);
  });
});
