import * as SecureStore from 'expo-secure-store';
import { apiFetch } from './api';

const KEY_PREFIX = 'ta_followed_live_rides';
const MIGRATION_PREFIX = 'ta_followed_live_rides_migrated';

function storageKey(riderId: number): string {
  // Expo SecureStore keys may contain only alphanumerics plus '.', '-' and '_'.
  // A colon worked in the Jest mock but throws on an actual iOS Keychain write.
  return `${KEY_PREFIX}_${riderId}`;
}

export async function getLocalFollowedRideIds(riderId: number): Promise<number[]> {
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
  onLogout: () => void,
): Promise<number[]> {
  const result = await apiFetch<{ ride_ids: number[] }>(
    `/api/me/followed-live-rides/${rideId}`,
    onLogout,
    { method: 'PUT', body: JSON.stringify({ followed }) },
  );
  await SecureStore.setItemAsync(storageKey(riderId), JSON.stringify(result.ride_ids));
  return result.ride_ids;
}

export async function getFollowedRideIds(
  riderId: number,
  onLogout: () => void,
): Promise<number[]> {
  let result = await apiFetch<{ ride_ids: number[] }>(
    '/api/me/followed-live-rides', onLogout,
  );
  // Preserve follows made by the older device-only release exactly once. Once
  // migrated, the server remains authoritative so stale local data cannot
  // resurrect a follow removed from another device or the desktop site.
  const migrationKey = `${MIGRATION_PREFIX}_${riderId}`;
  if (!(await SecureStore.getItemAsync(migrationKey))) {
    const localIds = await getLocalFollowedRideIds(riderId);
    for (const rideId of localIds) {
      if (!result.ride_ids.includes(rideId)) {
        result = await apiFetch<{ ride_ids: number[] }>(
          `/api/me/followed-live-rides/${rideId}`,
          onLogout,
          { method: 'PUT', body: JSON.stringify({ followed: true }) },
        );
      }
    }
    await SecureStore.setItemAsync(migrationKey, '1');
  }
  // Keep a local snapshot for safe one-version rollback; the server is authoritative.
  await SecureStore.setItemAsync(storageKey(riderId), JSON.stringify(result.ride_ids));
  return result.ride_ids;
}
