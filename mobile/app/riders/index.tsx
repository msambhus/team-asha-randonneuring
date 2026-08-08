import { useState } from 'react';
import { ActivityIndicator, FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from 'react-native';
import { Link } from 'expo-router';
import { usePublicRiders } from '../../hooks/usePublicRiders';
import type { PublicRiderSummary } from '../../lib/types';

export default function RidersDirectoryScreen() {
  const [season, setSeason] = useState<string | null>(null);
  const query = usePublicRiders(season);
  if (query.isLoading) return <View style={styles.center}><ActivityIndicator /></View>;
  if (query.isError || !query.data) return <View style={styles.center}><Text>Couldn’t load riders.</Text><Pressable onPress={() => query.refetch()}><Text style={styles.link}>Retry</Text></Pressable></View>;
  return (
    <FlatList
      contentContainerStyle={styles.list}
      data={query.data.riders}
      keyExtractor={(r) => String(r.id)}
      refreshControl={<RefreshControl refreshing={query.isRefetching} onRefresh={query.refetch} />}
      ListHeaderComponent={<View>
        <Text style={styles.intro}>Public randonneuring results and career totals.</Text>
        <FlatList horizontal showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.chips}
          data={query.data.seasons} keyExtractor={(s) => String(s.id)}
          renderItem={({ item }) => <Pressable style={[styles.chip, (season ?? query.data.season?.name) === item.name && styles.chipOn]} onPress={() => setSeason(item.name)}><Text style={(season ?? query.data.season?.name) === item.name ? styles.chipTextOn : styles.chipText}>{item.name}</Text></Pressable>} />
      </View>}
      ListEmptyComponent={<Text style={styles.muted}>No riders in this season.</Text>}
      renderItem={({ item }: { item: PublicRiderSummary }) => item.rusa_id ? (
        <Link href={`/riders/${item.rusa_id}`} asChild><Pressable style={styles.row}>
          <View style={styles.avatar}><Text style={styles.avatarText}>{item.display_name.charAt(0)}</Text></View>
          <View style={styles.body}><Text style={styles.name}>{item.display_name}</Text><Text style={styles.meta}>{item.season_rides} rides · {item.season_km.toLocaleString()} km this season</Text><Text style={styles.meta}>{item.total_rides} lifetime · {item.total_km.toLocaleString()} km{item.eddington_miles ? ` · E${item.eddington_miles}` : ''}</Text></View>
          <Text style={styles.chev}>›</Text>
        </Pressable></Link>
      ) : null}
    />
  );
}

const styles = StyleSheet.create({
  list: { padding: 16 }, center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 10 },
  intro: { color: '#64748b', marginBottom: 10 }, chips: { gap: 8, paddingBottom: 14 },
  chip: { borderWidth: 1, borderColor: '#cbd5e1', borderRadius: 18, paddingHorizontal: 13, paddingVertical: 7 }, chipOn: { backgroundColor: '#1a365d', borderColor: '#1a365d' }, chipText: { color: '#475569', fontWeight: '600' }, chipTextOn: { color: '#fff', fontWeight: '700' },
  row: { flexDirection: 'row', alignItems: 'center', backgroundColor: '#fff', borderWidth: 1, borderColor: '#e2e8f0', borderRadius: 12, padding: 14, marginBottom: 9 },
  avatar: { width: 42, height: 42, borderRadius: 21, backgroundColor: '#e0e7ff', alignItems: 'center', justifyContent: 'center' }, avatarText: { color: '#1a365d', fontWeight: '800', fontSize: 18 },
  body: { flex: 1, marginLeft: 12 }, name: { color: '#1a365d', fontSize: 16, fontWeight: '800' }, meta: { color: '#64748b', fontSize: 12, marginTop: 2 }, chev: { color: '#94a3b8', fontSize: 24 }, muted: { color: '#64748b' }, link: { color: '#2563eb', fontWeight: '700' },
});
