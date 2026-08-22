/**
 * mobile/app/index.tsx — the rider's upcoming rides; tap one to open its live map.
 */
import { useCallback, useState } from 'react';
import { ActivityIndicator, FlatList, Image, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { Link, useFocusEffect, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useRides } from '../hooks/useRides';
import { useCalendar } from '../hooks/useCalendar';
import { useFollowedRides } from '../hooks/useFollowedRides';
import { useSession } from '../contexts/SessionContext';
import { getSharingRideId } from '../location/backgroundLocation';
import Onboarding from '../components/Onboarding';
import type { RideSummary } from '../lib/types';

export default function RidesScreen() {
  const { data: rides, isLoading, isError, refetch, isRefetching } = useRides();
  const { data: calendarRides, refetch: refetchCalendar } = useCalendar();
  const { followedRideIds } = useFollowedRides();
  const { signOut, profileComplete, token } = useSession();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [sharingRideId, setSharingRideId] = useState<number | null>(null);

  const homeRides = (() => {
    const combined = new Map<number, RideSummary & { isFollowed?: boolean }>();
    for (const ride of rides ?? []) combined.set(ride.id, ride);
    const followedSet = new Set(followedRideIds);
    for (const ride of calendarRides ?? []) {
      if (!followedSet.has(ride.id) || combined.has(ride.id)) continue;
      combined.set(ride.id, {
        id: ride.id,
        name: ride.name,
        date: ride.date,
        distance_km: ride.distance_km,
        signup_status: null,
        isFollowed: true,
        is_live: ride.is_live,
      });
    }
    return [...combined.values()].sort((a, b) => (a.date ?? '').localeCompare(b.date ?? ''));
  })();

  // Re-check on every focus so the badge reflects starting/stopping sharing on
  // the ride screen (only one ride broadcasts at a time).
  useFocusEffect(useCallback(() => { getSharingRideId().then(setSharingRideId); }, []));

  if (!token) {
    return <PublicLanding onSignIn={() => router.push('/login')} />;
  }

  // A signed-in account with no linked rider (profile is created on the web)
  // can't load any data — show onboarding instead of an error (App Store 2.1a).
  if (!profileComplete) {
    return <Onboarding onSignOut={() => { void signOut(); }} />;
  }

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
      <View style={styles.summaryCard}>
        <Text style={styles.eyebrow}>TEAM ASHA RANDONNEURING</Text>
        <Text style={styles.summaryTitle}>Ride farther. Give back.</Text>
        <Text style={styles.summaryText}>A Bay Area community exploring long-distance brevets and supporting education for children in India.</Text>
      </View>
      <View style={styles.navRow}>
        <Link href="/calendar" asChild>
          <Pressable style={styles.calLink}><Text style={styles.link}>📅 Brevet calendar</Text></Pressable>
        </Link>
        <Link href="/season" asChild>
          <Pressable style={styles.calLink}><Text style={styles.link}>🏅 My Season</Text></Pressable>
        </Link>
        <Link href="/profile" asChild>
          <Pressable style={styles.calLink}><Text style={styles.link}>👤 Profile</Text></Pressable>
        </Link>
        <Link href="/riders" asChild>
          <Pressable style={styles.calLink}><Text style={styles.link}>👥 All Riders</Text></Pressable>
        </Link>
      </View>
      <FlatList
        data={homeRides}
        keyExtractor={(r) => String(r.id)}
        refreshControl={<RefreshControl refreshing={isRefetching} onRefresh={() => {
          void Promise.all([refetch(), refetchCalendar()]);
        }} />}
        ListEmptyComponent={<Text style={styles.muted}>No upcoming rides. Follow one from the Brevet Calendar.</Text>}
        renderItem={({ item }: { item: RideSummary & { isFollowed?: boolean } }) => (
          <Link href={`/ride/${item.id}`} asChild>
            <Pressable style={styles.row}>
              <View style={{ flex: 1 }}>
                <View style={styles.nameRow}>
                  <Text style={styles.name} numberOfLines={1}>{item.name}</Text>
                  {item.id === sharingRideId ? (
                    <Text style={styles.sharingBadge}>📍 Sharing</Text>
                  ) : null}
                  {item.is_live ? <Text style={styles.liveBadge}>● LIVE NOW</Text> : null}
                  {item.isFollowed ? <Text style={styles.followingBadge}>◎ Following live</Text> : null}
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
      <Pressable style={[styles.signOut, { paddingBottom: 14 + insets.bottom }]} onPress={() => signOut()}
        accessibilityRole="button" accessibilityLabel="Sign out">
        <Text style={styles.link}>Sign out</Text>
      </Pressable>
    </View>
  );
}

function PublicLanding({ onSignIn }: { onSignIn: () => void }) {
  return <ScrollView contentContainerStyle={styles.publicContainer}>
    <Image source={require('../assets/icon.png')} style={styles.logo} accessibilityLabel="Team Asha logo" />
    <Text style={styles.eyebrow}>TEAM ASHA RANDONNEURING</Text>
    <Text style={styles.publicTitle}>Ride farther.{"\n"}<Text style={styles.publicAccent}>Give back.</Text></Text>
    <Text style={styles.publicText}>We are a Bay Area community of cyclists who find joy in the quiet determination of randonneuring—and use every mile to support education for children in India.</Text>
    <View style={styles.publicCard}><Text style={styles.cardTitle}>What is randonneuring?</Text><Text style={styles.cardText}>Long-distance cycling built around self-reliance, navigation, and camaraderie. Complete timed 200K, 300K, 400K, and 600K brevets at your own pace.</Text></View>
    <View style={styles.publicCard}><Text style={styles.cardTitle}>What you’ll find here</Text><Text style={styles.cardText}>A welcoming community, shared routes, live ride support, and a team working toward meaningful miles together.</Text></View>
    <Pressable style={styles.signInButton} onPress={onSignIn}><Text style={styles.signInText}>Sign in to Team Asha</Text></Pressable>
  </ScrollView>;
}

const styles = StyleSheet.create({
  container: { flex: 1, padding: 16 },
  publicContainer: { padding: 28, paddingTop: 52, backgroundColor: '#f7fafc', flexGrow: 1 },
  logo: { width: 72, height: 72, alignSelf: 'center', borderRadius: 18, marginBottom: 20 },
  eyebrow: { color: '#d97706', fontSize: 11, fontWeight: '800', letterSpacing: 1.4, marginBottom: 8 },
  publicTitle: { color: '#1a365d', fontSize: 40, lineHeight: 44, fontWeight: '900', marginBottom: 16 },
  publicAccent: { color: '#d97706' },
  publicText: { color: '#475569', fontSize: 17, lineHeight: 25, marginBottom: 24 },
  publicCard: { backgroundColor: '#fff', borderRadius: 14, borderWidth: 1, borderColor: '#e5e7eb', padding: 18, marginBottom: 12 },
  cardTitle: { color: '#1a365d', fontSize: 17, fontWeight: '800', marginBottom: 6 },
  cardText: { color: '#64748b', fontSize: 14, lineHeight: 21 },
  signInButton: { backgroundColor: '#1a365d', borderRadius: 10, padding: 15, alignItems: 'center', marginTop: 10 },
  signInText: { color: '#fff', fontWeight: '800', fontSize: 16 },
  summaryCard: { backgroundColor: '#edf3fa', borderRadius: 14, padding: 16, marginBottom: 12 },
  summaryTitle: { color: '#1a365d', fontSize: 22, fontWeight: '900', marginBottom: 4 },
  summaryText: { color: '#475569', lineHeight: 20 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 10 },
  row: { flexDirection: 'row', alignItems: 'center', backgroundColor: '#fff', borderRadius: 12, padding: 16, marginBottom: 10, borderWidth: 1, borderColor: '#e5e7eb' },
  nameRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  name: { fontSize: 16, fontWeight: '700', flexShrink: 1 },
  sharingBadge: {
    fontSize: 11, fontWeight: '700', color: '#166534', backgroundColor: '#dcfce7',
    paddingHorizontal: 8, paddingVertical: 2, borderRadius: 10, overflow: 'hidden',
  },
  followingBadge: {
    fontSize: 11, fontWeight: '700', color: '#1e40af', backgroundColor: '#dbeafe',
    paddingHorizontal: 8, paddingVertical: 2, borderRadius: 10, overflow: 'hidden',
  },
  liveBadge: {
    fontSize: 11, fontWeight: '800', color: '#991b1b', backgroundColor: '#fee2e2',
    paddingHorizontal: 8, paddingVertical: 2, borderRadius: 10, overflow: 'hidden',
  },
  meta: { color: '#6b7280', fontSize: 13, marginTop: 2 },
  chev: { color: '#9ca3af', fontSize: 22 },
  muted: { color: '#6b7280' },
  link: { color: '#2563eb', fontWeight: '600' },
  signOut: { alignItems: 'center', paddingVertical: 14 },
  navRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 12, marginBottom: 4 },
  calLink: { paddingVertical: 10, paddingHorizontal: 4 },
});
