/**
 * mobile/hooks/useLivePositions.ts — poll a ride's live rider positions.
 * Refetches every 30s while the screen is mounted (matches the web map cadence).
 */
import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '../lib/api';
import { useSession } from '../contexts/SessionContext';
import type { PositionsResponse, LivePositionsResult, LivePlanId } from '../lib/types';

export function useLivePositions(rideId: number, planId?: LivePlanId | null) {
  const { token, signOut } = useSession();
  return useQuery({
    // planId is part of the key so switching plans re-polls (and re-grades everyone).
    queryKey: ['positions', rideId, planId ?? 'base'],
    enabled: !!token && !!rideId,
    refetchInterval: 30_000,
    queryFn: () =>
      apiFetch<PositionsResponse>(
        `/api/live/positions?ride_id=${encodeURIComponent(rideId)}` +
          (planId ? `&plan_id=${encodeURIComponent(planId)}` : ''),
        () => { void signOut(); },
      ),
    // Preserve the positions array, top-level chart_data, AND the plan-selector +
    // upcoming-controls payload — a positions-only projection would drop them before
    // any component sees them, so the selector / charts would never render.
    select: (d): LivePositionsResult => ({
      positions: d.positions,
      chart_data: d.chart_data ?? null,
      plans: d.plans ?? [],
      selected_plan_id: d.selected_plan_id ?? null,
      upcoming_controls: d.upcoming_controls ?? [],
      plan_snapshot: d.plan_snapshot ?? null,
    }),
  });
}
