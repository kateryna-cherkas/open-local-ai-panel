"""Project paths. All data paths are derived from PROJECT_ROOT so modules can
be run from any working directory."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
EXTRACT_DIR = PROJECT_ROOT / "extract"
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
CACHE_DIR = DATA_DIR / "cache"
REPORTS_DIR = PROJECT_ROOT / "reports"
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures"


def raw_wave_dir(wave: str) -> Path:
    return RAW_DIR / wave


def processed_wave_dir(wave: str) -> Path:
    return PROCESSED_DIR / wave
