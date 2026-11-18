from config.settings import BASE_DIR, Settings


def make(**kw):
    return Settings(_env_file=None, **kw)


def test_relative_sqlite_path_is_anchored_to_project_not_cwd():
    s = make(database_url="sqlite+aiosqlite:///./database/x.db")
    assert s.database_url == "sqlite+aiosqlite:///" + (BASE_DIR / "database" / "x.db").as_posix()


def test_memory_and_absolute_urls_untouched():
    assert make(database_url="sqlite+aiosqlite://").database_url == "sqlite+aiosqlite://"
    assert make(database_url="sqlite+aiosqlite:////tmp/a.db").database_url == "sqlite+aiosqlite:////tmp/a.db"


def test_api_key_safety():
    assert not make(admin_api_key="change-me").admin_api_key_is_safe
    assert not make(admin_api_key="short").admin_api_key_is_safe
    assert not make(admin_api_key="   ").admin_api_key_is_safe
    assert make(admin_api_key="a-long-random-key-123456").admin_api_key_is_safe


def test_empty_optional_ids_in_env_do_not_crash(monkeypatch):
    monkeypatch.setenv("SUPPORT_CHAT_ID", "")
    monkeypatch.setenv("CHANNEL_ID", "")
    assert make().support_chat_id is None
