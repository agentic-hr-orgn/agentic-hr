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
  const clerkKey = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;

  const bodyContent = <QueryProvider>{children}</QueryProvider>;

  if (clerkKey) {
    return (
      <ClerkProvider publishableKey={clerkKey}>
        <html lang="en">
          <body className="min-h-screen bg-appBg font-sans antialiased text-textPrimary">
            {bodyContent}
          </body>
        </html>
      </ClerkProvider>
    );
  }

  return (
    <html lang="en">
      <body className="min-h-screen bg-appBg font-sans antialiased text-textPrimary">
        {bodyContent}
      </body>
    </html>
  );
}
