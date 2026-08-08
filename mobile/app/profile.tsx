import { ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { Link } from 'expo-router';
import { useRiderProfile } from '../hooks/useRiderProfile';
import { useMySeason } from '../hooks/useMySeason';
import { TrainingCalendar } from '../components/TrainingCalendar';

export default function ProfileScreen() {
  const profile = useRiderProfile();
  const season = useMySeason();
  const refreshing = profile.isRefetching || season.isRefetching;

  if (profile.isLoading || season.isLoading) {
    return <View style={styles.center}><ActivityIndicator /></View>;
  }
  if (profile.isError || !profile.data) {
    return (
      <View style={styles.center}>
        <Text style={styles.muted}>Couldn't load your rider profile.</Text>
        <Pressable onPress={() => profile.refetch()}><Text style={styles.link}>Retry</Text></Pressable>
      </View>
    );
  }

  const { rider, career } = profile.data;
  const current = season.data;
  const name = [rider.first_name, rider.last_name].filter(Boolean).join(' ') || 'Rider';

  return (
    <ScrollView
      contentContainerStyle={styles.list}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => {
        void profile.refetch();
        void season.refetch();
      }} />}
    >
      <View style={styles.identity}>
        <View style={styles.avatar}><Text style={styles.avatarText}>{name.charAt(0)}</Text></View>
        <View>
          <Text style={styles.name}>{name}</Text>
          {rider.rusa_id ? <Text style={styles.muted}>RUSA #{rider.rusa_id}</Text> : null}
        </View>
      </View>

      <View style={styles.card}>
        <Text style={styles.cardTitle}>Randonneuring career</Text>
        <View style={styles.statRow}>
          <Stat value={career.distance_km.toLocaleString()} label="Lifetime km" />
          <Stat value={String(career.rides)} label="Finished rides" />
          <Stat value={String(career.super_randonneur)} label="SR awards" />
        </View>
        {current?.eddington ? (
          <View style={styles.eddingtonRow}>
            <Text style={styles.eddingtonValue}>E{current.eddington.value} {current.eddington.badge.emoji ?? ''}</Text>
            <Text style={styles.muted}>Eddington number · {current.eddington.badge.label}</Text>
          </View>
        ) : null}
      </View>

      {current ? (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>{current.season.name ?? 'Current season'}</Text>
          <View style={styles.statRow}>
            <Stat value={current.stats.distance_km.toLocaleString()} label="km" />
            <Stat value={String(current.stats.rides)} label="rides" />
            <Stat value={`${current.sr.distances_done.length}/4`} label="SR distances" />
          </View>
          <Link href="/season" asChild>
            <Pressable style={styles.details}><Text style={styles.link}>View current and past seasons →</Text></Pressable>
          </Link>
        </View>
      ) : null}

      <TrainingCalendar />

      <Text style={styles.note}>
        This is your private rider summary. Your public rider page remains on the Team Asha website.
      </Text>
    </ScrollView>
  );
}

function Stat({ value, label }: { value: string; label: string }) {
  return <View style={styles.stat}><Text style={styles.statValue}>{value}</Text><Text style={styles.statLabel}>{label}</Text></View>;
}

const styles = StyleSheet.create({
  list: { padding: 16 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 10 },
  identity: { flexDirection: 'row', alignItems: 'center', gap: 14, marginBottom: 18 },
  avatar: { width: 58, height: 58, borderRadius: 29, backgroundColor: '#1a365d', alignItems: 'center', justifyContent: 'center' },
  avatarText: { color: '#fff', fontSize: 25, fontWeight: '800' },
  name: { color: '#1a365d', fontSize: 23, fontWeight: '800' },
  card: { backgroundColor: '#fff', borderRadius: 12, padding: 16, marginBottom: 12, borderWidth: 1, borderColor: '#e5e7eb' },
  cardTitle: { color: '#1a365d', fontSize: 15, fontWeight: '700', marginBottom: 14 },
  statRow: { flexDirection: 'row' },
  stat: { flex: 1, alignItems: 'center' },
  statValue: { color: '#1a365d', fontSize: 20, fontWeight: '800' },
  statLabel: { color: '#6b7280', fontSize: 11, textAlign: 'center', marginTop: 3 },
  details: { alignSelf: 'flex-start', marginTop: 16 },
  eddingtonRow: { marginTop: 14, paddingTop: 12, borderTopWidth: 1, borderTopColor: '#e5e7eb' },
  eddingtonValue: { color: '#1a365d', fontSize: 20, fontWeight: '800', marginBottom: 2 },
  muted: { color: '#6b7280', fontSize: 13 },
  link: { color: '#2563eb', fontWeight: '700' },
  note: { color: '#6b7280', fontSize: 12, lineHeight: 18, paddingHorizontal: 4 },
});
