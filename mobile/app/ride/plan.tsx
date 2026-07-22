/**
 * mobile/app/ride/plan.tsx — a ride's plan (GET /api/ride/<id>/plan).
 *
 * Mirrors the web rpv2 plan page: a header (distance/elevation/cutoff/start), a live
 * gradient elevation profile with control/break dots, an inline "Choose your pace"
 * selector (Comfort/Standard/Push), and a compact stops table — location · cumulative
 * distance · segment time · time bank · ETA · wind. Tap any stop to expand the rest
 * (elapsed, climb, ft/mi, break, temp, notes). Reached from the ride's live-map header.
 * Reads ?id=<rideId>.
 *
 * ⚠️ EAS-ONLY VERIFICATION: the SVG elevation profile and the pace-selector tap →
 * itinerary + overlay re-render are React Native surfaces. The harness Playwright step
 * verifies only the backend JSON contract (GET /api/ride/<id>/plan's `elevation_profile`
 * + `pace_stops_map` fields, guest-safe no-live-fetch — see
 * tests/test_api_ride_plan_elevation.py). The visual RN output requires a manual EAS
 * build / Expo simulator; the jest test below asserts the components render given a
 * mocked profile + pace map, but does NOT prove pixel output.
 */
import { useState } from 'react';
import {
  ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View, useWindowDimensions,
} from 'react-native';
import Svg, { Circle, Path, Text as SvgText } from 'react-native-svg';
import { useLocalSearchParams } from 'expo-router';
import { useRidePlan } from '../../hooks/useRidePlan';
import { useAllowRotation } from '../../hooks/useAllowRotation';
import type {
  ElevationProfileAvailable, PaceStop, PlanStop, RidePlanAvailable,
} from '../../lib/types';
import { colors } from '../../lib/theme';
import { windColor } from '../../lib/format';

const RED = colors.red, GREEN = colors.green, BLUE = colors.blue;

const TYPE_COLOR: Record<string, string> = {
  start: GREEN, finish: colors.navy, control: BLUE, rest: colors.amber, waypoint: colors.placeholder,
};

// The three pace variants the backend serves in pace_stops_map, in slowest→fastest order.
const PACE_ORDER = ['comfort', 'standard', 'push'] as const;
const PACE_LABEL: Record<string, string> = { comfort: 'Comfort', standard: 'Standard', push: 'Push' };
type PaceId = (typeof PACE_ORDER)[number];

/** minutes → "3h15m" / "47m". */
function hm(min: number): string {
  const a = Math.abs(Math.round(min));
  const h = Math.floor(a / 60), m = a % 60;
  return h ? `${h}h${m.toString().padStart(2, '0')}m` : `${m}m`;
}

// ── Normalized itinerary row ─────────────────────────────────────────────────
// The base plan (PlanStop) and a pace variant (PaceStop) have different shapes, so
// both map into one Row the table renders — the base and pace views share the row
// renderer and can't drift (plan risk: "RN table shape drift").
interface Row {
  key: string;
  stopType: string;
  location: string;
  cumulMi: number;
  segTimeMin: number;
  bankMin: number | null;
  eta: string;
  windMph: number | null;
  windLabel: string | null;
  elapsedLabel: string;
  climbFt: number | null;
  ftPerMi: number;
  breakMin: number;
  breakName: string | null;
  tempF: number | null;
  notes: string | null;
}

function fromPlanStop(s: PlanStop): Row {
  return {
    key: `p${s.stop_order}`,
    stopType: s.stop_type,
    location: s.location || 'Stop',
    cumulMi: s.distance_mi,
    segTimeMin: s.segment_time_min,
    bankMin: s.time_bank_min,
    eta: s.eta,
    windMph: s.wind_speed_mph ?? null,
    windLabel: s.wind_label ?? null,
    elapsedLabel: hm(s.cum_time_min),
    climbFt: s.elevation_gain_ft,
    ftPerMi: s.ft_per_mi,
    breakMin: s.stop_duration_min,
    breakName: s.stop_name,
    tempF: s.temperature_f ?? null,
    notes: s.notes,
  };
}

function fromPaceStop(s: PaceStop): Row {
  return {
    key: `s${s.i}`,
    stopType: s.type,
    location: s.name || 'Stop',
    cumulMi: s.cumul_mi,
    segTimeMin: s.seg_time_min,
    bankMin: s.bank_min,
    eta: s.eta,
    windMph: s.wind_known ? s.headwind_mph : null,
    windLabel: s.wind_label || null,
    elapsedLabel: s.elapsed,
    climbFt: null,               // pace stops carry ft/mi, not absolute climb
    ftPerMi: s.fpm,
    breakMin: s.break_min,
    breakName: null,
    tempF: null,
    notes: null,
  };
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.detailItem}>
      <Text style={styles.detailLbl}>{label}</Text>
      <Text style={styles.detailVal}>{value}</Text>
    </View>
  );
}

function StopRow({ r, expanded, onToggle }: { r: Row; expanded: boolean; onToggle: () => void }) {
  const bank = r.bankMin;
  const bankColor = bank == null ? '#6b7280' : bank >= 0 ? GREEN : RED;
  const wind = r.windMph;
  return (
    <View style={[styles.rowWrap, expanded && styles.rowWrapOpen]}>
      <Pressable onPress={onToggle} style={styles.tr}>
        <View style={[styles.typeDot, { backgroundColor: TYPE_COLOR[r.stopType] || colors.placeholder }]} />
        <Text style={[styles.td, styles.cLoc]} numberOfLines={1}>{r.location}</Text>
        <Text style={[styles.td, styles.cNum]}>{Math.round(r.cumulMi)}</Text>
        <Text style={[styles.td, styles.cNum]}>{r.segTimeMin ? hm(r.segTimeMin) : '—'}</Text>
        <Text style={[styles.td, styles.cBank, { color: bankColor }]}>
          {bank == null ? '—' : (bank >= 0 ? '+' : '−') + hm(bank)}
        </Text>
        <Text style={[styles.td, styles.cEta]}>{r.eta}</Text>
        <Text style={[styles.td, styles.cWind, { color: windColor(r.windLabel) }]}>
          {wind != null ? Math.round(wind) : '—'}
        </Text>
      </Pressable>
      {expanded ? (
        <View style={styles.detailPanel}>
          <View style={styles.detailGrid}>
            <Detail label="elapsed" value={r.elapsedLabel} />
            {r.climbFt != null ? (
              <Detail label="climb" value={r.climbFt ? `${r.climbFt.toLocaleString()} ft` : '—'} />
            ) : null}
            <Detail label="ft/mi" value={String(r.ftPerMi)} />
            {r.breakMin ? (
              <Detail label="break" value={`${r.breakName ? r.breakName + ' · ' : ''}${r.breakMin}m`} />
            ) : null}
            {r.tempF != null ? <Detail label="temp" value={`${Math.round(r.tempF)}°F`} /> : null}
            {wind != null ? <Detail label="wind" value={`${Math.round(wind)} mph ${r.windLabel ?? ''}`.trim()} /> : null}
          </View>
          {r.notes ? <Text style={styles.notes}>{r.notes}</Text> : null}
        </View>
      ) : null}
    </View>
  );
}

// ── Elevation profile ────────────────────────────────────────────────────────
// Client twins of shared/live_radial.py::place_x / place_y so a control dot lands on
// the same pixel the server would compute — used to reposition the overlay when the
// pace changes (the ETAs update; the x is route-constant), client-side, no refetch.
function placeX(distMi: number, p: ElevationProfileAvailable): number | null {
  if (p.total_mi <= 0) return null;
  const frac = Math.min(1, Math.max(0, distMi / p.total_mi));
  return p.plot.x + frac * p.plot.w;
}

function placeY(distMi: number, p: ElevationProfileAvailable): number | null {
  const x = placeX(distMi, p);
  if (x == null || !p.points.length) return null;
  for (let i = 1; i < p.points.length; i++) {
    if (x <= p.points[i][0]) {
      const [x0, y0] = p.points[i - 1];
      const [x1, y1] = p.points[i];
      const t = x1 > x0 ? (x - x0) / (x1 - x0) : 0;
      return y0 + t * (y1 - y0);
    }
  }
  return p.points[p.points.length - 1][1];
}

/** The gradient elevation profile: per-segment coloured line (colours baked server-side
 *  from the _GRADE_BUCKETS map) + control/break dots re-derived from the visible stops. */
function ElevationProfileView({ profile, rows }: { profile: ElevationProfileAvailable; rows: Row[] }) {
  const { width: screenW } = useWindowDimensions();
  const W = Math.max(240, Math.min(640, screenW) - 32 - 24); // screen + card padding
  const H = W * (profile.height / profile.width);            // preserve the 5:1 aspect
  // Markers re-derive from the CURRENT itinerary on every render, so picking a pace
  // repositions them (ETA/type follow the selected variant) with no extra fetch.
  const markers = rows
    .map((r) => ({ x: placeX(r.cumulMi, profile), y: placeY(r.cumulMi, profile),
                   color: TYPE_COLOR[r.stopType] || colors.placeholder }))
    .filter((m): m is { x: number; y: number; color: string } => m.x != null && m.y != null);
  return (
    <View style={styles.card}>
      <Text style={styles.sectionTitle}>Elevation</Text>
      <Svg width={W} height={H} viewBox={`0 0 ${profile.width} ${profile.height}`}>
        <Path d={profile.area_path} fill={colors.navy} opacity={0.06} />
        {profile.segments.map((seg, i) => (
          <Path key={i} d={seg.d} stroke={seg.color} strokeWidth={2.5} fill="none" />
        ))}
        {markers.map((m, i) => (
          <Circle key={i} cx={m.x} cy={m.y} r={5} fill={m.color} stroke="#fff" strokeWidth={2} />
        ))}
        <SvgText x={4} y={profile.plot.y + 10} fontSize={11} fill="#6b7280">{profile.max_ft}</SvgText>
        <SvgText x={4} y={profile.plot.y + profile.plot.h} fontSize={11} fill="#6b7280">{profile.min_ft}</SvgText>
      </Svg>
      <View style={styles.legendRow}>
        {profile.legend.map((l) => (
          <View key={l.label} style={styles.legendItem}>
            <View style={[styles.swatch, { backgroundColor: l.color }]} />
            <Text style={styles.legendText}>{l.label}</Text>
          </View>
        ))}
      </View>
    </View>
  );
}

/** Inline "Choose your pace" selector — Comfort / Standard / Push. Picking a pace swaps
 *  the itinerary + overlay client-side (no refetch). Only the paces present in the map
 *  are offered. `active` is null when the base plan is shown. */
function PaceSelector(
  { paces, active, onPick }:
  { paces: PaceId[]; active: PaceId | null; onPick: (p: PaceId | null) => void },
) {
  return (
    <View style={styles.card}>
      <Text style={styles.sectionTitle}>Choose your pace</Text>
      <View style={styles.toggle}>
        {paces.map((pid) => {
          const on = active === pid;
          return (
            <Pressable key={pid} style={[styles.seg, on && styles.segOn]}
              accessibilityRole="button" accessibilityState={{ selected: on }}
              onPress={() => onPick(on ? null : pid)}>
              <Text style={[styles.segText, on && styles.segTextOn]}>{PACE_LABEL[pid]}</Text>
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}

function PlanBody({ data, onView }: { data: RidePlanAvailable; onView: (v: 'base' | 'custom') => void }) {
  const [open, setOpen] = useState<string | null>(null);
  const [pace, setPace] = useState<PaceId | null>(null);
  const p = data.plan;

  const paceMap = data.pace_stops_map ?? {};
  const paces = PACE_ORDER.filter((pid) => (paceMap[pid]?.length ?? 0) > 0);
  const profile = data.elevation_profile?.available ? data.elevation_profile : null;

  // The visible itinerary: the picked pace's stops, else the base/custom plan stops.
  const rows: Row[] = (pace && paceMap[pace])
    ? paceMap[pace].map(fromPaceStop)
    : data.stops.map(fromPlanStop);

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

      {/* Live gradient elevation profile with control/break dots (parity with web PR #534). */}
      {profile ? <ElevationProfileView profile={profile} rows={rows} /> : null}

      {/* Inline pace selector — swaps the itinerary + overlay on pick, client-side. */}
      {paces.length ? <PaceSelector paces={paces} active={pace} onPick={setPace} /> : null}

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
        {rows.map((r) => (
          <StopRow key={r.key} r={r} expanded={open === r.key}
            onToggle={() => setOpen(open === r.key ? null : r.key)} />
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
        <Pressable onPress={() => refetch()} hitSlop={8} accessibilityRole="button" accessibilityLabel="Retry loading the ride plan">
          <Text style={styles.link}>Retry</Text>
        </Pressable>
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
  sectionTitle: { fontSize: 13, fontWeight: '700', color: '#1a365d', marginBottom: 8 },
  toggle: { flexDirection: 'row', backgroundColor: '#eef2f7', borderRadius: 10, padding: 3, marginBottom: 12 },
  seg: { flex: 1, paddingVertical: 8, borderRadius: 8, alignItems: 'center' },
  segOn: { backgroundColor: '#fff', borderWidth: 1, borderColor: '#c7d2fe' },
  segText: { fontSize: 13, fontWeight: '700', color: '#6b7280' },
  segTextOn: { color: '#4338ca' },
  // elevation legend
  legendRow: { flexDirection: 'row', gap: 10, flexWrap: 'wrap', marginTop: 8 },
  legendItem: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  swatch: { width: 10, height: 10, borderRadius: 2 },
  legendText: { fontSize: 10, color: '#6b7280' },
  // table
  rowWrap: { borderRadius: 8 },
  rowWrapOpen: { backgroundColor: '#f8fafc' },
  tr: { flexDirection: 'row', alignItems: 'center', paddingVertical: 8, paddingHorizontal: 2, gap: 4 },
  thead: { borderBottomWidth: 1, borderBottomColor: '#e5e7eb', paddingBottom: 6 },
  typeDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: 'transparent' },
  th: { fontSize: 10, fontWeight: '700', color: colors.textMuted, textTransform: 'uppercase' },
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
  foot: { color: colors.textMuted, fontSize: 11, textAlign: 'center', marginTop: 4 },
});
