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

jest.mock('expo-location', () => ({
  Accuracy: { High: 4, Fitness: 5 },
  ActivityType: { Fitness: 3 },
  requestForegroundPermissionsAsync: jest.fn(async () => ({ status: 'granted' })),
  requestBackgroundPermissionsAsync: jest.fn(async () => ({ status: 'granted' })),
  startLocationUpdatesAsync: jest.fn(async () => undefined),
  stopLocationUpdatesAsync: jest.fn(async () => undefined),
  hasStartedLocationUpdatesAsync: jest.fn(async () => false),
}));
