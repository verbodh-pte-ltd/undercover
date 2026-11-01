// The appearance, the one setting each browser keeps for itself.
export type Theme = "light" | "dark" | "system";

const KEY = "undercover-theme";

export function savedTheme(): Theme {
  const saved = localStorage.getItem(KEY);
  return saved === "light" || saved === "dark" ? saved : "system";
}

// Light or dark sets data-theme on the page; the computer's leaves it to the media query.
export function applyTheme(theme: Theme) {
  if (theme === "system") {
    localStorage.removeItem(KEY);
    delete document.documentElement.dataset.theme;
  } else {
    localStorage.setItem(KEY, theme);
    document.documentElement.dataset.theme = theme;
  }
}
