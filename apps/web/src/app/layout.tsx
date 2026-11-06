import type { Metadata } from "next";
import { ClerkProvider } from "@clerk/nextjs";
import { Sidebar } from "@/components/layout/sidebar";
import { Header } from "@/components/layout/header";
import "./globals.css";

export const metadata: Metadata = {
  title: "Agentic HR Core — SME Workforce Platform",
  description: "Agentic AI Workforce for SMEs with DocuVerify, Screener, Code Tester, and Policy Brain.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const clerkKey = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;

  const content = (
    <div className="flex min-h-screen bg-appBg">
      <Sidebar />
      <div className="flex-1 ml-64 flex flex-col min-h-screen">
        <Header />
        <main className="flex-1 p-8">{children}</main>
      </div>
    </div>
  );

  if (clerkKey) {
    return (
      <ClerkProvider publishableKey={clerkKey}>
        <html lang="en">
          <body>{content}</body>
        </html>
      </ClerkProvider>
    );
  }

  return (
    <html lang="en">
      <body>{content}</body>
    </html>
  );
}

