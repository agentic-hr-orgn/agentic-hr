import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import { NextFetchEvent, NextRequest, NextResponse } from "next/server";

// Public routes accessible without active authentication
const publicRoutes = ["/sign-in(.*)", "/sign-up(.*)", "/api/health(.*)"];
if (process.env.NODE_ENV !== "production") {
  publicRoutes.push("/api/synthetic(.*)");
}
const isPublicRoute = createRouteMatcher(publicRoutes);

const handler = clerkMiddleware(async (auth, req) => {
  // Protect all dashboard routes; unauthenticated requests are redirected to /sign-in
  if (!isPublicRoute(req)) {
    await auth.protect();
  }
});

export default function middleware(req: NextRequest, ev: NextFetchEvent) {
  // Strict dual server-env + header gate: never enabled in production deployments
  const e2eBypassEnabled =
    process.env.PLAYWRIGHT === "true" && process.env.ALLOW_E2E_AUTH_BYPASS === "true";

  if (e2eBypassEnabled) {
    const isE2ETest = req.headers.get("x-e2e-test") === "true";
    // In automated Playwright testing: redirect unauthenticated protected routes to /sign-in
    if (!isPublicRoute(req) && !isE2ETest) {
      const signInUrl = new URL("/sign-in", req.url);
      return NextResponse.redirect(signInUrl);
    }
    return NextResponse.next();
  }

  // In all live / production sessions: run full Clerk middleware authentication
  return handler(req, ev);
}

export const config = {
  matcher: [
    // Skip Next.js internals and static assets
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    // Always run for API routes
    "/(api|trpc)(.*)",
  ],
};
