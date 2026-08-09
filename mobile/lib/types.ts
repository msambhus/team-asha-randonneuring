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
  is_live?: boolean;
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
  is_live?: boolean;
  signup_status: string | null;
}

export interface CalendarResponse {
  rides: BrevetSummary[];
}

export interface RiderProfileResponse {
  rider: {
    id: number;
    rusa_id: number | null;
    first_name: string | null;
    last_name: string | null;
  };
  career: {
    rides: number;
    distance_km: number;
    super_randonneur: number;
  };
}

export interface PublicRiderSummary {
  id: number;
  rusa_id: number | null;
  first_name: string | null;
  last_name: string | null;
  display_name: string;
  total_rides: number;
  total_km: number;
  season_rides: number;
  season_km: number;
  eddington_miles: number | null;
  sr_progress: number[];
}

export interface PublicRidersResponse {
  riders: PublicRiderSummary[];
  season: { id: number; name: string } | null;
  seasons: { id: number; name: string; is_current: boolean }[];
}

export interface PublicRiderResponse {
  rider: RiderProfileResponse['rider'];
  career: RiderProfileResponse['career'] & { r12: number };
  seasons: {
    id: number; name: string; is_current: boolean; rides: number;
    distance_km: number; sr_count: number;
    history: { id: number; name: string; date: string | null; distance_km: number | null;
      status: string | null; ride_type: string | null; finish_time: string | null }[];
  }[];
}

export interface TrainingActivity {
  id: string; name: string; type: string; start_local: string | null;
  date: string | null; distance_mi: number; moving_minutes: number;
  elapsed_minutes: number; elevation_ft: number; average_hr: number | null;
  average_watts: number | null; suffer_score: number | null; calories: number | null;
  trainer: boolean; commute: boolean; url: string | null;
}

export interface TrainingLogResponse {
  month: string; connected: boolean; activities: TrainingActivity[];
  attribution: string;
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
  season: { id: number; name: string | null; is_current: boolean };
  seasons: { id: number; name: string | null; is_current: boolean }[];
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

// ── Gradient elevation profile (PR #535, mobile parity with web PR #534) ──────
// Server-computed SVG geometry from the cron-warmed route track
// (shared/live_radial.py::build_elevation_profile). Colours are baked into each
// segment server-side from the _GRADE_BUCKETS map (descent=blue → flat=green →
// steep=dark red), so the client just renders them.
export interface ElevationSegment {
  d: string;                    // SVG path "M x1 y1L x2 y2"
  color: string;                // gradient-bucket colour
  grade: number | null;         // signed grade %
}

export interface ElevationTick {
  x?: number;                   // x_ticks carry x; y_ticks carry y
  y?: number;
  label: string;
}

// Control/break dot overlaid on the profile (from overlay_stop_markers). The
// client re-derives these from the selected pace's stops on pick (same x, updated
// ETA/type) — no refetch.
export interface ElevationMarker {
  i: number;
  x: number;
  y: number;
  color: string;
  name: string;
  cumul_mi: number;
  eta: string;
  break_min: number;
  type: string;                 // start | control | rest | finish | waypoint
}

export interface ElevationProfileAvailable {
  available: true;
  width: number;
  height: number;
  plot: { x: number; y: number; w: number; h: number };
  total_mi: number;
  min_ft: number;
  max_ft: number;
  area_path: string;
  segments: ElevationSegment[];
  points: [number, number][];
  x_ticks: ElevationTick[];
  y_ticks: ElevationTick[];
  legend: { color: string; label: string }[];
  markers?: ElevationMarker[];  // seeded from the standard pace stops server-side
}

export interface ElevationProfileUnavailable {
  available: false;
}

export type ElevationProfile = ElevationProfileAvailable | ElevationProfileUnavailable;

// ── Pace strategies (Comfort / Standard / Push) ──────────────────────────────
// Per-pace stop list from shared/strategies.py::compute_pace_strategies. Leaner
// than PlanStop — it carries the pace-varying timing fields the itinerary + overlay
// re-render from on pick.
export interface PaceStop {
  i: number;
  type: string;                 // start | control | rest | finish | waypoint
  name: string;
  cumul_mi: number;             // route-constant across paces
  eta: string;                  // clock time, e.g. "09:00" (24h, "+1" past midnight)
  elapsed: string;              // cumulative elapsed at arrival, e.g. "3h05"
  bank: string;                 // formatted "+2:05" cushion vs cutoff
  bank_min: number;             // signed minutes (0 when no cutoff)
  is_key: boolean;
  seg_mi: number;
  seg_time_min: number;         // varies with pace
  break_min: number;
  is_halt: boolean;
  fpm: number;
  seg_speed: number;
  seg_speed_known: boolean;
  headwind_mph: number;
  wind_label: string;
  wind_arrow_deg: number;
  wind_known: boolean;
  tough_class: string;
  tough_known: boolean;
}

export type PaceStopsMap = Record<string, PaceStop[]>;   // keyed comfort/standard/push

// A pace card's header labels — every compute_pace_strategies field except `stops`.
export interface PaceCardMeta {
  id: string;                   // comfort | standard | push
  name: string;
  color: string;
  summary: string;
  total: string;
  sleep: string;
  has_sleep: boolean;
  bank: string;
  bank_good: boolean;
  risk: string;
  recommended: boolean;
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
  // Additive (PR #535): old clients ignore these. `elevation_profile` is
  // {available:false} on a cache miss; `pace_stops_map` is {} when the ride has no plan.
  elevation_profile?: ElevationProfile;
  pace_stops_map?: PaceStopsMap;
  pace_cards_meta?: PaceCardMeta[];
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
  current_stop_min?: number | null;
  stopped_ride_day_min?: number | null;
  active_day?: number | null;
  stop_events?: LiveStopEvent[];
  heart_rate: number | null;
  power: number | null;
  cadence: number | null;
  // on-route only
  distance_mi?: number | null;
  route_position_mi?: number | null;
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
  arrival_time_min?: number | null;   // plan's reaching time (not departure)
  eta_iso: string | null;
  eta_label: string | null;           // club-local clock, e.g. "3:45 PM" — ARRIVAL time
  eta_pacific_label?: string | null;
  // Speed needed to reach the control at the plan's arrival; null when behind.
  required_mph?: number | null;
  behind?: boolean;                   // plan's arrival time already passed
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
  off_course_since_mi?: number | null;
  off_course_distance_m?: number | null;
  now: LivePositionTelemetryNow;
  remaining: LivePositionRemaining | null;
  next_control?: LivePositionNextControl | null;
  // Speed to reach the FINISH on time (item 3); same shape as next_control.
  finish?: LivePositionNextControl | null;
  // banked_min = delta_min surfaced explicitly (banked vs the plan).
  plan: { delta_min: number; banked_min?: number; status: 'ahead' | 'behind' | 'on' } | null;
  // Time banked vs the brevet cutoff (OTL margin) and vs the plan; either may be null.
  time_banked_cutoff_min?: number | null;
  time_banked_plan_min?: number | null;
  detailed_after_ride: boolean;
}

/** Top-level route-ahead chart series on the positions response (static per ride).
 *  Aligned arrays keyed by distance (mi); series are null when unavailable. */
export interface LiveChartData {
  labels: number[];                   // distance (mi)
  elevation_ft: number[] | null;
  headwind_mph: number[] | null;      // + head / − tail
  wind_gust_mph?: number[] | null;
  temperature_f: number[] | null;
}

export interface LiveElevationProfile {
  available: boolean;
  width?: number;
  height?: number;
  plot?: { x: number; y: number; w: number; h: number };
  total_mi?: number;
  segments?: { d: string; area_d: string; color: string; grade: number | null }[];
  points?: [number, number][];
  x_ticks?: { x: number; label: string }[];
  y_ticks?: { y: number; label: string }[];
  legend?: { color: string; label: string }[];
}

/** GET /api/ride/<id>/route — RWGPS route polyline as [[lng,lat],...]. */
export interface RideRouteResponse {
  ride_id: number;
  polyline: [number, number][];
}

/** A selectable plan in the live view (item 1). id is 'base', 'own', or an int
 *  custom-plan id the viewer is allowed to see (server-side allow-set). */
export type LivePlanId = number | 'base' | 'own';

export interface LivePlanOption {
  id: LivePlanId;
  name: string;
  owner?: string | null;
  is_custom: boolean;
}

/** A future control in the shared, ride-level upcoming-controls list (item 2). */
export interface UpcomingControl {
  name: string | null;
  type: string | null;
  distance_mi: number | null;
  arrival_time_min: number | null;
  eta_iso: string | null;
  eta_label: string | null;   // club-local clock, e.g. "3:45 PM"
  eta_pacific_label?: string | null;
}

export interface LiveStopEvent {
  distance_mi: number;
  duration_min: number;
  day_number: number;
  start_label: string;
  end_label: string;
}

export interface LivePlanSnapshotStop {
  name: string;
  distance_mi: number;
  eta: string;
  eta_event_zone?: string | null;
  eta_pacific?: string | null;
  show_pacific?: boolean;
  break_min: number;
  type: string;
  time_bank_min: number | null;
}

export interface LivePlanSnapshot {
  name: string | null;
  slug: string | null;
  active_day: number;
  is_current_day: boolean;
  day_distance_mi: number;
  day_elevation_ft: number;
  day_controls: number;
  day_moving_min: number;
  day_stopped_min: number;
  day_elapsed_min: number;
  day_time_bank_min: number | null;
  day_stops: LivePlanSnapshotStop[];
}

export interface LivePosition {
  rider_id: number;
  name: string;
  lat: number | null;
  lng: number | null;
  status: string | null;
  color: string;
  /** Dot color by plan timing (ahead=green / behind=red / grey=unknown). Falls
   *  back to `color` (signup status) server-side when no plan is matched. */
  plan_color?: string;
  recorded_at: string | null;
  minutes_ago: number | null;
  stale: boolean;
  source: 'garmin' | 'beacon' | null;
  not_sharing?: boolean;
  telemetry: LivePositionTelemetry | null;
  trail: [number, number][] | null;
}

export interface PositionsResponse {
  ride_id: number;
  positions: LivePosition[];
  stale_after_minutes: number;
  server_time: string;
  chart_data?: LiveChartData | null;   // route-ahead charts; null when no route
  elevation_profile?: LiveElevationProfile | null;
  plans?: LivePlanOption[];            // plan selector options (item 1)
  selected_plan_id?: LivePlanId | null; // the APPLIED plan (rejected id echoes 'base')
  upcoming_controls?: UpcomingControl[]; // shared ride-level list (item 2)
  plan_snapshot?: LivePlanSnapshot | null;
}

/** What useLivePositions exposes to the screen — the positions array plus the
 *  top-level chart_data (which a positions-only projection would otherwise drop). */
export interface LivePositionsResult {
  positions: LivePosition[];
  chart_data: LiveChartData | null;
  elevation_profile: LiveElevationProfile | null;
  plans: LivePlanOption[];
  selected_plan_id: LivePlanId | null;
  upcoming_controls: UpcomingControl[];
  plan_snapshot: LivePlanSnapshot | null;
}
