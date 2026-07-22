/**
 * mobile/app/ride/weather.tsx — a ride's weather forecast (GET /api/ride/<id>/weather).
 *
 * Mirrors the web /weather page on a phone: a summary card, a wind map (route line
 * + color-coded wind arrows), a per-segment table, and the six charts (temperature,
 * wind, headwind/tailwind, precipitation, elevation, humidity).
 *
 * Interactive like the web: a shared selectedIndex links everything — scrubbing any
 * chart, tapping a wind arrow on the map, or tapping a table row selects that point;
 * a sticky detail card shows its full forecast and every chart draws a synced
 * crosshair while the map rings the spot. Reached from the ride's live-map header.
 */
import { useMemo, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, useWindowDimensions, View } from 'react-native';
import { useLocalSearchParams } from 'expo-router';
import MapView, { Marker, Polyline, Region } from 'react-native-maps';
import { useRideWeather } from '../../hooks/useRideWeather';
import { useAllowRotation } from '../../hooks/useAllowRotation';
import { WeatherChart } from '../../components/WeatherChart';
import type { RideWeatherAvailable, WeatherSegment } from '../../lib/types';
import { colors } from '../../lib/theme';
import { windColor } from '../../lib/format';

const RED = colors.red, GREEN = colors.green, BLUE = colors.blue;

function regionForCoords(coords: { latitude: number; longitude: number }[]): Region | null {
  if (!coords.length) return null;
  const lats = coords.map((c) => c.latitude);
  const lngs = coords.map((c) => c.longitude);
  const minLat = Math.min(...lats), maxLat = Math.max(...lats);
  const minLng = Math.min(...lngs), maxLng = Math.max(...lngs);
  return {
    latitude: (minLat + maxLat) / 2,
    longitude: (minLng + maxLng) / 2,
    latitudeDelta: Math.max(0.05, (maxLat - minLat) * 1.3),
    longitudeDelta: Math.max(0.05, (maxLng - minLng) * 1.3),
  };
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.metric}>
      <Text style={styles.metricVal}>{value}</Text>
      <Text style={styles.metricLbl}>{label}</Text>
    </View>
  );
}

/** Sticky card showing the selected point's full forecast (mirrors the web popup). */
function DetailCard({ s }: { s: WeatherSegment }) {
  const hw = Math.round(s.headwind_mph);
  return (
    <View style={styles.detail}>
      <View style={styles.detailHead}>
        <Text style={styles.detailTitle}>Mile {Math.round(s.distance_mi)} · {s.arrival_time}</Text>
        <Text style={styles.detailCond}>{s.conditions_icon} {s.conditions}</Text>
      </View>
      <View style={styles.metricRow}>
        <Metric label="temp" value={`${Math.round(s.temperature_f)}°`} />
        <Metric label="feels" value={`${Math.round(s.feels_like_f)}°`} />
        <Metric label="wind" value={`${Math.round(s.wind_speed_mph)}`} />
        <Metric label="gust" value={`${Math.round(s.wind_gust_mph)}`} />
        <Metric label={hw >= 0 ? 'headwind' : 'tailwind'} value={`${Math.abs(hw)}`} />
      </View>
      <View style={styles.metricRow}>
        <Metric label="precip" value={`${s.precip_percent}%`} />
        <Metric label="rain" value={`${s.precipitation_mm} mm`} />
        <Metric label="cloud" value={`${s.cloud_cover}%`} />
        <Metric label="humid" value={`${s.humidity}%`} />
        <Metric label="climb" value={`${s.elevation_ft.toLocaleString()}`} />
      </View>
    </View>
  );
}

function SegmentRow({ s, active, onPress }: { s: WeatherSegment; active: boolean; onPress: () => void }) {
  const hw = s.headwind_mph;
  const hwColor = hw > 1 ? RED : hw < -1 ? GREEN : '#6b7280';
  return (
    <Pressable onPress={onPress} style={[styles.tr, active && styles.trActive]}>
      <Text style={[styles.td, styles.cDist]}>{Math.round(s.distance_mi)}</Text>
      <Text style={[styles.td, styles.cTime]}>{s.arrival_time}</Text>
      <Text style={[styles.td, styles.cTemp]}>{Math.round(s.temperature_f)}°</Text>
      <Text style={[styles.td, styles.cWind]}>{Math.round(s.wind_speed_mph)}</Text>
      <Text style={[styles.td, styles.cHw, { color: hwColor }]}>
        {hw > 1 ? '↑' : hw < -1 ? '↓' : '·'}{Math.abs(Math.round(hw)) || ''}
      </Text>
      <Text style={[styles.td, styles.cSky]}>{s.conditions_icon}</Text>
      <Text style={[styles.td, styles.cRain]}>{s.precip_percent}%</Text>
    </Pressable>
  );
}

function WeatherBody({ data }: { data: RideWeatherAvailable }) {
  const [sel, setSel] = useState(0);
  const { width, height } = useWindowDimensions();
  const landscape = width > height;   // give the route map more canvas when rotated
  const coords = useMemo(
    () => data.polyline.map(([lat, lng]) => ({ latitude: lat, longitude: lng })),
    [data.polyline],
  );
  const region = useMemo(() => regionForCoords(coords), [coords]);
  const c = data.chart_data;
  const n = c.labels.length;
  const selIdx = Math.min(sel, Math.max(0, n - 1));

  const headPos = useMemo(() => c.headwind_mph.map((v) => (v > 0 ? v : 0)), [c.headwind_mph]);
  const headNeg = useMemo(() => c.headwind_mph.map((v) => (v < 0 ? v : 0)), [c.headwind_mph]);

  // table rows are a subset of map_segments (same objects) — map each to its dense index.
  const tableMapIdx = useMemo(
    () => data.table_segments.map((ts) =>
      Math.max(0, data.map_segments.findIndex((ms) => ms.distance_mi === ts.distance_mi && ms.lat === ts.lat))),
    [data.table_segments, data.map_segments],
  );
  // active table row = the last row whose dense index is ≤ the selection.
  let activeRow = 0;
  for (let j = 0; j < tableMapIdx.length; j++) if (tableMapIdx[j] <= selIdx) activeRow = j;

  const selSeg = data.map_segments[selIdx];

  return (
    <ScrollView contentContainerStyle={styles.list} stickyHeaderIndices={[2]}>
      {/* 0: summary */}
      <View style={styles.card}>
        <Text style={styles.routeName}>{data.route_name}</Text>
        <Text style={styles.summaryMeta}>
          {data.total_distance_mi} mi · {data.total_elevation_ft.toLocaleString()} ft ·{' '}
          {data.temp_range.min_f}–{data.temp_range.max_f}°F
          {data.plan_source ? ` · ${data.plan_source} plan timing` : ''}
        </Text>
        {data.ride_summary ? <Text style={styles.summary}>{data.ride_summary}</Text> : null}
      </View>

      {/* 1: wind map */}
      {region ? (
        <View style={styles.mapCard}>
          <MapView style={[styles.map, landscape && styles.mapLandscape]} initialRegion={region}>
            {coords.length ? <Polyline coordinates={coords} strokeColor={BLUE} strokeWidth={3} /> : null}
            {data.map_segments.map((s, i) => {
              const color = windColor(s.wind_label);
              const rotate = (s.wind_direction_deg + 180) % 360; // arrow points where wind blows TO
              const size = s.wind_speed_mph < 8 ? 14 : s.wind_speed_mph < 16 ? 18 : 22;
              return (
                <Marker key={i} coordinate={{ latitude: s.lat, longitude: s.lng }}
                  anchor={{ x: 0.5, y: 0.5 }} tracksViewChanges={false} onPress={() => setSel(i)}>
                  <Text style={{ fontSize: size, color, transform: [{ rotate: `${rotate}deg` }] }}>↑</Text>
                </Marker>
              );
            })}
            {selSeg ? (
              <Marker key="sel-ring" coordinate={{ latitude: selSeg.lat, longitude: selSeg.lng }}
                anchor={{ x: 0.5, y: 0.5 }} tracksViewChanges={false}>
                <View style={styles.selRing} />
              </Marker>
            ) : null}
          </MapView>
          <View style={styles.legend}>
            <Legend color={RED} label="headwind" />
            <Legend color={GREEN} label="tailwind" />
            <Legend color={BLUE} label="crosswind" />
          </View>
        </View>
      ) : <View />}

      {/* 2: sticky detail card for the selected point */}
      {selSeg ? <DetailCard s={selSeg} /> : <View />}

      {/* 3–8: charts (all share selIdx + setSel) */}
      <WeatherChart title="Temperature" unit="°F" labels={c.labels} selectedIndex={selIdx} onScrub={setSel}
        series={[{ data: c.temperature_f, color: '#ef4444', fill: true }, { data: c.feels_like_f, color: '#f59e0b' }]}
        legend={[{ label: 'temp', color: '#ef4444' }, { label: 'feels like', color: '#f59e0b' }]} />

      <WeatherChart title="Wind" unit="mph" labels={c.labels} selectedIndex={selIdx} onScrub={setSel}
        series={[{ data: c.wind_speed_mph, color: BLUE, fill: true }, { data: c.wind_gust_mph, color: '#93c5fd' }]}
        legend={[{ label: 'wind', color: BLUE }, { label: 'gusts', color: '#93c5fd' }]} />

      <WeatherChart title="Headwind / Tailwind" unit="mph" labels={c.labels} baseline={0} selectedIndex={selIdx} onScrub={setSel}
        series={[{ data: headPos, color: RED, fill: true }, { data: headNeg, color: GREEN, fill: true }]}
        legend={[{ label: 'headwind', color: RED }, { label: 'tailwind', color: GREEN }]} />

      <WeatherChart title="Precipitation & cloud" unit="% chance / % cloud" labels={c.labels} selectedIndex={selIdx} onScrub={setSel}
        series={[{ data: c.precip_probability, color: BLUE, fill: true }, { data: c.cloud_cover, color: '#94a3b8' }]}
        legend={[{ label: 'precip %', color: BLUE }, { label: 'cloud %', color: '#94a3b8' }]} />

      <WeatherChart title="Elevation" unit="ft" labels={c.labels} selectedIndex={selIdx} onScrub={setSel}
        series={[{ data: c.elevation_ft, color: '#15803d', fill: true }]} />

      <WeatherChart title="Humidity" unit="%" labels={c.labels} selectedIndex={selIdx} onScrub={setSel}
        series={[{ data: c.humidity, color: '#0d9488', fill: true }]} />

      {/* 9: per-segment table (full detail) — at the bottom */}
      <View style={styles.card}>
        <View style={[styles.tr, styles.thead]}>
          <Text style={[styles.th, styles.cDist]}>mi</Text>
          <Text style={[styles.th, styles.cTime]}>time</Text>
          <Text style={[styles.th, styles.cTemp]}>temp</Text>
          <Text style={[styles.th, styles.cWind]}>mph</Text>
          <Text style={[styles.th, styles.cHw]}>h/t</Text>
          <Text style={[styles.th, styles.cSky]}>sky</Text>
          <Text style={[styles.th, styles.cRain]}>rain</Text>
        </View>
        {data.table_segments.map((s, j) => (
          <SegmentRow key={j} s={s} active={j === activeRow} onPress={() => setSel(tableMapIdx[j])} />
        ))}
      </View>

      <Text style={styles.attribution}>Tap a chart, arrow, or row to inspect a point · Weather data: Open-Meteo</Text>
    </ScrollView>
  );
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <View style={styles.legendItem}>
      <View style={[styles.swatch, { backgroundColor: color }]} />
      <Text style={styles.legendText}>{label}</Text>
    </View>
  );
}

export default function RideWeatherScreen() {
  useAllowRotation();   // the map + charts + per-segment table benefit from landscape
  const params = useLocalSearchParams<{ id: string }>();
  const rideId = parseInt(String(params.id), 10);
  const { data, isLoading, isError, refetch } = useRideWeather(rideId);

  if (isLoading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator />
        <Text style={styles.muted}>Fetching the forecast…</Text>
      </View>
    );
  }
  if (isError || !data) {
    return (
      <View style={styles.center}>
        <Text style={styles.muted}>Couldn't load the weather.</Text>
        <Pressable onPress={() => refetch()} hitSlop={8} accessibilityRole="button" accessibilityLabel="Retry loading the weather forecast">
          <Text style={styles.link}>Retry</Text>
        </Pressable>
      </View>
    );
  }
  if (!data.available) {
    return (
      <View style={styles.center}>
        <Text style={styles.bigEmoji}>🌤️</Text>
        <Text style={styles.unavailable}>{data.message}</Text>
      </View>
    );
  }
  return <WeatherBody data={data} />;
}

const styles = StyleSheet.create({
  list: { padding: 16, paddingBottom: 28 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 8, padding: 24 },
  muted: { color: '#6b7280' },
  link: { color: BLUE, fontWeight: '700' },
  bigEmoji: { fontSize: 40 },
  unavailable: { color: '#374151', fontSize: 15, textAlign: 'center' },
  card: { backgroundColor: '#fff', borderRadius: 12, padding: 12, marginBottom: 12, borderWidth: 1, borderColor: '#e5e7eb' },
  routeName: { fontSize: 18, fontWeight: '800', color: '#1a365d' },
  summaryMeta: { color: '#6b7280', fontSize: 13, marginTop: 4 },
  summary: { color: '#1f2937', fontSize: 14, marginTop: 8, lineHeight: 20 },
  mapCard: { backgroundColor: '#fff', borderRadius: 12, marginBottom: 12, borderWidth: 1, borderColor: '#e5e7eb', overflow: 'hidden' },
  map: { height: 240 },
  mapLandscape: { height: 340 },
  selRing: { width: 22, height: 22, borderRadius: 11, borderWidth: 3, borderColor: '#1a365d', backgroundColor: 'rgba(26,54,93,0.15)' },
  legend: { flexDirection: 'row', gap: 16, padding: 10 },
  legendItem: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  swatch: { width: 12, height: 12, borderRadius: 3 },
  legendText: { fontSize: 12, color: '#6b7280' },
  // sticky detail card
  detail: { backgroundColor: '#fff', borderRadius: 12, padding: 12, marginBottom: 12, borderWidth: 1, borderColor: '#1a365d' },
  detailHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 },
  detailTitle: { fontSize: 14, fontWeight: '800', color: '#1a365d' },
  detailCond: { fontSize: 13, color: '#374151', flexShrink: 1, textAlign: 'right' },
  metricRow: { flexDirection: 'row', justifyContent: 'space-between', marginTop: 2 },
  metric: { alignItems: 'center', flex: 1 },
  metricVal: { fontSize: 15, fontWeight: '700', color: '#1a365d' },
  metricLbl: { fontSize: 9, color: '#6b7280', textTransform: 'uppercase' },
  // table
  tr: { flexDirection: 'row', alignItems: 'center', paddingVertical: 6, paddingHorizontal: 4, borderRadius: 6 },
  trActive: { backgroundColor: '#eff6ff' },
  thead: { borderBottomWidth: 1, borderBottomColor: '#e5e7eb', paddingBottom: 6 },
  th: { fontSize: 10, fontWeight: '700', color: colors.textMuted, textTransform: 'uppercase' },
  td: { fontSize: 13, color: '#1f2937' },
  cDist: { width: 32 },
  cTime: { flex: 1 },
  cTemp: { width: 44, textAlign: 'right' },
  cWind: { width: 38, textAlign: 'right' },
  cHw: { width: 44, textAlign: 'right', fontWeight: '700' },
  cSky: { width: 30, textAlign: 'center' },
  cRain: { width: 40, textAlign: 'right' },
  attribution: { color: colors.textMuted, fontSize: 11, textAlign: 'center', marginTop: 4 },
});
