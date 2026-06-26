/**
 * mobile/app/calendar.tsx — the club's upcoming brevet calendar (read-only).
 */
import { ActivityIndicator, FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from 'react-native';
import { useCalendar } from '../hooks/useCalendar';
import type { BrevetSummary } from '../lib/types';

export default function CalendarScreen() {
  const { data: rides, isLoading, isError, refetch, isRefetching } = useCalendar();

  if (isLoading) return <View style={styles.center}><ActivityIndicator /></View>;
  if (isError) {
    return (
      <View style={styles.center}>
        <Text style={styles.muted}>Couldn't load the calendar.</Text>
        <Pressable onPress={() => refetch()}><Text style={styles.link}>Retry</Text></Pressable>
      </View>
    );
  }

  return (
    <FlatList
      contentContainerStyle={styles.list}
      data={rides ?? []}
      keyExtractor={(r) => String(r.id)}
      refreshControl={<RefreshControl refreshing={isRefetching} onRefresh={refetch} />}
      ListEmptyComponent={<Text style={styles.muted}>No upcoming brevets.</Text>}
      renderItem={({ item }: { item: BrevetSummary }) => (
        <View style={styles.row}>
          <Text style={styles.name}>{item.name}</Text>
          <Text style={styles.meta}>
            {item.date ?? ''}{item.distance_km ? ` · ${item.distance_km} km` : ''}
            {item.ride_type ? ` · ${item.ride_type}` : ''}
          </Text>
          {item.club_name ? (
            <Text style={[styles.meta, item.is_team_ride && styles.teamClub]}>
              {item.is_team_ride ? '🚴 ' : ''}{item.club_name}
            </Text>
          ) : null}
          {item.start_location ? <Text style={styles.meta}>Start: {item.start_location}</Text> : null}
          {item.signup_count ? <Text style={styles.count}>{item.signup_count} signed up</Text> : null}
        </View>
      )}
    />
  );
}

const styles = StyleSheet.create({
  list: { padding: 16 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 10 },
  row: { backgroundColor: '#fff', borderRadius: 12, padding: 16, marginBottom: 10, borderWidth: 1, borderColor: '#e5e7eb' },
  name: { fontSize: 16, fontWeight: '700' },
  meta: { color: '#6b7280', fontSize: 13, marginTop: 2 },
  count: { color: '#16a34a', fontSize: 12, marginTop: 4, fontWeight: '600' },
  teamClub: { color: '#1a365d', fontWeight: '700' },
  muted: { color: '#6b7280' },
  link: { color: '#2563eb', fontWeight: '600' },
});
