/**
 * layout.tsx — root layout for the Next.js App Router.
 *
 * Reconstructed for M8: this file is referenced throughout DECISIONS.md
 * (D-M7-3) and globals.css but was absent from the M7-flat.txt export used to
 * build this package — every prior milestone's flat file omitted it, which
 * only surfaces as a hard build failure once a fresh `npm run build` runs
 * against an environment with no leftover .next/out artifacts.
 *
 * D-M7-3: theme is driven by a `data-theme` attribute on <html>, not
 * `prefers-color-scheme`. The inline script below runs before paint (it is
 * not deferred, not type="module") so the saved theme applies before the
 * browser's first frame — this is what "prevents a flash of the wrong theme"
 * refers to in DECISIONS.md. It reads the same "ffg-theme" localStorage key
 * that components/Brand.tsx's ThemeToggle writes.
 */
import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Nexus Placement Intelligence",
  description: "Topical and SEO placement recommendations across FFG and client domains.",
};

// M9.2 (amends D-M7-3): the default theme is now dark. Light mode stays fully
// supported and is applied only when the user has explicitly saved "light".
// Any other stored value, or no stored value at all, resolves to dark. The
// script still runs before paint so there is no flash of the wrong theme.
const THEME_INIT_SCRIPT = `
(function () {
  try {
    var saved = localStorage.getItem("ffg-theme");
    var theme = saved === "light" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", theme);
  } catch (e) {
    document.documentElement.setAttribute("data-theme", "dark");
  }
})();
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        {/* eslint-disable-next-line @next/next/no-sync-scripts */}
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
