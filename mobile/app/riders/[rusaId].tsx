import { ActivityIndicator, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useLocalSearchParams } from 'expo-router';
import { usePublicRider } from '../../hooks/usePublicRiders';

export default function PublicRiderScreen() {
  const { rusaId } = useLocalSearchParams<{ rusaId: string }>();
  const query = usePublicRider(rusaId);
  if (query.isLoading) return <View style={styles.center}><ActivityIndicator /></View>;
  if (query.isError || !query.data) return <View style={styles.center}><Text>Couldn’t load this rider.</Text></View>;
  const { rider, career, seasons } = query.data;
  const name = [rider.first_name, rider.last_name].filter(Boolean).join(' ');
  return <ScrollView contentContainerStyle={styles.list} refreshControl={<RefreshControl refreshing={query.isRefetching} onRefresh={query.refetch} />}>
    <View style={styles.identity}><View style={styles.avatar}><Text style={styles.avatarText}>{name.charAt(0)}</Text></View><View><Text style={styles.name}>{name}</Text><Text style={styles.muted}>RUSA #{rider.rusa_id}</Text></View></View>
    <View style={styles.card}><Text style={styles.title}>Randonneuring career</Text><View style={styles.stats}><Stat value={career.distance_km.toLocaleString()} label="km" /><Stat value={String(career.rides)} label="rides" /><Stat value={String(career.super_randonneur)} label="SR" /><Stat value={String(career.r12)} label="R-12" /></View></View>
    {seasons.map((season) => <View style={styles.card} key={season.id}><Text style={styles.title}>{season.name}{season.is_current ? ' · Current' : ''}</Text><Text style={styles.muted}>{season.rides} rides · {season.distance_km.toLocaleString()} km{season.sr_count ? ` · ${season.sr_count} SR` : ''}</Text>{season.history.map((ride) => <View style={styles.ride} key={ride.id}><View style={{ flex: 1 }}><Text style={styles.rideName}>{ride.name}</Text><Text style={styles.muted}>{ride.date}{ride.ride_type ? ` · ${ride.ride_type}` : ''}</Text></View><Text style={styles.distance}>{ride.distance_km ? `${ride.distance_km} km` : ''}</Text></View>)}</View>)}
    <Text style={styles.note}>Public brevet, populaire, and permanent results only. Private training-provider data is not shown.</Text>
  </ScrollView>;
}

function Stat({ value, label }: { value: string; label: string }) { return <View style={styles.stat}><Text style={styles.statValue}>{value}</Text><Text style={styles.muted}>{label}</Text></View>; }
const styles = StyleSheet.create({ list: { padding: 16 }, center: { flex: 1, alignItems: 'center', justifyContent: 'center' }, identity: { flexDirection: 'row', alignItems: 'center', gap: 12, marginBottom: 16 }, avatar: { width: 54, height: 54, borderRadius: 27, backgroundColor: '#1a365d', alignItems: 'center', justifyContent: 'center' }, avatarText: { color: '#fff', fontSize: 23, fontWeight: '800' }, name: { color: '#1a365d', fontSize: 22, fontWeight: '800' }, muted: { color: '#64748b', fontSize: 12 }, card: { backgroundColor: '#fff', borderWidth: 1, borderColor: '#e2e8f0', borderRadius: 12, padding: 15, marginBottom: 11 }, title: { color: '#1a365d', fontSize: 16, fontWeight: '800', marginBottom: 9 }, stats: { flexDirection: 'row' }, stat: { flex: 1, alignItems: 'center' }, statValue: { color: '#1a365d', fontSize: 19, fontWeight: '800' }, ride: { flexDirection: 'row', gap: 10, borderTopWidth: 1, borderTopColor: '#f1f5f9', paddingTop: 10, marginTop: 10 }, rideName: { color: '#1e293b', fontWeight: '700' }, distance: { color: '#1a365d', fontWeight: '700' }, note: { color: '#64748b', fontSize: 12, lineHeight: 18, padding: 4 } });
