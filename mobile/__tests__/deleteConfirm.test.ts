/**
 * mobile/__tests__/deleteConfirm.test.ts — the confirm-before-delete rule.
 * With a known account email the user must retype it (case-insensitive); with
 * no email on record (demo / magic-link) they type the DELETE keyword.
 */
import { deleteConfirmSpec, isDeleteConfirmed, DELETE_KEYWORD } from '../lib/deleteConfirm';

describe('deleteConfirmSpec', () => {
  it('requires the email when one is known', () => {
    expect(deleteConfirmSpec('Rider@Example.com')).toEqual({
      requireEmail: true,
      target: 'rider@example.com',
    });
  });

  it('falls back to the DELETE keyword when no email is known', () => {
    for (const empty of [null, undefined, '', '   ']) {
      expect(deleteConfirmSpec(empty)).toEqual({
        requireEmail: false,
        target: DELETE_KEYWORD,
      });
    }
  });
});

describe('isDeleteConfirmed — email known', () => {
  const email = 'rider@example.com';

  it('accepts the exact email', () => {
    expect(isDeleteConfirmed('rider@example.com', email)).toBe(true);
  });

  it('accepts case/whitespace variants of the email', () => {
    expect(isDeleteConfirmed('  Rider@Example.COM ', email)).toBe(true);
  });

  it('rejects a different address, the DELETE keyword, and empty input', () => {
    expect(isDeleteConfirmed('someone@else.com', email)).toBe(false);
    expect(isDeleteConfirmed('DELETE', email)).toBe(false);
    expect(isDeleteConfirmed('', email)).toBe(false);
  });
});

describe('isDeleteConfirmed — no email (demo / magic-link)', () => {
  it('accepts the exact DELETE keyword only', () => {
    expect(isDeleteConfirmed('DELETE', null)).toBe(true);
    expect(isDeleteConfirmed('  DELETE  ', '')).toBe(true);
  });

  it('rejects lowercase, partial, or empty input', () => {
    expect(isDeleteConfirmed('delete', null)).toBe(false);
    expect(isDeleteConfirmed('Del', null)).toBe(false);
    expect(isDeleteConfirmed('', null)).toBe(false);
  });
});
