import React from "react";
import { SignIn } from "@clerk/nextjs";
import Link from "next/link";
import { ShieldCheck } from "lucide-react";

export default function SignInPage() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-appBg px-4 py-12">
      {/* Brand Header */}
      <div className="mb-8 flex flex-col items-center text-center">
        <Link href="/" className="mb-4 flex items-center gap-3">
          <div className="flex h-12 w-12 items-center justify-center rounded-medium bg-primary text-xl font-bold text-white shadow-button">
            A
          </div>
          <span className="text-2xl font-bold tracking-tight text-navy">Agentic HR Core</span>
        </Link>
        <h1 className="text-xl font-bold text-textPrimary">SME Workforce Platform Sign In</h1>
        <p className="mt-1 text-sm text-textSecondary max-w-sm">
          Sign in to access your organization dashboard with role-based security and tenant
          isolation.
        </p>
      </div>

      {/* Clerk Sign In Component with Theme Customization */}
      <div className="w-full max-w-md">
        <SignIn path="/sign-in" routing="path" signUpUrl="/sign-up" fallbackRedirectUrl="/" />
      </div>

      {/* Security notice footer */}
      <div className="mt-8 flex items-center gap-2 text-xs text-textSecondary">
        <ShieldCheck className="h-4 w-4 text-primary" />
        <span>Multi-Tenant Row-Level Security Enforced (ADR 002)</span>
      </div>
    </div>
  );
}
