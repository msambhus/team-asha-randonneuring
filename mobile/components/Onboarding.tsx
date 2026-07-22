/**
 * mobile/components/Onboarding.tsx
 *
 * Shown after login when the account has no linked rider profile yet
 * (profile_complete === false). The member enters their RUSA ID and we link it
 * to THIS account via POST /api/auth/setup-profile (token-authed) — no web/Google
 * detour, so it always updates the account the app is signed into and refreshes
 * instantly. Replaces the old "set up on the website" link, which authenticated
 * separately and could land on a different account.
 */
import { useState } from 'react';
import {
  ActivityIndicator, Pressable, StyleSheet, Text, TextInput, View,
} from 'react-native';
import { useSession } from '../contexts/SessionContext';

export default function Onboarding({ onSignOut }: { onSignOut: () => void }) {
  const { setupProfile } = useSession();
  const [rusaId, setRusaId] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setBusy(true);
    setError(null);
    const err = await setupProfile(rusaId.trim());
    setBusy(false);
    if (err) setError(err);
    // On success profileComplete flips true and the app renders the real screens.
  }

  return (
    <View style={styles.container}>
      <Text style={styles.emoji}>🚴</Text>
      <Text style={styles.title}>Welcome to Team Asha!</Text>
      <Text style={styles.body}>
        Enter your RUSA ID to finish setting up your rider profile — then your rides,
        brevet calendar, and season stats will show up here.
      </Text>

      <TextInput
        style={styles.input}
        placeholder="RUSA ID (e.g. 12345)"
        placeholderTextColor="#9ca3af"
        value={rusaId}
        onChangeText={setRusaId}
        keyboardType="number-pad"
        maxLength={12}
        editable={!busy}
      />
      <Pressable
        style={[styles.btn, (busy || !rusaId.trim()) && styles.btnDisabled]}
        disabled={busy || !rusaId.trim()}
        onPress={submit}
      >
        {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.btnText}>Link my profile</Text>}
      </Pressable>

      {error ? <Text style={styles.error}>{error}</Text> : null}

      <Pressable style={styles.signOut} onPress={onSignOut} hitSlop={8} disabled={busy}>
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
  input: {
    width: 240, height: 46, borderWidth: 1, borderColor: '#d1d5db', borderRadius: 10,
    paddingHorizontal: 14, fontSize: 15, color: '#111827', backgroundColor: '#fff', marginTop: 4,
  },
  btn: { backgroundColor: '#1a365d', width: 240, height: 48, borderRadius: 10, alignItems: 'center', justifyContent: 'center' },
  btnDisabled: { opacity: 0.6 },
  btnText: { color: '#fff', fontWeight: '700', fontSize: 15 },
  error: { color: '#b91c1c', textAlign: 'center', width: 260 },
  signOut: { paddingVertical: 12, marginTop: 4 },
  link: { color: '#2563eb', fontWeight: '600' },
});
