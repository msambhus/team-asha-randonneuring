import { useQuery } from '@tanstack/react-query';
import { useSession } from '../contexts/SessionContext';
import { apiFetch } from '../lib/api';
import type { PublicRiderResponse, PublicRidersResponse } from '../lib/types';

export function normalizeRusaId(value?: string | string[]): string | undefined {
  const candidate = Array.isArray(value) ? value[0] : value;
  return candidate && /^\d+$/.test(candidate) ? candidate : undefined;
}

export function publicRiderPath(rusaId?: string | string[]): string | undefined {
  const normalized = normalizeRusaId(rusaId);
  return normalized ? `/api/riders/${encodeURIComponent(normalized)}` : undefined;
}

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
    enabled: !!token && !!publicRiderPath(rusaId),
    staleTime: 5 * 60_000,
    queryFn: () => apiFetch<PublicRiderResponse>(
      publicRiderPath(rusaId)!, () => { void signOut(); },
    ),
  });
}
