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

// ── Ride plan (GET /api/ride/<id>/plan) — mirrors the web ride-plan page ──────
export interface PlanStop {
  stop_order: number;
  location: string;
  stop_type: string;            // start | control | rest | finish | waypoint
  stop_name: string | null;
  notes: string | null;
  distance_mi: number;          // cumulative
  seg_dist_mi: number;
  elevation_gain_ft: number;
  ft_per_mi: number;
  segment_time_min: number;
  stop_duration_min: number;
  cum_time_min: number;
  arrival_time_min: number;
  eta: string;                  // clock time, e.g. "9:00 AM"
  time_bank_min: number | null; // present when the ride has a cutoff
  is_custom_stop?: boolean;
  is_modified?: boolean;
  // best-effort per-stop wind/temp (when the route + forecast allow)
  wind_speed_mph?: number | null;
  wind_label?: string | null;
  wind_direction_deg?: number | null;
  temperature_f?: number | null;
}

export interface RidePlanAvailable {
  available: true;
  plan: {
    name: string;
    slug: string;
    total_distance_mi: number | null;
    total_elevation_ft: number | null;
    distance_km: number | null;
    cutoff_hours: number | null;
    start_time: string;
    overall_ft_per_mile: number | null;
  };
  has_custom: boolean;          // the rider has a custom plan for this ride
  using_custom: boolean;        // the returned stops are the custom plan
  custom_name: string | null;
  ride_date: string | null;
  stops: PlanStop[];
}

export interface RidePlanUnavailable {
  available: false;
  reason: 'no_plan' | 'no_stops';
  message: string;
}

export type RidePlanResponse = RidePlanAvailable | RidePlanUnavailable;

export interface LivePositionTelemetryNow {
  speed_mph: number | null;
  avg_elapsed_speed_mph?: number | null;
  avg_moving_speed_mph?: number | null;
  activity: 'paused' | 'walking' | 'cycling' | 'driving' | null;
  elapsed_min: number | null;
  moving_min: number | null;
  stopped_min: number | null;
  heart_rate: number | null;
  power: number | null;
  cadence: number | null;
  // on-route only
  distance_mi?: number | null;
  grade_pct?: number | null;          // current route grade at the rider's position
  ascent_done_ft?: number | null;
  headwind_done_mph?: number | null;
  headwind_done_label?: string | null;
}

/** Next waypoint/control ahead of the rider, with the plan's expected arrival. */
export interface LivePositionNextControl {
  name: string | null;
  type: string | null;
  distance_mi: number | null;         // cumulative distance of the control
  dist_to_go_mi: number | null;
  eta_iso: string | null;
  eta_label: string | null;           // club-local clock, e.g. "3:45 PM"
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
  next_control?: LivePositionNextControl | null;
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
  /** Dot color by plan timing (ahead=green / behind=red / grey=unknown). Falls
   *  back to `color` (signup status) server-side when no plan is matched. */
  plan_color?: string;
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
