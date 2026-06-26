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
import type { LivePosition } from '../../lib/types';

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

function planBadge(p: LivePosition): { text: string; color: string } | null {
  const t = p.telemetry;
  if (!t) return null;
  if (t.on_route === false) return { text: 'Off route', color: '#dc2626' };
  if (!t.plan) return null;
  if (t.plan.status === 'ahead') return { text: `${t.plan.delta_min} min ahead`, color: '#16a34a' };
  if (t.plan.status === 'behind') return { text: `${Math.abs(t.plan.delta_min)} min behind`, color: '#dc2626' };
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
  const badge = planBadge(p);
  return (
    <View style={[styles.card, p.stale && styles.cardStale]}>
      <View style={styles.cardHead}>
        <View style={[styles.dot, { backgroundColor: p.color }]}><Text style={styles.dotText}>{initials(p.name)}</Text></View>
        <View style={{ flex: 1 }}>
          <Text style={styles.cardName}>{p.name || 'Rider'} {p.source === 'garmin' ? '⌚' : '📱'}</Text>
          <Text style={styles.cardMeta}>updated {p.minutes_ago <= 0 ? 'just now' : `${p.minutes_ago} min ago`}</Text>
        </View>
        {badge ? <Text style={[styles.badge, { color: badge.color }]}>{badge.text}</Text> : null}
      </View>
      {now ? (
        <View style={styles.metricRow}>
          {now.distance_mi != null ? <Metric label="done" value={n(now.distance_mi, ' mi')} /> : null}
          <Metric label="speed" value={n(now.speed_mph, ' mph')} />
          {now.activity ? <Metric label="state" value={`${ACTIVITY_ICON[now.activity] ?? ''} ${now.activity}`} /> : null}
          <Metric label="moving" value={n(now.moving_min, ' min')} />
          <Metric label="stopped" value={n(now.stopped_min, ' min')} />
          {now.ascent_done_ft != null ? <Metric label="climb" value={n(now.ascent_done_ft, ' ft')} /> : null}
          {now.heart_rate != null ? <Metric label="HR" value={n(now.heart_rate, ' bpm')} /> : null}
          {now.power != null ? <Metric label="power" value={n(now.power, ' W')} /> : null}
          {now.headwind_done_label ? <Metric label="wind" value={now.headwind_done_label} /> : null}
        </View>
      ) : null}
      {rem ? (
        <View style={styles.metricRow}>
          <Metric label="to go" value={n(rem.distance_mi, ' mi')} />
          <Metric label="climb left" value={n(rem.ascent_left_ft, ' ft')} />
          <Metric label="time left" value={n(rem.time_left_min, ' min')} />
          {rem.toughness != null ? <Metric label="toughness" value={String(rem.toughness)} /> : null}
          {rem.headwind_ahead_label ? <Metric label="wind ahead" value={rem.headwind_ahead_label} /> : null}
        </View>
      ) : null}
      {t?.detailed_after_ride ? <Text style={styles.afterRide}>Power, pedaling & coasting time available after the ride.</Text> : null}
    </View>
  );
}

export default function RideLiveScreen() {
  const params = useLocalSearchParams<{ id: string }>();
  const rideId = parseInt(String(params.id), 10);
  const router = useRouter();
  const { data: positions, isLoading } = useLivePositions(rideId);
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
            <Pressable onPress={() => router.push(`/ride/plan?id=${rideId}`)} hitSlop={12}>
              <Feather name="list" size={22} color="#1a365d" />
            </Pressable>
            <Pressable onPress={() => router.push(`/ride/weather?id=${rideId}`)} hitSlop={12}>
              <Feather name="cloud-drizzle" size={22} color="#1a365d" />
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
              <View style={[styles.pin, { backgroundColor: p.color, borderStyle: p.source === 'garmin' ? 'solid' : 'dashed' }]}>
                <Text style={styles.pinText}>{initials(p.name)}</Text>
                {act ? <Text style={styles.pinAct}>{ACTIVITY_ICON[act] ?? ''}</Text> : null}
              </View>
            </Marker>
          );
        })}
      </MapView>

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

      <ScrollView style={styles.cards} contentContainerStyle={{ padding: 12, paddingBottom: 24 }}>
        {isLoading ? <ActivityIndicator style={{ marginTop: 16 }} /> : null}
        {(positions ?? []).map((p) => <RiderCard key={p.rider_id} p={p} />)}
        {!isLoading && !(positions ?? []).length ? <Text style={styles.empty}>No live riders yet.</Text> : null}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f7fafc' },
  map: { flex: 1.3 },
  pin: { width: 34, height: 34, borderRadius: 17, borderWidth: 2, borderColor: '#fff', alignItems: 'center', justifyContent: 'center' },
  pinText: { color: '#fff', fontWeight: '700', fontSize: 12 },
  pinAct: { position: 'absolute', bottom: -10, fontSize: 12 },
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
  metric: { minWidth: 56 },
  metricVal: { fontSize: 14, fontWeight: '700', color: '#1a365d' },
  metricLbl: { fontSize: 10, color: '#6b7280', textTransform: 'uppercase' },
  afterRide: { fontStyle: 'italic', color: '#6b7280', fontSize: 11, marginTop: 8 },
  empty: { color: '#6b7280', textAlign: 'center', marginTop: 16 },
});
