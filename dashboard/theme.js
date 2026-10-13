// Light or dark on every Embco page: the system's choice until the person picks one with the
// #theme-toggle button; the pick is remembered in this browser.

const THEME_KEY = "embco-theme";

function applyTheme(theme) {
  if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
  else delete document.documentElement.dataset.theme;
}

try {
  applyTheme(localStorage.getItem(THEME_KEY));
} catch {
  // no storage (private window): follow the system
}

document.getElementById("theme-toggle")?.addEventListener("click", () => {
  const current = document.documentElement.dataset.theme
    ?? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const next = current === "dark" ? "light" : "dark";
  applyTheme(next);
  try {
    localStorage.setItem(THEME_KEY, next);
  } catch {
    // the choice lasts for this visit only
  }
});
