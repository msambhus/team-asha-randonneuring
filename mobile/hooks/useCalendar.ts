/**
 * mobile/hooks/useCalendar.ts — Team Asha's upcoming brevets (GET /api/calendar).
 */
import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '../lib/api';
import { useSession } from '../contexts/SessionContext';
import type { CalendarResponse } from '../lib/types';

export function useCalendar() {
  const { token, signOut } = useSession();
  return useQuery({
    queryKey: ['calendar'],
    enabled: !!token,
    staleTime: 0,
    refetchOnMount: 'always',
    queryFn: () => apiFetch<CalendarResponse>('/api/calendar', () => { void signOut(); }),
    select: (d) => d.rides,
  });
}
