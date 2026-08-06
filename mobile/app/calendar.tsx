/**
 * mobile/app/calendar.tsx — the club's upcoming brevet calendar (read-only).
 */
import { ActivityIndicator, Alert, FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from 'react-native';
import { useCalendar } from '../hooks/useCalendar';
import { useRideSignup } from '../hooks/useRideSignup';
import { useFollowedRides } from '../hooks/useFollowedRides';
import type { BrevetSummary } from '../lib/types';

export default function CalendarScreen() {
  const { data: rides, isLoading, isError, refetch, isRefetching } = useCalendar();
  const signup = useRideSignup();
  const followed = useFollowedRides();

  function updateRide(rideId: number, going: boolean) {
    signup.mutate({ rideId, going }, {
      onError: () => Alert.alert('Could not update ride', 'Please try again.'),
    });
  }

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
          <View style={styles.actions}>
            <Pressable
              style={[styles.followAction, followed.followedRideIds.includes(item.id) && styles.followingAction]}
              disabled={followed.isPending && followed.pendingRideId === item.id}
              onPress={() => followed.setFollowed({
                rideId: item.id,
                followed: !followed.followedRideIds.includes(item.id),
              }, {
                onError: () => Alert.alert('Could not update followed rides', 'Please try again.'),
              })}
              accessibilityRole="button"
              accessibilityLabel={`${followed.followedRideIds.includes(item.id) ? 'Stop following' : 'Follow'} ${item.name} live`}
            >
              <Text style={[styles.followText, followed.followedRideIds.includes(item.id) && styles.followingText]}>
                {followed.isPending && followed.pendingRideId === item.id
                  ? 'Updating…'
                  : followed.followedRideIds.includes(item.id) ? '✓ Following live' : '◎ Follow live'}
              </Text>
            </Pressable>
            <Pressable
              style={[styles.action, item.signup_status === 'GOING' && styles.goingAction]}
              disabled={signup.isPending}
              onPress={() => updateRide(item.id, true)}
              accessibilityRole="button"
              accessibilityLabel={`Mark ${item.name} as going`}
            >
              <Text style={[styles.actionText, item.signup_status === 'GOING' && styles.goingText]}>
                {item.signup_status === 'GOING' ? '✓ Going' : 'Going'}
              </Text>
            </Pressable>
            {item.signup_status === 'GOING' ? (
              <Pressable
                style={styles.notGoing}
                disabled={signup.isPending}
                onPress={() => updateRide(item.id, false)}
                accessibilityRole="button"
                accessibilityLabel={`Mark ${item.name} as not going`}
              >
                <Text style={styles.notGoingText}>Not going</Text>
              </Pressable>
            ) : null}
          </View>
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
  actions: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 10, marginTop: 12 },
  followAction: { borderWidth: 1, borderColor: '#2563eb', borderRadius: 9, paddingHorizontal: 12, paddingVertical: 9 },
  followingAction: { backgroundColor: '#dbeafe', borderColor: '#2563eb' },
  followText: { color: '#1d4ed8', fontWeight: '700' },
  followingText: { color: '#1e40af' },
  action: { borderWidth: 1, borderColor: '#1a365d', borderRadius: 9, paddingHorizontal: 15, paddingVertical: 9 },
  goingAction: { backgroundColor: '#dcfce7', borderColor: '#16a34a' },
  actionText: { color: '#1a365d', fontWeight: '700' },
  goingText: { color: '#166534' },
  notGoing: { paddingHorizontal: 8, paddingVertical: 9 },
  notGoingText: { color: '#6b7280', fontWeight: '600' },
  muted: { color: '#6b7280' },
  link: { color: '#2563eb', fontWeight: '600' },
});
