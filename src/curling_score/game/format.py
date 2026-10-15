"""What kind of game is being played: how many stones, and who throws which.

Four-player curling and mixed doubles share the sheet, the stones and the
scoring. They differ in how an end is built (WCF R17). Doubles sets one stone
per team on the ice before the end and delivers five. One player throws a
team's first and last stones and the partner the three between. A blank end
hands the placement decision -- and so, in practice, the hammer -- to the team
that threw first, where in fours it keeps the hammer where it was.

Everything that used to read those numbers from ``geometry.constants`` takes
one of these instead. A timeline records which one built it, and a timeline
that does not say is fours, which is what every chart made before doubles
existed is.
"""

from dataclasses import dataclass

from curling_score.game.rules import COLORS, ThrowInfo, ordinal
from curling_score.geometry import constants as C

_NTH = ("first", "second", "third")


@dataclass(frozen=True)
class GameFormat:
    name: str
    stones_per_team: int          # in play per team per end, placed ones included
    placed_per_team: int          # set on the ice before the end, never delivered
    throw_table: tuple[int, ...]  # a team's k-th delivered stone -> player slot
    positions: tuple[str, ...]    # player slot 1.. -> what a curler calls them
    blank_passes_hammer: bool     # does a blank end move the hammer?
    swappable: bool               # may a team's two players swap roles per end?
    # Does a rock nobody held a broom for still get its thrown line? In
    # doubles the partner is usually sweeping, not holding a broom in the
    # house (across four doubles games, 227 of 240 seen rocks had no broom
    # held in the house; the 13 that did were checked on camera,
    # 2026-09-26), so the start, the line, the path and the curl are
    # measured without one. Not in the document: it is what this code does
    # with a format, not a fact about the game.
    line_without_broom: bool = False
    # Who throws a team's k-th stone when the team is three players (WCF R4:
    # the first two deliver three each, the last two), in the full team's
    # slots: the skip is still the last slot, and the slot nobody plays
    # throws nothing -- so a play saved as "skip" stays the skip's, and the
    # report's position rows still line up. None where a team cannot be
    # short. Which teams were is not the format's to know: the game's record
    # says (Source.short_handed), and api.game_doc writes it into the
    # document's format block (`short_handed_of`).
    short_throw_table: tuple[int, ...] | None = None

    @property
    def delivered_per_team(self) -> int:
        return self.stones_per_team - self.placed_per_team

    @property
    def delivered_per_end(self) -> int:
        return 2 * self.delivered_per_team

    @property
    def max_score_per_end(self) -> int:
        return self.stones_per_team

    def throw_info(self, shot_number: int, swapped: bool = False,
                   short: bool = False) -> ThrowInfo:
        """Who throws the end's ``shot_number``-th delivered stone.

        Teams alternate and the hammer team throws the even-numbered stones in
        both formats. ``swapped`` says this team's players traded roles this
        end, which only a two-player team can do: the slot then names the
        person, and ``rock_of_player`` still counts within the role.
        ``short`` says this team played with three, which only a format with
        a three-player table honours.
        """
        if not 1 <= shot_number <= self.delivered_per_end:
            raise ValueError(
                f"shot_number must be 1..{self.delivered_per_end}, got {shot_number}"
            )
        k = (shot_number + 1) // 2
        table = self.short_throw_table if short and self.short_throw_table else self.throw_table
        role = table[k - 1]
        slot = 3 - role if swapped and self.swappable else role
        return ThrowInfo(
            shot_number=shot_number,
            has_hammer=shot_number % 2 == 0,
            team_stone_number=k,
            position_slot=slot,
            rock_of_player=table[:k].count(role),
        )

    def shot_label(self, end: int, shot_number: int, swapped: bool = False,
                   short: bool = False) -> str:
        """"3rd end, second's first rock" -- or, in doubles, "3rd end, B's third rock"."""
        t = self.throw_info(shot_number, swapped, short)
        who = self.positions[t.position_slot - 1]
        return f"{ordinal(end)} end, {who}'s {_NTH[t.rock_of_player - 1]} rock"

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "stones_per_team": self.stones_per_team,
            "placed_per_team": self.placed_per_team,
            "delivered_per_team": self.delivered_per_team,
            "delivered_per_end": self.delivered_per_end,
            "positions": list(self.positions),
            "throw_table": list(self.throw_table),
            "blank_passes_hammer": self.blank_passes_hammer,
            "swappable": self.swappable,
            **({"short_throw_table": list(self.short_throw_table)}
               if self.short_throw_table else {}),
        }


FOURS = GameFormat(
    name="fours",
    stones_per_team=C.STONES_PER_TEAM_PER_END,
    placed_per_team=0,
    throw_table=(1, 1, 2, 2, 3, 3, 4, 4),
    positions=tuple(C.POSITION_NAMES[i] for i in sorted(C.POSITION_NAMES)),
    blank_passes_hammer=False,
    swappable=False,
    short_throw_table=(1, 1, 1, 2, 2, 2, 4, 4),
)
DOUBLES = GameFormat(
    name="doubles",
    stones_per_team=6,
    placed_per_team=1,
    throw_table=(1, 2, 2, 2, 1),
    positions=("A", "B"),
    blank_passes_hammer=True,
    swappable=True,
    line_without_broom=True,
)
FORMATS = {f.name: f for f in (FOURS, DOUBLES)}


def by_name(name: str) -> GameFormat:
    """The format called ``name``. Raises on anything else: a typo in a form
    or a flag must not quietly become fours."""
    try:
        return FORMATS[name]
    except KeyError:
        raise ValueError(
            f"unknown game format {name!r}; expected one of {sorted(FORMATS)}"
        ) from None


def of_document(doc) -> GameFormat:
    """The format a timeline was built for. Never raises: a document that does
    not say, or names a format this code does not know, is read as fours --
    the one every older chart was built for."""
    block = doc.get("format") if isinstance(doc, dict) else None
    name = block.get("name") if isinstance(block, dict) else None
    # Only a string can name a format; a list or object would not even hash.
    return FORMATS.get(name, FOURS) if isinstance(name, str) else FOURS


def short_handed_of(doc) -> frozenset[str]:
    """The colours that played with three, as api.game_doc wrote them into
    the document's format block. Never raises: a format with no three-player
    table, a block that does not say, or one not shaped as expected is
    nobody."""
    block = doc.get("format") if isinstance(doc, dict) else None
    if not isinstance(block, dict) or not of_document(doc).short_throw_table:
        return frozenset()
    said = block.get("short_handed")
    if not isinstance(said, list):
        return frozenset()
    return frozenset(c for c in said if isinstance(c, str) and c in COLORS)
