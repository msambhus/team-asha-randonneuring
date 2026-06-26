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

export interface BrevetSummary {
  id: number;
  name: string;
  date: string | null;
  distance_km: number | null;
  ride_type: string | null;
  start_location: string | null;
  club_name: string | null;
  signup_count: number | null;
  is_team_ride?: boolean;
}

export interface CalendarResponse {
  rides: BrevetSummary[];
}

export interface EddingtonBadge {
  level: string;
  color?: string;
  label: string;
  emoji?: string;
}

export interface SeasonRideDone {
  id: number;
  name: string;
  date: string | null;
  distance_km: number | null;
}

/** GET /api/me/season — the signed-in rider's current-season progress. */
export interface MySeasonResponse {
  season: { name: string | null };
  stats: { distance_km: number; rides: number; elevation_ft: number };
  // counts: how many finished rides in each SR tier this season, keyed "200".."600".
  sr: { has_sr: boolean; distances_done: number[]; counts: Record<string, number> };
  rides_done: SeasonRideDone[];
  r12: { months: number; active: boolean };
  career: { distance_km: number };
  eddington: { value: number; badge: EddingtonBadge } | null;
}

// ── Ride weather (GET /api/ride/<id>/weather) — mirrors the web /weather page ──
export interface WeatherSegment {
  distance_mi: number;
  arrival_time: string;
  temperature_f: number;
  feels_like_f: number;
  wind_speed_mph: number;
  wind_gust_mph: number;
  wind_direction_deg: number;
  headwind_mph: number;        // + = headwind, − = tailwind
  wind_label: string;
  precip_percent: number;
  precipitation_mm: number;
  cloud_cover: number;
  humidity: number;
  conditions: string;
  conditions_icon: string;
  elevation_ft: number;
  lat: number;
  lng: number;
  rider_bearing_deg: number;
}

export interface WeatherChartData {
  labels: number[];            // distance (mi)
  times: string[];
  temperature_f: number[];
  feels_like_f: number[];
  wind_speed_mph: number[];
  wind_gust_mph: number[];
  headwind_mph: number[];
  precip_probability: number[];
  precipitation_mm: number[];
  cloud_cover: number[];
  elevation_ft: number[];
  humidity: number[];
}

export interface RideWeatherAvailable {
  available: true;
  route_name: string;
  total_distance_mi: number;
  total_elevation_ft: number;
  plan_source: 'base' | 'custom' | null;
  polyline: [number, number][];   // [[lat,lng],...]
  table_segments: WeatherSegment[];
  map_segments: WeatherSegment[];
  chart_data: WeatherChartData;
  cue_points: { name: string; distance_mi: number; stop_type: string }[];
  ride_summary: string;
  temp_range: { min_f: number; max_f: number };
  attribution: string;
}

export interface RideWeatherUnavailable {
  available: false;
  reason: 'no_route' | 'no_date' | 'past_ride' | 'forecast_horizon';
  message: string;
  ride_date?: string;
}

export type RideWeatherResponse = RideWeatherAvailable | RideWeatherUnavailable;

export interface LivePositionTelemetryNow {
  speed_mph: number | null;
  activity: 'paused' | 'walking' | 'cycling' | 'driving' | null;
  elapsed_min: number | null;
  moving_min: number | null;
  stopped_min: number | null;
  heart_rate: number | null;
  power: number | null;
  cadence: number | null;
  // on-route only
  distance_mi?: number | null;
  ascent_done_ft?: number | null;
  headwind_done_mph?: number | null;
  headwind_done_label?: string | null;
}

export interface LivePositionRemaining {
  distance_mi: number | null;
  ascent_left_ft: number | null;
  headwind_ahead_mph: number | null;
  headwind_ahead_label: string | null;
  time_left_min: number | null;
  toughness: number | string | null;
}

export interface LivePositionTelemetry {
  on_route: boolean | null;
  now: LivePositionTelemetryNow;
  remaining: LivePositionRemaining | null;
  plan: { delta_min: number; status: 'ahead' | 'behind' | 'on' } | null;
  detailed_after_ride: boolean;
}

/** GET /api/ride/<id>/route — RWGPS route polyline as [[lng,lat],...]. */
export interface RideRouteResponse {
  ride_id: number;
  polyline: [number, number][];
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
  telemetry: LivePositionTelemetry | null;
  trail: [number, number][] | null;
}

export interface PositionsResponse {
  ride_id: number;
  positions: LivePosition[];
  stale_after_minutes: number;
  server_time: string;
}
