import { BottomNav } from "./BottomNav";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import type { ShellVariant } from "./nav";

export function AppShell({ variant, children }: { variant: ShellVariant; children: React.ReactNode }) {
  return (
    <div className="min-h-dvh bg-background md:flex">
      <Sidebar variant={variant} />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar variant={variant} />
        {/* Extra bottom padding keeps content clear of the worker tab bar on phones */}
        <main className={`flex-1 px-4 py-6 md:px-8 md:py-8 ${variant === "worker" ? "pb-28 md:pb-8" : "pb-safe"}`}>
          <div className="mx-auto w-full max-w-6xl">{children}</div>
        </main>
      </div>
      {variant === "worker" && <BottomNav />}
    </div>
  );
}

export function EmptyState({
  icon: Icon,
  title,
  description,
}: {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  description: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-3xl border border-dashed border-border bg-surface px-6 py-16 text-center">
      <span className="mb-4 flex size-14 items-center justify-center rounded-2xl bg-brand-soft text-brand-text">
        <Icon className="size-7" />
      </span>
      <h2 className="mb-1 text-lg font-bold">{title}</h2>
      <p className="max-w-sm text-muted">{description}</p>
    </div>
  );
}
