import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { ConfigProvider, theme as antdTheme } from 'antd';
import {
  applyResolvedTheme,
  readThemePreference,
  resolveTheme,
  writeThemePreference,
  type ResolvedTheme,
  type ThemePreference,
} from './preference';

interface ThemeCtx {
  preference: ThemePreference;
  resolved: ResolvedTheme;
  setPreference: (p: ThemePreference) => void;
}

const Ctx = createContext<ThemeCtx | null>(null);

function systemPrefersDark(): boolean {
  return (
    typeof window !== 'undefined' &&
    window.matchMedia?.('(prefers-color-scheme: dark)').matches
  );
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPref] = useState<ThemePreference>(() =>
    readThemePreference(),
  );
  const [systemDark, setSystemDark] = useState(systemPrefersDark);

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = () => setSystemDark(mq.matches);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

  const resolved = resolveTheme(preference, systemDark);

  useEffect(() => {
    applyResolvedTheme(resolved);
  }, [resolved]);

  const setPreference = useCallback((p: ThemePreference) => {
    writeThemePreference(p);
    setPref(p);
  }, []);

  const value = useMemo(
    () => ({ preference, resolved, setPreference }),
    [preference, resolved, setPreference],
  );

  return (
    <Ctx.Provider value={value}>
      <ConfigProvider
        theme={{
          algorithm:
            resolved === 'dark'
              ? antdTheme.darkAlgorithm
              : antdTheme.defaultAlgorithm,
          token: {
            colorPrimary: '#3d6b4f',
            borderRadius: 4,
            fontFamily:
              'ui-sans-serif, system-ui, "Segoe UI", Roboto, sans-serif',
          },
        }}
      >
        {children}
      </ConfigProvider>
    </Ctx.Provider>
  );
}

export function useThemePreference(): ThemeCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useThemePreference outside ThemeProvider');
  return ctx;
}
