/**
 * mobile/app/login.tsx — Google + Sign in with Apple + email/password.
 *
 * App Store Guideline 4.8 requires a privacy-preserving login option alongside
 * a third-party one, so we offer Sign in with Apple next to Google. Email +
 * password is a first-party 3rd option for members who prefer it.
 */
import { useEffect, useState } from 'react';
import {
  ActivityIndicator, Platform, Pressable, StyleSheet, Text, TextInput, View,
} from 'react-native';
import * as AppleAuthentication from 'expo-apple-authentication';
import { useSession } from '../contexts/SessionContext';

export default function LoginScreen() {
  const {
    signInWithGoogle, signInWithApple, signInDemo,
    signInWithPassword, signUpWithPassword,
  } = useSession();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [appleAvailable, setAppleAvailable] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [mode, setMode] = useState<'login' | 'signup'>('login');

  // Sign in with Apple is iOS 13+ only; hide the button where it's unavailable.
  useEffect(() => {
    if (Platform.OS === 'ios') {
      AppleAuthentication.isAvailableAsync().then(setAppleAvailable).catch(() => undefined);
    }
  }, []);

  async function run(fn: () => Promise<string | null>) {
    setBusy(true);
    setError(null);
    const err = await fn();
    setBusy(false);
    if (err) setError(err);
    // On success the AuthGate redirects into the app.
  }

  function submitPassword() {
    // Client-side min-length check on sign-up mirrors the backend (8-128) so a
    // too-short password doesn't round-trip. The backend stays authoritative.
    if (mode === 'signup' && password.length < 8) {
      setError('Password must be at least 8 characters');
      return;
    }
    run(() =>
      (mode === 'login' ? signInWithPassword : signUpWithPassword)(email.trim(), password),
    );
  }

  return (
    <View style={styles.container}>
      <Text style={styles.title}>Team Asha Randonneuring</Text>
      <Text style={styles.sub}>Live ride tracking</Text>

      <Pressable style={[styles.btn, busy && styles.btnDisabled]} onPress={() => run(signInWithGoogle)} disabled={busy}>
        {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.btnText}>Sign in with Google</Text>}
      </Pressable>

      {appleAvailable ? (
        <AppleAuthentication.AppleAuthenticationButton
          buttonType={AppleAuthentication.AppleAuthenticationButtonType.SIGN_IN}
          buttonStyle={AppleAuthentication.AppleAuthenticationButtonStyle.BLACK}
          cornerRadius={10}
          style={styles.appleBtn}
          onPress={() => run(signInWithApple)}
        />
      ) : null}

      <Text style={styles.or}>or</Text>

      {/* Email + password (first-party 3rd option). */}
      <TextInput
        style={styles.input}
        placeholder="Email"
        placeholderTextColor="#9ca3af"
        value={email}
        onChangeText={setEmail}
        autoCapitalize="none"
        autoCorrect={false}
        keyboardType="email-address"
        textContentType="emailAddress"
        editable={!busy}
      />
      <TextInput
        style={styles.input}
        placeholder="Password"
        placeholderTextColor="#9ca3af"
        value={password}
        onChangeText={setPassword}
        secureTextEntry
        autoCapitalize="none"
        textContentType={mode === 'signup' ? 'newPassword' : 'password'}
        editable={!busy}
      />
      <Pressable
        style={[styles.btn, (busy || !email.trim() || !password) && styles.btnDisabled]}
        disabled={busy || !email.trim() || !password}
        onPress={submitPassword}
      >
        {busy ? (
          <ActivityIndicator color="#fff" />
        ) : (
          <Text style={styles.btnText}>{mode === 'login' ? 'Log in' : 'Create account'}</Text>
        )}
      </Pressable>
      <Pressable
        onPress={() => { setMode(mode === 'login' ? 'signup' : 'login'); setError(null); }}
        disabled={busy}
        hitSlop={8}
        style={styles.toggleLink}
      >
        <Text style={styles.toggleText}>
          {mode === 'login' ? 'New here? Create an account' : 'Have an account? Log in'}
        </Text>
      </Pressable>

      {error ? <Text style={styles.error}>{error}</Text> : null}

      {/* Reviewer/demo entry — works only when the server has demo mode enabled. */}
      <Pressable onPress={() => run(signInDemo)} disabled={busy} hitSlop={8} style={styles.demoLink}>
        <Text style={styles.demoText}>Demo login (reviewers)</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24, gap: 8 },
  title: { fontSize: 24, fontWeight: '700' },
  sub: { color: '#6b7280', marginBottom: 24 },
  // Google + Apple buttons share identical dimensions so they line up 1:1.
  btn: { backgroundColor: '#1a2a4f', width: 240, height: 48, borderRadius: 10, alignItems: 'center', justifyContent: 'center' },
  btnDisabled: { opacity: 0.6 },
  btnText: { color: '#fff', fontWeight: '600', fontSize: 16 },
  appleBtn: { width: 240, height: 48 },
  or: { color: '#9ca3af', fontSize: 13, marginVertical: 12 },
  input: {
    width: 240, height: 46, borderWidth: 1, borderColor: '#d1d5db', borderRadius: 10,
    paddingHorizontal: 14, fontSize: 15, color: '#111827', backgroundColor: '#fff',
  },
  toggleLink: { marginTop: 12, paddingVertical: 6 },
  toggleText: { color: '#1a2a4f', fontSize: 13, textDecorationLine: 'underline' },
  error: { color: '#b91c1c', marginTop: 16, textAlign: 'center' },
  demoLink: { marginTop: 20, paddingVertical: 6 },
  demoText: { color: '#6b7280', fontSize: 13, textDecorationLine: 'underline' },
});
