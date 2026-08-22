import { normalizeRusaId, publicRiderPath } from '../hooks/usePublicRiders';

describe('public rider route contract', () => {
  it('normalizes Expo scalar and array route params', () => {
    expect(normalizeRusaId('14680')).toBe('14680');
    expect(normalizeRusaId(['14680'])).toBe('14680');
    expect(normalizeRusaId(['14680', 'ignored'])).toBe('14680');
  });

  it('rejects malformed IDs and builds the public API path', () => {
    expect(publicRiderPath('14680')).toBe('/api/riders/14680');
    expect(publicRiderPath(['14680'])).toBe('/api/riders/14680');
    expect(publicRiderPath('')).toBeUndefined();
    expect(publicRiderPath('not-a-rusa-id')).toBeUndefined();
  });
});
