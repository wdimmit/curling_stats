"""Sheet, house and stone dimensions.

All values in metres, taken from World Curling, *The Rules of Curling and Rules of
Competition* (July 2025), rules R1 and R2. Ring names ("12-foot") are diameters; the
rulebook specifies radii, which is what we store.
"""

# --- Stone (R2(a), C8(c)) ---------------------------------------------------
# 142 mm is World Curling's official radius for measurement purposes. Max legal
# circumference is 914 mm, i.e. a 290.9 mm diameter, but 142 mm is the constant
# competitions actually measure with.
STONE_RADIUS_M = 0.142
STONE_HEIGHT_MIN_M = 0.114

# --- House (R1(d)) ----------------------------------------------------------
R_BUTTON_M = 0.152  # minimum; the painted button varies by club
R_4FT_M = 0.610
R_8FT_M = 1.219
R_12FT_M = 1.829

# --- Sheet (R1) -------------------------------------------------------------
SHEET_WIDTH_M = 4.750
TEE_TO_TEE_M = 34.747
TEE_TO_BACKLINE_M = 1.829  # back edge of the 12-ft ring sits on the back line
TEE_TO_HOGLINE_M = 6.401  # to the inside edge
HOGLINE_WIDTH_M = 0.102
TEE_TO_HACKLINE_M = 3.658

# --- Derived play boundaries (centre of a stone at rest) --------------------
# A stone counts if any part of it touches the 12-ft ring (R12(b)).
IN_HOUSE_MAX_D_M = R_12FT_M + STONE_RADIUS_M  # 1.971
# Completely across the outside edge of the back line (R2(g)).
THROUGH_BACK_Y_M = -(TEE_TO_BACKLINE_M + STONE_RADIUS_M)  # -1.971
# Not completely past the inside edge of the hog line (R2(f)).
HOGGED_Y_M = TEE_TO_HOGLINE_M - STONE_RADIUS_M  # 6.259
# Touching a side line (R2(h)).
SIDELINE_ABS_X_M = SHEET_WIDTH_M / 2 - STONE_RADIUS_M  # 2.233

# --- Game structure (R3, R5, R6) --------------------------------------------
STONES_PER_TEAM_PER_END = 8
STONES_PER_END = 2 * STONES_PER_TEAM_PER_END
PLAYERS_PER_TEAM = 4
FGZ_PROTECTED_STONES = 5  # the five-rock rule applies before the sixth stone

POSITION_NAMES = {1: "lead", 2: "second", 3: "third", 4: "skip"}
