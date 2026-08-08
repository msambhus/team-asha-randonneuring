/**
 * mobile/hooks/useMySeason.ts — the signed-in rider's season progress
 * (GET /api/me/season). Mirrors useCalendar: token-gated, 401 → sign out.
 */
import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '../lib/api';
import { useSession } from '../contexts/SessionContext';
import type { MySeasonResponse } from '../lib/types';

export function useMySeason(seasonId?: number | null) {
  const { token, signOut } = useSession();
  return useQuery({
    queryKey: ['my-season', seasonId ?? 'current'],
    enabled: !!token,
    staleTime: 5 * 60_000,
    queryFn: () => apiFetch<MySeasonResponse>(
      `/api/me/season${seasonId ? `?season_id=${seasonId}` : ''}`,
      () => { void signOut(); },
    ),
  });
}
