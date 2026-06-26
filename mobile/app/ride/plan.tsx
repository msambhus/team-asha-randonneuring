/**
 * mobile/app/ride/plan.tsx — a ride's plan (GET /api/ride/<id>/plan).
 *
 * Mirrors the web ride-plan page: a header (distance/elevation/cutoff/start) and a
 * compact stops table — location · cumulative distance · segment time · time bank ·
 * ETA · wind. Tap any stop to expand the rest (elapsed, climb, ft/mi, break, temp,
 * notes). Reached from the ride's live-map header. Reads ?id=<rideId>.
 */
import { useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useLocalSearchParams } from 'expo-router';
import { useRidePlan } from '../../hooks/useRidePlan';
import { useAllowRotation } from '../../hooks/useAllowRotation';
import type { PlanStop, RidePlanAvailable } from '../../lib/types';

const RED = '#dc2626', GREEN = '#16a34a', BLUE = '#2563eb';

const TYPE_COLOR: Record<string, string> = {
  start: GREEN, finish: '#1a365d', control: BLUE, rest: '#d97706', waypoint: '#9ca3af',
};

/** minutes → "3h15m" / "47m". */
function hm(min: number): string {
  const a = Math.abs(Math.round(min));
  const h = Math.floor(a / 60), m = a % 60;
  return h ? `${h}h${m.toString().padStart(2, '0')}m` : `${m}m`;
}

function windColor(label?: string | null): string {
  if (!label) return '#6b7280';
  if (label.includes('headwind')) return RED;
  if (label.includes('tailwind')) return GREEN;
  return BLUE;
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.detailItem}>
      <Text style={styles.detailLbl}>{label}</Text>
      <Text style={styles.detailVal}>{value}</Text>
    </View>
  );
}

function StopRow({ s, expanded, onToggle }: { s: PlanStop; expanded: boolean; onToggle: () => void }) {
  const bank = s.time_bank_min;
  const bankColor = bank == null ? '#6b7280' : bank >= 0 ? GREEN : RED;
  const wind = s.wind_speed_mph;
  return (
    <View style={[styles.rowWrap, expanded && styles.rowWrapOpen]}>
      <Pressable onPress={onToggle} style={styles.tr}>
        <View style={[styles.typeDot, { backgroundColor: TYPE_COLOR[s.stop_type] || '#9ca3af' }]} />
        <Text style={[styles.td, styles.cLoc]} numberOfLines={1}>{s.location || 'Stop'}</Text>
        <Text style={[styles.td, styles.cNum]}>{Math.round(s.distance_mi)}</Text>
        <Text style={[styles.td, styles.cNum]}>{s.segment_time_min ? hm(s.segment_time_min) : '—'}</Text>
        <Text style={[styles.td, styles.cBank, { color: bankColor }]}>
          {bank == null ? '—' : (bank >= 0 ? '+' : '−') + hm(bank)}
        </Text>
        <Text style={[styles.td, styles.cEta]}>{s.eta}</Text>
        <Text style={[styles.td, styles.cWind, { color: windColor(s.wind_label) }]}>
          {wind != null ? Math.round(wind) : '—'}
        </Text>
      </Pressable>
      {expanded ? (
        <View style={styles.detailPanel}>
          <View style={styles.detailGrid}>
            <Detail label="elapsed" value={hm(s.cum_time_min)} />
            <Detail label="climb" value={s.elevation_gain_ft ? `${s.elevation_gain_ft.toLocaleString()} ft` : '—'} />
            <Detail label="ft/mi" value={String(s.ft_per_mi)} />
            {s.stop_duration_min ? (
              <Detail label="break" value={`${s.stop_name ? s.stop_name + ' · ' : ''}${s.stop_duration_min}m`} />
            ) : null}
            {s.temperature_f != null ? <Detail label="temp" value={`${Math.round(s.temperature_f)}°F`} /> : null}
            {wind != null ? <Detail label="wind" value={`${Math.round(wind)} mph ${s.wind_label ?? ''}`.trim()} /> : null}
          </View>
          {s.notes ? <Text style={styles.notes}>{s.notes}</Text> : null}
        </View>
      ) : null}
    </View>
  );
}

function PlanBody({ data, onView }: { data: RidePlanAvailable; onView: (v: 'base' | 'custom') => void }) {
  const [open, setOpen] = useState<number | null>(null);
  const p = data.plan;
  return (
    <ScrollView contentContainerStyle={styles.list}>
      <View style={styles.inner}>
      <View style={styles.card}>
        <Text style={styles.name}>{p.name}</Text>
        <Text style={styles.meta}>
          {p.total_distance_mi ? `${p.total_distance_mi} mi` : ''}
          {p.total_elevation_ft ? ` · ${p.total_elevation_ft.toLocaleString()} ft` : ''}
          {p.cutoff_hours ? ` · ${p.cutoff_hours}h cutoff` : ''}
          {` · start ${p.start_time}`}
        </Text>
        {data.using_custom ? (
          <Text style={styles.customNote}>Showing your custom plan{data.custom_name ? ` · ${data.custom_name}` : ''}.</Text>
        ) : data.has_custom ? (
          <Text style={styles.customNote}>Showing the Team plan.</Text>
        ) : null}
      </View>

      {/* Custom ⇄ Team toggle (only when the rider has a custom plan) */}
      {data.has_custom ? (
        <View style={styles.toggle}>
          <Pressable style={[styles.seg, data.using_custom && styles.segOn]} onPress={() => onView('custom')}>
            <Text style={[styles.segText, data.using_custom && styles.segTextOn]}>Your plan</Text>
          </Pressable>
          <Pressable style={[styles.seg, !data.using_custom && styles.segOn]} onPress={() => onView('base')}>
            <Text style={[styles.segText, !data.using_custom && styles.segTextOn]}>Team plan</Text>
          </Pressable>
        </View>
      ) : null}

      <View style={styles.card}>
        <View style={[styles.tr, styles.thead]}>
          <View style={styles.typeDot} />
          <Text style={[styles.th, styles.cLoc]}>stop</Text>
          <Text style={[styles.th, styles.cNum]}>mi</Text>
          <Text style={[styles.th, styles.cNum]}>seg</Text>
          <Text style={[styles.th, styles.cBank]}>bank</Text>
          <Text style={[styles.th, styles.cEta]}>eta</Text>
          <Text style={[styles.th, styles.cWind]}>wind</Text>
        </View>
        {data.stops.map((s) => (
          <StopRow key={s.stop_order} s={s} expanded={open === s.stop_order}
            onToggle={() => setOpen(open === s.stop_order ? null : s.stop_order)} />
        ))}
      </View>

      <Text style={styles.foot}>
        Tap a stop for ETA, break, climb &amp; notes. Time bank is your cushion vs the
        RUSA cutoff{data.stops.some((s) => s.wind_speed_mph != null) ? ' · wind from Open-Meteo' : ''}.
      </Text>
      </View>
    </ScrollView>
  );
}

export default function RidePlanScreen() {
  useAllowRotation();   // the wide plan table benefits from landscape
  const params = useLocalSearchParams<{ id: string }>();
  const rideId = parseInt(String(params.id), 10);
  const [view, setView] = useState<'base' | 'custom' | undefined>(undefined);
  const { data, isLoading, isError, refetch } = useRidePlan(rideId, view);

  if (isLoading) return <View style={styles.center}><ActivityIndicator /></View>;
  if (isError || !data) {
    return (
      <View style={styles.center}>
        <Text style={styles.muted}>Couldn't load the ride plan.</Text>
        <Text style={styles.link} onPress={() => refetch()}>Retry</Text>
      </View>
    );
  }
  if (!data.available) {
    return (
      <View style={styles.center}>
        <Text style={styles.bigEmoji}>🗺️</Text>
        <Text style={styles.unavailable}>{data.message}</Text>
      </View>
    );
  }
  return <PlanBody data={data} onView={setView} />;
}

const styles = StyleSheet.create({
  list: { padding: 16, paddingBottom: 28 },
  // Cap + center the column so the flex "stop" cell doesn't sprawl in landscape / on tablets.
  inner: { width: '100%', maxWidth: 640, alignSelf: 'center' },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 8, padding: 24 },
  muted: { color: '#6b7280' },
  link: { color: BLUE, fontWeight: '700' },
  bigEmoji: { fontSize: 40 },
  unavailable: { color: '#374151', fontSize: 15, textAlign: 'center' },
  card: { backgroundColor: '#fff', borderRadius: 12, padding: 12, marginBottom: 12, borderWidth: 1, borderColor: '#e5e7eb' },
  name: { fontSize: 18, fontWeight: '800', color: '#1a365d' },
  meta: { color: '#6b7280', fontSize: 13, marginTop: 4 },
  customNote: { color: '#4338ca', fontSize: 12, fontWeight: '600', marginTop: 6 },
  toggle: { flexDirection: 'row', backgroundColor: '#eef2f7', borderRadius: 10, padding: 3, marginBottom: 12 },
  seg: { flex: 1, paddingVertical: 8, borderRadius: 8, alignItems: 'center' },
  segOn: { backgroundColor: '#fff', borderWidth: 1, borderColor: '#c7d2fe' },
  segText: { fontSize: 13, fontWeight: '700', color: '#6b7280' },
  segTextOn: { color: '#4338ca' },
  // table
  rowWrap: { borderRadius: 8 },
  rowWrapOpen: { backgroundColor: '#f8fafc' },
  tr: { flexDirection: 'row', alignItems: 'center', paddingVertical: 8, paddingHorizontal: 2, gap: 4 },
  thead: { borderBottomWidth: 1, borderBottomColor: '#e5e7eb', paddingBottom: 6 },
  typeDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: 'transparent' },
  th: { fontSize: 10, fontWeight: '700', color: '#9ca3af', textTransform: 'uppercase' },
  td: { fontSize: 12.5, color: '#1f2937' },
  cLoc: { flex: 1, fontWeight: '600' },
  cNum: { width: 40, textAlign: 'right' },
  cBank: { width: 50, textAlign: 'right', fontWeight: '700' },
  cEta: { width: 64, textAlign: 'right' },
  cWind: { width: 38, textAlign: 'right', fontWeight: '600' },
  detailPanel: { paddingHorizontal: 8, paddingBottom: 10, paddingTop: 2 },
  detailGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 14 },
  detailItem: { minWidth: 60 },
  detailLbl: { fontSize: 9, color: '#6b7280', textTransform: 'uppercase' },
  detailVal: { fontSize: 13, fontWeight: '700', color: '#1a365d' },
  notes: { marginTop: 8, color: '#374151', fontSize: 12.5, fontStyle: 'italic' },
  foot: { color: '#9ca3af', fontSize: 11, textAlign: 'center', marginTop: 4 },
});
