/**
 * mobile/app/login.tsx — first-party login only.
 *
 * Google + Sign in with Apple were removed (App Store Guideline 4.8 only requires
 * a privacy-preserving option when a third-party login is offered; with none, no
 * Apple button is needed). Two options remain:
 *   • Email code (passwordless OTP): request a code, then enter it. This is also
 *     how an existing Google member signs in — the code goes to their email.
 *   • Email + password.
 * An optional phone number is collected on the code flow for a future
 * text-message sign-in (phase 2).
 */
import { useState } from 'react';
import {
  ActivityIndicator, Pressable, StyleSheet, Text, TextInput, View,
} from 'react-native';
import { useSession } from '../contexts/SessionContext';

type Method = 'otp' | 'password';

export default function LoginScreen() {
  const {
    signInDemo, signInWithPassword, signUpWithPassword,
    requestEmailOtp, verifyEmailOtp,
  } = useSession();

  const [method, setMethod] = useState<Method>('otp');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [mode, setMode] = useState<'login' | 'signup'>('login');   // password mode

  const [otpStage, setOtpStage] = useState<'request' | 'verify'>('request');
  const [code, setCode] = useState('');

  async function run(fn: () => Promise<string | null>) {
    setBusy(true);
    setError(null);
    const err = await fn();
    setBusy(false);
    if (err) setError(err);
    // On success the AuthGate redirects into the app.
  }

  function switchMethod(next: Method) {
    setMethod(next);
    setError(null);
    setInfo(null);
  }

  async function sendCode() {
    setBusy(true);
    setError(null);
    const err = await requestEmailOtp(email.trim());
    setBusy(false);
    if (err) { setError(err); return; }
    setOtpStage('verify');
    setInfo(`We emailed a 6-digit code to ${email.trim()}. Enter it below.`);
  }

  function verifyCode() {
    // Phone is intentionally NOT collected until SMS OTP (phase 2) is built.
    run(() => verifyEmailOtp({ email: email.trim(), code: code.trim() }));
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

      {/* Method switch */}
      <View style={styles.tabs}>
        <Pressable
          style={[styles.tab, method === 'otp' && styles.tabActive]}
          onPress={() => switchMethod('otp')}
          disabled={busy}
        >
          <Text style={[styles.tabText, method === 'otp' && styles.tabTextActive]}>Email code</Text>
        </Pressable>
        <Pressable
          style={[styles.tab, method === 'password' && styles.tabActive]}
          onPress={() => switchMethod('password')}
          disabled={busy}
        >
          <Text style={[styles.tabText, method === 'password' && styles.tabTextActive]}>Password</Text>
        </Pressable>
      </View>

      {/* Email is shared by both methods; lock it on the code-verify step. */}
      <TextInput
        style={[styles.input, method === 'otp' && otpStage === 'verify' && styles.inputLocked]}
        placeholder="Email"
        placeholderTextColor="#9ca3af"
        value={email}
        onChangeText={setEmail}
        autoCapitalize="none"
        autoCorrect={false}
        keyboardType="email-address"
        textContentType="emailAddress"
        editable={!busy && !(method === 'otp' && otpStage === 'verify')}
      />

      {method === 'otp' ? (
        otpStage === 'request' ? (
          <>
            <Pressable
              style={[styles.btn, (busy || !email.trim()) && styles.btnDisabled]}
              disabled={busy || !email.trim()}
              onPress={sendCode}
            >
              {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.btnText}>Email me a code</Text>}
            </Pressable>
            {/* Email-code is sign-up AND sign-in: entering an email and verifying
                the code creates the account if it's new. Say so, since (unlike the
                Password tab) there's no separate "create account" step. */}
            <Text style={styles.hint}>
              New or returning — we&apos;ll email you a sign-in code. No password needed;
              an account is created automatically the first time.
            </Text>
          </>
        ) : (
          <>
            <TextInput
              style={styles.input}
              placeholder="6-digit code"
              placeholderTextColor="#9ca3af"
              value={code}
              onChangeText={setCode}
              keyboardType="number-pad"
              maxLength={6}
              textContentType="oneTimeCode"
              editable={!busy}
            />
            <Pressable
              style={[styles.btn, (busy || code.trim().length < 6) && styles.btnDisabled]}
              disabled={busy || code.trim().length < 6}
              onPress={verifyCode}
            >
              {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.btnText}>Verify &amp; sign in</Text>}
            </Pressable>
            <Pressable
              onPress={() => { setOtpStage('request'); setCode(''); setInfo(null); setError(null); }}
              disabled={busy}
              hitSlop={8}
              style={styles.toggleLink}
            >
              <Text style={styles.toggleText}>Use a different email / resend code</Text>
            </Pressable>
          </>
        )
      ) : (
        <>
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
        </>
      )}

      {info ? <Text style={styles.info}>{info}</Text> : null}
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
  tabs: { flexDirection: 'row', width: 240, marginBottom: 8, borderRadius: 10, borderWidth: 1, borderColor: '#d1d5db', overflow: 'hidden' },
  tab: { flex: 1, paddingVertical: 10, alignItems: 'center', backgroundColor: '#fff' },
  tabActive: { backgroundColor: '#1a365d' },
  tabText: { color: '#1a365d', fontWeight: '600', fontSize: 14 },
  tabTextActive: { color: '#fff' },
  btn: { backgroundColor: '#1a365d', width: 240, height: 48, borderRadius: 10, alignItems: 'center', justifyContent: 'center' },
  btnDisabled: { opacity: 0.6 },
  btnText: { color: '#fff', fontWeight: '600', fontSize: 16 },
  input: {
    width: 240, height: 46, borderWidth: 1, borderColor: '#d1d5db', borderRadius: 10,
    paddingHorizontal: 14, fontSize: 15, color: '#111827', backgroundColor: '#fff',
  },
  inputLocked: { backgroundColor: '#f3f4f6', color: '#6b7280' },
  hint: { color: '#6b7280', fontSize: 12, textAlign: 'center', width: 240, marginTop: 10, lineHeight: 17 },
  toggleLink: { marginTop: 12, paddingVertical: 6 },
  toggleText: { color: '#1a365d', fontSize: 13, textDecorationLine: 'underline' },
  info: { color: '#1a365d', marginTop: 16, textAlign: 'center', width: 260 },
  error: { color: '#b91c1c', marginTop: 16, textAlign: 'center', width: 260 },
  demoLink: { marginTop: 20, paddingVertical: 6 },
  demoText: { color: '#6b7280', fontSize: 13, textDecorationLine: 'underline' },
});
