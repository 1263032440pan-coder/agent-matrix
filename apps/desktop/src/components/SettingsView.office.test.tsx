import { describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { SettingsView } from './SettingsView';

vi.mock('../theme/ThemeProvider', () => ({
  useThemePreference: () => ({
    preference: 'system',
    setPreference: vi.fn(),
  }),
}));

vi.mock('./LLMSettingsCard', () => ({
  LLMSettingsCard: () => <div data-testid="llm-card" />,
}));

vi.mock('./SkillPanel', () => ({
  SkillPanel: () => <div data-testid="skill-panel" />,
}));

vi.mock('./OfficeBackendsPanel', () => ({
  OfficeBackendsPanel: () => <div data-testid="office-backends-panel" />,
}));

describe('SettingsView office tab', () => {
  it('opens 办公后端 tab', () => {
    render(<SettingsView onClose={() => undefined} />);
    fireEvent.click(screen.getByTestId('settings-tab-office'));
    expect(screen.getByTestId('office-backends-panel')).toBeTruthy();
  });
});
