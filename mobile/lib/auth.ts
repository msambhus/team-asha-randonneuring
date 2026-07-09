/**
 * mobile/lib/auth.ts — login helpers that exchange credentials for our backend
 * bearer token.
 *
 * The iOS app dropped Google + Sign in with Apple (App Store Guideline 4.8) in
 * favour of first-party logins:
 *   • email + password
 *   • passwordless email OTP — a 6-digit code OR a magic link.
 * An existing Google/Apple member signs in with an email code: the backend sends
 * it to their verified email and it resolves to their SAME account, so removing
 * the buttons orphans no one. All helpers return the shared session shape
 * ({token, rider_id, profile_complete}).
 */
import { API_BASE } from './config';
import { authHeaders, getToken } from './api';
import type { GoogleAuthResponse } from './types';

/** Reviewer/demo login: exchange nothing for a backend app token via the gated
 *  /api/auth/demo endpoint. Only works when the server has demo mode enabled;
 *  otherwise it 404s and we surface a friendly message. */
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

/** Email + password sign-up → backend app token. Surfaces the backend's friendly
 *  error (e.g. "email already exists"). */
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

// ── Passwordless email OTP ────────────────────────────────────────────────

/** Ask the backend to email a 6-digit code + magic link to `email`. Resolves on
 *  success; throws with the backend's message on rate-limit / send failure. */
export async function requestEmailOtp(email: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/auth/otp/request`, {
    method: 'POST',
    headers: authHeaders(null),
    body: JSON.stringify({ email }),
  });
  const data = (await res.json().catch(() => ({}))) as { error?: string };
  if (!res.ok) throw new Error(data.error || `Could not send a code (${res.status})`);
}

/** Params for verifyEmailOtp: either a typed code (with email) or a magic-link
 *  token. `phone` is optional and, if given, stored UNVERIFIED for a future
 *  SMS-OTP login (phase 2). */
export type OtpVerifyParams =
  | { email: string; code: string; phone?: string }
  | { linkToken: string; phone?: string };

/** Verify an email OTP (typed code or magic-link token) → backend app token. */
export async function verifyEmailOtp(params: OtpVerifyParams): Promise<GoogleAuthResponse> {
  const body: Record<string, string> = {};
  if ('linkToken' in params) body.link_token = params.linkToken;
  else { body.email = params.email; body.code = params.code; }
  if (params.phone) body.phone = params.phone;

  const res = await fetch(`${API_BASE}/api/auth/otp/verify`, {
    method: 'POST',
    headers: authHeaders(null),
    body: JSON.stringify(body),
  });
  const data = (await res.json().catch(() => ({}))) as GoogleAuthResponse & { error?: string };
  if (!res.ok) throw new Error(data.error || `Sign-in failed (${res.status})`);
  return data;
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
