import type { Metadata } from "next";
import { ClerkProvider } from "@clerk/nextjs";
import { QueryProvider } from "@/providers/query-provider";
import "./globals.css";

export const metadata: Metadata = {
  title: "Agentic HR Core — SME Workforce Platform",
  description:
    "Agentic AI Workforce for SMEs featuring DocuVerify, Screener, Code Tester, and Policy Brain.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const clerkKey =
    process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY ||
    "pk_test_c2luY2VyZS1lc2NhcmdvdC05NTkuY2xlcmsuYWNjb3VudHMuZGV2JA";

  return (
    <ClerkProvider
      publishableKey={clerkKey}
      appearance={{
        variables: {
          colorPrimary: "#2A5BDB",
          colorText: "#0F172A",
          colorTextSecondary: "#64748B",
          colorBackground: "#FFFFFF",
          colorInputBackground: "#FFFFFF",
          borderRadius: "0.5rem",
        },
        elements: {
          card: "shadow-card border border-appBorder rounded-medium",
          formFieldInput:
            "border border-appBorder focus:border-primary focus:ring-1 focus:ring-primary",
          formButtonPrimary:
            "bg-primary hover:bg-primary-hover text-white font-semibold transition-all shadow-button",
          organizationSwitcherTrigger:
            "border border-appBorder rounded-medium px-3 py-1.5 hover:bg-surface-hover text-textPrimary text-xs font-semibold shadow-xs",
        },
      }}
    >
      <html lang="en">
        <body className="min-h-screen bg-appBg font-sans antialiased text-textPrimary">
          <QueryProvider>{children}</QueryProvider>
        </body>
      </html>
    </ClerkProvider>
  );
}
