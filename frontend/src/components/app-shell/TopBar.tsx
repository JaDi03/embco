"use client";

import Image from "next/image";
import { usePathname } from "next/navigation";
import { ThemeToggle } from "@/components/theme/ThemeToggle";
import { MobileDrawer } from "./MobileDrawer";
import { activeItem, type ShellVariant } from "./nav";

export function TopBar({ variant }: { variant: ShellVariant }) {
  const pathname = usePathname();
  const title = activeItem(variant, pathname)?.label ?? "";

  return (
    <header className="sticky top-0 z-30 border-b border-border bg-surface/90 pt-safe backdrop-blur-md">
      <div className="flex h-16 items-center gap-3 px-4 md:px-8">
        {variant === "agency" ? (
          <MobileDrawer variant={variant} />
        ) : (
          <Image src="/brand/logo.svg" alt="Embco" width={32} height={32} className="size-8 md:hidden" />
        )}
        <h1 className="truncate text-lg font-bold md:text-xl">{title}</h1>
        <div className="ml-auto">
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
