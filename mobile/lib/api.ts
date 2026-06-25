/**
 * mobile/lib/api.ts
 * Token store (expo-secure-store + in-memory cache) and a typed fetch wrapper.
 * Mirrors the thrrive mobile-app pattern: Bearer header, 401 → clear + onLogout.
 */
import * as SecureStore from 'expo-secure-store';
import { API_BASE } from './config';

const TOKEN_KEY = 'ta_token';

// In-memory cache avoids repeated SecureStore reads. null = absent/unloaded.
let cachedToken: string | null = null;

export async function storeToken(token: string): Promise<void> {
  await SecureStore.setItemAsync(TOKEN_KEY, token);
  cachedToken = token;
}

export async function getToken(): Promise<string | null> {
  if (cachedToken !== null) return cachedToken;
  cachedToken = await SecureStore.getItemAsync(TOKEN_KEY);
  return cachedToken;
}

export async function deleteToken(): Promise<void> {
  await SecureStore.deleteItemAsync(TOKEN_KEY);
  cachedToken = null;
}

/** Build standard headers including the Bearer token when present. Exported for
 *  the background location task, which posts outside the React tree. */
export function authHeaders(token: string | null): Record<string, string> {
  return {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

export const apiUrl = (path: string): string => `${API_BASE}${path}`;

/**
 * Typed fetch against the backend.
 * @throws Error('Unauthorized') on 401 (also clears the token + calls onLogout);
 *         Error('API error <status>') on other non-2xx.
 */
export async function apiFetch<T>(
  path: string,
  onLogout: () => void,
  init?: RequestInit,
): Promise<T> {
  const token = await getToken();
  const res = await fetch(apiUrl(path), {
    ...init,
    headers: { ...authHeaders(token), ...(init?.headers ?? {}) },
  });

  if (res.status === 401) {
    await deleteToken();
    onLogout();
    throw new Error('Unauthorized');
  }
  if (!res.ok) {
    throw new Error(`API error ${res.status}`);
  }
  return res.json() as Promise<T>;
}
