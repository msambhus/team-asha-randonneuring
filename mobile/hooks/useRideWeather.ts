/**
 * mobile/hooks/useRideWeather.ts — the weather forecast for a ride's route
 * (GET /api/ride/<id>/weather). Mirrors the web /weather page: table + wind map
 * + charts come from one payload. The backend caches the Open-Meteo fetch ~1h
 * and the fetch is slow, so cache aggressively and don't refetch on a poll.
 */
import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '../lib/api';
import { useSession } from '../contexts/SessionContext';
import type { RideWeatherResponse } from '../lib/types';

export function useRideWeather(rideId: number) {
  const { token, signOut } = useSession();
  return useQuery({
    queryKey: ['ride-weather', rideId],
    enabled: !!token && Number.isFinite(rideId),
    staleTime: 30 * 60_000, // 30m — forecast moves slowly; backend caches the fetch
    queryFn: () =>
      apiFetch<RideWeatherResponse>(`/api/ride/${rideId}/weather`, () => { void signOut(); }),
  });
}
