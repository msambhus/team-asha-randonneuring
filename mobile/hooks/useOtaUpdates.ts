/**
 * mobile/hooks/useOtaUpdates.ts — apply EAS Updates in one relaunch.
 *
 * By default expo-updates checks on cold start, downloads in the background, and
 * only swaps the new JS on the NEXT cold start ("kill the app twice"). This hook
 * removes that second kill: it checks/fetches on launch and every foreground, and
 * the moment a downloaded update is pending it offers a one-tap restart that
 * applies it immediately. No-op in dev / Expo Go (Updates.isEnabled is false).
 */
import { useEffect, useRef } from 'react';
import { Alert, AppState } from 'react-native';
import * as Updates from 'expo-updates';

export function useOtaUpdates() {
  const { isUpdatePending } = Updates.useUpdates();
  const prompted = useRef(false);

  // Trigger a check + background fetch on launch and whenever the app returns to
  // the foreground. Fail-soft: offline / no-update just retries next foreground.
  useEffect(() => {
    async function checkAndFetch() {
      if (!Updates.isEnabled) return;
      try {
        const result = await Updates.checkForUpdateAsync();
        if (result.isAvailable) await Updates.fetchUpdateAsync();
      } catch {
        // expected when offline or already up to date
      }
    }
    checkAndFetch();
    const sub = AppState.addEventListener('change', (state) => {
      if (state === 'active') checkAndFetch();
    });
    return () => sub.remove();
  }, []);

  // Once an update is downloaded and pending (this also covers the automatic
  // on-launch download), offer a one-tap restart. Prompt at most once per session.
  useEffect(() => {
    if (!isUpdatePending || prompted.current) return;
    prompted.current = true;
    Alert.alert(
      'Update available',
      'A new version of Team Asha is ready. Restart now to use it?',
      [
        { text: 'Later', style: 'cancel' },
        { text: 'Restart', onPress: () => { void Updates.reloadAsync(); } },
      ],
    );
  }, [isUpdatePending]);
}
