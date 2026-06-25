/**
 * mobile/lib/queryClient.ts — TanStack Query singleton + AppState focus manager.
 * Foreground transitions trigger refetch of stale queries (live data freshness).
 */
import { AppState, Platform } from 'react-native';
import { QueryClient, focusManager } from '@tanstack/react-query';

if (Platform.OS !== 'web') {
  AppState.addEventListener('change', (status) => {
    focusManager.setFocused(status === 'active');
  });
}

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      retry: 1,
    },
  },
});
