/**
 * mobile/location/backgroundLocation.ts
 *
 * The reason this app exists: keep posting the rider's GPS to the club's live
 * beacon **with the screen off / app backgrounded**. Uses expo-location's
 * background updates + a TaskManager task that runs in a headless JS context.
 *
 * Because the task runs outside the React tree, it reads the auth token and the
 * active ride id from SecureStore (not from component state). The pieces that
 * don't need native modules (the request payload + URL + headers) are factored
 * into pure functions so they can be unit-tested.
 *
 * NOTE: background location requires an EAS dev build (NOT Expo Go) and the iOS
 * "Always" permission. Cadence is OS-governed (~best effort), not guaranteed.
 */
import * as Location from 'expo-location';
import * as TaskManager from 'expo-task-manager';
import * as SecureStore from 'expo-secure-store';
import { getToken, authHeaders, apiUrl } from '../lib/api';

export const LOCATION_TASK = 'team-asha-live-location';
const RIDE_KEY = 'ta_active_ride_id';
const LOW_POWER_KEY = 'ta_low_power';

/** The expo-location options for a given power profile. Pure + exported so the
 *  profiles are inspectable/testable. Low power is the big battery saver: coarser
 *  GPS (~100m vs continuous high-accuracy), a slower cadence, and letting iOS
 *  pause updates while the rider is stopped. */
export function locationOptions(lowPower: boolean): Location.LocationTaskOptions {
  const foregroundService = {
    notificationTitle: lowPower
      ? 'Team Asha — sharing (low power)'
      : 'Team Asha — sharing your location',
    notificationBody: 'Tap to stop sharing.',
  };
  if (lowPower) {
    return {
      accuracy: Location.Accuracy.Balanced,   // ~100m — the dominant battery win vs High
      activityType: Location.ActivityType.Fitness,
      // ~2-minute cadence. Both thresholds are raised: updates fire on whichever
      // trips first, so the distance gate must also be wide or it dominates while
      // moving (80m ≈ every ~15s at speed). iOS treats these as hints.
      timeInterval: 120_000,                   // ~2 min
      distanceInterval: 250,                    // or every 250m, whichever first
      deferredUpdatesInterval: 120_000,         // let iOS batch + sleep between deliveries
      pausesUpdatesAutomatically: true,        // let iOS sleep GPS at controls/stops
      showsBackgroundLocationIndicator: true,
      foregroundService,
    };
  }
  return {
    accuracy: Location.Accuracy.High,
    activityType: Location.ActivityType.Fitness,
    timeInterval: 30_000,           // ~30s (iOS treats as a hint)
    distanceInterval: 25,            // or every 25m, whichever first
    deferredUpdatesInterval: 30_000,
    pausesUpdatesAutomatically: false,
    showsBackgroundLocationIndicator: true,
    foregroundService,
  };
}

/** Is low-power sharing enabled? Persisted (default off) so the headless task and
 *  startSharing can read it outside the React tree. */
export async function getLowPower(): Promise<boolean> {
  return (await SecureStore.getItemAsync(LOW_POWER_KEY).catch(() => null)) === '1';
}

/** Set the low-power preference. Applies immediately to an active beacon by
 *  restarting updates with the new profile (the active ride id is untouched). */
export async function setLowPower(on: boolean): Promise<void> {
  await SecureStore.setItemAsync(LOW_POWER_KEY, on ? '1' : '0');
  const started = await Location.hasStartedLocationUpdatesAsync(LOCATION_TASK).catch(() => false);
  if (started) await startUpdates(on);
}

/** Restart background updates with the given power profile (idempotent). */
async function startUpdates(lowPower: boolean): Promise<void> {
  const already = await Location.hasStartedLocationUpdatesAsync(LOCATION_TASK).catch(() => false);
  if (already) await Location.stopLocationUpdatesAsync(LOCATION_TASK).catch(() => undefined);
  await Location.startLocationUpdatesAsync(LOCATION_TASK, locationOptions(lowPower));
}

export interface BeaconPayload {
  ride_id: number;
  lat: number;
  lng: number;
  accuracy: number | null;
  speed: number | null;
}

/** Pure: turn an expo-location reading + ride id into the beacon body the
 *  backend expects (POST /api/live/beacon). */
export function buildBeaconPayload(
  loc: Location.LocationObject,
  rideId: number,
): BeaconPayload {
  return {
    ride_id: rideId,
    lat: loc.coords.latitude,
    lng: loc.coords.longitude,
    accuracy: loc.coords.accuracy ?? null,
    speed: loc.coords.speed != null && loc.coords.speed >= 0 ? loc.coords.speed : null,
  };
}

/** Pure: the fetch args for one beacon post. Separated for testing. */
export function beaconRequest(
  payload: BeaconPayload,
  token: string | null,
): { url: string; init: RequestInit } {
  return {
    url: apiUrl('/api/live/beacon'),
    init: {
      method: 'POST',
      headers: authHeaders(token),
      body: JSON.stringify(payload),
    },
  };
}

/** Post the most recent location of a batch to the beacon. Fail-soft. */
export async function postLocations(locations: Location.LocationObject[]): Promise<void> {
  if (!locations.length) return;
  const rideRaw = await SecureStore.getItemAsync(RIDE_KEY);
  const rideId = rideRaw ? parseInt(rideRaw, 10) : NaN;
  if (!rideId || Number.isNaN(rideId)) return;
  const token = await getToken();
  if (!token) return;

  const latest = locations[locations.length - 1];
  const { url, init } = beaconRequest(buildBeaconPayload(latest, rideId), token);
  try {
    await fetch(url, init);
  } catch {
    // Network blips are expected on a ride; the next update will retry.
  }
}

// Register the background task once at module load (idempotent).
if (!TaskManager.isTaskDefined(LOCATION_TASK)) {
  TaskManager.defineTask(LOCATION_TASK, async ({ data, error }) => {
    if (error) return;
    const locations = (data as { locations?: Location.LocationObject[] })?.locations ?? [];
    await postLocations(locations);
  });
}

/** Start sharing for a ride: request permissions, persist the ride id, and begin
 *  background location updates. Returns an error string on failure, else null. */
export async function startSharing(rideId: number): Promise<string | null> {
  const fg = await Location.requestForegroundPermissionsAsync();
  if (fg.status !== 'granted') return 'Location permission is required to share.';
  const bg = await Location.requestBackgroundPermissionsAsync();
  if (bg.status !== 'granted') {
    return 'Allow "Always" location so sharing keeps working with the screen off.';
  }

  await SecureStore.setItemAsync(RIDE_KEY, String(rideId));
  await startUpdates(await getLowPower());
  return null;
}

/** Stop sharing: end background updates and clear the active ride. */
export async function stopSharing(): Promise<void> {
  const started = await Location.hasStartedLocationUpdatesAsync(LOCATION_TASK).catch(() => false);
  if (started) await Location.stopLocationUpdatesAsync(LOCATION_TASK).catch(() => undefined);
  await SecureStore.deleteItemAsync(RIDE_KEY).catch(() => undefined);
}

/** Is background sharing currently running? */
export async function isSharing(): Promise<boolean> {
  return Location.hasStartedLocationUpdatesAsync(LOCATION_TASK).catch(() => false);
}

/** The ride id currently being shared, or null if not sharing. Lets the rides
 *  list badge the ride whose beacon is live (only one ride shares at a time). */
export async function getSharingRideId(): Promise<number | null> {
  const started = await Location.hasStartedLocationUpdatesAsync(LOCATION_TASK).catch(() => false);
  if (!started) return null;
  const raw = await SecureStore.getItemAsync(RIDE_KEY);
  const id = raw ? parseInt(raw, 10) : NaN;
  return Number.isNaN(id) ? null : id;
}
