/**
 * mobile/app/login.tsx — Google sign-in.
 */
import { useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';
import { useSession } from '../contexts/SessionContext';

export default function LoginScreen() {
  const { signInWithGoogle, signInDemo } = useSession();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onPress() {
    setBusy(true);
    setError(null);
    const err = await signInWithGoogle();
    setBusy(false);
    if (err) setError(err);
    // On success the AuthGate redirects into the app.
  }

  async function onDemo() {
    setBusy(true);
    setError(null);
    const err = await signInDemo();
    setBusy(false);
    if (err) setError(err);
  }

  return (
    <View style={styles.container}>
      <Text style={styles.title}>Team Asha Randonneuring</Text>
      <Text style={styles.sub}>Live ride tracking</Text>

      <Pressable style={[styles.btn, busy && styles.btnDisabled]} onPress={onPress} disabled={busy}>
        {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.btnText}>Sign in with Google</Text>}
      </Pressable>

      {error ? <Text style={styles.error}>{error}</Text> : null}

      {/* Reviewer/demo entry — works only when the server has demo mode enabled. */}
      <Pressable onPress={onDemo} disabled={busy} hitSlop={8} style={styles.demoLink}>
        <Text style={styles.demoText}>Demo login (reviewers)</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24, gap: 8 },
  title: { fontSize: 24, fontWeight: '700' },
  sub: { color: '#6b7280', marginBottom: 24 },
  btn: { backgroundColor: '#1a2a4f', paddingVertical: 14, paddingHorizontal: 28, borderRadius: 10, minWidth: 240, alignItems: 'center' },
  btnDisabled: { opacity: 0.6 },
  btnText: { color: '#fff', fontWeight: '600', fontSize: 16 },
  error: { color: '#b91c1c', marginTop: 16, textAlign: 'center' },
  demoLink: { marginTop: 20, paddingVertical: 6 },
  demoText: { color: '#6b7280', fontSize: 13, textDecorationLine: 'underline' },
});
