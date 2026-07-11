/**
 * mobile/hooks/useLivePositions.ts — poll a ride's live rider positions.
 * Refetches every ~20s while the screen is mounted (matches the web map cadence).
 */
import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '../lib/api';
import { useSession } from '../contexts/SessionContext';
import type { PositionsResponse, LivePositionsResult } from '../lib/types';

export function useLivePositions(rideId: number) {
  const { token, signOut } = useSession();
  return useQuery({
    queryKey: ['positions', rideId],
    enabled: !!token && !!rideId,
    refetchInterval: 20_000,
    queryFn: () =>
      apiFetch<PositionsResponse>(
        `/api/live/positions?ride_id=${encodeURIComponent(rideId)}`,
        () => { void signOut(); },
      ),
    // Preserve BOTH the positions array and the top-level chart_data — a
    // positions-only projection would drop chart_data before any component sees
    // it, so the live charts would never render on the real screen.
    select: (d): LivePositionsResult => ({
      positions: d.positions,
      chart_data: d.chart_data ?? null,
    }),
  });
}
