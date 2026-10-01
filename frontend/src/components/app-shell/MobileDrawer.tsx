"use client";

import { useRef } from "react";
import { Menu, X } from "lucide-react";
import { SidebarContent } from "./Sidebar";
import type { ShellVariant } from "./nav";

// Native <dialog> provides focus trapping, Esc-to-close and the backdrop
export function MobileDrawer({ variant }: { variant: ShellVariant }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const close = () => dialogRef.current?.close();

  return (
    <>
      <button
        type="button"
        aria-label="Open menu"
        onClick={() => dialogRef.current?.showModal()}
        className="-ml-2 flex size-11 items-center justify-center rounded-xl text-foreground hover:bg-surface-2 md:hidden"
      >
        <Menu className="size-6" aria-hidden />
      </button>

      <dialog
        ref={dialogRef}
        aria-label="Menu"
        // A click on the backdrop targets the <dialog> element itself
        onClick={(event) => {
          if (event.target === dialogRef.current) close();
        }}
        className="m-0 h-dvh max-h-none w-72 max-w-[85vw] bg-transparent p-0 backdrop:bg-black/50 md:hidden"
      >
        <div className="relative h-full bg-brand pt-safe">
          <SidebarContent variant={variant} onNavigate={close} inDrawer />
          <button
            type="button"
            aria-label="Close menu"
            onClick={close}
            className="absolute top-[calc(env(safe-area-inset-top)+1.25rem)] right-3 flex size-10 items-center justify-center rounded-xl bg-brand text-white/80 hover:bg-white/10"
          >
            <X className="size-5" aria-hidden />
          </button>
        </div>
      </dialog>
    </>
  );
}
