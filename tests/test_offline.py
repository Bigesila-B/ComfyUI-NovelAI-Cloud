"""离线单元测试（不联网、不消耗 Anlas、不含任何凭据）。

运行：python tests/test_offline.py
"""

import importlib.util
import math
import os
import sys

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(PKG_DIR, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


anlas = load("anlas")
catalog = load("models_catalog")
api = load("nai_api")

# 节点模块需要包上下文（相对导入），按包加载
spec = importlib.util.spec_from_file_location(
    "nai_pkg", os.path.join(PKG_DIR, "__init__.py"),
    submodule_search_locations=[PKG_DIR])
pkg = importlib.util.module_from_spec(spec)
sys.modules["nai_pkg"] = pkg
spec.loader.exec_module(pkg)
nodes = pkg.nodes

PASS = 0


def ok(cond, label):
    global PASS
    assert cond, f"FAIL: {label}"
    PASS += 1
    print(f"  ok - {label}")


# ---- 积分公式（锚点来自 2026-08-30 官网实测：V5 Curated 1024x1536@23 步收 26 Anlas） ----
print("[anlas]")
e = anlas.estimate_cost(1024, 1536, 23, 1)
ok(e["total"] == 26, f"1024x1536@23 非Opus = 26 Anlas（实测锚点），got {e['total']}")
e = anlas.estimate_cost(1024, 1536, 23, 1, opus=True)
ok((not e["opus_free"]) and e["total"] == 26, "1024x1536 Opus 不免费（面积超 1024²）→ 26 Anlas")
e = anlas.estimate_cost(832, 1216, 23, 1, opus=True)
ok(e["total"] == 0, "832x1216@23 Opus = 0（首张免费，实测锚点）")
e = anlas.estimate_cost(832, 1216, 23, 4, opus=True)
ok(e["billable"] == 3, "4 张 Opus 首张免费 → 计费 3 张")
e = anlas.estimate_cost(64, 64, 1, 1)
ok(e["per_image"] == 2, "极小参数按最低 2 Anlas/张")
e = anlas.estimate_cost(3072, 3072, 50, 1)
ok(e["over_cap"], "3072x3072 @ 50 步触发单张超 140 上限告警")
e = anlas.estimate_cost(832, 1216, 23, 1, strength=0.5)
ok(e["per_image"] == math.ceil(
    math.ceil(2.951823174884865e-6 * 832 * 1216 + 5.753298233447344e-7 * 832 * 1216 * 23) * 0.5),
   "图生图强度折算")
e = anlas.estimate_cost(1024, 1024, 23, 1, smea=True)
ok(e["per_image"] == math.ceil(math.ceil(
    2.951823174884865e-6 * 1048576 + 5.753298233447344e-7 * 1048576 * 23) * 1.2), "SMEA ×1.2")

# ---- 模型目录 ----
print("[models_catalog]")
ok(catalog.resolve_model_id("V5 完整版") == "nai-diffusion-5-full",
   "中文名下拉值解析回模型 ID")
ok(catalog.resolve_model_id("whatever", "nai-diffusion-6-full") == "nai-diffusion-6-full",
   "自定义模型 ID 优先")
ok(catalog.resolve_model_id("nai-diffusion-5-full") == "nai-diffusion-5-full",
   "裸 ID 直通")
ok(catalog.resolve_model_id("nai-diffusion-6-full（联网）") == "nai-diffusion-6-full",
   "联网标记名解析")
ok(catalog.sampler_to_api("欧拉·a") == "k_euler_ancestral", "采样器中文→API")
ok(catalog.get_inpainting_id("nai-diffusion-4-5-full") == "nai-diffusion-4-5-full-inpainting",
   "V4.5 Full → inpainting 变体")
ok(catalog.get_family("nai-diffusion-3") == "v3" and
   catalog.get_family("nai-diffusion-5-full") == "v4plus", "家族判定")
ok("nsfw" in catalog.get_uc_string("nai-diffusion-4-5-full", "重度(Heavy)"), "V4.5 Full Heavy UC 含 nsfw")
ok(catalog.get_uc_string("nai-diffusion-4-5-full", "无") == "", "UC 无 为空串")
q, flag = catalog.get_quality_string("nai-diffusion-4-5-full", "标准")
ok(q == ", very aesthetic, masterpiece, no text" and flag == "standard",
   "V4.5 质量词与抓包一致")
q, flag = catalog.get_quality_string("nai-diffusion-3", "标准")
ok(q is None and flag is True, "V3 返回 qualityToggle 布尔")
ok(catalog.extract_model_ids('x = "nai-diffusion-6-full"\ny="nai-diffusion-5-full"') ==
   ["nai-diffusion-5-full", "nai-diffusion-6-full"], "联网源模型 ID 提取")
ok("V4.5 完整版" in catalog.model_choices(), "下拉列表含中文名")
ok(len(catalog.model_choices()) == len(set(catalog.model_choices())), "下拉列表无重复")
ok(catalog.resolve_size("常规", "竖") == (832, 1216), "常规竖 = 832x1216")
ok(catalog.resolve_size("大图", "方") == (1472, 1472), "大图方 = 1472x1472")
ok(catalog.resolve_size("壁纸", "方") is None, "壁纸档方图不支持（官网无此组合）")

# ---- 载荷构造 ----
print("[payload]")
p = api.build_payload("nai-diffusion-4-5-full", "v4plus", prompt="1girl", negative="lowres",
                      width=832, height=1216, steps=23, scale=5, sampler="k_euler_ancestral",
                      seed=42, uc_preset_id="heavy")
ok(p["parameters"]["params_version"] == 4, "V4+ params_version=4（实测值）")
ok("v4_prompt" in p["parameters"] and p["parameters"]["v4_prompt"]["caption"]["base_caption"] == "1girl",
   "v4_prompt 结构")
ok(p["parameters"]["ucPresetId"] == "heavy", "ucPresetId 字符串")
ok(p["action"] == "generate" and "image" not in p["parameters"], "文生图不含 image 字段")

p = api.build_payload("nai-diffusion-3", "v3", prompt="1girl", negative="lowres",
                      width=832, height=1216, steps=28, scale=11, sampler="ddim", seed=1,
                      uc_preset_v3=0)
ok(p["parameters"]["ucPreset"] == 0 and p["parameters"]["qualityToggle"] is True,
   "V3 整数 ucPreset + qualityToggle")
ok(p["parameters"]["sampler"] == "ddim_v3", "V3 ddim 采样器改名")
ok("v4_prompt" not in p["parameters"], "V3 不带 v4_prompt")

p = api.build_payload("nai-diffusion-4-5-full", "v4plus", prompt="x", negative="y",
                      width=832, height=1216, steps=23, scale=5,
                      sampler="k_euler_ancestral", seed=1, action="img2img",
                      image_b64="AAA", strength=0.6, noise=0.05)
ok(p["parameters"]["image"] == "AAA" and p["parameters"]["strength"] == 0.6,
   "图生图 image/strength")
p = api.build_payload("nai-diffusion-4-5-full", "v4plus", prompt="x", negative="y",
                      width=832, height=1216, steps=23, scale=5,
                      sampler="k_euler_ancestral", seed=1, action="inpaint",
                      image_b64="AAA", mask_b64="MMM", strength=0.7)
ok(p["parameters"]["mask"] == "MMM", "重绘 mask 字段")

# ---- CONDITIONING 原文反查 ----
print("[nodes]")
graph = {
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "silver hair, 1girl", "clip": ["4", 0]}},
    "7": {"class_type": "SomePromptNode", "inputs": {"string": ["6", 0]}},
    "9": {"class_type": "NovelAICloudGenerator",
          "inputs": {"positive": ["7", 0], "negative_cond": ["6", 0]}},
}
ok(nodes._find_widget_text(graph, "7") == "silver hair, 1girl", "跨节点反查提示词原文")
ok(nodes._find_widget_text(graph, "6") == "silver hair, 1girl", "直接读编码器 text")
ok(nodes._find_widget_text(graph, "99") is None, "未知节点返回 None")
# 循环引用不死循环
loop = {"1": {"inputs": {"text": ["2", 0]}}, "2": {"inputs": {"text": ["1", 0]}}}
ok(nodes._find_widget_text(loop, "1") is None, "循环引用安全返回 None")

print(f"\n全部通过：{PASS} 项")
