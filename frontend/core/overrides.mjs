/* Pure transitions on the override map.
 *
 * Each returns a new map; none of them knows about saving, rendering or the
 * DOM. Read-only is enforced by the store that calls these, not here.
 */

export function patch(overrides, key, fields) {
  return { ...overrides, [key]: { ...(overrides[key] || {}), ...fields } };
}

export function unpatch(overrides, key, field) {
  if (!overrides[key]) return overrides;
  const one = { ...overrides[key] };
  delete one[field];
  const next = { ...overrides };
  if (Object.keys(one).length) next[key] = one; else delete next[key];
  return next;
}

export function clearKey(overrides, key) {
  if (!(key in overrides)) return overrides;
  const next = { ...overrides };
  delete next[key];
  return next;
}

/* Stones live in the patch as a whole array, because the patch is a shallow
 * merge -- so the first edit takes a copy of whatever is currently shown. */
export const copyStones = shot => (shot?.stones || []).map(s => ({ ...s }));

export const withStones = (overrides, key, stones) =>
  patch(overrides, key, { stones, state_known: true });

/* Removing a stone renumbers everything after it, so the pointer to the stone
 * that was thrown has to move with it -- otherwise deleting a rock silently
 * re-labels a different one as the shot. */
export function removeStone(shot, i) {
  const stones = copyStones(shot);
  if (!stones[i]) return null;
  stones.splice(i, 1);
  let thrown = shot.delivered_stone_index;
  if (typeof thrown === "number")
    thrown = thrown === i ? null : thrown > i ? thrown - 1 : thrown;
  return { stones, state_known: true, delivered_stone_index: thrown ?? null };
}

export function toggleStoneColor(shot, i) {
  const stones = copyStones(shot);
  if (!stones[i]) return null;
  stones[i].color = stones[i].color === "red" ? "yellow" : "red";
  return stones;
}
