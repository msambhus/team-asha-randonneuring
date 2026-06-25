/**
 * mobile/lib/types.ts — shapes returned by the Team Asha JSON API.
 * Mirrors the Flask responses in routes/api_auth.py and routes/live.py.
 */

export interface GoogleAuthResponse {
  token: string;
  rider_id: number | null;
  profile_complete: boolean;
}

export interface RideSummary {
  id: number;
  name: string;
  date: string | null;
  distance_km: number | null;
  signup_status: string | null;
}

export interface RidesResponse {
  rides: RideSummary[];
}

export interface LivePositionTelemetryNow {
  speed_mph: number | null;
  activity: 'paused' | 'walking' | 'cycling' | 'driving' | null;
  elapsed_min: number | null;
  moving_min: number | null;
  stopped_min: number | null;
  heart_rate: number | null;
  power: number | null;
  cadence: number | null;
}

export interface LivePosition {
  rider_id: number;
  name: string;
  lat: number;
  lng: number;
  status: string | null;
  color: string;
  recorded_at: string;
  minutes_ago: number;
  stale: boolean;
  source: 'garmin' | 'beacon';
  telemetry: { now?: LivePositionTelemetryNow } | null;
  trail: [number, number][] | null;
}

export interface PositionsResponse {
  ride_id: number;
  positions: LivePosition[];
  stale_after_minutes: number;
  server_time: string;
}
