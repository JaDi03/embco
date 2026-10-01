import type { Metadata } from "next";
import { ListTodo } from "lucide-react";
import { EmptyState } from "@/components/app-shell/AppShell";

export const metadata: Metadata = { title: "Tasks" };

export default function WorkerTasksPage() {
  return (
    <EmptyState
      icon={ListTodo}
      title="No tasks yet"
      description="Open tasks from agencies will appear here. Pick one, answer it and get paid in USDC."
    />
  );
}
