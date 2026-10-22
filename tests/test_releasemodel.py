import subprocess
import sys

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from curling_score import weights  # noqa: E402
from curling_score.detect import releasemodel  # noqa: E402


def _save(model, path):
    torch.save({k: v.half() if v.is_floating_point() else v
                for k, v in model.state_dict().items()}, path)


@pytest.fixture
def saved(tmp_path):
    torch.manual_seed(0)
    path = tmp_path / "r.pt"
    _save(releasemodel.build(), path)
    return path


def test_importing_it_does_not_import_torch():
    code = ("import sys; import curling_score.detect.releasemodel; "
            "sys.exit(1 if 'torch' in sys.modules else 0)")
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0


def test_build_is_a_resnet18_with_one_output():
    m = releasemodel.build()
    assert m.fc.in_features == 512 and m.fc.out_features == 1
    assert m(torch.zeros(1, 3, 214, 320)).shape == (1, 1)


def test_prepare_upsamples_two_fold_and_normalises():
    out = releasemodel.prepare(torch.full((2, 3, 107, 160), 0.45))
    assert out.shape == (2, 3, 214, 320)
    assert torch.allclose(out, torch.zeros_like(out), atol=1e-6)
    one = releasemodel.prepare(torch.ones(1, 3, 107, 160))
    assert torch.allclose(one, torch.full_like(one, (1 - 0.45) / 0.225), atol=1e-5)


def test_a_saved_model_loads_and_gives_probabilities(saved):
    stacks = np.random.default_rng(0).random((5, 3, 107, 160), dtype="float32")
    p = releasemodel.p_off(releasemodel._load(str(saved)), stacks)
    assert p.shape == (5,) and p.dtype.kind == "f"
    assert ((0 <= p) & (p <= 1)).all()


def test_the_loaded_model_is_in_eval_mode_and_the_models_dtype(saved):
    model = releasemodel._load(str(saved))
    assert not model.training
    want = torch.float16 if torch.cuda.is_available() else torch.float32
    assert next(model.parameters()).dtype == want


def test_p_off_does_not_depend_on_the_batch_size(saved, monkeypatch):
    model = releasemodel._load(str(saved))
    stacks = np.random.default_rng(1).random((5, 3, 107, 160), dtype="float32")
    whole = releasemodel.p_off(model, stacks)
    monkeypatch.setattr(releasemodel, "BATCH", 2)
    assert np.allclose(releasemodel.p_off(model, stacks), whole, atol=2e-3)


def test_p_off_of_nothing_is_empty(saved):
    p = releasemodel.p_off(releasemodel._load(str(saved)),
                           np.zeros((0, 3, 107, 160), dtype="float32"))
    assert p.shape == (0,)


def test_no_weights_means_no_model(monkeypatch):
    monkeypatch.setattr(weights, "release_path", lambda: None)
    assert releasemodel.default_model() is None


def test_default_model_loads_the_configured_weights(saved, monkeypatch):
    monkeypatch.setattr(weights, "release_path", lambda: saved)
    model = releasemodel.default_model()
    assert model is not None and not model.training


def test_without_cuda_it_runs_in_float32_on_the_cpu(saved, monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    model = releasemodel._load(str(saved))
    p = next(model.parameters())
    assert p.dtype == torch.float32 and p.device.type == "cpu"
    out = releasemodel.p_off(model, np.zeros((2, 3, 107, 160), dtype="float32"))
    assert out.shape == (2,)
