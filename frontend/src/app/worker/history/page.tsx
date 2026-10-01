import type { Metadata } from "next";
import { History } from "lucide-react";
import { EmptyState } from "@/components/app-shell/AppShell";

export const metadata: Metadata = { title: "History" };

export default function WorkerHistoryPage() {
  return (
    <EmptyState
      icon={History}
      title="Nothing here yet"
      description="Every task you submit, with its status and payout, will be listed here."
    />
  );
}
