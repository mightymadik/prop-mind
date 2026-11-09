from app.config import load_config

CFG = load_config("config/demo.yaml")


def test_demo_config_consistent():
    assert set(CFG.districts) == set(CFG.cities)
    assert CFG.embeddings.dim == 1024  # = vector(1024) в db/schema.sql
