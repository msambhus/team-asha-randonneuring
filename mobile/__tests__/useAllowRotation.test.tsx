/**
 * mobile/__tests__/useAllowRotation.test.tsx — the hook unlocks rotation while the
 * screen is focused and re-locks portrait on blur. expo-screen-orientation is mocked
 * globally (jest.setup.ts); expo-router's useFocusEffect is captured here.
 */
import { renderHook } from '@testing-library/react-native';
import * as ScreenOrientation from 'expo-screen-orientation';
import { useFocusEffect } from 'expo-router';
import { useAllowRotation } from '../hooks/useAllowRotation';

jest.mock('expo-router', () => ({ useFocusEffect: jest.fn() }));

describe('useAllowRotation', () => {
  afterEach(() => jest.clearAllMocks());

  it('unlocks on focus and re-locks portrait on blur', () => {
    renderHook(() => useAllowRotation());

    // useFocusEffect received the effect; run it (focus) then its cleanup (blur).
    const effect = (useFocusEffect as jest.Mock).mock.calls[0][0] as () => () => void;
    const cleanup = effect();
    expect(ScreenOrientation.unlockAsync).toHaveBeenCalledTimes(1);
    expect(ScreenOrientation.lockAsync).not.toHaveBeenCalled();

    cleanup();
    expect(ScreenOrientation.lockAsync).toHaveBeenCalledWith(
      ScreenOrientation.OrientationLock.PORTRAIT_UP,
    );
  });
});
