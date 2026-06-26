/**
 * mobile/hooks/useRidePlan.ts — the ride plan (stops + timing) for a ride
 * (GET /api/ride/<id>/plan). Mostly static per ride (the per-stop wind is
 * best-effort), so cached aggressively and not refetched on a poll.
 */
import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '../lib/api';
import { useSession } from '../contexts/SessionContext';
import type { RidePlanResponse } from '../lib/types';

export function useRidePlan(rideId: number, view?: 'base' | 'custom') {
  const { token, signOut } = useSession();
  return useQuery({
    queryKey: ['ride-plan', rideId, view ?? 'auto'],
    enabled: !!token && Number.isFinite(rideId),
    staleTime: 30 * 60_000, // 30m — plan is static; wind is cached server-side
    queryFn: () =>
      apiFetch<RidePlanResponse>(
        `/api/ride/${rideId}/plan${view ? `?view=${view}` : ''}`,
        () => { void signOut(); },
      ),
  });
}
