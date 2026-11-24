import { ref, watchEffect } from "vue";

export type ThemeMode = "aurora" | "nebula" | "editorial";
export type NavTab = "workspace" | "guide" | "history" | "faq";

const VALID_THEMES: ThemeMode[] = ["aurora", "nebula", "editorial"];
const VALID_TABS: NavTab[] = ["workspace", "guide", "history", "faq"];

function readInitialTheme(): ThemeMode {
  const saved = localStorage.getItem("i2e_glass_theme") as ThemeMode | null;
  return saved && VALID_THEMES.includes(saved) ? saved : "aurora";
}

function readInitialTab(): NavTab {
  const hashTab = window.location.hash.replace("#/", "").split("?")[0] as NavTab;
  if (hashTab && VALID_TABS.includes(hashTab)) return hashTab;
  const saved = localStorage.getItem("i2e_active_tab") as NavTab | null;
  return saved && VALID_TABS.includes(saved) ? saved : "workspace";
}

const theme = ref<ThemeMode>(readInitialTheme());
const glass = ref<boolean>(localStorage.getItem("i2e_glass_enabled") !== "off");
const activeTab = ref<NavTab>(readInitialTab());

watchEffect(() => {
  const el = document.documentElement;
  el.setAttribute("data-glass-theme", theme.value);
  el.classList.toggle("dark", theme.value === "nebula");
  el.classList.toggle("editorial", theme.value === "editorial");
  el.classList.toggle("no-glass", !glass.value || theme.value === "editorial");
  localStorage.setItem("i2e_glass_theme", theme.value);
  localStorage.setItem("i2e_glass_enabled", glass.value ? "on" : "off");
});

watchEffect(() => {
  localStorage.setItem("i2e_active_tab", activeTab.value);
  const targetHash = `#/${activeTab.value}`;
  if (window.location.hash !== targetHash) {
    window.history.replaceState(null, "", targetHash);
  }
});

export const useTheme = () => ({ theme, glass, activeTab });
