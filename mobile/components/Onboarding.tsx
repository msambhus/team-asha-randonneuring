/**
 * mobile/components/Onboarding.tsx
 *
 * Shown after login when the account has no linked rider profile yet
 * (profile_complete === false). Rider profiles are created on the web, so the
 * data screens 403 for a profile-less account — this replaces the old
 * "Couldn't load your rides" error (the App Store 2.1a rejection) with a clear
 * next step instead of a dead end.
 */
import { Linking, Pressable, StyleSheet, Text, View } from 'react-native';
import { API_BASE } from '../lib/config';

export default function Onboarding({ onSignOut }: { onSignOut: () => void }) {
  return (
    <View style={styles.container}>
      <Text style={styles.emoji}>🚴</Text>
      <Text style={styles.title}>Welcome to Team Asha!</Text>
      <Text style={styles.body}>
        You're signed in, but your rider profile isn't set up yet. Finish setting up
        your member profile on our website — then your rides, brevet calendar, and
        season stats will show up here.
      </Text>
      <Pressable style={styles.btn} onPress={() => Linking.openURL(`${API_BASE}/login`).catch(() => undefined)}>
        <Text style={styles.btnText}>Set up my profile on the web</Text>
      </Pressable>
      <Pressable style={styles.signOut} onPress={onSignOut} hitSlop={8}>
        <Text style={styles.link}>Sign out</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 28, gap: 12 },
  emoji: { fontSize: 44 },
  title: { fontSize: 22, fontWeight: '800', color: '#1a365d' },
  body: { color: '#4b5563', fontSize: 15, lineHeight: 22, textAlign: 'center' },
  btn: { backgroundColor: '#1a2a4f', paddingVertical: 14, paddingHorizontal: 24, borderRadius: 10, marginTop: 8, alignItems: 'center' },
  btnText: { color: '#fff', fontWeight: '700', fontSize: 15 },
  signOut: { paddingVertical: 12, marginTop: 4 },
  link: { color: '#2563eb', fontWeight: '600' },
});
