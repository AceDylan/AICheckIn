# -*- coding: utf-8 -*-
"""「AI 聊天」「笔记」两个框跟着本站的深浅色。

两边默认「跟随系统」，本站的主题却是自己记着的（默认深色）：系统是浅色时，深色的 Hub 里嵌着两块白页面，
而跨源的框看不到本站选了什么（框元素的 color-scheme 不会传进跨源页面的 prefers-color-scheme）。
所以开框时把此刻的深浅色写进地址的 #（hub_theme=；笔记框经 /vault/open 的 ?theme= 转交，挂在表单地址的 # 上，
对面 303 回笔记页时浏览器把 # 带过去），之后本站换主题再给两个框各发一条消息（目标源写死成配置的那个）。

前端部分把模板里的主脚本放进 node + DOM 替身里真跑（同 test_chat_reauth）；后端部分看 /vault/open 的页面。
"""
import copy
import json
import os
import subprocess
import tempfile
import unittest

from tests.test_script_boot import CONFIGS_RESPONSE, NODE, RESPONSES, STUB, _inline_script
from tests.test_vault_embed import VAULT, VaultCase, parse

HALO = "https://halo.example"
NOTES = "https://notes.example"

SCENARIO = r"""
const L = {};
globalThis.addEventListener = (type, fn) => { (L[type] = L[type] || []).push(fn); };
// <html data-theme>：替身默认不记属性，这里记下来，好让 frameTheme() 读到 applyTheme 写进去的值。
const attrs = {};
document.documentElement.setAttribute = (k, v) => { attrs[k] = String(v); };
document.documentElement.getAttribute = (k) => (k in attrs ? attrs[k] : null);
VIEWS.push('chat', 'vault');
for (const n of ['chat', 'vault']) {
  const v = el('view-' + n);
  v.id = 'view-' + n;
  v.classList.toggle = function (cls, on) { if (cls === 'active' && on) activeView = n; origToggle.call(this, cls, on); };
}
const posted = [];
const frameWin = (kind, n) => ({ kind, n, postMessage: (msg, origin) => posted.push({ kind, n, msg, origin }) });
const chats = [], notes = [], hops = [];
const drop = (list, f) => () => { const i = list.indexOf(f); if (i >= 0) list.splice(i, 1); };
el('chatStage').appendChild = (f) => { f.contentWindow = frameWin('chat', chats.length); f.remove = drop(chats, f); chats.push(f); };
el('chatStage').querySelector = () => chats.length ? chats[chats.length - 1] : null;
el('vaultStage').appendChild = (f) => { f.contentWindow = frameWin('vault', notes.length); f.remove = drop(notes, f); notes.push(f); };
el('vaultStage').querySelector = () => notes.length ? notes[notes.length - 1] : null;
document.body.appendChild = (h) => { hops.push(h); };
__SCRIPT__
const tick = () => new Promise((r) => setTimeout(r, 30));
setTimeout(async () => {
  const out = { steps: [] };
  const snap = (label) => out.steps.push({
    label, theme: attrs['data-theme'] || '', chat: chats.length ? chats[chats.length - 1].src || '' : '',
    hop: hops.length ? hops[hops.length - 1].src : '', posted: posted.splice(0),
  });
  switchView('chat'); await tick(); snap('chat opened');
  switchView('vault'); await tick(); snap('vault opened');
  setTheme('light'); await tick(); snap('switched to light');
  __RESPONSES['/api/chat/ticket'] = { ok: true, sso: false, url: __HALO__ + '/' };
  openChat(true); openVault(true); await tick(); snap('reopened while light');
  closeChat(); closeVault(); setTheme('dark'); await tick(); snap('no frames to tell');
  setTheme('light'); openChat(true); openVault(true); await tick(); snap('light, frames open');
  writePref('bh_wallpaper', 'galaxy'); applyLook(); await tick(); snap('wallpaper turned on');
  openChat(true); await tick(); snap('reopened under the wallpaper');
  writePref('bh_wallpaper', 'off'); applyLook(); await tick(); snap('wallpaper turned off');
  writePref('bh_wallpaper', 'galaxy'); applyLook(); setTheme('dark'); await tick(); snap('dark under the wallpaper');
  // 设备是浅色：「跟随系统」就是浅色，开着壁纸也一样（壁纸只管本站自己的工作区）。
  window.matchMedia = (q) => ({ matches: /light/.test(q), addEventListener() {}, addListener() {} });
  setTheme('system'); await tick(); snap('system (light device) under the wallpaper');
  openChat(true); await tick(); snap('reopened, system under the wallpaper');
  out.errors = __CALLS.errors;
  console.log(JSON.stringify(out));
}, 60);
"""


@unittest.skipIf(NODE is None, "未安装 node，跳过")
class FrameThemeScriptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        responses = copy.deepcopy(RESPONSES)
        responses["/api/configs"] = dict(copy.deepcopy(CONFIGS_RESPONSE), chat={"url": HALO}, vault_embed={"url": NOTES})
        responses["/api/chat/ticket"] = {"ok": True, "sso": True, "url": HALO + "/auth#hub_ticket=T"}
        with open(STUB, encoding="utf-8") as fh:
            stub = fh.read()
        harness = "\n".join([
            stub,
            "globalThis.__RESPONSES = %s;" % json.dumps(responses),
            SCENARIO.replace("__SCRIPT__", _inline_script()).replace("__HALO__", json.dumps(HALO)),
        ])
        with tempfile.NamedTemporaryFile("w", suffix=".cjs", delete=False, encoding="utf-8") as fh:
            fh.write(harness)
            path = fh.name
        try:
            proc = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60)
        finally:
            os.unlink(path)
        if proc.returncode != 0:
            raise AssertionError(proc.stderr[-2000:])
        cls.out = json.loads(proc.stdout.strip().splitlines()[-1])
        cls.steps = {step["label"]: step for step in cls.out["steps"]}

    def test_script_runs_without_errors(self):
        self.assertEqual(self.out["errors"], [])

    def test_a_new_chat_frame_carries_the_hubs_theme_after_the_ticket(self):
        step = self.steps["chat opened"]
        self.assertEqual(step["theme"], "dark")   # 没有 Cookie：本站默认深色
        self.assertEqual(step["chat"], HALO + "/auth#hub_ticket=T&hub_theme=dark")

    def test_the_note_frames_sign_in_page_is_told_the_theme(self):
        hop = self.steps["vault opened"]["hop"]
        self.assertTrue(hop.startswith("/vault/open?to=%2F&theme=dark&target=hub-vault-"), hop)

    def test_switching_the_theme_tells_both_frames_on_their_own_origins_only(self):
        step = self.steps["switched to light"]
        self.assertEqual(step["theme"], "light")
        msg = {"source": "hub", "type": "theme", "theme": "light"}
        self.assertEqual(sorted((p["kind"], p["origin"]) for p in step["posted"]), [("chat", HALO), ("vault", NOTES)])
        for p in step["posted"]:
            self.assertEqual(p["msg"], msg)

    def test_frames_opened_later_start_in_the_current_theme(self):
        step = self.steps["reopened while light"]
        self.assertEqual(step["chat"], HALO + "/#hub_theme=light")   # 签不了票的普通地址也带上
        self.assertIn("&theme=light&target=hub-vault-", step["hop"])
        self.assertEqual(step["posted"], [])

    def test_without_frames_nothing_is_posted(self):
        step = self.steps["no frames to tell"]
        self.assertEqual((step["theme"], step["posted"]), ("dark", []))


    def test_the_wallpaper_does_not_change_the_frames_theme(self):
        # 开着壁纸时工作区固定是深色玻璃，但框里的页面跟主题 / 设备走：开、关壁纸都不给框发消息，
        # 壁纸下新开的框也还是此刻的主题。
        self.assertEqual(self.steps["light, frames open"]["chat"], HALO + "/#hub_theme=light")
        self.assertEqual(self.steps["wallpaper turned on"]["posted"], [])
        self.assertEqual(self.steps["reopened under the wallpaper"]["chat"], HALO + "/#hub_theme=light")
        self.assertEqual(self.steps["wallpaper turned off"]["posted"], [])

    def test_under_the_wallpaper_the_theme_still_reaches_the_frames(self):
        step = self.steps["dark under the wallpaper"]
        self.assertEqual(sorted((p["kind"], p["origin"]) for p in step["posted"]), [("chat", HALO), ("vault", NOTES)])
        self.assertTrue(all(p["msg"]["theme"] == "dark" for p in step["posted"]), step["posted"])

    def test_following_the_system_under_the_wallpaper_gives_the_devices_theme(self):
        step = self.steps["system (light device) under the wallpaper"]
        self.assertEqual(step["theme"], "light")
        self.assertTrue(step["posted"] and all(p["msg"]["theme"] == "light" for p in step["posted"]), step["posted"])
        self.assertEqual(self.steps["reopened, system under the wallpaper"]["chat"], HALO + "/#hub_theme=light")


class VaultOpenThemeTest(VaultCase):
    def test_the_theme_rides_on_the_form_address_fragment(self):
        self.unlock()
        for theme in ("dark", "light"):
            resp = self.open_page("?to=%2Fnote%2Fa.md&theme=" + theme)
            self.assertEqual(parse(resp).forms, [("post", VAULT + "/auth/hub/sso#hub_theme=" + theme)])
            page = resp.get_data(as_text=True)
            self.assertIn(":root { color-scheme: %s; }" % theme, page)
            self.assertNotIn("prefers-color-scheme", page)
            # 表单地址的 # 不影响 form-action 的比对（CSP 只看源和路径）。
            self.assertIn("form-action " + VAULT + ";", resp.headers["Content-Security-Policy"])

    def test_anything_else_is_ignored(self):
        self.unlock()
        for query in ("", "&theme=", "&theme=system", "&theme=dark%23x", "&theme=%3Cb%3E", "&theme=DARK"):
            resp = self.open_page("?to=%2F" + query)
            self.assertEqual(parse(resp).forms, [("post", VAULT + "/auth/hub/sso")], query)
            page = resp.get_data(as_text=True)
            self.assertIn(":root { color-scheme: light dark; }", page, query)
            self.assertIn("@media (prefers-color-scheme: dark)", page, query)

    def test_an_explanation_page_in_the_frame_follows_the_theme_too(self):
        resp = self.open_page("?theme=dark")
        self.assertEqual(resp.status_code, 401)
        self.assertIn(":root { color-scheme: dark; }", resp.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
