/**
 * mobile/__tests__/useMySeason.test.tsx — the useMySeason hook fetches
 * /api/me/season and surfaces the parsed season summary. apiFetch + the session
 * are mocked, so no network and no provider tree are needed beyond React Query.
 */
import React from 'react';
import { renderHook, waitFor } from '@testing-library/react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useMySeason } from '../hooks/useMySeason';
import * as api from '../lib/api';
import type { MySeasonResponse } from '../lib/types';

jest.mock('../contexts/SessionContext', () => ({
  useSession: () => ({ token: 'tok-1', signOut: jest.fn() }),
}));

const SEASON: MySeasonResponse = {
  season: { name: '2025-2026' },
  stats: { distance_km: 1200, rides: 5, elevation_ft: 42000 },
  sr: { has_sr: false, distances_done: [200, 300] },
  r12: { months: 8, active: true },
  career: { distance_km: 9000 },
  eddington: { value: 62, badge: { level: 'strong', label: 'Strong', emoji: '💪' } },
};

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe('useMySeason', () => {
  afterEach(() => jest.restoreAllMocks());

  it('requests /api/me/season and returns the parsed summary', async () => {
    const spy = jest.spyOn(api, 'apiFetch').mockResolvedValue(SEASON as never);

    const { result } = renderHook(() => useMySeason(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(spy).toHaveBeenCalledWith('/api/me/season', expect.any(Function));
    expect(result.current.data).toEqual(SEASON);
    expect(result.current.data?.sr.distances_done).toEqual([200, 300]);
    expect(result.current.data?.eddington?.value).toBe(62);
  });
});
