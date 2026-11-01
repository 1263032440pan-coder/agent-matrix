import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ThemeProvider, useThemePreference } from './ThemeProvider';

function Probe() {
  const { preference, setPreference } = useThemePreference();
  return (
    <div>
      <span data-testid="pref">{preference}</span>
      <button type="button" onClick={() => setPreference('dark')}>
        dark
      </button>
      <button type="button" onClick={() => setPreference('light')}>
        light
      </button>
    </div>
  );
}

describe('ThemeProvider', () => {
  it('defaults to system and can switch', async () => {
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
    expect(screen.getByTestId('pref')).toHaveTextContent('system');
    await userEvent.click(screen.getByText('dark'));
    expect(screen.getByTestId('pref')).toHaveTextContent('dark');
    expect(document.documentElement.dataset.theme).toBe('dark');
  });
});
