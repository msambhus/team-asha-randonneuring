/**
 * mobile/lib/deleteConfirm.ts — gating logic for the "confirm account deletion"
 * step. Deletion is irreversible, so the user must type a confirmation value
 * before the Delete button unlocks:
 *   • if we know the account's email (captured at sign-in), they must type it
 *     (case-insensitive, trimmed) — a deliberate, ownership-style confirmation;
 *   • otherwise (reviewer demo login, or a magic-link session with no stored
 *     email), they type the word DELETE — so no one is ever locked out of
 *     deleting their own account.
 * Pure functions so the rule is unit-tested without rendering the screen.
 */
export const DELETE_KEYWORD = 'DELETE';

export interface DeleteConfirmSpec {
  /** true → confirm by typing the email; false → confirm by typing DELETE. */
  requireEmail: boolean;
  /** The exact value the user must type (normalized for comparison). */
  target: string;
}

export function deleteConfirmSpec(accountEmail: string | null | undefined): DeleteConfirmSpec {
  const email = (accountEmail || '').trim();
  return email
    ? { requireEmail: true, target: email.toLowerCase() }
    : { requireEmail: false, target: DELETE_KEYWORD };
}

export function isDeleteConfirmed(
  input: string,
  accountEmail: string | null | undefined,
): boolean {
  const { requireEmail, target } = deleteConfirmSpec(accountEmail);
  const typed = (input || '').trim();
  // Email match is case-insensitive (addresses are case-insensitive in practice);
  // the DELETE keyword is an exact, all-caps match for a deliberate action.
  return requireEmail ? typed.toLowerCase() === target : typed === target;
}
