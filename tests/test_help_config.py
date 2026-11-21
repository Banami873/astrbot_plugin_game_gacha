"""issue 12：帮助文案、配置 schema、自检脚本。

这三件的共同要求是「不许与代码脱节」，所以断言都对着**代码本身**写：帮助对着
`main.py` 的指令注册、schema 对着 SPEC 的默认值、自检对着前面两者。
"""

from __future__ import annotations

import json
import re
import sqlite3

import pytest

# SPEC「配置项」一节：键 → (类型, 默认值)。默认值变了要同时改 SPEC，不许只改一边。
EXPECTED_CONFIG = {
    "enable_image": ("bool", True),
    "image_quality": ("string", "中"),
    "signin_reward": ("int", 10),
    "newbie_bonus": ("int", 30),
    "data_refresh_hours": ("int", 6),
    "prts_refresh_hours": ("int", 24),
    "allow_network": ("bool", True),
}


@pytest.fixture(scope="session")
def schema(plugin_dir):
    return json.loads((plugin_dir / "_conf_schema.json").read_text("utf-8"))


class TestHelp:
    def test_every_command_appears_in_help(self, help_mod):
        text = help_mod.render_help()
        for doc in help_mod.COMMANDS:
            assert doc.usage in text, f"{doc.name} 没进帮助"

    def test_help_mentions_no_unknown_command(self, help_mod):
        """帮助里写到的每个斜杠词都得是清单里的指令，不许出现「写了没做」的。"""
        text = help_mod.render_help()
        mentioned = set(re.findall(r"/([\u4e00-\u9fff]+)", text))
        assert mentioned, "帮助里一条指令都没提到"
        assert mentioned <= set(help_mod.command_names())

    def test_help_has_no_pool_id(self, help_mod):
        """SPEC：帮助文案里不出现任何池编号说法（用户只认池名）。"""
        text = help_mod.render_help()
        assert not re.search(r"[A-Z]{3,}_\d+", text)
        assert "池编号" not in text

    def test_rewards_come_from_config(self, help_mod):
        default = help_mod.render_help()
        assert "+10" in default and "+30" in default
        custom = help_mod.render_help(signin_reward=25, newbie_bonus=77)
        assert "+25" in custom and "+77" in custom

    def test_docs_are_well_formed(self, help_mod):
        names = help_mod.command_names()
        assert len(names) == len(set(names)), "指令名重复"
        for doc in help_mod.COMMANDS:
            assert doc.usage.startswith("/" + doc.name), doc.usage
            assert doc.summary.strip(), f"{doc.name} 没有说明"

    def test_admin_command_is_marked(self, help_mod):
        admin = [doc.name for doc in help_mod.COMMANDS if doc.admin_only]
        assert admin == ["抽数修改"]


class TestConfigSchema:
    def test_all_seven_keys_declared(self, schema):
        assert set(schema) == set(EXPECTED_CONFIG)

    def test_types_and_defaults_match_spec(self, schema):
        for key, (kind, default) in EXPECTED_CONFIG.items():
            entry = schema[key]
            assert entry["type"] == kind, key
            assert entry["default"] == default, key

    def test_quality_options(self, schema):
        assert set(schema["image_quality"]["options"]) == {"高", "中", "低"}

    def test_every_entry_is_explained(self, schema):
        """面板上点不到说明的配置项等于没写。"""
        for key, entry in schema.items():
            assert entry.get("description"), key
            assert entry.get("hint"), key


class TestAliases:
    """方舟 9 条各挂 `/方舟*` 别名，星铁 6 条不挂。

    别名是 AstrBot 的注册参数，写错或漏写不会报错——只是那条指令在群里「不存在」。
    所以对着 `main.py` 的语法树核对，和自检取指令名用的是同一套办法。
    """

    @staticmethod
    def _aliases(plugin_dir):
        import ast

        tree = ast.parse((plugin_dir / "main.py").read_text("utf-8"))
        found: dict[str, set[str]] = {}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not decorator.args:
                    continue
                func = decorator.func
                if (getattr(func, "attr", None) or getattr(func, "id", "")) != "command":
                    continue
                name = decorator.args[0].value
                aliases = set()
                for keyword in decorator.keywords:
                    if keyword.arg == "alias" and isinstance(keyword.value, ast.Set):
                        aliases = {item.value for item in keyword.value.elts}
                found[name] = aliases
        return found

    def test_arknights_commands_have_fangzhou_alias(self, plugin_dir, help_mod):
        aliases = self._aliases(plugin_dir)
        for name in help_mod.command_names():
            assert aliases[name] == {"方舟" + name}, name

    def test_hsr_commands_have_no_alias(self, plugin_dir, pkg):
        aliases = self._aliases(plugin_dir)
        for name in pkg("games.hsr.views").command_names():
            assert aliases[name] == set(), name


class TestSelfcheck:
    def test_registered_commands_match_help(self, selfcheck_mod, help_mod, pkg):
        """19 条 = 方舟 9 + 星铁 10。两个游戏各有一份清单，各管自己那半边。"""
        hsr_views = pkg("games.hsr.views")
        declared = sorted([*help_mod.command_names(), *hsr_views.command_names()])
        assert len(declared) == 19
        assert selfcheck_mod.registered_commands() == declared
        assert "抽卡帮助" in declared and "星铁十连" in declared

    def test_table_names_match_database(self, store_mod, tmp_path):
        db = tmp_path / "tables.db"
        store_mod.Store(db).ensure_schema()
        conn = sqlite3.connect(db)
        try:
            actual = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
        finally:
            conn.close()
        assert set(store_mod.table_names()) == actual
        assert "users" in actual

    def test_selfcheck_passes(self, selfcheck_mod, capsys):
        assert selfcheck_mod.main() == 0
        first = capsys.readouterr().out.splitlines()[0]
        assert re.fullmatch(r"commands=\d+ tables=\d+ snapshot=ok", first), first

    def test_reports_missing_table(self, selfcheck_mod, pkg, monkeypatch, capsys):
        store_pkg = pkg("games.arknights.store")
        original = store_pkg.table_names
        monkeypatch.setattr(
            store_pkg, "table_names", lambda: (*original(), "ghost_table")
        )
        assert selfcheck_mod.main() == 1
        out = capsys.readouterr().out
        assert "ghost_table" in out and "FAIL" in out

    def test_reports_missing_snapshot(self, selfcheck_mod, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(selfcheck_mod, "SNAPSHOT_DIR", tmp_path)
        assert selfcheck_mod.main() == 1
        out = capsys.readouterr().out
        assert "snapshot=FAIL" in out
        assert "卡池快照无法解析" in out
