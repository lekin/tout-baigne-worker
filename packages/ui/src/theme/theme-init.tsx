// Inline equivalent of public/theme-init.js — resolves the stored/system
// theme before paint and mirrors explicit choices into a cookie so the
// server can apply the class directly on the next render.
const THEME_INIT_JS = `(function () {
  try {
    var t = localStorage.getItem("theme");
    var d =
      t === "dark" ||
      ((!t || t === "system") &&
        window.matchMedia("(prefers-color-scheme: dark)").matches);
    var e = document.documentElement;
    e.classList.remove("light", "dark");
    e.classList.add(d ? "dark" : "light");
    e.style.colorScheme = d ? "dark" : "light";
    if (t === "dark" || t === "light") {
      document.cookie =
        "theme=" + t + ";path=/;max-age=31536000;SameSite=Lax";
    }
  } catch {}
})();`;

export function ThemeInitScript() {
  return <script dangerouslySetInnerHTML={{ __html: THEME_INIT_JS }} />;
}
