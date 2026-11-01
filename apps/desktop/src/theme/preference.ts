/** SYS-164: theme preference — system | light | dark */

export type ThemePreference = 'system' | 'light' | 'dark';
export type ResolvedTheme = 'light' | 'dark';

export const THEME_STORAGE_KEY = 'agent-matrix.theme';

export function isThemePreference(v: unknown): v is ThemePreference {
  return v === 'system' || v === 'light' || v === 'dark';
}

export function readThemePreference(
  storage: Pick<Storage, 'getItem'> = localStorage,
): ThemePreference {
  try {
    const raw = storage.getItem(THEME_STORAGE_KEY);
    if (isThemePreference(raw)) return raw;
  } catch {
    /* ignore */
  }
  return 'system';
}

export function writeThemePreference(
  value: ThemePreference,
  storage: Pick<Storage, 'setItem'> = localStorage,
): void {
  storage.setItem(THEME_STORAGE_KEY, value);
}

export function resolveTheme(
  preference: ThemePreference,
  systemDark: boolean,
): ResolvedTheme {
  if (preference === 'light') return 'light';
  if (preference === 'dark') return 'dark';
  return systemDark ? 'dark' : 'light';
}

export function applyResolvedTheme(resolved: ResolvedTheme, root: HTMLElement = document.documentElement): void {
  root.dataset.theme = resolved;
  root.style.colorScheme = resolved;
}
