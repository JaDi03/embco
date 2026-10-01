"use client";

import { SerwistProvider } from "@serwist/turbopack/react";

export function PwaProvider({ children }: { children: React.ReactNode }) {
  return (
    <SerwistProvider
      swUrl="/serwist/sw.js"
      options={{ scope: "/" }}
      // A service worker in dev serves stale bundles and hides changes
      disable={process.env.NODE_ENV === "development"}
    >
      {children}
    </SerwistProvider>
  );
}
