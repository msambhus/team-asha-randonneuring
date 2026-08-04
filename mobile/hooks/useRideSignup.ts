import { useMutation, useQueryClient } from '@tanstack/react-query';
import { apiFetch } from '../lib/api';
import { useSession } from '../contexts/SessionContext';

export function useRideSignup() {
  const queryClient = useQueryClient();
  const { signOut } = useSession();
  return useMutation({
    mutationFn: ({ rideId, going }: { rideId: number; going: boolean }) =>
      apiFetch<{ success: boolean; status: string | null }>(
        `/api/calendar/${rideId}/status`,
        () => { void signOut(); },
        { method: 'POST', body: JSON.stringify({ status: going ? 'GOING' : 'NONE' }) },
      ),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['calendar'] }),
        queryClient.invalidateQueries({ queryKey: ['rides'] }),
      ]);
    },
  });
}
