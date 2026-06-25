/**
 * mobile/app/ride/[id].tsx — a ride's live map + background-share toggle.
 *
 * The map polls /api/live/positions and renders rider markers (initials, colour,
 * ⌚/📱 source). The share button starts/stops the iOS background location task
 * (works screen-off) that posts this device's GPS to the beacon for this ride.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';
import { useLocalSearchParams } from 'expo-router';
import MapView, { Marker, Region } from 'react-native-maps';
import { useLivePositions } from '../../hooks/useLivePositions';
import { startSharing, stopSharing, isSharing } from '../../location/backgroundLocation';
import type { LivePosition } from '../../lib/types';

const FALLBACK_REGION: Region = {
  latitude: 37.3, longitude: -121.9, latitudeDelta: 0.4, longitudeDelta: 0.4,
};

function initials(name: string): string {
  const parts = (name || '').trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return '?';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export default function RideLiveScreen() {
  const params = useLocalSearchParams<{ id: string }>();
  const rideId = parseInt(String(params.id), 10);
  const { data: positions, isLoading } = useLivePositions(rideId);

  const [sharing, setSharing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mapRef = useRef<MapView>(null);
  const framedOnce = useRef(false);

  useEffect(() => {
    isSharing().then(setSharing);
  }, []);

  // Frame the map to the riders the first time we have any.
  useEffect(() => {
    if (framedOnce.current || !positions?.length || !mapRef.current) return;
    framedOnce.current = true;
    mapRef.current.fitToCoordinates(
      positions.map((p) => ({ latitude: p.lat, longitude: p.lng })),
      { edgePadding: { top: 80, right: 80, bottom: 160, left: 80 }, animated: true },
    );
  }, [positions]);

  const initialRegion = useMemo<Region>(() => {
    const first = positions?.[0];
    return first
      ? { latitude: first.lat, longitude: first.lng, latitudeDelta: 0.05, longitudeDelta: 0.05 }
      : FALLBACK_REGION;
  }, [positions]);

  async function toggleShare() {
    setBusy(true);
    setError(null);
    if (sharing) {
      await stopSharing();
      setSharing(false);
    } else {
      const err = await startSharing(rideId);
      if (err) setError(err);
      else setSharing(true);
    }
    setBusy(false);
  }

  return (
    <View style={styles.container}>
      <MapView ref={mapRef} style={styles.map} initialRegion={initialRegion} showsUserLocation>
        {(positions ?? []).map((p: LivePosition) => (
          <Marker
            key={p.rider_id}
            coordinate={{ latitude: p.lat, longitude: p.lng }}
            title={p.name}
            description={`${p.source === 'garmin' ? '⌚ Garmin' : '📱 Phone'} · ${p.minutes_ago <= 0 ? 'just now' : `${p.minutes_ago}m ago`}`}
            opacity={p.stale ? 0.45 : 1}
          >
            <View style={[styles.pin, { backgroundColor: p.color, borderStyle: p.source === 'garmin' ? 'solid' : 'dashed' }]}>
              <Text style={styles.pinText}>{initials(p.name)}</Text>
            </View>
          </Marker>
        ))}
      </MapView>

      <View style={styles.overlay} pointerEvents="box-none">
        {isLoading ? <ActivityIndicator style={{ marginBottom: 8 }} /> : null}
        {error ? <Text style={styles.error}>{error}</Text> : null}
        <Pressable
          style={[styles.shareBtn, sharing ? styles.stop : styles.start, busy && styles.busy]}
          onPress={toggleShare}
          disabled={busy}
        >
          <Text style={styles.shareText}>
            {busy ? '…' : sharing ? 'Stop sharing' : 'Share my location'}
          </Text>
        </Pressable>
        {sharing ? (
          <Text style={styles.hint}>Sharing — works with the screen off. Tap stop when you're done.</Text>
        ) : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  map: { flex: 1 },
  overlay: { position: 'absolute', left: 0, right: 0, bottom: 0, padding: 16, alignItems: 'center' },
  pin: { width: 34, height: 34, borderRadius: 17, borderWidth: 2, borderColor: '#fff', alignItems: 'center', justifyContent: 'center' },
  pinText: { color: '#fff', fontWeight: '700', fontSize: 12 },
  shareBtn: { paddingVertical: 14, paddingHorizontal: 28, borderRadius: 12, minWidth: 240, alignItems: 'center' },
  start: { backgroundColor: '#16a34a' },
  stop: { backgroundColor: '#dc2626' },
  busy: { opacity: 0.6 },
  shareText: { color: '#fff', fontWeight: '700', fontSize: 16 },
  hint: { color: '#374151', fontSize: 12, marginTop: 8, textAlign: 'center' },
  error: { color: '#b91c1c', marginBottom: 8, textAlign: 'center' },
});
