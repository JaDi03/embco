import type { Metadata } from "next";
import { AppShell } from "@/components/app-shell/AppShell";

export const metadata: Metadata = {
  title: { default: "Worker", template: "%s | Worker · Embco" },
};

export default function WorkerLayout({ children }: { children: React.ReactNode }) {
  return <AppShell variant="worker">{children}</AppShell>;
}
