import { useQuery } from '@tanstack/react-query';
import { useSession } from '../contexts/SessionContext';
import { apiFetch } from '../lib/api';
import type { TrainingLogResponse } from '../lib/types';

export function useTrainingLog(month: string) {
  const { token, profileComplete, signOut } = useSession();
  return useQuery({
    queryKey: ['training-log', month],
    enabled: !!token && profileComplete,
    staleTime: 5 * 60_000,
    queryFn: () => apiFetch<TrainingLogResponse>(
      `/api/me/training-log?month=${month}`, () => { void signOut(); },
    ),
  });
}
