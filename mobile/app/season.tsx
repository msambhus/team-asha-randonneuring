/**
 * mobile/app/season.tsx — the rider's "My Season" progress (read-only).
 * Season totals, SR badges, R-12 streak, and Eddington number.
 */
import { ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useMySeason } from '../hooks/useMySeason';

const SR_TIERS = [200, 300, 400, 600];

export default function SeasonScreen() {
  const { data, isLoading, isError, refetch, isRefetching } = useMySeason();

  if (isLoading) return <View style={styles.center}><ActivityIndicator /></View>;
  if (isError || !data) {
    return (
      <View style={styles.center}>
        <Text style={styles.muted}>Couldn't load your season.</Text>
        <Pressable onPress={() => refetch()}><Text style={styles.link}>Retry</Text></Pressable>
      </View>
    );
  }

  const { season, stats, sr, r12, career, eddington } = data;
  // Default for deploy-ordering safety: OTA JS may briefly hit the pre-deploy API.
  const rides_done = data.rides_done ?? [];
  const doneTiers = new Set(sr.distances_done);

  return (
    <ScrollView
      contentContainerStyle={styles.list}
      refreshControl={<RefreshControl refreshing={isRefetching} onRefresh={refetch} />}
    >
      <Text style={styles.heading}>{season.name ?? 'This season'}</Text>

      {/* Season totals */}
      <View style={styles.card}>
        <View style={styles.statRow}>
          <Stat label="Distance" value={`${stats.distance_km.toLocaleString()} km`} />
          <Stat label="Rides" value={String(stats.rides)} />
          <Stat label="Climbing" value={`${stats.elevation_ft.toLocaleString()} ft`} />
        </View>
      </View>

      {/* Super Randonneur */}
      <View style={styles.card}>
        <Text style={styles.cardTitle}>
          Super Randonneur {sr.has_sr ? '✅' : ''}
        </Text>
        <View style={styles.badgeRow}>
          {SR_TIERS.map((tier) => {
            const done = doneTiers.has(tier);
            const count = sr.counts?.[String(tier)] ?? 0;
            return (
              <View key={tier} style={[styles.badge, done ? styles.badgeDone : styles.badgeTodo]}>
                <Text style={[styles.badgeText, done ? styles.badgeTextDone : styles.badgeTextTodo]}>
                  {tier}
                </Text>
                <Text style={[styles.badgeCount, done ? styles.badgeTextDone : styles.badgeTextTodo]}>
                  {count > 0 ? `×${count}` : '—'}
                </Text>
              </View>
            );
          })}
        </View>
        <Text style={styles.muted}>
          {sr.has_sr ? 'SR complete this season!' : `${sr.distances_done.length} of 4 distances done`}
        </Text>
      </View>

      {/* Rides done this season */}
      <View style={styles.card}>
        <Text style={styles.cardTitle}>Rides done ({rides_done.length})</Text>
        {rides_done.length ? (
          rides_done.map((r, i) => (
            <View key={r.id} style={[styles.rideRow, i > 0 && styles.rideRowBorder]}>
              <Text style={styles.rideName} numberOfLines={1}>{r.name || 'Ride'}</Text>
              <Text style={styles.rideMeta}>
                {r.date ?? ''}{r.distance_km ? ` · ${r.distance_km} km` : ''}
              </Text>
            </View>
          ))
        ) : (
          <Text style={styles.muted}>No finished rides yet this season.</Text>
        )}
      </View>

      {/* R-12 streak — only meaningful once a few months are stacked up. */}
      {r12.months >= 4 ? (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>R-12 streak</Text>
          <Text style={styles.big}>{r12.months} {r12.months === 1 ? 'month' : 'months'}</Text>
          <Text style={[styles.muted, r12.active && styles.active]}>
            {r12.active ? '🔥 Active — keep it going' : 'Not currently active'}
          </Text>
        </View>
      ) : null}

      {/* Eddington */}
      {eddington ? (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Eddington number</Text>
          <Text style={styles.big}>
            E{eddington.value} {eddington.badge.emoji ?? ''}
          </Text>
          <Text style={styles.muted}>{eddington.badge.label}</Text>
        </View>
      ) : null}

      {/* Career */}
      <View style={styles.card}>
        <Text style={styles.cardTitle}>Career</Text>
        <Text style={styles.big}>{career.distance_km.toLocaleString()} km</Text>
        <Text style={styles.muted}>All-time finished distance</Text>
      </View>
    </ScrollView>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.stat}>
      <Text style={styles.statValue}>{value}</Text>
      <Text style={styles.statLabel}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  list: { padding: 16 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 10 },
  heading: { fontSize: 22, fontWeight: '800', marginBottom: 12 },
  card: { backgroundColor: '#fff', borderRadius: 12, padding: 16, marginBottom: 12, borderWidth: 1, borderColor: '#e5e7eb' },
  cardTitle: { fontSize: 15, fontWeight: '700', marginBottom: 8 },
  statRow: { flexDirection: 'row', justifyContent: 'space-between' },
  stat: { alignItems: 'center', flex: 1 },
  statValue: { fontSize: 18, fontWeight: '800' },
  statLabel: { color: '#6b7280', fontSize: 12, marginTop: 2 },
  badgeRow: { flexDirection: 'row', gap: 8, marginBottom: 8 },
  badge: { flex: 1, alignItems: 'center', paddingVertical: 10, borderRadius: 10, borderWidth: 1 },
  badgeDone: { backgroundColor: '#dcfce7', borderColor: '#16a34a' },
  badgeTodo: { backgroundColor: '#f3f4f6', borderColor: '#e5e7eb' },
  badgeText: { fontWeight: '700' },
  badgeCount: { fontWeight: '700', fontSize: 12, marginTop: 2 },
  badgeTextDone: { color: '#166534' },
  badgeTextTodo: { color: '#9ca3af' },
  rideRow: { paddingVertical: 8 },
  rideRowBorder: { borderTopWidth: 1, borderTopColor: '#f3f4f6' },
  rideName: { fontSize: 14, fontWeight: '600', color: '#1a365d' },
  rideMeta: { color: '#6b7280', fontSize: 12, marginTop: 2 },
  big: { fontSize: 24, fontWeight: '800' },
  muted: { color: '#6b7280', fontSize: 13, marginTop: 2 },
  active: { color: '#16a34a', fontWeight: '600' },
  link: { color: '#2563eb', fontWeight: '600' },
});
