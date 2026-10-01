import type { Metadata } from "next";
import { AppShell } from "@/components/app-shell/AppShell";

export const metadata: Metadata = {
  title: { default: "Agency", template: "%s | Agency · Embco" },
};

export default function AgencyLayout({ children }: { children: React.ReactNode }) {
  return <AppShell variant="agency">{children}</AppShell>;
}
