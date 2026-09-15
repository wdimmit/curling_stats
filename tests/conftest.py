import pytest

from curling_score.ingest import cache

PRIMARY_VID = "VXU9xwmugRg"  # Sheet 2, 4/30 — the reference VOD


@pytest.fixture(scope="session")
def primary_video():
    """Path to the cached reference VOD, or skip if it has not been fetched."""
    if not cache.is_cached(PRIMARY_VID):
        pytest.skip(f"{PRIMARY_VID} not in cache; run `curling-score fetch` first")
    return cache.video_path(PRIMARY_VID)


@pytest.fixture(scope="session")
def known_frame():
    """Load a frame from the hand-picked regression set, or skip."""
    import cv2

    root = cache.default_root() / "frames" / "_known"

    def _load(name):
        path = root / name
        if not path.is_file():
            pytest.skip(f"regression frame {name} not harvested")
        img = cv2.imread(str(path))
        if img is None:
            pytest.skip(f"could not decode {path}")
        return img

    return _load


# Sheet number -> video id, for the cross-sheet regression gate.
VALIDATION_VIDS = {
    1: "13REHKrIE9I",
    2: "Y1VZCk9tIsg",
    3: "wVkiErqeKBc",
    4: "QnWHfqaLzzc",
    5: "EznLfUsF57w",
}


@pytest.fixture(scope="session")
def harvested_frames():
    """Load the sampled frames for a validation video, or skip."""
    import cv2

    root = cache.default_root() / "frames"

    def _load(vid, minimum=8):
        paths = sorted((root / vid).glob("*.png"))
        if len(paths) < minimum:
            pytest.skip(f"{vid}: only {len(paths)} frames harvested, need {minimum}")
        return [cv2.imread(str(p)) for p in paths]

    return _load
