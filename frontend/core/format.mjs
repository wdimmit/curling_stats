/* What kind of game a chart is: how many rocks, and who throws which.
 *
 * Mirrors curling_score.game.format. A doubles timeline carries its whole
 * format block -- throw table and position names included -- so this never
 * has to know doubles by name: it reads the table it is given, and a document
 * without one (every chart made before doubles existed) is four-player.
 */

export const FOURS = Object.freeze({
  name: "fours", stones_per_team: 8, placed_per_team: 0,
  delivered_per_team: 8, delivered_per_end: 16,
  positions: ["lead", "second", "third", "skip"],
  throw_table: [1, 1, 2, 2, 3, 3, 4, 4],
  blank_passes_hammer: false, swappable: false,
});

const NTH = ["first", "second", "third", "fourth", "fifth"];
const NUM = ["1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th"];

/* The format a document was analysed as. A block that lacks what the throw
 * arithmetic needs is not trusted: four-player, the shape every older chart
 * has, rather than a crash. */
export function formatOf(doc) {
  const f = doc?.format;
  if (f && Array.isArray(f.positions) && Array.isArray(f.throw_table)
      && f.throw_table.length && f.throw_table.every(Number.isInteger)) return f;
  return FOURS;
}

/* Who throws the end's n-th delivered rock. `swapped` says this team's two
 * players traded roles this end: the slot then names the person, and
 * rock_of_player still counts within the role. */
export function throwInfo(n, fmt = FOURS, swapped = false) {
  const table = fmt.throw_table;
  const k = Math.min((n + 1) >> 1, table.length);   // this team's k-th rock
  const role = table[k - 1];
  let rock = 0;
  for (let i = 0; i < k; i++) if (table[i] === role) rock++;
  const slot = swapped && fmt.swappable ? 3 - role : role;
  return { has_hammer: n % 2 === 0, thrower_slot: slot, rock_of_player: rock };
}

export const ordinalOf = n => n + (n % 100 >= 11 && n % 100 <= 13 ? "th"
  : { 1: "st", 2: "nd", 3: "rd" }[n % 10] || "th");

export function shotLabel(endNo, n, fmt = FOURS, swapped = false) {
  const t = throwInfo(n, fmt, swapped);
  return `${ordinalOf(endNo)} end, ${fmt.positions[t.thrower_slot - 1]}'s `
       + `${NTH[t.rock_of_player - 1]} rock`;
}

/* Which of a team's rocks a player's role throws: "1st & 5th", "2nd–4th". */
export function roleText(fmt, slot) {
  const ks = fmt.throw_table.map((r, i) => (r === slot ? i + 1 : 0)).filter(Boolean);
  if (!ks.length) return "";
  const run = ks.every((k, i) => i === 0 || k === ks[i - 1] + 1);
  if (run && ks.length > 2) return `${NUM[ks[0] - 1]}–${NUM[ks[ks.length - 1] - 1]}`;
  return ks.map(k => NUM[k - 1]).join(" & ");
}

/* The Detail row: "second (rock 1)" in fours, as it always read; "Player B
 * (rock 2 of 3)" where a team is two players. */
export function throwerText(shot, fmt = FOURS) {
  if (!shot) return "—";
  if (!fmt.swappable) return `${shot.position ?? "—"} (rock ${shot.rock_of_player})`;
  const k = Math.min(((shot.number || 1) + 1) >> 1, fmt.throw_table.length);
  const role = fmt.throw_table[k - 1];
  const of = fmt.throw_table.filter(r => r === role).length;
  return `Player ${shot.position ?? "?"} (rock ${shot.rock_of_player} of ${of})`;
}

/* A sentence for the chart page when the ends do not look like the format
 * they were analysed as -- the pipeline flags it, never overrides it. */
export function formatWarning(doc) {
  if (doc?.format_warning) return String(doc.format_warning);
  const check = doc?.format?.check;
  if (check && check.looks_like && check.looks_like !== "unknown"
      && check.looks_like !== doc.format.name)
    return `This game was analysed as doubles, but its ends look like a `
         + `four-player game (a median of ${check.median_offered} rocks offered `
         + `across ${check.ends} ends). If it is, resubmit it as four-player.`;
  return null;
}
