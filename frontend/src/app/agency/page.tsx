import { LayoutDashboard } from "lucide-react";
import { EmptyState } from "@/components/app-shell/AppShell";

// No own title: section roots use the layout default (e.g. "Worker | Embco")

export default function AgencyOverviewPage() {
  return (
    <EmptyState
      icon={LayoutDashboard}
      title="No campaigns yet"
      description="Your campaigns, budget in escrow and progress will show up here."
    />
  );
}
