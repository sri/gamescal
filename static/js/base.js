(() => {
  const storageKey = "gamescal-theme";
  const themes = {
    default: { label: "Default", color: "#ffffff", mode: "light" },
    everforest: { label: "Everforest", color: "#2d353b", mode: "dark" },
    "solarized-dark": {
      label: "Solarized Dark",
      color: "#002b36",
      mode: "dark",
    },
    vantablack: { label: "Vantablack", color: "#000000", mode: "dark" },
    "osaka-jade": { label: "Osaka Jade", color: "#111c18", mode: "dark" },
    ristretto: { label: "Ristretto", color: "#2c2525", mode: "dark" },
    "tokyo-night": { label: "Tokyo Night", color: "#1a1b26", mode: "dark" },
  };

  function savedTheme() {
    try {
      const theme = localStorage.getItem(storageKey);
      return Object.hasOwn(themes, theme) ? theme : "default";
    } catch (error) {
      return "default";
    }
  }

  function applyTheme(theme, persist = false) {
    const selected = Object.hasOwn(themes, theme) ? theme : "default";
    const config = themes[selected];
    document.documentElement.dataset.theme = selected;
    document.documentElement.dataset.bsTheme = config.mode;

    const themeColor = document.getElementById("theme-color-meta");
    if (themeColor) themeColor.content = config.color;

    document.querySelectorAll("[data-theme-option]").forEach((option) => {
      const active = option.dataset.themeOption === selected;
      option.classList.toggle("theme-option-active", active);
      option.setAttribute("aria-pressed", String(active));
    });

    const status = document.getElementById("theme-status");
    if (status) status.textContent = `${config.label} theme selected.`;

    if (persist) {
      try {
        localStorage.setItem(storageKey, selected);
      } catch (error) {
        // The theme still applies for this page when storage is unavailable.
      }
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    applyTheme(savedTheme());
    document.querySelectorAll("[data-theme-option]").forEach((option) => {
      option.addEventListener("click", () => {
        applyTheme(option.dataset.themeOption, true);
      });
    });
  });
})();
