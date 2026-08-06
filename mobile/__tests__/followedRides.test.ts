import * as SecureStore from 'expo-secure-store';
import { getFollowedRideIds, setRideFollowed } from '../lib/followedRides';

describe('followed live rides', () => {
  it('persists follows per rider and removes them independently of signup state', async () => {
    await expect(getFollowedRideIds(14680)).resolves.toEqual([]);
    await expect(setRideFollowed(14680, 101, true)).resolves.toEqual([101]);
    await expect(setRideFollowed(14680, 102, true)).resolves.toEqual([101, 102]);
    await expect(setRideFollowed(14680, 101, false)).resolves.toEqual([102]);
    await expect(getFollowedRideIds(99999)).resolves.toEqual([]);
  });

  it('recovers safely from malformed or duplicate stored values', async () => {
    await SecureStore.setItemAsync('ta_followed_live_rides:7', '[4,"4",null,-2,"bad"]');
    await expect(getFollowedRideIds(7)).resolves.toEqual([4]);
    await SecureStore.setItemAsync('ta_followed_live_rides:8', 'not json');
    await expect(getFollowedRideIds(8)).resolves.toEqual([]);
  });
});
