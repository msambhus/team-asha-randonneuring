// mobile/jest.setup.ts — mocks for native modules used in unit tests.
import '@testing-library/react-native';

jest.mock('expo-secure-store', () => {
  const store: Record<string, string> = {};
  return {
    setItemAsync: jest.fn(async (k: string, v: string) => { store[k] = v; }),
    getItemAsync: jest.fn(async (k: string) => (k in store ? store[k] : null)),
    deleteItemAsync: jest.fn(async (k: string) => { delete store[k]; }),
  };
});

jest.mock('expo-constants', () => ({
  __esModule: true,
  default: {
    expoConfig: {
      extra: {
        apiBase: 'https://example.test',
        googleIosClientId: 'test-ios-client-id.apps.googleusercontent.com',
      },
    },
  },
}));

jest.mock('expo-task-manager', () => ({
  isTaskDefined: jest.fn(() => false),
  defineTask: jest.fn(),
}));

jest.mock('expo-screen-orientation', () => ({
  unlockAsync: jest.fn(async () => undefined),
  lockAsync: jest.fn(async () => undefined),
  OrientationLock: { PORTRAIT_UP: 1 },
}));

jest.mock('expo-location', () => ({
  Accuracy: { High: 4, Fitness: 5 },
  ActivityType: { Fitness: 3 },
  requestForegroundPermissionsAsync: jest.fn(async () => ({ status: 'granted' })),
  requestBackgroundPermissionsAsync: jest.fn(async () => ({ status: 'granted' })),
  startLocationUpdatesAsync: jest.fn(async () => undefined),
  stopLocationUpdatesAsync: jest.fn(async () => undefined),
  hasStartedLocationUpdatesAsync: jest.fn(async () => false),
}));

// Screens read safe-area insets via useSafeAreaInsets(); production wraps them in
// <SafeAreaProvider> (app/_layout.tsx) but unit tests render screens in isolation,
// so provide zero insets instead of requiring a provider in every test.
jest.mock('react-native-safe-area-context', () => {
  const inset = { top: 0, right: 0, bottom: 0, left: 0 };
  const frame = { x: 0, y: 0, width: 390, height: 844 };
  return {
    SafeAreaProvider: ({ children }: { children: unknown }) => children,
    SafeAreaConsumer: ({ children }: { children: (i: typeof inset) => unknown }) => children(inset),
    SafeAreaView: ({ children }: { children: unknown }) => children,
    useSafeAreaInsets: () => inset,
    useSafeAreaFrame: () => frame,
    initialWindowMetrics: { insets: inset, frame },
  };
});
