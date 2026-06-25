/**
 * mobile/app/index.tsx — the rider's upcoming rides; tap one to open its live map.
 */
import { ActivityIndicator, FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from 'react-native';
import { Link } from 'expo-router';
import { useRides } from '../hooks/useRides';
import { useSession } from '../contexts/SessionContext';
import type { RideSummary } from '../lib/types';

export default function RidesScreen() {
  const { data: rides, isLoading, isError, refetch, isRefetching } = useRides();
  const { signOut } = useSession();

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
      <Link href="/calendar" asChild>
        <Pressable style={styles.calLink}><Text style={styles.link}>📅 Brevet calendar</Text></Pressable>
      </Link>
      <FlatList
        data={rides ?? []}
        keyExtractor={(r) => String(r.id)}
        refreshControl={<RefreshControl refreshing={isRefetching} onRefresh={refetch} />}
        ListEmptyComponent={<Text style={styles.muted}>No upcoming rides. Sign up on the website.</Text>}
        renderItem={({ item }: { item: RideSummary }) => (
          <Link href={`/ride/${item.id}`} asChild>
            <Pressable style={styles.row}>
              <View style={{ flex: 1 }}>
                <Text style={styles.name}>{item.name}</Text>
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
  name: { fontSize: 16, fontWeight: '700' },
  meta: { color: '#6b7280', fontSize: 13, marginTop: 2 },
  chev: { color: '#9ca3af', fontSize: 22 },
  muted: { color: '#6b7280' },
  link: { color: '#2563eb', fontWeight: '600' },
  signOut: { alignItems: 'center', paddingVertical: 14 },
  calLink: { paddingVertical: 10, paddingHorizontal: 4, marginBottom: 4 },
});
