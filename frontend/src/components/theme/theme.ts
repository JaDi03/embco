export type ThemePreference = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

export const THEME_STORAGE_KEY = "embco-theme";
export const THEME_CHANGE_EVENT = "embco-theme-change";

// Browser UI color (status bar / task switcher) per resolved theme; matches --background
export const THEME_COLORS: Record<ResolvedTheme, string> = {
  light: "#f8fafc",
  dark: "#020617",
};

// Runs inline in <head> before first paint so the page never flashes the wrong theme.
// Keep in sync with readPreference/applyTheme in ThemeProvider.
export const themeScript = `(function(){try{var p=localStorage.getItem("${THEME_STORAGE_KEY}");var t=p==="light"||p==="dark"?p:(matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light");document.documentElement.dataset.theme=t;}catch(e){}})();`;
