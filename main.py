#!/usr/bin/env python3
"""Генератор проверяемых RouterOS address-list конфигураций для сети филиалов."""

from __future__ import annotations

import argparse
import difflib
import ipaddress
import json
import logging
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateError

LOGGER = logging.getLogger("branchcfg")
IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,30}[a-z0-9]$")
PRIVATE_RANGES = tuple(
    ipaddress.IPv4Network(cidr)
    for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
TEMPLATE_DIRECTORY = Path(__file__).resolve().parent / "templates"


class ConfigError(Exception):
    """Ошибка исходного inventory или генерации."""


class UniqueKeyLoader(yaml.SafeLoader):
    """Безопасный YAML-парсер с запретом повторяющихся ключей."""


def construct_unique_mapping(
    loader: UniqueKeyLoader, node: yaml.MappingNode
) -> dict[str, Any]:
    # safe_load по умолчанию молча оставляет последнее значение одинакового ключа.
    # Для сетевого inventory это риск: можно незаметно перезаписать профиль или LAN.
    loader.flatten_mapping(node)
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, str):
            raise ConfigError(f"YAML, строка {key_node.start_mark.line + 1}: ключ должен быть строкой")
        if key in result:
            raise ConfigError(f"YAML, строка {key_node.start_mark.line + 1}: повторный ключ {key!r}")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_unique_mapping
)


@dataclass(frozen=True, slots=True)
class Site:
    site_id: str
    lan: ipaddress.IPv4Network
    profiles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Inventory:
    sites: tuple[Site, ...]
    policies: dict[str, tuple[ipaddress.IPv4Network, ...]]


def require_mapping(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ConfigError(f"{location}: ожидается объект с текстовыми ключами")
    return value


def require_keys(value: dict[str, Any], required: set[str], location: str) -> None:
    missing = required - value.keys()
    extra = value.keys() - required
    if missing or extra:
        raise ConfigError(
            f"{location}: отсутствуют {sorted(missing)}; неизвестные поля {sorted(extra)}"
        )


def parse_identifier(value: Any, location: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER_PATTERN.fullmatch(value):
        raise ConfigError(f"{location}: 2–32 символа, a-z/0-9/дефис; начало — буква, конец — буква или цифра")
    return value


def parse_network(value: Any, location: str) -> ipaddress.IPv4Network:
    if not isinstance(value, str):
        raise ConfigError(f"{location}: сеть должна быть строкой CIDR")
    try:
        network = ipaddress.ip_network(value, strict=True)
    except ValueError as exc:
        raise ConfigError(f"{location}: неверная сеть {value!r}: {exc}") from exc
    if not isinstance(network, ipaddress.IPv4Network):
        raise ConfigError(f"{location}: поддерживается только IPv4")
    if not any(network.subnet_of(parent) for parent in PRIVATE_RANGES):
        raise ConfigError(f"{location}: разрешены только IPv4-сети RFC1918")
    return network


def require_list(value: Any, location: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{location}: ожидается непустой список")
    return value


def load_inventory(path: Path) -> Inventory:
    try:
        with path.open("r", encoding="utf-8") as source:
            document = yaml.load(source, Loader=UniqueKeyLoader)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"Не удалось прочитать {path}: {exc}") from exc

    root = require_mapping(document, "inventory")
    require_keys(root, {"version", "policies", "sites"}, "inventory")
    if type(root["version"]) is not int or root["version"] != 1:
        raise ConfigError("version: поддерживается только версия схемы 1")

    raw_policies = require_mapping(root["policies"], "policies")
    if not raw_policies:
        raise ConfigError("policies: нужен хотя бы один профиль")
    policies: dict[str, tuple[ipaddress.IPv4Network, ...]] = {}
    for profile_name, raw_networks in raw_policies.items():
        name = parse_identifier(profile_name, "policies: имя профиля")
        networks = tuple(
            parse_network(item, f"policies.{name}[{index}]")
            for index, item in enumerate(require_list(raw_networks, f"policies.{name}"))
        )
        if len(set(networks)) != len(networks):
            raise ConfigError(f"policies.{name}: дублирующиеся сети")
        policies[name] = networks

    sites: list[Site] = []
    ids: set[str] = set()
    for index, raw_site in enumerate(require_list(root["sites"], "sites")):
        site_data = require_mapping(raw_site, f"sites[{index}]")
        require_keys(site_data, {"id", "lan", "profiles"}, f"sites[{index}]")
        site_id = parse_identifier(site_data["id"], f"sites[{index}].id")
        if site_id in ids:
            raise ConfigError(f"sites[{index}].id: дублирующийся филиал {site_id}")
        ids.add(site_id)
        lan = parse_network(site_data["lan"], f"sites[{index}].lan")
        raw_profiles = require_list(site_data["profiles"], f"sites[{index}].profiles")
        profiles = tuple(
            parse_identifier(name, f"sites[{index}].profiles") for name in raw_profiles
        )
        if len(set(profiles)) != len(profiles):
            raise ConfigError(f"{site_id}: один профиль указан дважды")
        for profile in profiles:
            if profile not in policies:
                raise ConfigError(f"{site_id}: неизвестный профиль {profile}")
        sites.append(Site(site_id=site_id, lan=lan, profiles=tuple(sorted(profiles))))

    # Пересекающиеся LAN филиалов нельзя безопасно маршрутизировать в одной VPN-схеме.
    for first_index, first in enumerate(sites):
        for second in sites[first_index + 1 :]:
            if first.lan.overlaps(second.lan):
                raise ConfigError(f"Пересечение LAN: {first.site_id} и {second.site_id}")
        for profile in first.profiles:
            for allowed_network in policies[profile]:
                if first.lan.overlaps(allowed_network):
                    raise ConfigError(
                        f"{first.site_id}: LAN пересекается с разрешённой сетью "
                        f"из профиля {profile} ({allowed_network})"
                    )

    return Inventory(sites=tuple(sorted(sites, key=lambda site: site.site_id)), policies=policies)


def network_sort_key(network: ipaddress.IPv4Network) -> tuple[int, int]:
    return (int(network.network_address), network.prefixlen)


def render_files(inventory: Inventory) -> dict[Path, str]:
    environment = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIRECTORY)),
        undefined=StrictUndefined,
        autoescape=False,  # Здесь RouterOS CLI, а не HTML. Входные данные уже провалидированы.
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    try:
        hub_template = environment.get_template("hub.rsc.j2")
        site_template = environment.get_template("site.rsc.j2")
        files: dict[Path, str] = {
            Path("hub") / "branch-lans.rsc": hub_template.render(
                sites=[{"id": site.site_id, "lan": str(site.lan)} for site in inventory.sites]
            )
        }
        report: list[dict[str, object]] = []
        for site in inventory.sites:
            allowed = sorted(
                {network for profile in site.profiles for network in inventory.policies[profile]},
                key=network_sort_key,
            )
            files[Path("sites") / f"{site.site_id}.rsc"] = site_template.render(
                site_id=site.site_id,
                allowed_networks=[str(network) for network in allowed],
            )
            report.append(
                {
                    "id": site.site_id,
                    "lan": str(site.lan),
                    "profiles": list(site.profiles),
                    "allowed_networks": [str(network) for network in allowed],
                }
            )
        files[Path("inventory.json")] = json.dumps(
            {"version": 1, "sites": report}, ensure_ascii=False, indent=2
        ) + "\n"
        return files
    except TemplateError as exc:
        raise ConfigError(f"Ошибка Jinja2: {exc}") from exc


def read_existing(path: Path) -> str | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ConfigError(f"Некорректный путь для вывода: {path}")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"Не удалось прочитать {path}: {exc}") from exc


def find_stale_files(output_dir: Path, expected_files: dict[Path, str]) -> list[Path]:
    stale: list[Path] = []
    # Чужие файлы не трогаем. Ищем устаревшие .rsc только в каталогах генератора.
    for folder in ("sites", "hub"):
        directory = output_dir / folder
        if directory.is_dir():
            for candidate in directory.glob("*.rsc"):
                if candidate.relative_to(output_dir) not in expected_files:
                    stale.append(candidate)
    return sorted(stale)


def show_diff(path: Path, current: str | None, expected: str) -> None:
    delta = difflib.unified_diff(
        (current or "").splitlines(keepends=True),
        expected.splitlines(keepends=True),
        fromfile=f"current/{path}",
        tofile=f"expected/{path}",
    )
    sys.stdout.writelines(delta)


def write_atomically(path: Path, content: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            raise ConfigError(f"Символьная ссылка вместо файла: {path}")
        # Временный файл рядом с итоговым: os.replace будет атомарным на этой ФС.
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
    except OSError as exc:
        raise ConfigError(f"Не удалось записать {path}: {exc}") from exc


def process(inventory_path: Path, output_dir: Path, check: bool, diff: bool = False) -> int:
    inventory = load_inventory(inventory_path)
    generated = render_files(inventory)

    # Каталоги результатов не должны вести по ссылке куда-то за пределы output.
    # Отдельные файлы проверяются перед чтением и записью.
    for directory in (output_dir, output_dir / "hub", output_dir / "sites"):
        if directory.is_symlink():
            raise ConfigError(f"Каталог вывода — символьная ссылка: {directory}")
        if directory.exists() and not directory.is_dir():
            raise ConfigError(f"Ожидался каталог вывода: {directory}")

    changed = 0
    for relative_path, expected in generated.items():
        destination = output_dir / relative_path
        current = read_existing(destination)
        if current == expected:
            LOGGER.info("Без изменений: %s", relative_path)
            continue
        changed += 1
        if check:
            LOGGER.warning("DRIFT: %s", relative_path)
        else:
            LOGGER.info("Обновление: %s", relative_path)
        if diff:
            show_diff(relative_path, current, expected)
        if not check:
            write_atomically(destination, expected)

    stale = find_stale_files(output_dir, generated)
    for path in stale:
        LOGGER.warning("Устаревший файл, удалить вручную: %s", path)

    if check and (changed or stale):
        LOGGER.error("Проверка не пройдена: изменений %d, старых файлов %d", changed, len(stale))
        return 2
    LOGGER.info("Готово: файлов %d, изменений %d", len(generated), changed)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Генератор RouterOS-конфигураций для филиалов")
    parser.add_argument("command", nargs="?", choices=("generate", "check"), default="generate")
    parser.add_argument(
        "--config", type=Path,
        default=Path(os.getenv("BRANCHCFG_CONFIG", "config.example.yaml")),
        help="путь к YAML inventory",
    )
    parser.add_argument(
        "--output", type=Path,
        default=Path(os.getenv("BRANCHCFG_OUTPUT", "output")),
        help="каталог результатов",
    )
    parser.add_argument("--diff", action="store_true", help="показать unified diff")
    parser.add_argument(
        "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default=os.getenv("BRANCHCFG_LOG_LEVEL", "INFO").upper(),
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(levelname)s %(message)s")
    try:
        return process(args.config, args.output, check=args.command == "check", diff=args.diff)
    except ConfigError as exc:
        LOGGER.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
