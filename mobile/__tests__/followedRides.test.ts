import * as SecureStore from 'expo-secure-store';
import * as api from '../lib/api';
import { getFollowedRideIds, getLocalFollowedRideIds, setRideFollowed } from '../lib/followedRides';

describe('followed live rides', () => {
  afterEach(() => jest.restoreAllMocks());

  it('reads account follows from the server and keeps a rollback snapshot', async () => {
    const fetch = jest.spyOn(api, 'apiFetch').mockResolvedValue({ ride_ids: [101, 102] });
    await expect(getFollowedRideIds(14680, jest.fn())).resolves.toEqual([101, 102]);
    expect(fetch).toHaveBeenCalledWith('/api/me/followed-live-rides', expect.any(Function));
    await expect(getLocalFollowedRideIds(14680)).resolves.toEqual([101, 102]);
  });

  it('migrates device-only follows to the account once', async () => {
    await SecureStore.setItemAsync('ta_followed_live_rides_77', '[201]');
    const fetch = jest.spyOn(api, 'apiFetch')
      .mockResolvedValueOnce({ ride_ids: [] })
      .mockResolvedValueOnce({ ride_ids: [201] })
      .mockResolvedValueOnce({ ride_ids: [] });
    await expect(getFollowedRideIds(77, jest.fn())).resolves.toEqual([201]);
    await expect(getFollowedRideIds(77, jest.fn())).resolves.toEqual([]);
    expect(fetch).toHaveBeenCalledTimes(3);
  });

  it('updates the server without changing signup state', async () => {
    const fetch = jest.spyOn(api, 'apiFetch').mockResolvedValue({ ride_ids: [102] });
    await expect(setRideFollowed(14680, 101, false, jest.fn())).resolves.toEqual([102]);
    expect(fetch).toHaveBeenCalledWith(
      '/api/me/followed-live-rides/101', expect.any(Function),
      { method: 'PUT', body: JSON.stringify({ followed: false }) },
    );
  });

  it('recovers safely from malformed or duplicate stored values', async () => {
    await SecureStore.setItemAsync('ta_followed_live_rides_7', '[4,"4",null,-2,"bad"]');
    await expect(getLocalFollowedRideIds(7)).resolves.toEqual([4]);
    await SecureStore.setItemAsync('ta_followed_live_rides_8', 'not json');
    await expect(getLocalFollowedRideIds(8)).resolves.toEqual([]);
  });
});
