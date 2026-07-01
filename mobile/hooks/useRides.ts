/**
 * mobile/hooks/useRides.ts — the rider's upcoming rides (GET /api/live/rides).
 */
import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '../lib/api';
import { useSession } from '../contexts/SessionContext';
import type { RidesResponse } from '../lib/types';

export function useRides() {
  const { token, profileComplete, signOut } = useSession();
  return useQuery({
    queryKey: ['rides'],
    // Only fetch once the account has a linked rider profile — otherwise the
    // endpoint 403s (a new/profile-less account). The home screen shows the
    // onboarding view in that case instead of an error.
    enabled: !!token && profileComplete,
    queryFn: () => apiFetch<RidesResponse>('/api/live/rides', () => { void signOut(); }),
    select: (d) => d.rides,
  });
}
