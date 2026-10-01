import type { NextConfig } from "next";
import { withSerwist } from "@serwist/turbopack";

// Baseline hardening. A full Content-Security-Policy is added once the Circle
// Modular Wallets and Reown integrations define which origins we must allow.
const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" },
  {
    key: "Permissions-Policy",
    value:
      "camera=(), microphone=(), geolocation=(), publickey-credentials-get=(self), publickey-credentials-create=(self)",
  },
];

const nextConfig: NextConfig = {
  poweredByHeader: false,
  // The app root is this folder; stops Turbopack from picking up stray parent lockfiles
  turbopack: {
    root: import.meta.dirname,
  },
  async headers() {
    return [
      { source: "/:path*", headers: securityHeaders },
      {
        // Always revalidate the service worker so updates reach installed PWAs
        source: "/serwist/:path*",
        headers: [{ key: "Cache-Control", value: "no-cache, no-store, must-revalidate" }],
      },
    ];
  },
};

export default withSerwist(nextConfig);
