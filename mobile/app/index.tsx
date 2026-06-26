/**
 * mobile/app/index.tsx — the rider's upcoming rides; tap one to open its live map.
 */
import { useCallback, useState } from 'react';
import { ActivityIndicator, FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from 'react-native';
import { Link, useFocusEffect } from 'expo-router';
import { useRides } from '../hooks/useRides';
import { useSession } from '../contexts/SessionContext';
import { getSharingRideId } from '../location/backgroundLocation';
import type { RideSummary } from '../lib/types';

export default function RidesScreen() {
  const { data: rides, isLoading, isError, refetch, isRefetching } = useRides();
  const { signOut } = useSession();
  const [sharingRideId, setSharingRideId] = useState<number | null>(null);

  // Re-check on every focus so the badge reflects starting/stopping sharing on
  // the ride screen (only one ride broadcasts at a time).
  useFocusEffect(useCallback(() => { getSharingRideId().then(setSharingRideId); }, []));

  if (isLoading) {
    return <View style={styles.center}><ActivityIndicator /></View>;
  }
  if (isError) {
    return (
      <View style={styles.center}>
        <Text style={styles.muted}>Couldn't load your rides.</Text>
        <Pressable onPress={() => refetch()}><Text style={styles.link}>Retry</Text></Pressable>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <View style={styles.navRow}>
        <Link href="/calendar" asChild>
          <Pressable style={styles.calLink}><Text style={styles.link}>📅 Brevet calendar</Text></Pressable>
        </Link>
        <Link href="/season" asChild>
          <Pressable style={styles.calLink}><Text style={styles.link}>🏅 My Season</Text></Pressable>
        </Link>
      </View>
      <FlatList
        data={rides ?? []}
        keyExtractor={(r) => String(r.id)}
        refreshControl={<RefreshControl refreshing={isRefetching} onRefresh={refetch} />}
        ListEmptyComponent={<Text style={styles.muted}>No upcoming rides. Sign up on the website.</Text>}
        renderItem={({ item }: { item: RideSummary }) => (
          <Link href={`/ride/${item.id}`} asChild>
            <Pressable style={styles.row}>
              <View style={{ flex: 1 }}>
                <View style={styles.nameRow}>
                  <Text style={styles.name} numberOfLines={1}>{item.name}</Text>
                  {item.id === sharingRideId ? (
                    <Text style={styles.sharingBadge}>📍 Sharing</Text>
                  ) : null}
                </View>
                <Text style={styles.meta}>
                  {item.date ?? ''}{item.distance_km ? ` · ${item.distance_km} km` : ''}
                  {item.signup_status ? ` · ${item.signup_status}` : ''}
                </Text>
              </View>
              <Text style={styles.chev}>›</Text>
            </Pressable>
          </Link>
        )}
      />
      <Pressable style={styles.signOut} onPress={() => signOut()}>
        <Text style={styles.link}>Sign out</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, padding: 16 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 10 },
  row: { flexDirection: 'row', alignItems: 'center', backgroundColor: '#fff', borderRadius: 12, padding: 16, marginBottom: 10, borderWidth: 1, borderColor: '#e5e7eb' },
  nameRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  name: { fontSize: 16, fontWeight: '700', flexShrink: 1 },
  sharingBadge: {
    fontSize: 11, fontWeight: '700', color: '#166534', backgroundColor: '#dcfce7',
    paddingHorizontal: 8, paddingVertical: 2, borderRadius: 10, overflow: 'hidden',
  },
  meta: { color: '#6b7280', fontSize: 13, marginTop: 2 },
  chev: { color: '#9ca3af', fontSize: 22 },
  muted: { color: '#6b7280' },
  link: { color: '#2563eb', fontWeight: '600' },
  signOut: { alignItems: 'center', paddingVertical: 14 },
  navRow: { flexDirection: 'row', gap: 16, marginBottom: 4 },
  calLink: { paddingVertical: 10, paddingHorizontal: 4 },
});
