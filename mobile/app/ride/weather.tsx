/**
 * mobile/app/ride/weather.tsx — a ride's weather forecast (GET /api/ride/<id>/weather).
 *
 * Mirrors the web /weather page on a phone: a summary card, a wind map (route line
 * + color-coded wind arrows), a per-segment table, and the six charts (temperature,
 * wind, headwind/tailwind, precipitation, elevation, humidity). Reached from the
 * ride's live-map header. Reads ?id=<rideId> from the route.
 */
import { useMemo } from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useLocalSearchParams } from 'expo-router';
import MapView, { Marker, Polyline, Region } from 'react-native-maps';
import { useRideWeather } from '../../hooks/useRideWeather';
import { WeatherChart } from '../../components/WeatherChart';
import type { RideWeatherAvailable, WeatherSegment } from '../../lib/types';

const RED = '#dc2626', GREEN = '#16a34a', BLUE = '#2563eb';

function windColor(label: string): string {
  if (label.includes('headwind')) return RED;
  if (label.includes('tailwind')) return GREEN;
  return BLUE;
}

/** A map region framing all [lat,lng] coords, with padding. */
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

function SegmentRow({ s }: { s: WeatherSegment }) {
  const hw = s.headwind_mph;
  const hwColor = hw > 1 ? RED : hw < -1 ? GREEN : '#6b7280';
  return (
    <View style={styles.tr}>
      <Text style={[styles.td, styles.cDist]}>{Math.round(s.distance_mi)}</Text>
      <Text style={[styles.td, styles.cTime]}>{s.arrival_time}</Text>
      <Text style={[styles.td, styles.cTemp]}>{Math.round(s.temperature_f)}°</Text>
      <Text style={[styles.td, styles.cWind]}>{Math.round(s.wind_speed_mph)}</Text>
      <Text style={[styles.td, styles.cHw, { color: hwColor }]}>
        {hw > 1 ? '↑' : hw < -1 ? '↓' : '·'}{Math.abs(Math.round(hw)) || ''}
      </Text>
      <Text style={[styles.td, styles.cSky]}>{s.conditions_icon}</Text>
      <Text style={[styles.td, styles.cRain]}>{s.precip_percent}%</Text>
    </View>
  );
}

function WeatherBody({ data }: { data: RideWeatherAvailable }) {
  const coords = useMemo(
    () => data.polyline.map(([lat, lng]) => ({ latitude: lat, longitude: lng })),
    [data.polyline],
  );
  const region = useMemo(() => regionForCoords(coords), [coords]);
  const c = data.chart_data;
  const headPos = c.headwind_mph.map((v) => (v > 0 ? v : 0));
  const headNeg = c.headwind_mph.map((v) => (v < 0 ? v : 0));

  return (
    <ScrollView contentContainerStyle={styles.list}>
      {/* Summary */}
      <View style={styles.card}>
        <Text style={styles.routeName}>{data.route_name}</Text>
        <Text style={styles.summaryMeta}>
          {data.total_distance_mi} mi · {data.total_elevation_ft.toLocaleString()} ft ·{' '}
          {data.temp_range.min_f}–{data.temp_range.max_f}°F
          {data.plan_source ? ` · ${data.plan_source} plan timing` : ''}
        </Text>
        {data.ride_summary ? <Text style={styles.summary}>{data.ride_summary}</Text> : null}
      </View>

      {/* Wind map */}
      {region ? (
        <View style={styles.mapCard}>
          <MapView style={styles.map} initialRegion={region}>
            {coords.length ? <Polyline coordinates={coords} strokeColor={BLUE} strokeWidth={3} /> : null}
            {data.map_segments.map((s, i) => {
              const color = windColor(s.wind_label);
              // Arrow points the way the wind blows TO (dir is where it comes FROM).
              const rotate = (s.wind_direction_deg + 180) % 360;
              const size = s.wind_speed_mph < 8 ? 14 : s.wind_speed_mph < 16 ? 18 : 22;
              return (
                <Marker key={i} coordinate={{ latitude: s.lat, longitude: s.lng }}
                  anchor={{ x: 0.5, y: 0.5 }} tracksViewChanges={false}>
                  <Text style={{ fontSize: size, color, transform: [{ rotate: `${rotate}deg` }] }}>↑</Text>
                </Marker>
              );
            })}
          </MapView>
          <View style={styles.legend}>
            <Legend color={RED} label="headwind" />
            <Legend color={GREEN} label="tailwind" />
            <Legend color={BLUE} label="crosswind" />
          </View>
        </View>
      ) : null}

      {/* Per-segment table */}
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
        {data.table_segments.map((s, i) => <SegmentRow key={i} s={s} />)}
      </View>

      {/* Charts */}
      <WeatherChart title="Temperature" unit="°F" labels={c.labels}
        series={[{ data: c.temperature_f, color: '#ef4444', fill: true }, { data: c.feels_like_f, color: '#f59e0b' }]}
        legend={[{ label: 'temp', color: '#ef4444' }, { label: 'feels like', color: '#f59e0b' }]} />

      <WeatherChart title="Wind" unit="mph" labels={c.labels}
        series={[{ data: c.wind_speed_mph, color: BLUE, fill: true }, { data: c.wind_gust_mph, color: '#93c5fd' }]}
        legend={[{ label: 'wind', color: BLUE }, { label: 'gusts', color: '#93c5fd' }]} />

      <WeatherChart title="Headwind / Tailwind" unit="mph" labels={c.labels} baseline={0}
        series={[{ data: headPos, color: RED, fill: true }, { data: headNeg, color: GREEN, fill: true }]}
        legend={[{ label: 'headwind', color: RED }, { label: 'tailwind', color: GREEN }]} />

      <WeatherChart title="Precipitation & cloud" unit="% chance / % cloud" labels={c.labels}
        series={[{ data: c.precip_probability, color: BLUE, fill: true }, { data: c.cloud_cover, color: '#94a3b8' }]}
        legend={[{ label: 'precip %', color: BLUE }, { label: 'cloud %', color: '#94a3b8' }]} />

      <WeatherChart title="Elevation" unit="ft" labels={c.labels}
        series={[{ data: c.elevation_ft, color: '#15803d', fill: true }]} />

      <WeatherChart title="Humidity" unit="%" labels={c.labels}
        series={[{ data: c.humidity, color: '#0d9488', fill: true }]} />

      <Text style={styles.attribution}>Weather data: Open-Meteo</Text>
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
        <Text style={styles.link} onPress={() => refetch()}>Retry</Text>
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
  legend: { flexDirection: 'row', gap: 16, padding: 10 },
  legendItem: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  swatch: { width: 12, height: 12, borderRadius: 3 },
  legendText: { fontSize: 12, color: '#6b7280' },
  // table
  tr: { flexDirection: 'row', alignItems: 'center', paddingVertical: 6 },
  thead: { borderBottomWidth: 1, borderBottomColor: '#e5e7eb', paddingBottom: 6 },
  th: { fontSize: 10, fontWeight: '700', color: '#9ca3af', textTransform: 'uppercase' },
  td: { fontSize: 13, color: '#1f2937' },
  cDist: { width: 32 },
  cTime: { flex: 1 },
  cTemp: { width: 44, textAlign: 'right' },
  cWind: { width: 38, textAlign: 'right' },
  cHw: { width: 44, textAlign: 'right', fontWeight: '700' },
  cSky: { width: 30, textAlign: 'center' },
  cRain: { width: 40, textAlign: 'right' },
  attribution: { color: '#9ca3af', fontSize: 11, textAlign: 'center', marginTop: 4 },
});
