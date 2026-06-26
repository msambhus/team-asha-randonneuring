/**
 * mobile/hooks/useAllowRotation.ts — let a single screen rotate to landscape.
 *
 * The app is portrait by default (locked at the root in app/_layout.tsx). A screen
 * that benefits from the extra width — the ride plan + weather tables/charts/map —
 * calls this hook to unlock rotation while it is focused, and re-locks portrait when
 * the user navigates away, so the rest of the app stays portrait. OTA-safe to use
 * only once expo-screen-orientation is in the native build (1.0.2+).
 */
import { useCallback } from 'react';
import { useFocusEffect } from 'expo-router';
import * as ScreenOrientation from 'expo-screen-orientation';

export function useAllowRotation() {
  useFocusEffect(
    useCallback(() => {
      ScreenOrientation.unlockAsync();
      return () => {
        ScreenOrientation.lockAsync(ScreenOrientation.OrientationLock.PORTRAIT_UP);
      };
    }, []),
  );
}
