/**
 * mobile/app/_layout.tsx — providers + auth gate.
 * Redirects to /login when there's no token, and into the app once signed in.
 */
import { useEffect, type ReactNode } from 'react';
import { ActivityIndicator, Pressable, View } from 'react-native';
import { Stack, useRouter, useSegments } from 'expo-router';
import { Feather } from '@expo/vector-icons';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { QueryClientProvider } from '@tanstack/react-query';
import { queryClient } from '../lib/queryClient';
import { SessionProvider, useSession } from '../contexts/SessionContext';
import { useOtaUpdates } from '../hooks/useOtaUpdates';
// Register the background location task at app ENTRY (side-effect import), so
// TaskManager.defineTask has run before iOS cold-launches the app in the
// background to deliver a queued location — otherwise screen-off updates drop.
import '../location/backgroundLocation';

/** Gear button in the home header → the Settings screen. */
function SettingsButton() {
  const router = useRouter();
  return (
    <Pressable onPress={() => router.push('/settings')} hitSlop={12} style={{ paddingHorizontal: 4 }}>
      <Feather name="settings" size={22} color="#1a365d" />
    </Pressable>
  );
}

function AuthGate({ children }: { children: ReactNode }) {
  const { token, isLoading } = useSession();
  const segments = useSegments();
  const router = useRouter();
  useOtaUpdates();   // apply EAS Updates in one relaunch (prompt to restart)

  useEffect(() => {
    if (isLoading) return;
    const inLogin = segments[0] === 'login';
    if (!token && !inLogin) router.replace('/login');
    else if (token && inLogin) router.replace('/');
  }, [token, isLoading, segments, router]);

  if (isLoading) {
    return (
      <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center' }}>
        <ActivityIndicator />
      </View>
    );
  }
  return <>{children}</>;
}

export default function RootLayout() {
  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <SafeAreaProvider>
        <QueryClientProvider client={queryClient}>
          <SessionProvider>
            <AuthGate>
              <Stack>
                <Stack.Screen name="login" options={{ headerShown: false }} />
                <Stack.Screen
                  name="index"
                  options={{ title: 'Live Rides', headerRight: () => <SettingsButton /> }}
                />
                <Stack.Screen name="calendar" options={{ title: 'Brevet Calendar' }} />
                <Stack.Screen name="season" options={{ title: 'My Season' }} />
                <Stack.Screen name="settings" options={{ title: 'Settings' }} />
                <Stack.Screen name="ride/[id]" options={{ title: 'Live Map' }} />
                <Stack.Screen name="ride/weather" options={{ title: 'Ride Weather' }} />
              </Stack>
            </AuthGate>
          </SessionProvider>
        </QueryClientProvider>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}
