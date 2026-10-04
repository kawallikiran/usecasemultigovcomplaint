"""YAML config loader. Relative paths resolve against the project root (parent of config/)."""
import os
import yaml

PATH_KEYS = {"dataset", "villages", "routing_matrix", "templates", "languages", "lexicon", "image_dir", "output_dir"}


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    for key in ("use_case", "dataset"):
        if key not in cfg:
            raise SystemExit(f"Config {path} is missing required key '{key}'")
    root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(path)), os.pardir))
    cfg["_root"] = root
    # Environment overrides (handy in containers)
    for env, key in (("AIGP_DATASET", "dataset"), ("AIGP_OUTPUT_DIR", "output_dir"),
                     ("AIGP_IMAGE_DIR", "image_dir")):
        if os.environ.get(env):
            cfg[key] = os.environ[env]

    def resolve(d):
        for k, v in d.items():
            if isinstance(v, dict):
                resolve(v)
            elif k in PATH_KEYS and isinstance(v, str) and not os.path.isabs(v):
                d[k] = os.path.join(root, v)
    resolve(cfg)
    return cfg
