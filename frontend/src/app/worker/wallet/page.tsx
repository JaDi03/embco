import type { Metadata } from "next";
import { Wallet } from "lucide-react";
import { EmptyState } from "@/components/app-shell/AppShell";

export const metadata: Metadata = { title: "Wallet" };

export default function WorkerWalletPage() {
  return (
    <EmptyState
      icon={Wallet}
      title="Your wallet"
      description="Your USDC balance and withdrawals to any address will live here."
    />
  );
}
