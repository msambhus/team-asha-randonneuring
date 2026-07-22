/**
 * mobile/app/auth/otp.tsx — magic-link landing.
 *
 * The email's "tap to sign in" link is https and points at the backend, which
 * redirects into teamasha://auth/otp?token=<link_token>. expo-router opens this
 * screen; we exchange the one-time token for a session, then route on. We consume
 * the token exactly once (redemption is single-use on the backend).
 */
import { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useSession } from '../../contexts/SessionContext';

export default function OtpLinkScreen() {
  const { token } = useLocalSearchParams<{ token?: string }>();
  const { verifyEmailOtp } = useSession();
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const ran = useRef(false);

  useEffect(() => {
    if (ran.current) return;   // guard StrictMode / re-renders: redeem once
    ran.current = true;
    (async () => {
      if (!token) { setError('This sign-in link is missing its code.'); return; }
      const err = await verifyEmailOtp({ linkToken: String(token) });
      if (err) setError(err);
      else router.replace('/');   // signed in → home
    })();
  }, [token, verifyEmailOtp, router]);

  if (error) {
    return (
      <View style={styles.center}>
        <Text style={styles.error}>{error}</Text>
        <Pressable onPress={() => router.replace('/login')} hitSlop={8} style={styles.link}>
          <Text style={styles.linkText}>Back to sign in</Text>
        </Pressable>
      </View>
    );
  }
  return (
    <View style={styles.center}>
      <ActivityIndicator />
      <Text style={styles.msg}>Signing you in…</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24, gap: 12 },
  msg: { color: '#6b7280' },
  error: { color: '#b91c1c', textAlign: 'center' },
  link: { paddingVertical: 6 },
  linkText: { color: '#1a365d', textDecorationLine: 'underline' },
});
