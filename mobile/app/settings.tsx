/**
 * mobile/app/settings.tsx — account-level settings.
 *
 * Home for the GLOBAL "Share my location with the club" consent toggle
 * (POST /api/live/sharing). This is the master switch; individual ride screens
 * only have a per-ride Start/Stop that streams while this is on. Turning it OFF
 * here is a kill-switch — the backend then rejects every beacon.
 */
import { useEffect, useState } from 'react';
import { ActivityIndicator, Alert, Pressable, ScrollView, StyleSheet, Switch, Text, TextInput, View } from 'react-native';
import { useSharing } from '../hooks/useSharing';
import { useSession } from '../contexts/SessionContext';
import { getLowPower, setLowPower, stopSharing } from '../location/backgroundLocation';
import { deleteConfirmSpec, isDeleteConfirmed, DELETE_KEYWORD } from '../lib/deleteConfirm';

export default function SettingsScreen() {
  const { enabled, isLoading, isError, refetch, setEnabled, saving } = useSharing();
  const { deleteAccount, accountEmail } = useSession();
  const [error, setError] = useState<string | null>(null);
  const [lowPower, setLowPowerState] = useState(false);
  const [deleting, setDeleting] = useState(false);
  // Two-step delete: tapping "Delete account" reveals a confirmation box where
  // the user must retype their email (or the DELETE keyword when we don't know
  // their email) before the final button unlocks.
  const [confirming, setConfirming] = useState(false);
  const [confirmText, setConfirmText] = useState('');

  useEffect(() => { getLowPower().then(setLowPowerState); }, []);

  const { requireEmail } = deleteConfirmSpec(accountEmail);
  const confirmed = isDeleteConfirmed(confirmText, accountEmail);

  function cancelDelete() {
    setConfirming(false);
    setConfirmText('');
  }

  async function onConfirmDelete() {
    if (!confirmed) return;
    setDeleting(true);
    const err = await deleteAccount();
    setDeleting(false);
    if (err) {
      Alert.alert('Could not delete account', err);
      return;
    }
    // On success, signOut (inside deleteAccount) sends us back to login.
  }

  async function onToggleLowPower(on: boolean) {
    setLowPowerState(on);                  // optimistic; setLowPower is best-effort
    await setLowPower(on).catch(() => undefined);
  }

  async function onToggle(on: boolean) {
    setError(null);
    try {
      await setEnabled(on);
    } catch {
      setError('Could not update your sharing setting. Try again.');
      return;
    }
    // Revoking consent is a kill-switch: stop any active background beacon.
    // Best-effort — the server already rejects beacons now, so a local stop
    // failure must not masquerade as a save failure.
    if (!on) await stopSharing().catch(() => undefined);
  }

  if (isLoading) {
    return <View style={styles.center}><ActivityIndicator /></View>;
  }
  if (isError) {
    return (
      <View style={styles.center}>
        <Text style={styles.muted}>Couldn't load your settings.</Text>
        <Pressable onPress={() => refetch()}><Text style={styles.link}>Retry</Text></Pressable>
      </View>
    );
  }

  return (
    <ScrollView style={styles.container} contentContainerStyle={{ padding: 16 }}>
      <Text style={styles.section}>Location sharing</Text>
      <View style={styles.card}>
        <View style={styles.row}>
          <Text style={styles.rowLabel}>Share my location with the club</Text>
          <Switch value={enabled === true} onValueChange={onToggle} disabled={saving} />
        </View>
        <Text style={styles.help}>
          When this is on, you can broadcast your live position from any ride's map
          using its “Share my location” button — it keeps posting with the screen
          off. Turn this off to stop all sharing immediately.
        </Text>
        {error ? <Text style={styles.error}>{error}</Text> : null}
      </View>

      <Text style={[styles.section, styles.sectionTop]}>Battery</Text>
      <View style={styles.card}>
        <View style={styles.row}>
          <Text style={styles.rowLabel}>Low power mode</Text>
          <Switch value={lowPower} onValueChange={onToggleLowPower} />
        </View>
        <Text style={styles.help}>
          Uses coarser GPS (~100m) and updates about every 2 minutes to save battery
          on long rides — recommended for 300 km+ brevets. Your live dot may lag up to
          a couple minutes (about a block off). Takes effect immediately while sharing.
        </Text>
      </View>

      <Text style={[styles.section, styles.sectionTop]}>Account</Text>
      <View style={styles.card}>
        {!confirming ? (
          <>
            <Pressable
              style={styles.dangerBtn}
              onPress={() => setConfirming(true)}
            >
              <Text style={styles.dangerText}>Delete account</Text>
            </Pressable>
            <Text style={styles.help}>
              Permanently deletes your account and all your data. This can't be undone.
            </Text>
          </>
        ) : (
          <>
            <Text style={styles.confirmTitle}>Permanently delete your account?</Text>
            <Text style={styles.help}>
              This deletes your account and all your data (ride history, Strava
              connection, live-tracking, and settings). This cannot be undone.
            </Text>
            <Text style={styles.confirmPrompt}>
              {requireEmail
                ? 'To confirm, type your account email address:'
                : `To confirm, type ${DELETE_KEYWORD} below:`}
            </Text>
            {requireEmail
              ? <Text selectable style={styles.confirmTarget}>{accountEmail}</Text>
              : null}
            <TextInput
              style={styles.confirmInput}
              value={confirmText}
              onChangeText={setConfirmText}
              placeholder={requireEmail ? 'you@example.com' : DELETE_KEYWORD}
              placeholderTextColor="#9ca3af"
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType={requireEmail ? 'email-address' : 'default'}
              editable={!deleting}
              autoFocus
            />
            <Pressable
              style={[styles.dangerBtn, (!confirmed || deleting) && styles.btnDisabled]}
              onPress={onConfirmDelete}
              disabled={!confirmed || deleting}
            >
              {deleting
                ? <ActivityIndicator color="#b91c1c" />
                : <Text style={styles.dangerText}>Delete my account</Text>}
            </Pressable>
            <Pressable
              style={styles.cancelBtn}
              onPress={cancelDelete}
              disabled={deleting}
            >
              <Text style={styles.cancelText}>Cancel</Text>
            </Pressable>
          </>
        )}
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f7fafc' },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 10 },
  section: { fontSize: 13, fontWeight: '700', color: '#6b7280', textTransform: 'uppercase', marginBottom: 8 },
  sectionTop: { marginTop: 20 },
  card: { backgroundColor: '#fff', borderRadius: 12, padding: 16, borderWidth: 1, borderColor: '#e5e7eb' },
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
  rowLabel: { flex: 1, fontSize: 15, fontWeight: '600', color: '#1a365d' },
  help: { color: '#6b7280', fontSize: 13, marginTop: 10, lineHeight: 19 },
  muted: { color: '#6b7280' },
  link: { color: '#2563eb', fontWeight: '600' },
  error: { color: '#b91c1c', marginTop: 10, fontSize: 13 },
  btnDisabled: { opacity: 0.6 },
  dangerBtn: { borderWidth: 1, borderColor: '#fecaca', backgroundColor: '#fef2f2', borderRadius: 10, paddingVertical: 12, alignItems: 'center' },
  dangerText: { color: '#b91c1c', fontWeight: '700', fontSize: 15 },
  confirmTitle: { fontSize: 16, fontWeight: '700', color: '#1a365d', marginBottom: 4 },
  confirmPrompt: { color: '#374151', fontSize: 14, fontWeight: '600', marginTop: 14 },
  confirmTarget: { color: '#1a365d', fontSize: 15, fontWeight: '700', marginTop: 4 },
  confirmInput: {
    borderWidth: 1, borderColor: '#d1d5db', borderRadius: 10, paddingHorizontal: 14,
    paddingVertical: 10, fontSize: 15, color: '#111827', backgroundColor: '#fff',
    marginTop: 10, marginBottom: 14,
  },
  cancelBtn: { paddingVertical: 12, alignItems: 'center', marginTop: 8 },
  cancelText: { color: '#6b7280', fontWeight: '600', fontSize: 15 },
});
