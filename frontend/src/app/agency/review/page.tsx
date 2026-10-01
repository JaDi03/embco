import type { Metadata } from "next";
import { CheckSquare } from "lucide-react";
import { EmptyState } from "@/components/app-shell/AppShell";

export const metadata: Metadata = { title: "Review Tasks" };

export default function AgencyReviewPage() {
  return (
    <EmptyState
      icon={CheckSquare}
      title="Nothing to review"
      description="Submissions that need your approval will be queued here."
    />
  );
}
