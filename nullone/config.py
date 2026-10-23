from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from nullone.ssl_certs import ensure_ssl_certificates

load_dotenv()
ensure_ssl_certificates()

ROOT_DIR = Path(__file__).resolve().parent.parent
SPECS_DIR = ROOT_DIR / "specs"
LOG_DIR = Path(os.getenv("NULLONE_LOG_DIR", ROOT_DIR / "logs" / "releases"))
CALENDAR_DIR = Path(__file__).resolve().parent / "calendar"

WNGSR_JSON_URL = "https://ir.eia.gov/ngs/wngsr.json"
WNGSR_CSV_URL = "https://ir.eia.gov/ngs/wngsr.csv"
LOWER48_SERIES_ID = "png.nw2_epg0_swo_r48_bcf.w"


@dataclass(frozen=True)
class KalshiConfig:
    api_key: str
    private_key_path: str | None
    private_key_pem: str | None
    base_url: str
    ws_url: str


@dataclass(frozen=True)
class PolymarketConfig:
    private_key: str
    host: str
    chain_id: int
    signature_type: int
    funder: str | None
    api_key: str | None
    api_secret: str | None
    api_passphrase: str | None
    gamma_url: str


@dataclass(frozen=True)
class Settings:
    kalshi: KalshiConfig | None
    polymarket: PolymarketConfig | None
    kalshi_gas_storage_series: str
    release_capital_usd: float
    execution_strategy: str
    parse_error_rate: float
    min_leg_usd: float
    max_legs: int
    min_fill_prob: float
    winner_yes_min_pct: float
    max_leg_usd: float | None
    book_depth_usd: float
    max_slippage_yes: float
    absolute_floor_yes: float
    force_winner_first: bool
    enable_loser_no: bool
    kalshi_fee_rate: float
    mode: str
    log_dir: Path


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return float(value)


def _optional_float_env(name: str) -> float | None:
    value = os.getenv(name)
    if value is None or value == "":
        return None
    return float(value)


def _execution_strategy_env(default: str = "auto") -> str:
    value = os.getenv("NULLONE_EXECUTION_STRATEGY", default).strip().lower()
    if value not in {"single_leg", "auto"}:
        raise ValueError(
            "NULLONE_EXECUTION_STRATEGY must be one of: single_leg, auto"
        )
    return value


def load_settings() -> Settings:
    mode = os.getenv("NULLONE_MODE", "paper").lower()
    log_dir = Path(os.getenv("NULLONE_LOG_DIR", ROOT_DIR / "logs" / "releases"))

    kalshi_key = os.getenv("KALSHI_API_KEY", "")
    kalshi: KalshiConfig | None = None
    if kalshi_key:
        kalshi = KalshiConfig(
            api_key=kalshi_key,
            private_key_path=os.getenv("KALSHI_PRIVATE_KEY_PATH"),
            private_key_pem=os.getenv("KALSHI_PRIVATE_KEY_PEM"),
            base_url=os.getenv(
                "KALSHI_BASE_URL",
                "https://external-api.kalshi.com/trade-api/v2",
            ),
            ws_url=os.getenv(
                "KALSHI_WS_URL",
                "wss://external-api-ws.kalshi.com/trade-api/ws/v2",
            ),
        )

    poly_pk = os.getenv("POLYMARKET_PK", "")
    polymarket: PolymarketConfig | None = None
    if poly_pk:
        polymarket = PolymarketConfig(
            private_key=poly_pk,
            host=os.getenv("POLYMARKET_HOST", "https://clob.polymarket.com"),
            chain_id=int(os.getenv("POLYMARKET_CHAIN_ID", "137")),
            signature_type=int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "0")),
            funder=os.getenv("POLYMARKET_FUNDER") or None,
            api_key=os.getenv("CLOB_API_KEY") or None,
            api_secret=os.getenv("CLOB_SECRET") or None,
            api_passphrase=os.getenv("CLOB_PASS_PHRASE") or None,
            gamma_url=os.getenv("GAMMA_API_URL", "https://gamma-api.polymarket.com"),
        )

    kalshi_gas_storage_series = os.getenv("KALSHI_GAS_STORAGE_SERIES", "KXGAS")
    legacy_order_size_usd = _float_env("NULLONE_ORDER_SIZE_USD", 100.0)
    release_capital_usd = _float_env("NULLONE_RELEASE_CAPITAL_USD", legacy_order_size_usd)

    return Settings(
        kalshi=kalshi,
        polymarket=polymarket,
        kalshi_gas_storage_series=kalshi_gas_storage_series,
        release_capital_usd=release_capital_usd,
        execution_strategy=_execution_strategy_env(),
        parse_error_rate=_float_env("NULLONE_PARSE_ERROR_RATE", 0.02),
        min_leg_usd=_float_env("NULLONE_MIN_LEG_USD", 5.0),
        max_legs=int(_float_env("NULLONE_MAX_LEGS", 6)),
        min_fill_prob=_float_env("NULLONE_MIN_FILL_PROB", 0.3),
        winner_yes_min_pct=_float_env("NULLONE_WINNER_YES_MIN_PCT", 0.5),
        max_leg_usd=_optional_float_env("NULLONE_MAX_LEG_USD"),
        book_depth_usd=_float_env("NULLONE_BOOK_DEPTH_USD", release_capital_usd),
        max_slippage_yes=_float_env("NULLONE_MAX_SLIPPAGE_YES", 0.02),
        absolute_floor_yes=_float_env("NULLONE_ABSOLUTE_FLOOR_YES", 0.01),
        force_winner_first=_bool_env("NULLONE_FORCE_WINNER_FIRST", True),
        enable_loser_no=_bool_env("NULLONE_ENABLE_LOSER_NO", True),
        kalshi_fee_rate=_float_env("NULLONE_KALSHI_FEE_RATE", 0.01),
        mode=mode,
        log_dir=log_dir,
    )
