/**
 * mobile/app/ride/[id].tsx — a ride's live map (parity with the web live map).
 *
 * Map: RWGPS route line + every rider's marker (initials, colour, ⌚/📱 source,
 * activity badge) + per-rider breadcrumb trail. Below: rider telemetry cards.
 * Controls: a per-ride Share/Stop button driving the screen-off background
 * beacon. The account-level consent toggle lives on the Settings screen; this
 * screen reads it (useSharing) and only lets you Share while it's on.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import MapView, { Marker, Polyline, Region } from 'react-native-maps';
import { Feather } from '@expo/vector-icons';
import { useLivePositions } from '../../hooks/useLivePositions';
import { useRideRoute } from '../../hooks/useRideRoute';
import { useSharing } from '../../hooks/useSharing';
import { startSharing, stopSharing, isSharing } from '../../location/backgroundLocation';
import { WeatherChart } from '../../components/WeatherChart';
import type { LivePosition, LiveChartData, LivePlanOption, LivePlanId, UpcomingControl } from '../../lib/types';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { colors } from '../../lib/theme';

const FALLBACK_REGION: Region = {
  latitude: 37.3, longitude: -121.9, latitudeDelta: 0.4, longitudeDelta: 0.4,
};
const ACTIVITY_ICON: Record<string, string> = { paused: '⏸', walking: '🚶', cycling: '🚴', driving: '🚗' };

function initials(name: string): string {
  const parts = (name || '').trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return '?';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}
const n = (v: number | null | undefined, unit = ''): string =>
  v == null ? '—' : `${typeof v === 'number' && !Number.isInteger(v) ? v.toFixed(1) : v}${unit}`;

// Minutes -> "Xh YYm" (>= 1h) or "Ym" (< 1h). Always shown in hours+minutes.
const hm = (v: number | null | undefined): string => {
  if (v == null) return '—';
  const total = Math.round(Math.abs(v));
  const h = Math.floor(total / 60), m = total % 60;
  return h > 0 ? `${h}h ${String(m).padStart(2, '0')}m` : `${m}m`;
};

// Signed "time banked": +Xh YYm in hand, −Xh YYm behind. '—' when unknown.
const fmtBank = (v: number | null | undefined): string =>
  v == null ? '—' : `${v < 0 ? '−' : '+'}${hm(v)}`;

// Nearest chart index (into `labels`, distance in mi) for a rider's mileage.
function nearestIndex(labels: number[], mi: number | null | undefined): number {
  if (mi == null || !labels.length) return -1;
  let best = 0, bd = Infinity;
  for (let i = 0; i < labels.length; i++) {
    const d = Math.abs(labels[i] - mi);
    if (d < bd) { bd = d; best = i; }
  }
  return best;
}

const RED = '#dc2626', GREEN = '#16a34a';

/** Route-ahead charts (elevation / headwind / temperature) with a vertical marker
 *  at each on-route rider's current position — mirrors the web live page and reuses
 *  the weather page's WeatherChart (react-native-svg, no new native dependency). */
function LiveCharts({ chart, positions }: { chart: LiveChartData; positions: LivePosition[] }) {
  const labels = chart.labels ?? [];
  if (labels.length < 2) return null;
  // One labeled dot per on-route rider at their current mileage, colored by plan
  // pace (item 4) — initials keep them distinguishable.
  const markers = positions
    .filter((p) => p.telemetry?.now?.distance_mi != null)
    .map((p) => ({ index: nearestIndex(labels, p.telemetry?.now?.distance_mi),
                   color: p.plan_color ?? p.color, label: initials(p.name) }));
  return (
    <View>
      <Text style={styles.chartsTitle}>Route ahead</Text>
      {chart.elevation_ft ? (
        <WeatherChart title="Elevation" unit="ft" labels={labels} markers={markers}
          series={[{ data: chart.elevation_ft, color: '#15803d', fill: true }]} />
      ) : null}
      {chart.headwind_mph ? (
        <WeatherChart title="Headwind / Tailwind" unit="mph" labels={labels} baseline={0} markers={markers}
          series={[
            { data: chart.headwind_mph.map((v) => (v > 0 ? v : 0)), color: RED, fill: true },
            { data: chart.headwind_mph.map((v) => (v < 0 ? v : 0)), color: GREEN, fill: true },
          ]}
          legend={[{ label: 'headwind', color: RED }, { label: 'tailwind', color: GREEN }]} />
      ) : null}
      {chart.temperature_f ? (
        // Temperature red (#ef4444) to match the weather page's live-chart color scheme.
        <WeatherChart title="Temperature" unit="°F" labels={labels} markers={markers}
          series={[{ data: chart.temperature_f, color: '#ef4444', fill: true }]} />
      ) : null}
    </View>
  );
}

function planBadge(p: LivePosition): { text: string; color: string } | null {
  const t = p.telemetry;
  if (!t) return null;
  if (t.on_route === false) return { text: 'Off route', color: '#dc2626' };
  if (!t.plan) return null;
  if (t.plan.status === 'ahead') return { text: `${hm(t.plan.delta_min)} ahead`, color: '#16a34a' };
  if (t.plan.status === 'behind') return { text: `${hm(t.plan.delta_min)} behind`, color: '#dc2626' };
  return { text: 'On plan', color: '#16a34a' };
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.metric}>
      <Text style={styles.metricVal}>{value}</Text>
      <Text style={styles.metricLbl}>{label}</Text>
    </View>
  );
}

function RiderCard({ p }: { p: LivePosition }) {
  const t = p.telemetry;
  const now = t?.now;
  const rem = t?.remaining;
  const nc = t?.next_control;
  const fin = t?.finish;
  const badge = planBadge(p);
  return (
    <View style={[styles.card, p.stale && styles.cardStale]}>
      <View style={styles.cardHead}>
        <View style={[styles.dot, { backgroundColor: p.plan_color ?? p.color }]}><Text style={styles.dotText}>{initials(p.name)}</Text></View>
        <View style={{ flex: 1 }}>
          <Text style={styles.cardName}>{p.name || 'Rider'} {p.source === 'garmin' ? '⌚' : '📱'}</Text>
          <Text style={styles.cardMeta}>updated {p.minutes_ago <= 0 ? 'just now' : `${hm(p.minutes_ago)} ago`}</Text>
        </View>
        {badge ? <Text style={[styles.badge, { color: badge.color }]}>{badge.text}</Text> : null}
      </View>
      {now ? (
        <View style={styles.metricRow}>
          {now.distance_mi != null ? <Metric label="done" value={n(now.distance_mi, ' mi')} /> : null}
          <Metric label="speed" value={n(now.speed_mph, ' mph')} />
          {now.avg_elapsed_speed_mph != null ? <Metric label="avg (elapsed)" value={n(now.avg_elapsed_speed_mph, ' mph')} /> : null}
          {now.avg_moving_speed_mph != null ? <Metric label="avg (moving)" value={n(now.avg_moving_speed_mph, ' mph')} /> : null}
          {now.activity ? <Metric label="state" value={`${ACTIVITY_ICON[now.activity] ?? ''} ${now.activity}`} /> : null}
          <Metric label="moving" value={hm(now.moving_min)} />
          <Metric label="stopped" value={hm(now.stopped_min)} />
          {now.ascent_done_ft != null ? <Metric label="climb" value={n(now.ascent_done_ft, ' ft')} /> : null}
          {now.heart_rate != null ? <Metric label="HR" value={n(now.heart_rate, ' bpm')} /> : null}
          {now.power != null ? <Metric label="power" value={n(now.power, ' W')} /> : null}
          {now.cadence != null ? <Metric label="cadence" value={n(now.cadence, ' rpm')} /> : null}
          {now.grade_pct != null ? <Metric label="grade" value={n(now.grade_pct, '%')} /> : null}
          {now.headwind_done_label ? <Metric label="wind" value={now.headwind_done_label} /> : null}
        </View>
      ) : null}
      {rem ? (
        <View style={styles.metricRow}>
          <Metric label="to go" value={n(rem.distance_mi, ' mi')} />
          <Metric label="climb left" value={n(rem.ascent_left_ft, ' ft')} />
          <Metric label="time left" value={hm(rem.time_left_min)} />
          {rem.toughness != null ? <Metric label="toughness" value={String(rem.toughness)} /> : null}
          {rem.headwind_ahead_label ? <Metric label="wind ahead" value={rem.headwind_ahead_label} /> : null}
        </View>
      ) : null}
      {t && (t.time_banked_cutoff_min != null || t.time_banked_plan_min != null) ? (
        <View style={styles.metricRow}>
          <Metric label="banked (cutoff)" value={fmtBank(t.time_banked_cutoff_min)} />
          <Metric label="banked (plan)" value={fmtBank(t.time_banked_plan_min)} />
        </View>
      ) : null}
      {nc ? (
        <View style={styles.nextControl}>
          <Text style={styles.nextControlName}>Next: {(nc.name || 'control').replace(', CA', '')}</Text>
          <View style={styles.metricRow}>
            <Metric label="ETA (arrival)" value={nc.eta_label ?? '—'} />
            {/* Speed to hit the plan's arrival; em-dash when behind. */}
            <Metric label="req speed" value={nc.required_mph != null ? n(nc.required_mph, ' mph') : '—'} />
            {nc.dist_to_go_mi != null ? <Metric label="to go" value={n(nc.dist_to_go_mi, ' mi')} /> : null}
            {nc.distance_mi != null ? <Metric label="at" value={n(nc.distance_mi, ' mi')} /> : null}
          </View>
        </View>
      ) : null}
      {fin ? (
        <View style={styles.nextControl}>
          <Text style={styles.nextControlName}>To finish</Text>
          <View style={styles.metricRow}>
            <Metric label="ETA (arrival)" value={fin.eta_label ?? '—'} />
            {/* Speed to reach the finish on time; em-dash when behind (item 3). */}
            <Metric label="req speed" value={fin.required_mph != null ? n(fin.required_mph, ' mph') : '—'} />
            {fin.dist_to_go_mi != null ? <Metric label="to go" value={n(fin.dist_to_go_mi, ' mi')} /> : null}
          </View>
        </View>
      ) : null}
      {t?.detailed_after_ride ? <Text style={styles.afterRide}>Power, pedaling & coasting time available after the ride.</Text> : null}
    </View>
  );
}

/** Plan selector (item 1): a chip row when the ride has >1 plan; otherwise just a
 *  "base plan" label. Picking a plan re-polls so ALL riders re-grade against it. */
function PlanSelector({ plans, applied, onSelect }: {
  plans: LivePlanOption[]; applied: LivePlanId | null; onSelect: (id: LivePlanId) => void;
}) {
  if (plans.length <= 1) {
    return <Text style={styles.planLabel}>Plan: <Text style={styles.planLabelStrong}>base plan</Text></Text>;
  }
  return (
    <View style={styles.planWrap}>
      <Text style={styles.planTitle}>Plan</Text>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.planChips}>
        {plans.map((p) => {
          const on = String(applied) === String(p.id);
          return (
            <Pressable key={String(p.id)} onPress={() => onSelect(p.id)}
              style={[styles.planChip, on && styles.planChipOn]}>
              <Text style={[styles.planChipText, on && styles.planChipTextOn]}>
                {p.name}{p.owner ? ` · ${p.owner}` : ''}
              </Text>
            </Pressable>
          );
        })}
      </ScrollView>
    </View>
  );
}

/** Shared, ride-level upcoming-controls list (item 2) — one list, not per rider. */
function UpcomingControls({ controls, showOwnNote }: { controls: UpcomingControl[]; showOwnNote?: boolean }) {
  if (!controls.length) return null;
  return (
    <View style={styles.ucBox}>
      <Text style={styles.ucTitle}>Upcoming controls</Text>
      {showOwnNote ? (
        <Text style={styles.planNote}>Upcoming controls use base-plan timing (each rider graded against their own plan).</Text>
      ) : null}
      {controls.map((c, i) => (
        <View key={i} style={styles.ucRow}>
          <Text style={styles.ucName} numberOfLines={1}>{(c.name || 'control').replace(', CA', '')}</Text>
          <Text style={styles.ucDist}>{c.distance_mi != null ? `${c.distance_mi} mi` : ''}</Text>
          <Text style={styles.ucEta}>{c.eta_label ?? '—'}</Text>
        </View>
      ))}
    </View>
  );
}

export default function RideLiveScreen() {
  const params = useLocalSearchParams<{ id: string }>();
  const rideId = parseInt(String(params.id), 10);
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [selectedPlanId, setSelectedPlanId] = useState<LivePlanId | null>(null);
  const { data, isLoading } = useLivePositions(rideId, selectedPlanId);
  const positions = data?.positions ?? null;
  const chartData = data?.chart_data ?? null;
  const plans = data?.plans ?? [];
  const appliedPlanId = data?.selected_plan_id ?? null;
  const upcoming = data?.upcoming_controls ?? [];
  const { data: route, isLoading: routeLoading } = useRideRoute(rideId);
  const { enabled } = useSharing();   // global account consent (Settings)

  const [sharing, setSharing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mapRef = useRef<MapView>(null);
  const framedOnce = useRef(false);

  useEffect(() => { isSharing().then(setSharing); }, []);

  // If consent is revoked from Settings while this screen is open, the beacon is
  // already stopped there — keep the button in sync (don't strand it on "Stop").
  useEffect(() => { if (enabled === false && sharing) setSharing(false); }, [enabled, sharing]);

  // Frame the map to the route (preferred) or the riders, once.
  useEffect(() => {
    if (framedOnce.current || !mapRef.current) return;
    const coords = (route?.length ? route : (positions ?? []).map((p) => ({ latitude: p.lat, longitude: p.lng })));
    if (!coords.length) return;
    framedOnce.current = true;
    mapRef.current.fitToCoordinates(coords, { edgePadding: { top: 70, right: 70, bottom: 70, left: 70 }, animated: true });
  }, [route, positions]);

  const initialRegion = useMemo<Region>(() => {
    const first = route?.[0] ?? (positions?.[0] ? { latitude: positions[0].lat, longitude: positions[0].lng } : null);
    return first ? { ...first, latitudeDelta: 0.08, longitudeDelta: 0.08 } : FALLBACK_REGION;
  }, [route, positions]);

  async function toggleShare() {
    setBusy(true); setError(null);
    if (sharing) {
      await stopSharing(); setSharing(false);
    } else if (enabled !== true) {
      // Strict consent gate: don't even request OS permissions until the
      // account-level toggle is on. Point the rider to Settings.
      setError('Turn on location sharing in Settings to broadcast on rides.');
    } else {
      const err = await startSharing(rideId);
      if (err) setError(err);
      else setSharing(true);
    }
    setBusy(false);
  }

  return (
    <View style={styles.container}>
      <Stack.Screen options={{
        headerRight: () => (
          <View style={{ flexDirection: 'row', gap: 18 }}>
            <Pressable onPress={() => router.push(`/ride/plan?id=${rideId}`)} hitSlop={12}
              accessibilityRole="button" accessibilityLabel="Ride plan">
              <Feather name="list" size={22} color={colors.navy} />
            </Pressable>
            <Pressable onPress={() => router.push(`/ride/weather?id=${rideId}`)} hitSlop={12}
              accessibilityRole="button" accessibilityLabel="Ride weather">
              <Feather name="cloud-drizzle" size={22} color={colors.navy} />
            </Pressable>
          </View>
        ),
      }} />
      <MapView ref={mapRef} style={styles.map} initialRegion={initialRegion} showsUserLocation>
        {route?.length ? <Polyline coordinates={route} strokeColor="#2563eb" strokeWidth={4} /> : null}
        {(positions ?? []).map((p) =>
          p.trail?.length ? (
            <Polyline key={`t-${p.rider_id}`} coordinates={p.trail.map(([lng, lat]) => ({ latitude: lat, longitude: lng }))}
              strokeColor={p.color} strokeWidth={2} />
          ) : null,
        )}
        {(positions ?? []).map((p: LivePosition) => {
          const act = p.telemetry?.now?.activity;
          return (
            <Marker key={p.rider_id} coordinate={{ latitude: p.lat, longitude: p.lng }} title={p.name}
              opacity={p.stale ? 0.45 : 1} anchor={{ x: 0.5, y: 0.5 }}>
              <View style={[styles.pin, { backgroundColor: p.plan_color ?? p.color, borderStyle: p.source === 'garmin' ? 'solid' : 'dashed' }]}>
                <Text style={styles.pinText}>{initials(p.name)}</Text>
                {act ? <Text style={styles.pinAct}>{ACTIVITY_ICON[act] ?? ''}</Text> : null}
              </View>
            </Marker>
          );
        })}
        {/* Dot colors mean pace vs. the ride plan. */}
      </MapView>

      <View style={styles.legend} pointerEvents="none">
        <View style={styles.legendItem}><View style={[styles.legendDot, { backgroundColor: '#16a34a' }]} /><Text style={styles.legendText}>Ahead/on plan</Text></View>
        <View style={styles.legendItem}><View style={[styles.legendDot, { backgroundColor: '#dc2626' }]} /><Text style={styles.legendText}>Behind</Text></View>
        <View style={styles.legendItem}><View style={[styles.legendDot, { backgroundColor: '#6b7280' }]} /><Text style={styles.legendText}>Off-route/no plan</Text></View>
      </View>

      {routeLoading ? (
        <View style={styles.routeLoadingWrap} pointerEvents="none">
          <View style={styles.routeLoadingPill}>
            <ActivityIndicator size="small" color="#2563eb" />
            <Text style={styles.routeLoadingText}>Loading route…</Text>
          </View>
        </View>
      ) : null}

      <View style={styles.controls}>
        {error ? <Text style={styles.error}>{error}</Text> : null}
        <Pressable
          style={[styles.shareBtn, sharing ? styles.stop : styles.start, (busy || (!sharing && enabled !== true)) && styles.busy]}
          onPress={toggleShare}
          disabled={busy || (!sharing && enabled !== true)}>
          <Feather name="map-pin" size={16} color="#fff" />
          <Text style={styles.shareText}>{busy ? '…' : sharing ? 'Stop sharing' : 'Share my location'}</Text>
        </Pressable>
        {sharing ? <Text style={styles.hint}>Sharing on this ride — works with the screen off.</Text> : null}
        {!sharing && enabled === false ? (
          <View style={styles.offNote}>
            <Text style={styles.hint}>Location sharing is off for your account.</Text>
            <Pressable onPress={() => router.push('/settings')}>
              <Text style={styles.link}>Turn it on in Settings</Text>
            </Pressable>
          </View>
        ) : null}
      </View>

      <ScrollView style={styles.cards} contentContainerStyle={{ padding: 12, paddingBottom: 24 + insets.bottom }}>
        {isLoading ? <ActivityIndicator style={{ marginTop: 16 }} /> : null}
        <PlanSelector plans={plans} applied={appliedPlanId} onSelect={setSelectedPlanId} />
        {(positions ?? []).map((p) => <RiderCard key={p.rider_id} p={p} />)}
        {!isLoading && !(positions ?? []).length ? <Text style={styles.empty}>No live riders yet.</Text> : null}
        {chartData ? <LiveCharts chart={chartData} positions={positions ?? []} /> : null}
        {/* Shared upcoming-controls list — placed AFTER the weather charts. */}
        <UpcomingControls controls={upcoming} showOwnNote={String(appliedPlanId) === 'own'} />
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f7fafc' },
  // Smaller than the cards below so the first rider stats peek above the fold
  // (otherwise users don't realize there's data to scroll to).
  map: { flex: 0.9 },
  pin: { width: 34, height: 34, borderRadius: 17, borderWidth: 2, borderColor: '#fff', alignItems: 'center', justifyContent: 'center' },
  pinText: { color: '#fff', fontWeight: '700', fontSize: 12 },
  pinAct: { position: 'absolute', bottom: -10, fontSize: 12 },
  legend: {
    position: 'absolute', top: 8, left: 8, flexDirection: 'row', flexWrap: 'wrap', gap: 10,
    backgroundColor: 'rgba(255,255,255,0.9)', paddingHorizontal: 8, paddingVertical: 5, borderRadius: 8,
  },
  legendItem: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  legendDot: { width: 9, height: 9, borderRadius: 5 },
  legendText: { fontSize: 10, color: '#374151' },
  controls: { paddingHorizontal: 16, paddingVertical: 10, backgroundColor: '#fff', borderTopWidth: 1, borderColor: '#e5e7eb' },
  routeLoadingWrap: { position: 'absolute', top: 12, left: 0, right: 0, alignItems: 'center' },
  routeLoadingPill: {
    flexDirection: 'row', alignItems: 'center', gap: 8, backgroundColor: '#fff',
    paddingVertical: 6, paddingHorizontal: 12, borderRadius: 20,
    shadowColor: '#000', shadowOpacity: 0.15, shadowRadius: 4, shadowOffset: { width: 0, height: 2 }, elevation: 3,
  },
  routeLoadingText: { color: '#1a365d', fontSize: 13, fontWeight: '600' },
  offNote: { alignItems: 'center', marginTop: 8, gap: 2 },
  link: { color: '#2563eb', fontWeight: '700', fontSize: 13 },
  shareBtn: { flexDirection: 'row', gap: 8, paddingVertical: 13, borderRadius: 12, alignItems: 'center', justifyContent: 'center' },
  start: { backgroundColor: '#16a34a' },
  stop: { backgroundColor: '#dc2626' },
  busy: { opacity: 0.6 },
  shareText: { color: '#fff', fontWeight: '700', fontSize: 16 },
  hint: { color: '#374151', fontSize: 12, marginTop: 6, textAlign: 'center' },
  error: { color: '#b91c1c', marginBottom: 8, textAlign: 'center', fontSize: 13 },
  cards: { flex: 1 },
  card: { backgroundColor: '#fff', borderRadius: 12, padding: 12, marginBottom: 10, borderWidth: 1, borderColor: '#e5e7eb' },
  cardStale: { opacity: 0.55 },
  cardHead: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 8 },
  dot: { width: 30, height: 30, borderRadius: 15, alignItems: 'center', justifyContent: 'center' },
  dotText: { color: '#fff', fontWeight: '700', fontSize: 11 },
  cardName: { fontSize: 15, fontWeight: '700' },
  cardMeta: { color: '#6b7280', fontSize: 12 },
  badge: { fontSize: 12, fontWeight: '700' },
  metricRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 14, marginTop: 4 },
  nextControl: { marginTop: 10, paddingTop: 8, borderTopWidth: 1, borderTopColor: '#eef2f7' },
  nextControlName: { fontSize: 12, fontWeight: '700', color: '#1a365d' },
  metric: { minWidth: 56 },
  metricVal: { fontSize: 14, fontWeight: '700', color: '#1a365d' },
  metricLbl: { fontSize: 10, color: '#6b7280', textTransform: 'uppercase' },
  afterRide: { fontStyle: 'italic', color: '#6b7280', fontSize: 11, marginTop: 8 },
  empty: { color: '#6b7280', textAlign: 'center', marginTop: 16 },
  chartsTitle: { fontSize: 15, fontWeight: '800', color: '#1a365d', marginTop: 6, marginBottom: 8 },
  planWrap: { marginBottom: 10 },
  planTitle: { fontSize: 11, fontWeight: '700', color: '#6b7280', textTransform: 'uppercase', marginBottom: 6 },
  planChips: { gap: 8, paddingRight: 8 },
  planChip: { paddingVertical: 6, paddingHorizontal: 12, borderRadius: 16, borderWidth: 1, borderColor: '#cbd5e1', backgroundColor: '#fff' },
  planChipOn: { backgroundColor: '#1a365d', borderColor: '#1a365d' },
  planChipText: { fontSize: 12, color: '#1a365d', fontWeight: '600' },
  planChipTextOn: { color: '#fff' },
  planNote: { fontSize: 11, color: '#6b7280', marginTop: 6 },
  planLabel: { fontSize: 13, color: '#6b7280', marginBottom: 10 },
  planLabelStrong: { fontWeight: '700', color: '#1a365d' },
  ucBox: { backgroundColor: '#fff', borderRadius: 12, padding: 12, marginBottom: 10, borderWidth: 1, borderColor: '#e5e7eb' },
  ucTitle: { fontSize: 14, fontWeight: '800', color: '#1a365d', marginBottom: 8 },
  ucRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 5, borderTopWidth: 1, borderTopColor: '#f1f5f9' },
  ucName: { flex: 1, fontSize: 13, fontWeight: '600', color: '#1a365d' },
  ucDist: { fontSize: 12, color: '#6b7280' },
  ucEta: { fontSize: 13, fontWeight: '700', color: '#1a365d' },
});
