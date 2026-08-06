import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSession } from '../contexts/SessionContext';
import { getFollowedRideIds, setRideFollowed } from '../lib/followedRides';

export function useFollowedRides() {
  const { riderId } = useSession();
  const queryClient = useQueryClient();
  const queryKey = ['followed-live-rides', riderId] as const;
  const query = useQuery({
    queryKey,
    enabled: riderId != null,
    staleTime: Infinity,
    queryFn: () => getFollowedRideIds(riderId as number),
  });
  const mutation = useMutation({
    mutationFn: ({ rideId, followed }: { rideId: number; followed: boolean }) => {
      if (riderId == null) throw new Error('Complete your profile to follow rides');
      return setRideFollowed(riderId, rideId, followed);
    },
    onSuccess: (ids) => queryClient.setQueryData(queryKey, ids),
  });
  return {
    followedRideIds: query.data ?? [],
    isLoading: query.isLoading,
    setFollowed: mutation.mutate,
    isPending: mutation.isPending,
    pendingRideId: mutation.variables?.rideId ?? null,
  };
}
