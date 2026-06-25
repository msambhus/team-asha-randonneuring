/**
 * mobile/__tests__/backgroundLocation.test.ts — the pure beacon payload/request
 * builders that turn a GPS reading into the POST /api/live/beacon call.
 */
import * as SecureStore from 'expo-secure-store';
import { buildBeaconPayload, beaconRequest, postLocations } from '../location/backgroundLocation';
import { storeToken, deleteToken } from '../lib/api';

const loc = (lat: number, lng: number, accuracy: number | null, speed: number | null) =>
  ({ coords: { latitude: lat, longitude: lng, accuracy, speed } } as never);

describe('buildBeaconPayload', () => {
  it('maps coords + ride id into the beacon body', () => {
    const p = buildBeaconPayload(loc(37.8, -122.2, 5, 6.1), 42);
    expect(p).toEqual({ ride_id: 42, lat: 37.8, lng: -122.2, accuracy: 5, speed: 6.1 });
  });

  it('normalises a negative/invalid speed to null', () => {
    // iOS reports speed = -1 when unknown.
    expect(buildBeaconPayload(loc(1, 2, null, -1), 9).speed).toBeNull();
  });
});

describe('beaconRequest', () => {
  it('builds an authenticated POST to the beacon endpoint', () => {
    const payload = { ride_id: 42, lat: 1, lng: 2, accuracy: null, speed: null };
    const { url, init } = beaconRequest(payload, 'tok-9');
    expect(url).toMatch(/\/api\/live\/beacon$/);
    expect(init.method).toBe('POST');
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok-9');
    expect(JSON.parse(init.body as string)).toEqual(payload);
  });
});

describe('postLocations (headless task body)', () => {
  afterEach(async () => {
    await deleteToken();
    await SecureStore.deleteItemAsync('ta_active_ride_id');
    jest.restoreAllMocks();
  });

  it('does nothing when there is no active ride id', async () => {
    await storeToken('tok');
    const fetchMock = jest.spyOn(global, 'fetch' as never);
    await postLocations([loc(1, 2, 3, 4)]);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('does nothing when there is no token', async () => {
    await SecureStore.setItemAsync('ta_active_ride_id', '42');
    const fetchMock = jest.spyOn(global, 'fetch' as never);
    await postLocations([loc(1, 2, 3, 4)]);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('posts the latest location to the beacon when ride + token present', async () => {
    await SecureStore.setItemAsync('ta_active_ride_id', '42');
    await storeToken('tok');
    const fetchMock = jest.spyOn(global, 'fetch' as never).mockResolvedValue({ ok: true } as never);
    await postLocations([loc(1, 2, 3, 4), loc(37.8, -122.2, 5, 6.1)]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/api\/live\/beacon$/);
    expect(JSON.parse(init.body as string)).toEqual({ ride_id: 42, lat: 37.8, lng: -122.2, accuracy: 5, speed: 6.1 });
  });
});
