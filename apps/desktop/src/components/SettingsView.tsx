import { useState } from 'react';
import { LLMSettingsCard } from './LLMSettingsCard';
import { OfficeBackendsPanel } from './OfficeBackendsPanel';
import { SkillPanel } from './SkillPanel';
import { useThemePreference } from '../theme/ThemeProvider';
import type { ThemePreference } from '../theme/preference';

type Tab = 'appearance' | 'llm' | 'skills' | 'office';

const THEME_OPTS: Array<{ id: ThemePreference; label: string }> = [
  { id: 'system', label: '跟随系统' },
  { id: 'light', label: '亮' },
  { id: 'dark', label: '暗' },
];

export function SettingsView({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<Tab>('appearance');
  const { preference, setPreference } = useThemePreference();

  return (
    <div className="settings-layout" data-testid="settings-view">
      <nav className="settings-nav">
        <button
          type="button"
          data-active={tab === 'appearance'}
          data-testid="settings-tab-appearance"
          onClick={() => setTab('appearance')}
        >
          外观
        </button>
        <button
          type="button"
          data-active={tab === 'llm'}
          data-testid="settings-tab-llm"
          onClick={() => setTab('llm')}
        >
          模型与 API Key
        </button>
        <button
          type="button"
          data-active={tab === 'skills'}
          data-testid="settings-tab-skills"
          onClick={() => setTab('skills')}
        >
          Skills
        </button>
        <button
          type="button"
          data-active={tab === 'office'}
          data-testid="settings-tab-office"
          onClick={() => setTab('office')}
        >
          办公后端
        </button>
        <div style={{ flex: 1 }} />
        <button type="button" onClick={onClose} data-testid="settings-close">
          ← 返回
        </button>
      </nav>
      <div className="settings-main">
        {tab === 'appearance' && (
          <div data-testid="settings-appearance">
            <h2 style={{ marginTop: 0 }}>外观</h2>
            <p style={{ color: 'var(--ui-text-muted)', fontSize: 13 }}>
              主题影响整应用壳（顶栏 / 侧栏 / 工作台 / 浮层）
            </p>
            <div style={{ marginTop: 16 }}>
              <div style={{ fontWeight: 600, marginBottom: 8, fontSize: 13 }}>
                主题
              </div>
              <div className="ui-segment" data-testid="theme-segment">
                {THEME_OPTS.map((o) => (
                  <button
                    key={o.id}
                    type="button"
                    data-active={preference === o.id}
                    data-testid={`theme-opt-${o.id}`}
                    onClick={() => setPreference(o.id)}
                  >
                    {o.label}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}
        {tab === 'llm' && <LLMSettingsCard />}
        {tab === 'skills' && <SkillPanel />}
        {tab === 'office' && <OfficeBackendsPanel />}
      </div>
    </div>
  );
}
