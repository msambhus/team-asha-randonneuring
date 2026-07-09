/**
 * mobile/lib/auth.ts — native Google sign-in → backend token exchange.
 *
 * Flow: GoogleSignin gives a Google ID token; we POST it to /api/auth/google,
 * which verifies it (audience = our iOS client id) and returns our app token.
 */
import { GoogleSignin } from '@react-native-google-signin/google-signin';
import * as AppleAuthentication from 'expo-apple-authentication';
import { API_BASE, GOOGLE_IOS_CLIENT_ID, GOOGLE_WEB_CLIENT_ID } from './config';
import { authHeaders, getToken } from './api';
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

/** Email + password sign-up (mobile's 3rd login option) → backend app token.
 *  Surfaces the backend's friendly error (e.g. "email already exists"). */
export async function passwordSignup(email: string, password: string): Promise<GoogleAuthResponse> {
  const res = await fetch(`${API_BASE}/api/auth/signup`, {
    method: 'POST',
    headers: authHeaders(null),
    body: JSON.stringify({ email, password }),
  });
  const data = (await res.json().catch(() => ({}))) as GoogleAuthResponse & { error?: string };
  if (!res.ok) throw new Error(data.error || `Sign-up failed (${res.status})`);
  return data;
}

/** Email + password sign-in → backend app token. */
export async function passwordLogin(email: string, password: string): Promise<GoogleAuthResponse> {
  const res = await fetch(`${API_BASE}/api/auth/login`, {
    method: 'POST',
    headers: authHeaders(null),
    body: JSON.stringify({ email, password }),
  });
  const data = (await res.json().catch(() => ({}))) as GoogleAuthResponse & { error?: string };
  if (!res.ok) throw new Error(data.error || `Sign-in failed (${res.status})`);
  return data;
}

export async function googleSignOut(): Promise<void> {
  try {
    configureGoogle();
    await GoogleSignin.signOut();
  } catch {
    // best-effort — clearing our own token is what matters
  }
}

// ── Sign in with Apple (App Store Guideline 4.8) ──────────────────────────

/** Whether Sign in with Apple is available (iOS 13+). Cheap to call; false on
 *  Android/web so the button can be hidden. */
export async function isAppleSignInAvailable(): Promise<boolean> {
  try {
    return await AppleAuthentication.isAvailableAsync();
  } catch {
    return false;
  }
}

/**
 * Run native Sign in with Apple. Returns {identityToken, email}, or `null` if
 * the user cancelled (a normal action — treat null as a no-op, not an error).
 * Apple only returns the email on the FIRST authorization; later logins omit it
 * (the backend already knows the user by the token's stable `sub`).
 */
export async function getAppleCredential(): Promise<{ identityToken: string; email: string | null } | null> {
  try {
    const cred = await AppleAuthentication.signInAsync({
      requestedScopes: [
        AppleAuthentication.AppleAuthenticationScope.FULL_NAME,
        AppleAuthentication.AppleAuthenticationScope.EMAIL,
      ],
    });
    if (!cred.identityToken) throw new Error('Apple did not return an identity token.');
    return { identityToken: cred.identityToken, email: cred.email ?? null };
  } catch (e) {
    if ((e as { code?: string })?.code === 'ERR_REQUEST_CANCELED') return null;
    throw e;
  }
}

/** Exchange an Apple identity token for our backend app token. Pure-ish: takes
 *  the token so it's unit-testable without the native module. */
export async function exchangeAppleToken(
  identityToken: string,
  email: string | null,
): Promise<GoogleAuthResponse> {
  const res = await fetch(`${API_BASE}/api/auth/apple`, {
    method: 'POST',
    headers: authHeaders(null),
    body: JSON.stringify({ identity_token: identityToken, email }),
  });
  if (!res.ok) {
    throw new Error(res.status === 401 ? 'Apple sign-in was rejected' : `Sign-in failed (${res.status})`);
  }
  return res.json() as Promise<GoogleAuthResponse>;
}

// ── Account deletion (App Store Guideline 5.1.1(v)) ───────────────────────

/** Permanently delete the signed-in account (DELETE /api/auth/account). The
 *  caller should sign out afterwards. Throws on any non-2xx. */
export async function deleteAccount(): Promise<void> {
  const token = await getToken();
  const res = await fetch(`${API_BASE}/api/auth/account`, {
    method: 'DELETE',
    headers: authHeaders(token),
  });
  if (!res.ok) {
    throw new Error(`Account deletion failed (${res.status})`);
  }
}
