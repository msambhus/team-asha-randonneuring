/**
 * mobile/hooks/useRideRoute.ts — the RWGPS route polyline for a ride
 * (GET /api/ride/<id>/route). Static per ride, so cached aggressively and not
 * refetched on a poll. Returns [{latitude,longitude}] ready for <Polyline>.
 */
import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '../lib/api';
import { useSession } from '../contexts/SessionContext';
import type { RideRouteResponse } from '../lib/types';

export function useRideRoute(rideId: number) {
  const { token, signOut } = useSession();
  return useQuery({
    queryKey: ['ride-route', rideId],
    enabled: !!token && Number.isFinite(rideId),
    staleTime: 60 * 60_000, // 1h — route geometry is static per ride
    queryFn: () => apiFetch<RideRouteResponse>(`/api/ride/${rideId}/route`, () => { void signOut(); }),
    // [[lng,lat],...] → map coords
    select: (d) => (d.polyline ?? []).map(([lng, lat]) => ({ latitude: lat, longitude: lng })),
  });
}
