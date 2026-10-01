import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Offline",
};

export default function OfflinePage() {
  return (
    <main className="flex min-h-dvh flex-col items-center justify-center gap-3 px-6 pt-safe pb-safe text-center">
      <h1 className="text-xl font-semibold">You are offline</h1>
      <p className="max-w-xs text-muted">
        Check your connection. Your work is safe; reopen the app once you are back online.
      </p>
    </main>
  );
}
