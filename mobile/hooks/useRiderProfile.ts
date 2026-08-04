import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '../lib/api';
import { useSession } from '../contexts/SessionContext';
import type { RiderProfileResponse } from '../lib/types';

export function useRiderProfile() {
  const { token, profileComplete, signOut } = useSession();
  return useQuery({
    queryKey: ['rider-profile'],
    enabled: !!token && profileComplete,
    staleTime: 5 * 60_000,
    queryFn: () => apiFetch<RiderProfileResponse>(
      '/api/me/profile', () => { void signOut(); },
    ),
  });
}
