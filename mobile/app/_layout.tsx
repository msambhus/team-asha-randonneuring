/**
 * mobile/app/_layout.tsx — providers + auth gate.
 * Redirects to /login when there's no token, and into the app once signed in.
 */
import { useEffect, type ReactNode } from 'react';
import { ActivityIndicator, View } from 'react-native';
import { Stack, useRouter, useSegments } from 'expo-router';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { QueryClientProvider } from '@tanstack/react-query';
import { queryClient } from '../lib/queryClient';
import { SessionProvider, useSession } from '../contexts/SessionContext';
// Register the background location task at app ENTRY (side-effect import), so
// TaskManager.defineTask has run before iOS cold-launches the app in the
// background to deliver a queued location — otherwise screen-off updates drop.
import '../location/backgroundLocation';

function AuthGate({ children }: { children: ReactNode }) {
  const { token, isLoading } = useSession();
  const segments = useSegments();
  const router = useRouter();

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
                <Stack.Screen name="index" options={{ title: 'Live Rides' }} />
                <Stack.Screen name="calendar" options={{ title: 'Brevet Calendar' }} />
                <Stack.Screen name="ride/[id]" options={{ title: 'Live Map' }} />
              </Stack>
            </AuthGate>
          </SessionProvider>
        </QueryClientProvider>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}
