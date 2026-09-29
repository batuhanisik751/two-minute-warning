"use client";

import { useLayoutEffect } from "react";
import { THEME_KEY } from "@/lib/theme";

function systemTheme(): "light" | "dark" {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function stored(): "light" | "dark" | null {
  try {
    const t = localStorage.getItem(THEME_KEY);
    return t === "light" || t === "dark" ? t : null;
  } catch {
    return null;
  }
}

/** Switches between light and dark. Choosing the theme the system already uses forgets the
 *  choice, so the site follows the system again from then on. */
export default function ThemeToggle() {
  // React's development remount resets <html>'s attributes: re-apply the stored choice
  // (a no-op in production, where the inline script already did it).
  useLayoutEffect(() => {
    const t = stored();
    if (t) document.documentElement.setAttribute("data-theme", t);
  }, []);

  function toggle() {
    const root = document.documentElement;
    const sys = systemTheme();
    const attr = root.getAttribute("data-theme");
    const current = attr === "light" || attr === "dark" ? attr : sys;
    const next = current === "dark" ? "light" : "dark";
    try {
      if (next === sys) localStorage.removeItem(THEME_KEY);
      else localStorage.setItem(THEME_KEY, next);
    } catch {
      // storage unavailable (private mode): the choice lasts for this page only
    }
    if (next === sys) root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", next);
  }

  return (
    <button
      type="button"
      onClick={toggle}
      className="strip-button inline-flex min-h-11 min-w-11 items-center justify-center gap-2 rounded-md px-2.5 text-sm font-medium sm:px-3"
    >
      <svg aria-hidden="true" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M12 3a9 9 0 1 0 9 9 7 7 0 0 1-9-9z" />
      </svg>
      {/* the name stays for screen readers on phones, where only the icon shows */}
      <span className="sr-only sm:not-sr-only">Light / dark</span>
    </button>
  );
}
