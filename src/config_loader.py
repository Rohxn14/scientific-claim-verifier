import yaml
import os
from paths import CONFIG_DOMAINS_DIR as CONFIG_DIR


def load_domain_config(domain_name: str) -> dict:
    path = os.path.join(CONFIG_DIR, f"{domain_name}.yaml")
    if not os.path.exists(path):
        raise FileNotFoundError(f"No config found for domain '{domain_name}'")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def list_domains() -> list:
    if not os.path.exists(CONFIG_DIR):
        return []
    return [f.replace(".yaml", "") for f in os.listdir(CONFIG_DIR) if f.endswith(".yaml")]
