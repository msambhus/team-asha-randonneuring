/**
 * mobile/lib/config.ts
 * Runtime config sourced from app.json `extra` (via expo-constants), with safe
 * fallbacks. Set the real values in app.json or via EAS build env.
 */
import Constants from 'expo-constants';

const extra = (Constants.expoConfig?.extra ?? {}) as Record<string, string>;

/** Base URL of the Team Asha Flask backend (the existing web app). */
export const API_BASE: string =
  extra.apiBase || 'https://team-asha-randonneuring.vercel.app';

/** Google **iOS** OAuth client id — drives the native sign-in UI. */
export const GOOGLE_IOS_CLIENT_ID: string = extra.googleIosClientId || '';

/**
 * Google **Web** OAuth client id. With @react-native-google-signin, an ID token
 * is only reliably returned when a webClientId is configured, and that token's
 * audience (`aud`) is THIS web client id — so the backend's verification
 * audience (GOOGLE_IOS_CLIENT_ID env on Vercel) must be set to this value.
 */
export const GOOGLE_WEB_CLIENT_ID: string = extra.googleWebClientId || '';
