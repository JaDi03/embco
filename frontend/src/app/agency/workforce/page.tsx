import type { Metadata } from "next";
import { Users } from "lucide-react";
import { EmptyState } from "@/components/app-shell/AppShell";

export const metadata: Metadata = { title: "Workforce" };

export default function AgencyWorkforcePage() {
  return (
    <EmptyState
      icon={Users}
      title="No workers yet"
      description="Workers who complete your tasks, with their accuracy and reputation, will appear here."
    />
  );
}
