"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Plus } from "lucide-react";
import { NAV, isActive, type ShellVariant } from "./nav";

// Brand-gradient navigation, shared by the desktop sidebar and the agency mobile drawer
export function SidebarContent({
  variant,
  onNavigate,
  inDrawer = false,
}: {
  variant: ShellVariant;
  onNavigate?: () => void;
  inDrawer?: boolean;
}) {
  const pathname = usePathname();

  return (
    <div className="flex h-full flex-col bg-gradient-brand-vertical text-white">
      {/* In the drawer, leave room on the right for the close button */}
      <div className={`flex h-20 shrink-0 items-center gap-2.5 border-b border-white/10 pl-6 ${inDrawer ? "pr-16" : "pr-6"}`}>
        <Image src="/brand/logo.svg" alt="" width={36} height={36} className="size-9 rounded-xl ring-1 ring-white/30" />
        <span className="text-xl font-extrabold tracking-tight">Embco</span>
        <span className="ml-auto rounded-full bg-white/15 px-2 py-0.5 text-[10px] font-bold uppercase tracking-widest">
          {variant}
        </span>
      </div>

      <nav aria-label="Main" className="flex-1 space-y-1 overflow-y-auto p-4">
        {NAV[variant].map(({ href, label, icon: Icon }) => {
          const active = isActive(pathname, href);
          return (
            <Link
              key={href}
              href={href}
              onClick={onNavigate}
              aria-current={active ? "page" : undefined}
              className={`flex min-h-11 items-center gap-3 rounded-lg px-4 py-3 font-medium transition ${
                active ? "bg-white/20 text-white shadow-sm" : "text-white/75 hover:bg-white/10 hover:text-white"
              }`}
            >
              <Icon className="size-5 shrink-0" aria-hidden />
              {label}
            </Link>
          );
        })}
      </nav>

      {variant === "agency" && (
        <div className="shrink-0 border-t border-white/10 p-4">
          {/* Campaign creation is migrated in the agency step */}
          <button
            type="button"
            disabled
            className="flex h-11 w-full cursor-not-allowed items-center justify-center gap-2 rounded-xl bg-white text-sm font-bold text-brand opacity-80"
          >
            <Plus className="size-4" aria-hidden /> New campaign · soon
          </button>
        </div>
      )}
    </div>
  );
}

export function Sidebar({ variant }: { variant: ShellVariant }) {
  return (
    <aside className="sticky top-0 hidden h-dvh w-64 shrink-0 md:block">
      <SidebarContent variant={variant} />
    </aside>
  );
}
