import { useQuery } from '@tanstack/react-query';
import { useSession } from '../contexts/SessionContext';
import { apiFetch } from '../lib/api';
import type { PublicRiderResponse, PublicRidersResponse } from '../lib/types';

export function usePublicRiders(season?: string | null) {
  const { token, signOut } = useSession();
  const suffix = season ? `?season=${encodeURIComponent(season)}` : '';
  return useQuery({
    queryKey: ['public-riders', season ?? 'current'],
    enabled: !!token,
    staleTime: 5 * 60_000,
    queryFn: () => apiFetch<PublicRidersResponse>(
      `/api/riders${suffix}`, () => { void signOut(); },
    ),
  });
}

export function usePublicRider(rusaId?: string) {
  const { token, signOut } = useSession();
  return useQuery({
    queryKey: ['public-rider', rusaId],
    enabled: !!token && !!rusaId,
    staleTime: 5 * 60_000,
    queryFn: () => apiFetch<PublicRiderResponse>(
      `/api/riders/${rusaId}`, () => { void signOut(); },
    ),
  });
}
