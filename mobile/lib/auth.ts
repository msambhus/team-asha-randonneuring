/**
 * mobile/lib/auth.ts — native Google sign-in → backend token exchange.
 *
 * Flow: GoogleSignin gives a Google ID token; we POST it to /api/auth/google,
 * which verifies it (audience = our iOS client id) and returns our app token.
 */
import { GoogleSignin } from '@react-native-google-signin/google-signin';
import { API_BASE, GOOGLE_IOS_CLIENT_ID, GOOGLE_WEB_CLIENT_ID } from './config';
import { authHeaders } from './api';
import type { GoogleAuthResponse } from './types';

let configured = false;

export function configureGoogle(): void {
  if (configured) return;
  // webClientId is what makes getTokens() return an ID token reliably; its aud
  // is the web client id, which the backend must verify against.
  GoogleSignin.configure({
    iosClientId: GOOGLE_IOS_CLIENT_ID,
    webClientId: GOOGLE_WEB_CLIENT_ID || undefined,
  });
  configured = true;
}

/**
 * Run native Google sign-in. Returns the Google ID token, or `null` if the user
 * cancelled (a normal action — callers should treat null as a no-op, not an error).
 */
export async function getGoogleIdToken(): Promise<string | null> {
  configureGoogle();
  await GoogleSignin.hasPlayServices();
  const result = await GoogleSignin.signIn();
  // v13 returns a discriminated result; a cancel is not an error.
  if (result && (result as { type?: string }).type === 'cancelled') return null;
  const { idToken } = await GoogleSignin.getTokens();
  if (!idToken) throw new Error('Google did not return an ID token (configure a Web client id).');
  return idToken;
}

/** Exchange a Google ID token for our backend app token. Pure-ish: takes the
 *  id token so it's unit-testable without the native module. */
export async function exchangeGoogleToken(idToken: string): Promise<GoogleAuthResponse> {
  const res = await fetch(`${API_BASE}/api/auth/google`, {
    method: 'POST',
    headers: authHeaders(null),
    body: JSON.stringify({ id_token: idToken }),
  });
  if (!res.ok) {
    throw new Error(res.status === 401 ? 'Google sign-in was rejected' : `Sign-in failed (${res.status})`);
  }
  return res.json() as Promise<GoogleAuthResponse>;
}

/** Reviewer/demo login: exchange nothing for a backend app token via the gated
 *  /api/auth/demo endpoint (no Google). Only works when the server has demo mode
 *  enabled; otherwise it 404s and we surface a friendly message. */
export async function demoSignIn(): Promise<GoogleAuthResponse> {
  const res = await fetch(`${API_BASE}/api/auth/demo`, {
    method: 'POST',
    headers: authHeaders(null),
  });
  if (!res.ok) {
    throw new Error(res.status === 404 ? 'Demo login is not available right now' : `Demo sign-in failed (${res.status})`);
  }
  return res.json() as Promise<GoogleAuthResponse>;
}

export async function googleSignOut(): Promise<void> {
  try {
    configureGoogle();
    await GoogleSignin.signOut();
  } catch {
    // best-effort — clearing our own token is what matters
  }
}
