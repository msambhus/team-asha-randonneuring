/**
 * mobile/hooks/useRides.ts — the rider's upcoming rides (GET /api/live/rides).
 */
import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '../lib/api';
import { useSession } from '../contexts/SessionContext';
import type { RidesResponse } from '../lib/types';

export function useRides() {
  const { token, signOut } = useSession();
  return useQuery({
    queryKey: ['rides'],
    enabled: !!token,
    queryFn: () => apiFetch<RidesResponse>('/api/live/rides', () => { void signOut(); }),
    select: (d) => d.rides,
  });
}
