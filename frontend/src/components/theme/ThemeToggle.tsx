"use client";

import { Moon, Sun } from "lucide-react";
import { useTheme } from "./ThemeProvider";

// One button that flips between light and dark. Until the user picks, the device setting decides.
export function ThemeToggle() {
  const { resolved, setPreference } = useTheme();
  const next = resolved === "dark" ? "light" : "dark";
  const Icon = resolved === "dark" ? Sun : Moon;

  return (
    <button
      type="button"
      aria-label={`Switch to ${next} mode`}
      onClick={() => setPreference(next)}
      className="flex size-10 items-center justify-center rounded-xl border border-border bg-surface text-muted transition-colors hover:bg-surface-2 hover:text-foreground"
    >
      <Icon className="size-5" aria-hidden />
    </button>
  );
}
