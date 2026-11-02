"""Командная строка: тот же конвейер, что и в веб-интерфейсе, без сервера.

Пример:
    python -m app.cli examples/mp3-u.json --provider mock --out output
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.config import PROVIDER_TITLES, load_settings
from app.llm.base import LLMError
from app.llm.factory import SUPPORTED_PROVIDERS, create_client
from app.models import ProductInput
from app.pipeline import analyze_product, generate_card
from app.render import to_json


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="Генерация черновика карточки товара по JSON-описанию прибора.",
    )
    parser.add_argument("inputs", nargs="+", help="JSON-файлы с данными товаров")
    parser.add_argument("--out", default="output", help="каталог для результатов (по умолчанию output)")
    parser.add_argument(
        "--provider",
        default="",
        choices=["", *SUPPORTED_PROVIDERS],
        help="провайдер LLM; по умолчанию берётся LLM_PROVIDER из .env",
    )
    parser.add_argument("--model", default="", help="модель; по умолчанию из .env")
    parser.add_argument("--base-url", default="", help="base_url для OpenAI-совместимого endpoint")
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument(
        "--mock",
        action="store_true",
        help="офлайн-режим без обращения к модели (то же, что --provider mock)",
    )
    parser.add_argument(
        "--analyze-only",
        action="store_true",
        help="только проверить полноту входных данных, без генерации",
    )
    parser.add_argument(
        "--formats",
        default="md,json",
        help="что сохранять: md, json или md,json",
    )
    return parser


def _read_products(paths: list[str]) -> list[tuple[Path, ProductInput]]:
    products: list[tuple[Path, ProductInput]] = []
    for raw_path in paths:
        path = Path(raw_path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise SystemExit(f"Не удалось прочитать {path}: {exc}") from None
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}: некорректный JSON — {exc.msg} (строка {exc.lineno})") from None
        try:
            products.append((path, ProductInput.model_validate(payload)))
        except Exception as exc:  # pydantic.ValidationError
            raise SystemExit(f"{path}: данные товара не прошли проверку — {exc}") from None
    return products


async def _run(args: argparse.Namespace) -> int:
    products = _read_products(args.inputs)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    formats = {part.strip().lower() for part in args.formats.split(",") if part.strip()}

    settings = load_settings()
    provider = "mock" if args.mock else (args.provider or settings.provider)
    client = create_client(
        provider=provider,
        api_key=settings.api_key,
        model=args.model or settings.model,
        base_url=args.base_url or settings.base_url,
        temperature=args.temperature if args.temperature is not None else settings.temperature,
        timeout=settings.timeout,
        user_agent=settings.user_agent,
        extra_headers=settings.extra_headers,
    )
    provider_title = PROVIDER_TITLES.get(provider, provider)

    for path, product in products:
        report = analyze_product(product)
        print(f"\n=== {product.name} ({path.name}) ===")
        print(f"Семейство: {report.family_title}")
        if report.missing:
            print("Не хватает: " + ", ".join(report.missing))
        for warning in report.warnings:
            print(f"  ! {warning}")
        if args.analyze_only:
            continue

        try:
            result = await generate_card(product, client, provider_title=provider_title)
        except LLMError as exc:
            print(f"Ошибка генерации: {exc}", file=sys.stderr)
            return 1

        if "md" in formats:
            (out_dir / f"{path.stem}.md").write_text(result.markdown, encoding="utf-8")
        if "json" in formats:
            (out_dir / f"{path.stem}.json").write_text(to_json(result.card), encoding="utf-8")

        print(
            f"Готово за {result.elapsed_ms / 1000:.1f} с, обращений к модели: {result.attempts}. "
            f"Заголовок {len(result.card.seo_title)}/{70} символов, "
            f"описание {len(result.card.seo_description)} символов."
        )
        if result.card.warnings:
            print("Замечания проверок:")
            for warning in result.card.warnings:
                print(f"  - {warning}")

    print(f"\nРезультаты: {out_dir.resolve()}")
    return 0


def main() -> int:
    args = _build_parser().parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
