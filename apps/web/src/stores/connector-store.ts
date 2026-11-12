import { create } from "zustand";
import type { ConnectorResponse } from "@agentic-hr/contracts";

export const DEFAULT_MOCK_CONNECTOR: ConnectorResponse = {
  id: "f63315da-87b3-4648-bb1f-eca593e60674",
  tenant_id: "b4ec4ca5-f07b-4f61-82a9-ab78b8252496",
  provider: "google",
  account_email: "agentic.hr.test.ubaid.owner@gmail.com",
  scopes: [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/calendar.events",
  ],
  status: "connected",
  is_read_only: false,
  jobs_paused: false,
  error_message: null,
  created_at: "2026-11-11T10:00:00Z",
  updated_at: "2026-11-11T10:00:00Z",
};

export interface ConnectorStoreState {
  connector: ConnectorResponse;
  bannerMessage: string | null;
  setConnector: (connector: ConnectorResponse) => void;
  toggleReadOnly: () => boolean;
  refreshToken: () => void;
  revokeConnector: () => void;
  reconnectConnector: () => void;
  setBannerMessage: (msg: string | null) => void;
}

export const useConnectorStore = create<ConnectorStoreState>((set) => ({
  connector: DEFAULT_MOCK_CONNECTOR,
  bannerMessage: null,
  setConnector: (connector) => set({ connector }),
  toggleReadOnly: () => {
    let nextVal = false;
    set((state) => {
      nextVal = !state.connector.is_read_only;
      return {
        connector: {
          ...state.connector,
          is_read_only: nextVal,
          updated_at: new Date().toISOString(),
        },
        bannerMessage: nextVal
          ? "Read-Only Mode enabled. Outbound write operations (send email) are blocked."
          : "Read-Only Mode disabled. Outbound write operations are active.",
      };
    });
    return nextVal;
  },
  refreshToken: () =>
    set((state) => ({
      connector: {
        ...state.connector,
        status: "connected",
        jobs_paused: false,
        error_message: null,
        token_expiry: new Date(Date.now() + 3600 * 1000).toISOString(),
        updated_at: new Date().toISOString(),
      },
      bannerMessage: "OAuth access token refreshed successfully (+1h expiry).",
    })),
  revokeConnector: () =>
    set((state) => ({
      connector: {
        ...state.connector,
        status: "revoked",
        jobs_paused: true,
        updated_at: new Date().toISOString(),
      },
      bannerMessage: "Google Workspace connector disconnected. Status set to Revoked.",
    })),
  reconnectConnector: () =>
    set((state) => ({
      connector: {
        ...state.connector,
        status: "connected",
        jobs_paused: false,
        error_message: null,
        token_expiry: new Date(Date.now() + 3600 * 1000).toISOString(),
        updated_at: new Date().toISOString(),
      },
      bannerMessage: "Google Workspace connector reconnected successfully.",
    })),
  setBannerMessage: (bannerMessage) => set({ bannerMessage }),
}));
