/**
 * mobile/components/WeatherChart.tsx — a small multi-series line/area chart drawn
 * with react-native-svg. Mirrors the web /weather Chart.js panels (temp, wind,
 * headwind/tailwind, precip, elevation, humidity) on a phone-sized canvas.
 *
 * Pass a `baseline` (e.g. 0 for headwind) to fill areas toward it and draw a
 * dashed reference line; otherwise areas fill to the data minimum.
 */
import React from 'react';
import { StyleSheet, Text, View, useWindowDimensions } from 'react-native';
import Svg, { Line, Path, Polyline, Text as SvgText } from 'react-native-svg';

export interface ChartSeries {
  data: number[];
  color: string;
  fill?: boolean;
}

interface Props {
  title: string;
  unit?: string;
  labels: number[];                 // x values (distance in mi)
  series: ChartSeries[];
  baseline?: number;                // y the area fills toward; also draws a ref line
  height?: number;
  legend?: { label: string; color: string }[];
}

export function WeatherChart({ title, unit, labels, series, baseline, height = 150, legend }: Props) {
  const { width: screenW } = useWindowDimensions();
  const W = Math.max(240, screenW - 32 - 24); // screen padding (16×2) + card padding (12×2)
  const H = height;
  const padL = 34, padR = 12, padT = 12, padB = 22;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;

  const finite = series.flatMap((s) => s.data).filter((v) => Number.isFinite(v));
  if (finite.length < 2 || labels.length < 2) return null;

  let yMin = Math.min(...finite);
  let yMax = Math.max(...finite);
  if (baseline != null) { yMin = Math.min(yMin, baseline); yMax = Math.max(yMax, baseline); }
  if (yMin === yMax) yMax = yMin + 1;

  const n = labels.length;
  const base = baseline != null ? baseline : yMin;
  const sx = (i: number) => padL + (plotW * i) / (n - 1);
  const sy = (v: number) => padT + plotH * (1 - ((Number.isFinite(v) ? v : base) - yMin) / (yMax - yMin));

  return (
    <View style={styles.card}>
      <Text style={styles.title}>{title}{unit ? `  ·  ${unit}` : ''}</Text>
      <Svg width={W} height={H}>
        {baseline != null ? (
          <Line x1={padL} y1={sy(base)} x2={W - padR} y2={sy(base)}
            stroke="#cbd5e1" strokeWidth={1} strokeDasharray="3 3" />
        ) : null}
        {series.map((s, si) => {
          const pts = s.data.map((v, i) => `${sx(i)},${sy(v)}`).join(' ');
          const area =
            `M ${sx(0)},${sy(base)} ` +
            s.data.map((v, i) => `L ${sx(i)},${sy(v)}`).join(' ') +
            ` L ${sx(n - 1)},${sy(base)} Z`;
          return (
            <React.Fragment key={si}>
              {s.fill ? <Path d={area} fill={s.color} opacity={0.18} /> : null}
              <Polyline points={pts} fill="none" stroke={s.color} strokeWidth={2} />
            </React.Fragment>
          );
        })}
        <SvgText x={4} y={sy(yMax) + 3} fontSize={9} fill="#6b7280">{Math.round(yMax)}</SvgText>
        <SvgText x={4} y={sy(yMin) + 3} fontSize={9} fill="#6b7280">{Math.round(yMin)}</SvgText>
        <SvgText x={padL} y={H - 6} fontSize={9} fill="#9ca3af">{Math.round(labels[0])}</SvgText>
        <SvgText x={padL + plotW / 2 - 10} y={H - 6} fontSize={9} fill="#9ca3af">
          {Math.round(labels[Math.floor(n / 2)])}
        </SvgText>
        <SvgText x={W - padR - 18} y={H - 6} fontSize={9} fill="#9ca3af">{Math.round(labels[n - 1])}</SvgText>
      </Svg>
      <View style={styles.footer}>
        {legend?.length ? (
          <View style={styles.legendRow}>
            {legend.map((l) => (
              <View key={l.label} style={styles.legendItem}>
                <View style={[styles.swatch, { backgroundColor: l.color }]} />
                <Text style={styles.legendText}>{l.label}</Text>
              </View>
            ))}
          </View>
        ) : <View />}
        <Text style={styles.axis}>distance (mi)</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: { backgroundColor: '#fff', borderRadius: 12, padding: 12, marginBottom: 12, borderWidth: 1, borderColor: '#e5e7eb' },
  title: { fontSize: 13, fontWeight: '700', color: '#1a365d', marginBottom: 6 },
  footer: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginTop: 4 },
  legendRow: { flexDirection: 'row', gap: 12, flexWrap: 'wrap', flexShrink: 1 },
  legendItem: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  swatch: { width: 10, height: 10, borderRadius: 2 },
  legendText: { fontSize: 10, color: '#6b7280' },
  axis: { fontSize: 9, color: '#9ca3af' },
});
