/**
 * mobile/__tests__/useLivePositions.test.tsx — the REAL useLivePositions hook must
 * preserve the top-level chart_data end-to-end (network → hook), not just the
 * positions array. This is the non-substitutable proof that the hook's `select`
 * doesn't drop chart_data (a positions-only projection would). Only the network
 * boundary (apiFetch) is mocked; the hook itself is exercised unmocked.
 */
import React from 'react';
import { renderHook, waitFor } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useLivePositions } from '../hooks/useLivePositions';
import * as api from '../lib/api';
import type { PositionsResponse } from '../lib/types';

jest.mock('../contexts/SessionContext', () => ({
  useSession: () => ({ token: 'tok-1', signOut: jest.fn() }),
}));

const RESPONSE: PositionsResponse = {
  ride_id: 5,
  positions: [{
    rider_id: 7, name: 'Asha', lat: 37, lng: -122, status: 'GOING', color: '#16a34a',
    recorded_at: '2026-06-23T14:00:00Z', minutes_ago: 1, stale: false, source: 'garmin',
    telemetry: null, trail: null,
  }],
  stale_after_minutes: 10,
  server_time: '2026-06-23T14:01:00Z',
  chart_data: {
    labels: [0, 1, 2], elevation_ft: [100, 120, 90],
    headwind_mph: [5, 4, 3], temperature_f: [60, 61, 62],
  },
};

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe('useLivePositions', () => {
  afterEach(() => jest.restoreAllMocks());

  it('preserves top-level chart_data alongside positions', async () => {
    const spy = jest.spyOn(api, 'apiFetch').mockResolvedValue(RESPONSE as never);

    const { result } = renderHook(() => useLivePositions(5), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(spy).toHaveBeenCalledWith(
      '/api/live/positions?ride_id=5', expect.any(Function));
    // The hook must expose BOTH positions and chart_data — the trap is a `select`
    // that returns only d.positions and silently discards chart_data.
    expect(result.current.data?.positions).toHaveLength(1);
    expect(result.current.data?.chart_data).not.toBeNull();
    expect(result.current.data?.chart_data?.labels).toEqual([0, 1, 2]);
    expect(result.current.data?.chart_data?.elevation_ft).toEqual([100, 120, 90]);
  });

  it('defaults chart_data to null when an old backend omits it', async () => {
    const noChart: PositionsResponse = { ...RESPONSE, chart_data: undefined };
    jest.spyOn(api, 'apiFetch').mockResolvedValue(noChart as never);

    const { result } = renderHook(() => useLivePositions(5), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.chart_data).toBeNull();     // null-safe, not undefined
    expect(result.current.data?.positions).toHaveLength(1);
  });
});
