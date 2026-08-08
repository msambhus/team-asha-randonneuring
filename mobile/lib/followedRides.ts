import * as SecureStore from 'expo-secure-store';

const KEY_PREFIX = 'ta_followed_live_rides';

function storageKey(riderId: number): string {
  // Expo SecureStore keys may contain only alphanumerics plus '.', '-' and '_'.
  // A colon worked in the Jest mock but throws on an actual iOS Keychain write.
  return `${KEY_PREFIX}_${riderId}`;
}

export async function getFollowedRideIds(riderId: number): Promise<number[]> {
  const raw = await SecureStore.getItemAsync(storageKey(riderId));
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return [...new Set(parsed
      .map((id) => Number(id))
      .filter((id) => Number.isInteger(id) && id > 0))];
  } catch {
    return [];
  }
}

export async function setRideFollowed(
  riderId: number,
  rideId: number,
  followed: boolean,
): Promise<number[]> {
  const current = await getFollowedRideIds(riderId);
  const next = followed
    ? [...new Set([...current, rideId])]
    : current.filter((id) => id !== rideId);
  await SecureStore.setItemAsync(storageKey(riderId), JSON.stringify(next));
  return next;
}
