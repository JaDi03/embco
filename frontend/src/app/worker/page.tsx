import { Home } from "lucide-react";
import { EmptyState } from "@/components/app-shell/AppShell";

// No own title: section roots use the layout default (e.g. "Worker | Embco")

export default function WorkerHomePage() {
  return (
    <EmptyState
      icon={Home}
      title="Your earnings at a glance"
      description="Balance, recent payouts and your reputation will show up here once you sign in."
    />
  );
}
