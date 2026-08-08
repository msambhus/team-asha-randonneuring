import { useMemo, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';
import { Feather } from '@expo/vector-icons';
import { useTrainingLog } from '../hooks/useTrainingLog';
import type { TrainingActivity } from '../lib/types';

const monthKey = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
const dateKey = (date: Date) => `${monthKey(date)}-${String(date.getDate()).padStart(2, '0')}`;
const addMonth = (date: Date, delta: number) => new Date(date.getFullYear(), date.getMonth() + delta, 1);

function activityIcon(type: string): keyof typeof Feather.glyphMap {
  const value = type.toLowerCase();
  if (value.includes('ride') || value.includes('cycle')) return 'navigation';
  if (value.includes('run')) return 'activity';
  if (value.includes('swim')) return 'droplet';
  if (value.includes('walk') || value.includes('hike')) return 'map';
  if (value.includes('ski')) return 'wind';
  return 'zap';
}

const activityColor = (type: string) => type.toLowerCase().includes('ride') ? '#fc4c02' : '#2563eb';

export function TrainingCalendar() {
  const [month, setMonth] = useState(() => new Date(new Date().getFullYear(), new Date().getMonth(), 1));
  const query = useTrainingLog(monthKey(month));
  const cells = useMemo(() => {
    const first = new Date(month.getFullYear(), month.getMonth(), 1);
    const days = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate();
    const out: (Date | null)[] = Array(first.getDay()).fill(null);
    for (let day = 1; day <= days; day += 1) out.push(new Date(month.getFullYear(), month.getMonth(), day));
    while (out.length % 7) out.push(null);
    return out;
  }, [month]);
  const byDate = useMemo(() => {
    const map = new Map<string, TrainingActivity[]>();
    for (const activity of query.data?.activities ?? []) {
      if (!activity.date) continue;
      map.set(activity.date, [...(map.get(activity.date) ?? []), activity]);
    }
    return map;
  }, [query.data]);
  const label = month.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
  const activities = query.data?.activities ?? [];
  const totalMiles = activities.reduce((sum, a) => sum + a.distance_mi, 0);
  const totalMinutes = activities.reduce((sum, a) => sum + a.moving_minutes, 0);

  return <View style={styles.card}>
    <View style={styles.header}><View><Text style={styles.title}>Training log</Text><Text style={styles.attribution}>{query.data?.attribution ?? 'Powered by Strava'}</Text></View><View style={styles.monthNav}><Pressable onPress={() => setMonth(addMonth(month, -1))} hitSlop={8}><Feather name="chevron-left" size={22} color="#1a365d" /></Pressable><Text style={styles.month}>{label}</Text><Pressable onPress={() => setMonth(addMonth(month, 1))} disabled={monthKey(month) >= monthKey(new Date())} hitSlop={8}><Feather name="chevron-right" size={22} color={monthKey(month) >= monthKey(new Date()) ? '#cbd5e1' : '#1a365d'} /></Pressable></View></View>
    {query.isLoading ? <ActivityIndicator style={{ margin: 24 }} /> : !query.data?.connected ? <Text style={styles.empty}>Connect Strava in Settings to see your training calendar.</Text> : <>
      <View style={styles.week}><Text style={styles.weekday}>S</Text><Text style={styles.weekday}>M</Text><Text style={styles.weekday}>T</Text><Text style={styles.weekday}>W</Text><Text style={styles.weekday}>T</Text><Text style={styles.weekday}>F</Text><Text style={styles.weekday}>S</Text></View>
      <View style={styles.grid}>{cells.map((day, index) => { const dayActivities = day ? (byDate.get(dateKey(day)) ?? []) : []; return <View style={styles.day} key={day ? dateKey(day) : `blank-${index}`}><Text style={styles.dayNumber}>{day?.getDate() ?? ''}</Text><View style={styles.icons}>{dayActivities.slice(0, 3).map((a) => <Feather key={a.id} name={activityIcon(a.type)} size={11} color={activityColor(a.type)} />)}</View>{dayActivities.length > 3 ? <Text style={styles.more}>+{dayActivities.length - 3}</Text> : null}</View>; })}</View>
      <View style={styles.summary}><Text style={styles.summaryText}>{activities.length} activities</Text><Text style={styles.summaryText}>{totalMiles.toFixed(1)} mi</Text><Text style={styles.summaryText}>{Math.floor(totalMinutes / 60)}h {totalMinutes % 60}m</Text></View>
      {activities.slice(0, 12).map((activity) => <View style={styles.activity} key={activity.id}><View style={[styles.activityIcon, { backgroundColor: `${activityColor(activity.type)}18` }]}><Feather name={activityIcon(activity.type)} size={17} color={activityColor(activity.type)} /></View><View style={{ flex: 1 }}><Text style={styles.activityName}>{activity.name}</Text><Text style={styles.activityMeta}>{activity.date} · {activity.moving_minutes} min{activity.distance_mi ? ` · ${activity.distance_mi} mi` : ''}{activity.elevation_ft ? ` · ${activity.elevation_ft.toLocaleString()} ft` : ''}</Text></View>{activity.suffer_score != null ? <Text style={styles.effort}>{activity.suffer_score}</Text> : null}</View>)}
      {!activities.length ? <Text style={styles.empty}>No synced activities this month.</Text> : null}
    </>}
  </View>;
}

const styles = StyleSheet.create({ card: { backgroundColor: '#fff', borderRadius: 12, padding: 16, marginBottom: 12, borderWidth: 1, borderColor: '#e5e7eb' }, header: { gap: 12, marginBottom: 14 }, title: { color: '#1a365d', fontSize: 16, fontWeight: '800' }, attribution: { color: '#fc4c02', fontSize: 11, fontWeight: '700', marginTop: 2 }, monthNav: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }, month: { color: '#1a365d', fontWeight: '700' }, week: { flexDirection: 'row' }, weekday: { width: '14.285%', textAlign: 'center', color: '#94a3b8', fontSize: 11, fontWeight: '700' }, grid: { flexDirection: 'row', flexWrap: 'wrap', marginTop: 5 }, day: { width: '14.285%', height: 48, borderTopWidth: 1, borderTopColor: '#f1f5f9', alignItems: 'center', paddingTop: 3 }, dayNumber: { color: '#64748b', fontSize: 11 }, icons: { flexDirection: 'row', gap: 1, marginTop: 3 }, more: { color: '#94a3b8', fontSize: 8 }, summary: { flexDirection: 'row', justifyContent: 'space-around', backgroundColor: '#fff7ed', borderRadius: 9, padding: 10, marginTop: 10 }, summaryText: { color: '#9a3412', fontSize: 12, fontWeight: '700' }, activity: { flexDirection: 'row', alignItems: 'center', gap: 10, borderTopWidth: 1, borderTopColor: '#f1f5f9', paddingVertical: 10 }, activityIcon: { width: 34, height: 34, borderRadius: 17, alignItems: 'center', justifyContent: 'center' }, activityName: { color: '#1e293b', fontWeight: '700' }, activityMeta: { color: '#64748b', fontSize: 11, marginTop: 2 }, effort: { color: '#fc4c02', fontWeight: '800' }, empty: { color: '#64748b', fontSize: 13, paddingVertical: 18, textAlign: 'center' } });
