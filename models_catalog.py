"""NovelAI 模型目录：内置模型清单、质量词表、负面预设(UC)表、在线刷新。

显示层全中文（下拉框显示中文名，运行时映射回英文 API ID 发给服务器）；
工作流键名保持英文，由前端 JS 负责把字段标签中文化。

数据来源（2026-08 校对）：
- 模型 ID：官方文档 + 实测抓包（nai-diffusion-4-5-full 已实测验证）
- 质量词 / UC 预设串：NovelAI 前端逆向（Aedial/novelai-api ImagePreset._UC_Presets，
  其中 V4.5 Full Heavy 已与本机抓包核对一致）
- 在线刷新默认源：novelai-sdk 社区常量表（新模型发布后跟进很快）
"""

import re

# (模型ID, 中文显示名, 家族, inpainting变体ID或None, 默认步数, 默认CFG, 默认采样器)
# 家族: "v4plus" = V4/V4.5/V5 新版载荷；"v3" = V3 旧版载荷
BUILTIN_MODELS = [
    ("nai-diffusion-5-full", "V5 完整版", "v4plus",
     "nai-diffusion-5-full-inpainting", 23, 6.0, "k_euler_ancestral"),
    ("nai-diffusion-5-curated", "V5 策展版", "v4plus",
     "nai-diffusion-5-curated-inpainting", 23, 6.0, "k_euler_ancestral"),
    ("nai-diffusion-4-5-full", "V4.5 完整版", "v4plus",
     "nai-diffusion-4-5-full-inpainting", 23, 5.0, "k_euler_ancestral"),
    ("nai-diffusion-4-5-curated", "V4.5 策展版", "v4plus",
     "nai-diffusion-4-5-curated-inpainting", 23, 5.0, "k_euler_ancestral"),
    ("nai-diffusion-4-full", "V4 完整版", "v4plus",
     "nai-diffusion-4-full-inpainting", 23, 5.0, "k_euler_ancestral"),
    ("nai-diffusion-4-curated", "V4 策展版", "v4plus",
     "nai-diffusion-4-curated-inpainting", 23, 5.0, "k_euler_ancestral"),
    ("nai-diffusion-3", "V3 动漫", "v3",
     "nai-diffusion-3-inpainting", 28, 11.0, "k_euler_ancestral"),
    ("nai-diffusion-3-furry", "V3 兽人", "v3",
     "nai-diffusion-furry-3-inpainting", 28, 11.0, "k_euler_ancestral"),
]

# 采样器：下拉显示中文，运行时映射回 API ID
SAMPLERS = ["欧拉·a", "欧拉", "DPM++ 2S a", "DPM++ 2M", "DPM++ SDE", "DDIM", "DDPM"]
SAMPLER_TO_API = {
    "欧拉·a": "k_euler_ancestral",
    "欧拉": "k_euler",
    "DPM++ 2S a": "k_dpmpp_2s_ancestral",
    "DPM++ 2M": "k_dpmpp_2m",
    "DPM++ SDE": "k_dpmpp_sde",
    "DDIM": "ddim",
    "DDPM": "ddpm",
}

# 噪声调度自动按模型选择（V4+ → karras，V3 → native），不再暴露为参数

# V4+ 的 ucPresetId（字符串）；V3 的 ucPreset（整数）映射。下拉显示中文。
UC_PRESET_CHOICES = ["重度(Heavy)", "轻度(Light)", "人物聚焦", "兽人聚焦", "无"]
UC_ID_V4PLUS = {
    "重度(Heavy)": "heavy",
    "轻度(Light)": "light",
    "人物聚焦": "human_focus",
    "兽人聚焦": "furry_focus",
    "无": "none",
}
UC_ID_V3 = {"重度(Heavy)": 0, "轻度(Light)": 1, "人物聚焦": 3, "兽人聚焦": 2, "无": 4}

QUALITY_CHOICES = ["标准", "无"]

# 出图尺寸：尺寸档位 × 比例 → 官方支持的精确分辨率（与官网一致）
# 只列官方预设，避免填入不支持的分辨率导致生成失败或白扣积分
SIZE_TIERS = ["常规", "大图", "壁纸"]
ASPECTS = ["竖", "方", "横"]
SIZE_PRESETS = {
    ("常规", "竖"): (832, 1216),
    ("常规", "方"): (1024, 1024),
    ("常规", "横"): (1216, 832),
    ("大图", "竖"): (1024, 1536),
    ("大图", "方"): (1472, 1472),
    ("大图", "横"): (1536, 1024),
    ("壁纸", "竖"): (1088, 1920),
    ("壁纸", "横"): (1920, 1088),
    # 壁纸档无方图（官方不支持），resolve_size 返回 None
}


def resolve_size(tier, aspect):
    """返回 (width, height) 或 None（该档位+比例不支持）。"""
    return SIZE_PRESETS.get((tier, aspect))

# 质量词（qualityPresetId="standard" 时由客户端拼在正面提示词末尾，与官方 UI 行为一致）
# V3 用 qualityToggle 开关由服务端注入，不在本地拼接
_QUALITY_STANDARD = {
    "nai-diffusion-5-full": ", very aesthetic, masterpiece, no text",
    "nai-diffusion-5-curated": ", very aesthetic, masterpiece, no text",
    "nai-diffusion-4-5-full": ", very aesthetic, masterpiece, no text",
    "nai-diffusion-4-5-curated": ", location, masterpiece, no text, -0.8::feet::, rating:general",
    "nai-diffusion-4-full": ", no text, best quality, very aesthetic, absurdres",
    "nai-diffusion-4-curated": ", rating:general, amazing quality, very aesthetic, absurdres",
}

# UC 预设串（负面提示词基础部分，官方 Heavy 会压制 nsfw，Full 系自带 nsfw 前缀）
_UC = {
    "nai-diffusion-4-5-full": {
        "heavy": "nsfw, lowres, artistic error, film grain, scan artifacts, worst quality, "
                 "bad quality, jpeg artifacts, very displeasing, chromatic aberration, dithering, "
                 "halftone, screentone, multiple views, logo, too many watermarks, negative space, blank page",
        "light": "nsfw, lowres, artistic error, scan artifacts, worst quality, bad quality, "
                 "jpeg artifacts, multiple views, very displeasing, too many watermarks, negative space, blank page",
        "human_focus": "nsfw, lowres, artistic error, film grain, scan artifacts, worst quality, "
                       "bad quality, jpeg artifacts, very displeasing, chromatic aberration, dithering, "
                       "halftone, screentone, multiple views, logo, too many watermarks, negative space, "
                       "blank page, @_@, mismatched pupils, glowing eyes, bad anatomy",
        "furry_focus": "nsfw, {worst quality}, distracting watermark, unfinished, bad quality, "
                       "{widescreen}, upscale, {sequence}, {{grandfathered content}}, blurred foreground, "
                       "chromatic aberration, sketch, everyone, [sketch background], simple, [flat colors], "
                       "ych (character), outline, multiple scenes, [[horror (theme)]], comic",
        "none": "",
    },
    "nai-diffusion-4-5-curated": {
        "heavy": "blurry, lowres, upscaled, artistic error, film grain, scan artifacts, worst quality, "
                 "bad quality, jpeg artifacts, very displeasing, chromatic aberration, halftone, "
                 "multiple views, logo, too many watermarks, negative space, blank page",
        "light": "blurry, lowres, upscaled, artistic error, scan artifacts, jpeg artifacts, logo, "
                 "too many watermarks, negative space, blank page",
        "human_focus": "blurry, lowres, upscaled, artistic error, film grain, scan artifacts, bad anatomy, "
                       "bad hands, worst quality, bad quality, jpeg artifacts, very displeasing, "
                       "chromatic aberration, halftone, multiple views, logo, too many watermarks, @_@, "
                       "mismatched pupils, glowing eyes, negative space, blank page",
        "none": "",
    },
    "nai-diffusion-4-full": {
        "heavy": "nsfw, blurry, lowres, error, film grain, scan artifacts, worst quality, bad quality, "
                 "jpeg artifacts, very displeasing, chromatic aberration, multiple views, logo, "
                 "too many watermarks, white blank page, blank page",
        "light": "nsfw, blurry, lowres, error, worst quality, bad quality, jpeg artifacts, "
                 "very displeasing, white blank page, blank page",
        "none": "",
    },
    "nai-diffusion-4-curated": {
        "heavy": "blurry, lowres, error, film grain, scan artifacts, worst quality, bad quality, "
                 "jpeg artifacts, very displeasing, chromatic aberration, logo, dated, signature, "
                 "multiple views, gigantic breasts, white blank page, blank page",
        "light": "blurry, lowres, error, worst quality, bad quality, jpeg artifacts, very displeasing, "
                 "logo, dated, signature",
        "none": "",
    },
    "nai-diffusion-3": {
        "heavy": "nsfw, lowres, {bad}, error, fewer, extra, missing, worst quality, jpeg artifacts, "
                 "bad quality, watermark, unfinished, displeasing, chromatic aberration, signature, "
                 "extra digits, artistic error, username, scan, [abstract]",
        "light": "nsfw, lowres, jpeg artifacts, worst quality, watermark, blurry, very displeasing",
        "none": "lowres",
    },
    "nai-diffusion-furry-3": {
        "heavy": "nsfw, {{worst quality}}, [displeasing], {unusual pupils}, guide lines, {{unfinished}}, "
                 "{bad}, url, artist name, {{tall image}}, mosaic, {sketch page}, comic panel, "
                 "impact (font), [dated], {logo}, ych, {what}, {where is your god now}, {distorted text}, "
                 "repeated text, {floating head}, {1994}, {widescreen}, absolutely everyone, sequence, "
                 "{compression artifacts}, hard translated, {cropped}, {commissioner name}, unknown text, "
                 "high contrast",
        "light": "{worst quality}, guide lines, unfinished, bad, url, tall image, widescreen, "
                 "compression artifacts, unknown text",
        "none": "lowres",
    },
}

# v5 暂时沿用 v4.5 的 UC 表（官方未公开 V5 专属预设，社区以 V4.5 近似）
_UC["nai-diffusion-5-full"] = _UC["nai-diffusion-4-5-full"]
_UC["nai-diffusion-5-curated"] = _UC["nai-diffusion-4-5-curated"]

# 在线刷新默认源：novelai-sdk 社区维护的模型常量表
DEFAULT_MODELS_URL = ("https://raw.githubusercontent.com/caru-ini/novelai-sdk/main/"
                      "src/novelai/constants/models.py")
_MODEL_ID_RE = re.compile(r"nai-diffusion-[a-z0-9\-]+")

_BUILTIN_IDS = {m[0] for m in BUILTIN_MODELS}
_LABEL_TO_ID = {m[1]: m[0] for m in BUILTIN_MODELS}
_MODEL_META = {m[0]: m for m in BUILTIN_MODELS}


def model_choices(extra_ids=()):
    """下拉框可选项：内置模型中文名 + 联网新增模型（带（联网）标记）。"""
    choices = [m[1] for m in BUILTIN_MODELS]
    for mid in extra_ids:
        if mid not in _BUILTIN_IDS:
            choices.append(f"{mid}（联网）")
    return choices


def resolve_model_id(choice, custom_id=""):
    """下拉显示值 / 自定义模型ID → 实际发给 API 的模型 ID。

    支持：内置中文名（查表）、联网标记名（正则取 ID）、旧式“名 (ID)”、裸 ID。
    """
    if custom_id and custom_id.strip():
        return custom_id.strip()
    if not choice:
        return "nai-diffusion-4-5-full"
    if choice in _LABEL_TO_ID:
        return _LABEL_TO_ID[choice]
    m = _MODEL_ID_RE.search(choice)
    return m.group(0) if m else choice.strip()


def sampler_to_api(display):
    return SAMPLER_TO_API.get(display, "k_euler_ancestral")


def noise_to_api(display):
    return NOISE_TO_API.get(display, "karras")


def get_family(model_id):
    meta = _MODEL_META.get(model_id)
    if meta:
        return meta[2]
    if re.search(r"(^|-)nai-diffusion-(furry-)?3(-|$)", model_id):
        return "v3"
    return "v4plus"


def get_inpainting_id(model_id, known_ids=()):
    meta = _MODEL_META.get(model_id)
    if meta:
        return meta[3]
    if "-inpainting" in model_id:
        return model_id
    if get_family(model_id) == "v4plus":
        cand = model_id + "-inpainting"
        return cand if not known_ids or cand in known_ids else None
    return None


def get_default_params(model_id):
    meta = _MODEL_META.get(model_id)
    if meta:
        return {"steps": meta[4], "scale": meta[5], "sampler": meta[6]}
    return {"steps": 23, "scale": 5.0, "sampler": "k_euler_ancestral"}


def get_quality_string(model_id, quality_choice):
    """返回 (质量词后缀或空串, qualityPresetId或qualityToggle)。V3 返回 (None, bool)。"""
    if get_family(model_id) == "v3":
        return None, quality_choice == "标准"
    if quality_choice == "标准":
        q = _QUALITY_STANDARD.get(model_id)
        if q is None:  # 联网新增模型：按 full/curated 近似
            q = (", very aesthetic, masterpiece, no text" if "full" in model_id
                 else ", location, masterpiece, no text, rating:general")
        return q, "standard"
    return "", "none"


def get_uc_string(model_id, uc_choice):
    """返回该模型在指定 UC 预设下的基础负面串。"""
    table = _UC.get(model_id)
    if table is None:  # 联网新增模型：按 full/curated 近似
        base = "nai-diffusion-4-5-full" if "full" in model_id else "nai-diffusion-4-5-curated"
        table = _UC[base]
    key = UC_ID_V4PLUS.get(uc_choice, "none")
    return table.get(key, "")


def extract_model_ids(text):
    return sorted(set(_MODEL_ID_RE.findall(text or "")))
