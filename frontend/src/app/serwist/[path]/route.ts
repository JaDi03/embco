import { createSerwistRoute } from "@serwist/turbopack";
import { randomUUID } from "node:crypto";

// Serves the bundled service worker at /serwist/sw.js (Turbopack has no plugin support yet)
export const { dynamic, dynamicParams, revalidate, generateStaticParams, GET } = createSerwistRoute({
  swSrc: "src/app/sw.ts",
  useNativeEsbuild: true,
  additionalPrecacheEntries: [{ url: "/~offline", revision: randomUUID() }],
});
