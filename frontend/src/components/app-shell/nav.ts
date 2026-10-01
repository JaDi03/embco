import {
  CheckSquare,
  History,
  Home,
  LayoutDashboard,
  ListTodo,
  Settings,
  Users,
  Wallet,
  type LucideIcon,
} from "lucide-react";

export type ShellVariant = "worker" | "agency";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
}

// Imported only by client components: icons are functions and can't be passed as props from server components
export const NAV: Record<ShellVariant, NavItem[]> = {
  worker: [
    { href: "/worker", label: "Home", icon: Home },
    { href: "/worker/tasks", label: "Tasks", icon: ListTodo },
    { href: "/worker/history", label: "History", icon: History },
    { href: "/worker/wallet", label: "Wallet", icon: Wallet },
  ],
  agency: [
    { href: "/agency", label: "Overview", icon: LayoutDashboard },
    { href: "/agency/review", label: "Review Tasks", icon: CheckSquare },
    { href: "/agency/workforce", label: "Workforce", icon: Users },
    { href: "/agency/settings", label: "Settings", icon: Settings },
  ],
};

// Section roots (/worker, /agency) match exactly; deeper items also match their sub-routes
export function isActive(pathname: string, href: string) {
  const isRoot = href.split("/").length === 2;
  return isRoot ? pathname === href : pathname === href || pathname.startsWith(`${href}/`);
}

export function activeItem(variant: ShellVariant, pathname: string) {
  return NAV[variant].find((item) => isActive(pathname, item.href));
}
