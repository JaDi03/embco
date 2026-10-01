import type { Metadata } from "next";
import { Settings } from "lucide-react";
import { EmptyState } from "@/components/app-shell/AppShell";

export const metadata: Metadata = { title: "Settings" };

export default function AgencySettingsPage() {
  return (
    <EmptyState
      icon={Settings}
      title="Settings"
      description="Organization profile, payout wallet and API access will be managed here."
    />
  );
}
