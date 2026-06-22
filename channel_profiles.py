#!/usr/bin/env python3
"""
Channel Profiles — загрузка, валидация и управление профилями каналов.
Профили хранятся в channel_profiles.yaml (основной) и channel_profiles/*.yaml (дополнительные).
"""
from __future__ import annotations
import json
import yaml
import sys
from pathlib import Path
from typing import Optional

PROFILES_YAML = Path(__file__).parent / "channel_profiles.yaml"
PROFILES_DIR = Path(__file__).parent / "channel_profiles"

# ─── Platform defaults ────────────────────────────────────────────────────────

PLATFORM_DEFAULTS = {
    "listmonk": {
        "approval_required": True,
        "draft_dir": "{channel_id}/",
        "format": {"style": "brief", "max_posts": 5, "max_length": 8000},
        "compose": {
            "skill": "",
            "temperature": 0.7,
            "max_tokens": 0,
            "style": "brief",
            "system_prompt_addon": "",
        },
        "post": {
            "skill": "listmonk-campaign-api",
            "use_draft": True,
            "approval_required": True,
            "params": {},
        },
    },
    "telegram": {
        "approval_required": True,
        "draft_dir": "{channel_id}/",
        "format": {"style": "news", "max_posts": 3, "max_length": 2000},
        "compose": {
            "skill": "",
            "temperature": 0.7,
            "max_tokens": 2000,
            "style": "news",
            "system_prompt_addon": "",
        },
        "post": {
            "skill": "telegram-messaging",
            "use_draft": True,
            "approval_required": True,
            "params": {"parse_mode": "HTML", "disable_web_preview": True},
        },
    },
    "instagram": {
        "approval_required": True,
        "draft_dir": "{channel_id}/",
        "format": {"style": "single", "max_posts": 1, "max_length": 2200},
        "compose": {
            "skill": "",
            "temperature": 0.8,
            "max_tokens": 2200,
            "style": "single",
            "system_prompt_addon": "",
        },
        "post": {
            "skill": "telegram-images",
            "use_draft": True,
            "approval_required": True,
            "params": {
                "image_required": True,
                "image_aspect": 1.0,
                "caption_max_length": 2200,
                "hashtags_auto": True,
                "hashtags": [],
            },
        },
    },
    "linkedin": {
        "approval_required": True,
        "draft_dir": "{channel_id}/",
        "format": {"style": "article", "max_posts": 2, "max_length": 3000},
        "compose": {
            "skill": "",
            "temperature": 0.6,
            "max_tokens": 3000,
            "style": "article",
            "system_prompt_addon": "",
        },
        "post": {
            "skill": "xurl",
            "use_draft": True,
            "approval_required": True,
            "params": {"content_type": "article", "visibility": "PUBLIC", "image_required": False},
        },
    },
    "facebook": {
        "approval_required": True,
        "draft_dir": "{channel_id}/",
        "format": {"style": "news", "max_posts": 3, "max_length": 2000},
        "compose": {
            "skill": "",
            "temperature": 0.7,
            "max_tokens": 2000,
            "style": "news",
            "system_prompt_addon": "",
        },
        "post": {
            "skill": "native",
            "use_draft": True,
            "approval_required": True,
            "params": {"content_type": "post", "image_required": False},
        },
    },
    "twitter": {
        "approval_required": True,
        "draft_dir": "{channel_id}/",
        "format": {"style": "single", "max_posts": 1, "max_length": 280},
        "compose": {
            "skill": "",
            "temperature": 0.8,
            "max_tokens": 280,
            "style": "single",
            "system_prompt_addon": "",
        },
        "post": {
            "skill": "xurl",
            "use_draft": True,
            "approval_required": True,
            "params": {"thread_mode": False, "image_required": False},
        },
    },
}

# ─── Load / Save ─────────────────────────────────────────────────────────────

def load_all_profiles() -> list[dict]:
    """Загружает все профили из YAML."""
    if not PROFILES_YAML.exists():
        return []

    with open(PROFILES_YAML, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    profiles = raw if isinstance(raw, list) else raw.get("profiles", [])
    return [_apply_defaults(p) for p in profiles]


def load_profile(channel_id: str) -> Optional[dict]:
    """Найти профиль по channel_id."""
    all_p = load_all_profiles()
    for p in all_p:
        if p.get("channel_id") == channel_id:
            return p
    return None


def save_profile(channel_id: str, profile: dict) -> Path:
    """Сохранить или обновить профиль в YAML."""
    all_p = load_all_profiles()

    # Remove existing with same channel_id
    all_p = [p for p in all_p if p.get("channel_id") != channel_id]

    profile["channel_id"] = channel_id
    all_p.append(_apply_defaults(profile))

    with open(PROFILES_YAML, "w", encoding="utf-8") as f:
        yaml.dump(all_p, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    return PROFILES_YAML


def delete_profile(channel_id: str) -> bool:
    """Удалить профиль из YAML."""
    all_p = load_all_profiles()
    before = len(all_p)
    all_p = [p for p in all_p if p.get("channel_id") != channel_id]
    if len(all_p) < before:
        with open(PROFILES_YAML, "w", encoding="utf-8") as f:
            yaml.dump(all_p, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        return True
    return False


def _apply_defaults(profile: dict) -> dict:
    """Применить defaults платформы (deep merge для compose, post, format)."""
    platform = profile.get("platform", "telegram")
    defaults = PLATFORM_DEFAULTS.get(platform, {})

    result = {}
    for k, v in defaults.items():
        if k in ("format", "compose", "post"):
            result[k] = {**v, **profile.get(k, {})}
        elif k == "draft_dir":
            result[k] = profile.get(k, v).format(channel_id=profile.get("channel_id", ""))
        else:
            if k not in profile:
                result[k] = v

    return {**profile, **result}


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    import argparse
    ap = argparse.ArgumentParser(description="Channel Profiles")
    ap.add_argument("--list", action="store_true", help="Список всех профилей")
    ap.add_argument("--get", metavar="CHANNEL_ID", help="Показать профиль")
    ap.add_argument("--add", metavar="CHANNEL_ID", help="Добавить шаблон профиля в YAML")
    ap.add_argument("--remove", metavar="CHANNEL_ID", help="Удалить профиль")
    ap.add_argument("--platform", default="telegram", help="Платформа для --add")
    ap.add_argument("--validate", action="store_true", help="Проверить YAML на ошибки")
    args = ap.parse_args()

    if args.validate:
        try:
            profiles = load_all_profiles()
            print(f"✅ YAML валиден. Профилей: {len(profiles)}")
            for p in profiles:
                status = "🟢" if p.get("enabled") else "🔴"
                print(f"  {status} [{p['platform']}] {p['channel_id']} — {p.get('name','?')}")
        except Exception as e:
            print(f"❌ Ошибка: {e}")
        return

    if args.list:
        profiles = load_all_profiles()
        print(f"📋 Профилей: {len(profiles)}")
        for p in profiles:
            status = "🟢" if p.get("enabled") else "🔴"
            platform = p.get("platform", "?")
            name = p.get("name", "?")
            ch_id = p.get("channel_id", "?")
            posts = p.get("schedule", {}).get("posts_per_day", "?")
            print(f"  {status} [{platform}] {ch_id} — {name} (posts/day={posts})")
        return

    if args.get:
        p = load_profile(args.get)
        if p:
            print(yaml.dump([p], allow_unicode=True, default_flow_style=False, sort_keys=False))
        else:
            print(f"❌ Профиль {args.get} не найден")
        return

    if args.remove:
        ok = delete_profile(args.remove)
        print(f"{'✅ Удалён' if ok else '❌ Не найден'}: {args.remove}")
        return

    if args.add:
        # Minimal template
        template = {
            "channel_id": args.add,
            "name": args.add,
            "platform": args.platform,
            "enabled": True,
            "kb_query": {
                "categories": [],
                "languages": [],
                "priorities": [],
                "sources": [],
                "days_back": 7,
                "fts_query": "",
                "limit": 20,
            },
            "schedule": {"posts_per_day": 1, "times": ["09:00"]},
            "moderation": {"approval_required": True, "draft_dir": f"drafts/{args.add}/"},
            "format": {"max_posts": 3, "max_length": 2000, "style": "news", "template": ""},
        }
        if args.platform == "listmonk":
            template["listmonk"] = {"list_id": 0, "campaign_name": "", "subject": "", "from_name": "", "reply_to": "", "content_type": "html"}
        elif args.platform == "telegram":
            template["telegram"] = {"chat_id": "", "parse_mode": "HTML"}
        elif args.platform == "instagram":
            template["instagram"] = {"username": "", "image_required": True, "image_aspect": 1.0, "caption_max_length": 2200, "hashtags_auto": True, "hashtags": []}
        elif args.platform == "linkedin":
            template["linkedin"] = {"company_id": "", "image_required": False, "content_type": "article", "visibility": "PUBLIC"}

        save_profile(args.add, template)
        print(f"✅ Добавлен: {args.add} ({args.platform})")
        print(yaml.dump([template], allow_unicode=True, default_flow_style=False, sort_keys=False))
        return

    ap.print_help()


if __name__ == "__main__":
    main()
