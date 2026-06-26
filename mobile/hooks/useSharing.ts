/**
 * mobile/hooks/useSharing.ts — the rider's GLOBAL live-sharing opt-in flag
 * (GET/POST /api/live/sharing). This is the account-level consent gate: the
 * per-ride "Share my location" beacon only streams while this is on. The toggle
 * for it lives on the Settings screen; ride screens read it to gate sharing.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiFetch } from '../lib/api';
import { useSession } from '../contexts/SessionContext';

const KEY = ['live-sharing'];

export function useSharing() {
  const { token, signOut } = useSession();
  const qc = useQueryClient();

  const query = useQuery({
    queryKey: KEY,
    enabled: !!token,
    staleTime: 30_000,
    queryFn: () =>
      apiFetch<{ enabled: boolean }>('/api/live/sharing', () => { void signOut(); }),
    select: (d) => d.enabled,
  });

  const mutation = useMutation({
    mutationFn: (enabled: boolean) =>
      apiFetch<{ ok: boolean; enabled: boolean }>(
        '/api/live/sharing',
        () => { void signOut(); },
        { method: 'POST', body: JSON.stringify({ enabled }) },
      ),
    // Reflect the confirmed server value immediately (select turns it boolean).
    onSuccess: (res) => qc.setQueryData(KEY, { enabled: res.enabled }),
  });

  return {
    enabled: query.data,            // boolean | undefined (undefined = loading)
    isLoading: query.isLoading,
    isError: query.isError,
    refetch: query.refetch,
    setEnabled: mutation.mutateAsync,
    saving: mutation.isPending,
  };
}
