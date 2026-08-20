#!/usr/bin/env python3
"""prune_build_cache 的判断逻辑测试。

真实缓存仓里的层往往全是"今天刚命中过",所以拿它跑不出"该删"的分支 —— 报告永远是 0,
看不出选择逻辑到底有没有在工作。这里用假的 artifactory 把边界和护栏都逼出来。

不联网。真实的 DELETE 已经在真仓上验证过(返回 204),这里只管"选谁"和"敢不敢删"。
"""

from __future__ import annotations

import datetime as dt
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import prune_build_cache as mod  # noqa: E402

HEX = "a" * 64
HEX2 = "b" * 64


def ms(days_ago: float) -> int:
    """毫秒时间戳,距今 days_ago 天。"""
    moment = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)
    return int(moment.timestamp() * 1000)


def iso(days_ago: float) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)).isoformat()


class FakeArtifactory:
    """只记下被要求删什么,不联网。"""

    def __init__(self, results):
        self._results = results
        self.deleted: list[str] = []

    def aql(self, query):
        assert "stat.downloaded" in query, "查询必须取最后下载时间,否则会按创建时间误删"
        return {"results": self._results}

    def delete(self, repo_key, path):
        self.deleted.append(path)
        return 204


def item(tag, *, created_days, downloaded_days=None):
    entry = {
        "repo": "r",
        "path": f"proj-rvc/rp-ai/build-cache/{tag}",
        "name": "manifest.json",
        "created": iso(created_days),
    }
    if downloaded_days is not None:
        entry["stats"] = [{"downloaded": ms(downloaded_days)}]
    return entry


class TestSelection(unittest.TestCase):
    def rows(self, results):
        return mod.collect(FakeArtifactory(results), "r", "proj-rvc/rp-ai/build-cache")

    def test_按最后命中而非创建时间(self):
        """老层但天天命中 —— 必须留下。这是整个工具的关键取舍。"""
        rows = self.rows([item(HEX, created_days=200, downloaded_days=1)])
        self.assertEqual(rows[0]["age_days"], 1,
                         "应按最后命中(1 天)判断,而不是创建时间(200 天)")

    def test_从未命中回落到创建时间(self):
        rows = self.rows([item(HEX, created_days=50)])
        self.assertEqual(rows[0]["age_days"], 50)
        self.assertIsNone(rows[0]["downloaded"])

    def test_非缓存形状的_tag_不算缓存(self):
        rows = self.rows([item("sizeprobe-nocss", created_days=99)])
        self.assertFalse(rows[0]["is_cache"])

    def test_六十四位十六进制才算缓存(self):
        rows = self.rows([item(HEX, created_days=1), item("a" * 63, created_days=1),
                          item("A" * 64, created_days=1)])
        self.assertEqual([r["is_cache"] for r in rows], [True, False, False],
                         "长度不足或大写都不是 kaniko 的缓存 tag")


class TestApply(unittest.TestCase):
    def run_main(self, results, argv):
        fake = FakeArtifactory(results)
        real_cls = mod.Artifactory
        mod.Artifactory = lambda *a, **k: fake
        old_argv = sys.argv
        sys.argv = ["prune_build_cache.py"] + argv
        import os
        os.environ.setdefault("docker_user", "u")
        os.environ.setdefault("docker_password", "p")
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                code = mod.main()
        finally:
            mod.Artifactory = real_cls
            sys.argv = old_argv
        return code, buf.getvalue(), fake

    def test_默认只报告不删(self):
        code, out, fake = self.run_main([item(HEX, created_days=99)], [])
        self.assertEqual(fake.deleted, [], "没给 --apply 就不许删任何东西")
        self.assertIn("将删除", out)
        self.assertEqual(code, 0)

    def test_加_apply_才删_且只删过期的(self):
        results = [
            item(HEX, created_days=200, downloaded_days=99),   # 该删
            item(HEX2, created_days=200, downloaded_days=1),   # 天天命中,该留
        ]
        code, out, fake = self.run_main(results, ["--apply", "--keep-days", "30"])
        self.assertEqual(len(fake.deleted), 1, f"只应删一个,实际 {fake.deleted}")
        self.assertTrue(fake.deleted[0].endswith(HEX))
        self.assertEqual(code, 0)

    def test_不碰非缓存形状的_tag(self):
        results = [item("release-probe", created_days=500)]
        code, out, fake = self.run_main(results, ["--apply", "--keep-days", "30"])
        self.assertEqual(fake.deleted, [], "非缓存 tag 再老也不许删")
        self.assertIn("非缓存", out)

    def test_边界是严格大于(self):
        results = [item(HEX, created_days=30, downloaded_days=30)]
        code, out, fake = self.run_main(results, ["--apply", "--keep-days", "30"])
        self.assertEqual(fake.deleted, [], "正好等于阈值应保留")


if __name__ == "__main__":
    unittest.main(verbosity=2)
