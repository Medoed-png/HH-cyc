"""Реестр адаптеров сайтов поиска работы.

Добавить новый сайт = создать sites/<name>/ с классом-наследником SiteAdapter
и зарегистрировать его здесь в SITES. Ядро берёт адаптер через get_adapter().
"""
from __future__ import annotations

from .base import SiteAdapter
from .hh import HHAdapter
from .superjob import SuperJobAdapter
from .extra import (
    AvitoAdapter, HabrCareerAdapter, RabotaRuAdapter, TrudvsemAdapter,
)

# id сайта -> класс адаптера. Порядок задаёт порядок в выпадающем списке UI.
SITES: dict[str, type[SiteAdapter]] = {
    HHAdapter.site_id: HHAdapter,
    SuperJobAdapter.site_id: SuperJobAdapter,
    AvitoAdapter.site_id: AvitoAdapter,
    HabrCareerAdapter.site_id: HabrCareerAdapter,
    RabotaRuAdapter.site_id: RabotaRuAdapter,
    TrudvsemAdapter.site_id: TrudvsemAdapter,
}

DEFAULT_SITE = HHAdapter.site_id
# Спец-значение «искать на всех сайтах сразу» (не реальный адаптер).
ALL_SITES = "all"

# Кэш экземпляров адаптеров (они без состояния, переиспользуем).
_instances: dict[str, SiteAdapter] = {}


def get_adapter(site_id: str = DEFAULT_SITE) -> SiteAdapter:
    """Вернуть (создав при необходимости) экземпляр адаптера по id сайта."""
    site_id = site_id or DEFAULT_SITE
    if site_id not in SITES:
        raise KeyError(f"Неизвестный сайт: {site_id!r}. Доступны: {list(SITES)}")
    if site_id not in _instances:
        _instances[site_id] = SITES[site_id]()
    return _instances[site_id]


def list_sites(include_all: bool = True) -> list[dict]:
    """Список сайтов для UI: [{"id", "display_name"}].

    include_all — добавить в начало спец-пункт «Все сайты» (поиск сразу по всем).
    """
    items = [{"id": sid, "display_name": cls.display_name}
             for sid, cls in SITES.items()]
    if include_all:
        items.insert(0, {"id": ALL_SITES, "display_name": "🌐 Все сайты"})
    return items


def real_site_ids() -> list[str]:
    """Идентификаторы реальных сайтов (без спец-пункта «Все сайты»)."""
    return list(SITES.keys())
