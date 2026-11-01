import { describe, expect, it } from 'vitest';
import {
  applyResolvedTheme,
  isThemePreference,
  readThemePreference,
  resolveTheme,
  THEME_STORAGE_KEY,
  writeThemePreference,
} from './preference';

describe('theme preference', () => {
  it('accepts only system|light|dark', () => {
    expect(isThemePreference('system')).toBe(true);
    expect(isThemePreference('light')).toBe(true);
    expect(isThemePreference('dark')).toBe(true);
    expect(isThemePreference('auto')).toBe(false);
  });

  it('defaults to system when missing', () => {
    const storage = { getItem: () => null };
    expect(readThemePreference(storage)).toBe('system');
  });

  it('reads and writes preference', () => {
    const map = new Map<string, string>();
    const storage = {
      getItem: (k: string) => map.get(k) ?? null,
      setItem: (k: string, v: string) => {
        map.set(k, v);
      },
    };
    writeThemePreference('dark', storage);
    expect(map.get(THEME_STORAGE_KEY)).toBe('dark');
    expect(readThemePreference(storage)).toBe('dark');
  });

  it('resolves system from OS flag', () => {
    expect(resolveTheme('system', true)).toBe('dark');
    expect(resolveTheme('system', false)).toBe('light');
    expect(resolveTheme('light', true)).toBe('light');
    expect(resolveTheme('dark', false)).toBe('dark');
  });

  it('applies data-theme on root', () => {
    const el = document.createElement('div');
    applyResolvedTheme('dark', el);
    expect(el.dataset.theme).toBe('dark');
    expect(el.style.colorScheme).toBe('dark');
  });
});
