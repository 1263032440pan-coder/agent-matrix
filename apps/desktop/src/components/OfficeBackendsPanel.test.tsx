import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { OfficeBackendsPanel } from './OfficeBackendsPanel';
import {
  getOfficeBackends,
  patchOfficeBackend,
  patchOfficeBackendGlobal,
} from '../api';

vi.mock('../api', () => ({
  getOfficeBackends: vi.fn(),
  patchOfficeBackend: vi.fn(),
  patchOfficeBackendGlobal: vi.fn(),
}));

const sample = {
  fallback: 'auto' as const,
  officecli_path: '',
  last_fallback: {
    op: 'office.render.pptx',
    from: 'office-cli:office.render.pptx',
    to: 'office-builtin:office.render.pptx',
    at: '2026-11-04T00:00:00Z',
  },
  backends: [
    {
      name: 'office-builtin:office.render.pptx',
      op: 'office.render.pptx',
      source: 'builtin',
      priority: 100,
      enabled: true,
      usable: true,
      health: true,
    },
    {
      name: 'office-cli:office.render.pptx',
      op: 'office.render.pptx',
      source: 'plugin',
      priority: 200,
      enabled: true,
      usable: false,
      health: false,
    },
  ],
};

describe('OfficeBackendsPanel', () => {
  beforeEach(() => {
    vi.mocked(getOfficeBackends).mockResolvedValue(sample);
    vi.mocked(patchOfficeBackendGlobal).mockResolvedValue({
      ...sample,
      fallback: 'strict',
    });
    vi.mocked(patchOfficeBackend).mockResolvedValue({
      ...sample,
      backends: sample.backends.map((b) =>
        b.name === 'office-cli:office.render.pptx'
          ? { ...b, enabled: false }
          : b,
      ),
    });
  });

  it('lists backends and last fallback', async () => {
    render(<OfficeBackendsPanel />);
    await waitFor(() => {
      expect(screen.getByTestId('office-backends-panel')).toBeTruthy();
    });
    expect(screen.getByTestId('office-backends-table')).toBeTruthy();
    expect(screen.getByTestId('office-last-fallback').textContent).toMatch(
      /最近回落/,
    );
    expect(
      screen.getByTestId('office-backend-row-office-builtin:office.render.pptx'),
    ).toBeTruthy();
  });

  it('switches fallback to strict', async () => {
    render(<OfficeBackendsPanel />);
    await waitFor(() => screen.getByTestId('office-fallback-strict'));
    fireEvent.click(screen.getByTestId('office-fallback-strict'));
    await waitFor(() => {
      expect(patchOfficeBackendGlobal).toHaveBeenCalledWith({
        fallback: 'strict',
      });
    });
  });

  it('toggles backend enabled', async () => {
    render(<OfficeBackendsPanel />);
    const id = 'office-enabled-office-cli:office.render.pptx';
    await waitFor(() => screen.getByTestId(id));
    fireEvent.click(screen.getByTestId(id));
    await waitFor(() => {
      expect(patchOfficeBackend).toHaveBeenCalledWith(
        'office-cli:office.render.pptx',
        { enabled: false },
      );
    });
  });
});
