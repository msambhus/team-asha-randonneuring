/**
 * mobile/lib/config.ts
 * Runtime config sourced from app.json `extra` (via expo-constants), with a safe
 * fallback. Set the real value in app.json or via EAS build env.
 */
import Constants from 'expo-constants';

const extra = (Constants.expoConfig?.extra ?? {}) as Record<string, string>;

/** Base URL of the Team Asha Flask backend (the existing web app). */
export const API_BASE: string =
  extra.apiBase || 'https://team-asha-randonneuring.vercel.app';
