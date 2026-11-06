"""NovelAI 云端生图节点。

- 提示词双通道：自带文本框；接了 CONDITIONING（文本编码器）后自动改用编码器文本
  （CONDITIONING 里只有编码张量不含原文，因此通过隐藏的执行图反查上游节点的 text 值）
- 图生图：接 IMAGE；局部重绘：再接 MASK（白色=重绘区域），模型自动切换到 inpainting 变体
- 输出 IMAGE，可接放大节点后再保存
"""

import io

import numpy as np
from PIL import Image

from . import anlas, models_catalog
from .nai_api import NAIError, NovelAIClient, build_payload, image_to_b64_png


def _say(*args, **kw):
    """print() 的替身：遇到控制台编码打不出的字符也不抛异常。

    为什么要它：中文 Windows 的控制台是 GBK，日志里只要出现一个编不出的字符
    （例如面积说明里的 U+00B2，也就是 "²"），print() 就抛 UnicodeEncodeError；
    而 ComfyUI 打印这个异常时又编不出来 —— 结果是执行线程直接死掉，
    任务永远停在 0%、界面上没有任何可读的报错。

    宁可日志里少一个字符（打成 ?），也不能让一次出图白跑。
    """
    try:
        print(*args, **kw)
    except UnicodeEncodeError:
        safe = [str(a).encode("gbk", "replace").decode("gbk", "replace") for a in args]
        print(*safe, **kw)


_RESIZE_HINT = "原图尺寸与分辨率不一致，已自动缩放到设定分辨率"


def _find_widget_text(graph, node_id, visited=None):
    """在执行图里递归找上游节点携带的提示词文本。"""
    visited = visited or set()
    node_id = str(node_id)
    if node_id in visited:
        return None
    visited.add(node_id)
    node = graph.get(node_id) or {}
    inputs = node.get("inputs", {})
    for key in ("text", "string", "prompt", "caption", "value"):
        v = inputs.get(key)
        if isinstance(v, str):
            return v
        if isinstance(v, list) and len(v) == 2:  # 输入来自另一个节点，继续向上找
            got = _find_widget_text(graph, v[0], visited)
            if got is not None:
                return got
    return None


def _tensor_to_pil(tensor):
    """ComfyUI IMAGE (H,W,C) 0-1 float → PIL RGB。"""
    arr = tensor.squeeze().cpu().numpy()
    arr = (np.clip(arr, 0.0, 1.0) * 255.0).round().astype(np.uint8)
    return Image.fromarray(arr, mode="RGB")


def _mask_to_pil(mask):
    """ComfyUI MASK (H,W) 0-1 float（1=重绘）→ PIL L（白=重绘，与 NAI 约定一致）。"""
    arr = mask.squeeze().cpu().numpy()
    arr = (np.clip(arr, 0.0, 1.0) * 255.0).round().astype(np.uint8)
    return Image.fromarray(arr, mode="L")


class NovelAICloudGenerator:
    CATEGORY = "NovelAI"
    FUNCTION = "generate"
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("图像",)
    OUTPUT_TOOLTIPS = ("生成的图片，可接放大节点后再接保存节点",)

    @classmethod
    def INPUT_TYPES(cls):
        cached = models_catalog.model_choices(_cached_extra_models())
        return {
            "required": {
                "token": ("STRING", {"default": "", "multiline": False, "password": True,
                                     "tooltip": "NovelAI Persistent API Token（网页右上角 Account → "
                                                "Get Persistent API Token，pst- 开头）。存在工作流里，"
                                                "不要分享含此节点的工作流"}),
                "proxy": ("STRING", {"default": "", "multiline": False,
                                     "tooltip": "http 代理地址，如 http://127.0.0.1:7897。"
                                                "留空则读环境变量 NAI_PROXY；开 TUN 模式可留空"}),
                "model": (cached, {"tooltip": "模型列表可点“刷新模型”联网更新；"
                                              "官方出新模型可在“自定义模型ID”直接填"}),
                "custom_model_id": ("STRING", {"default": "", "multiline": False,
                                               "tooltip": "填了则覆盖上面的下拉框，用于第一时间使用新模型"}),
                "model_search": ("STRING", {"default": "", "multiline": False,
                                            "tooltip": "输入关键词即时过滤上方模型下拉框（仅界面用）"}),
                "positive_text": ("STRING", {"default": "", "multiline": True,
                                             "tooltip": "正面提示词。接了 CONDITIONING 输入后此项被忽略"}),
                "negative_text": ("STRING", {"default": "", "multiline": True,
                                             "tooltip": "额外负面提示词，会拼在 UC 预设串之后"}),
                "uc_preset": (models_catalog.UC_PRESET_CHOICES,
                              {"default": "无",
                               "tooltip": "官方负面预设。Heavy 含官方负面词（Full 系含 nsfw 压制）"}),
                "quality_tags": (models_catalog.QUALITY_CHOICES,
                                 {"default": "标准", "tooltip": "官方质量词"}),
                "size_tier": (models_catalog.SIZE_TIERS,
                              {"default": "常规", "tooltip": "出图尺寸档位（与官网一致），"
                               "搭配右侧比例得到官方支持的精确分辨率，避免填入不支持的尺寸"}),
                "aspect": (models_catalog.ASPECTS,
                           {"default": "竖", "tooltip": "画面比例：竖/方/横"}),
                "steps": ("INT", {"default": 23, "min": 1, "max": 50,
                                  "tooltip": "步数。Opus 订阅 ≤28 步且 ≤1024² 时首张免费"}),
                "cfg_scale": ("FLOAT", {"default": 5.0, "min": 0.0, "max": 10.0, "step": 0.1,
                                        "tooltip": "Guidance/提示词引导度，官方推荐 5~6（V3 社区惯用 11）"}),
                "sampler": (models_catalog.SAMPLERS,),
                "cfg_rescale": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05}),
                "variety_plus": ("BOOLEAN", {"default": False, "tooltip": "Variety+ 增加多样性"}),
                "decrisp": ("BOOLEAN", {"default": False, "tooltip": "Decrisper（V4.5+）"}),
                "smea": (["关", "SMEA", "SMEA+DYN"],
                         {"default": "关", "tooltip": "仅 V3 有效；会提高积分消耗"}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 4294967295,
                                 "control_after_generate": True}),
                "n_samples": ("INT", {"default": 1, "min": 1, "max": 8,
                                      "tooltip": "张数。账号同时只能跑 1 张，因此串行逐张生成"}),
            },
            "optional": {
                "positive": ("CONDITIONING", {"tooltip": "可选：接文本编码器节点（如 CLIP Text Encode），"
                                                         "接上后其文本替代内置正面提示词"}),
                "negative_cond": ("CONDITIONING", {"tooltip": "可选：负面提示词编码，接上后替代内置负面框"}),
                "image": ("IMAGE", {"tooltip": "可选：图生图原图（img2img）"}),
                "mask": ("MASK", {"tooltip": "可选：局部重绘蒙版（白色=重绘区域），需搭配 image；"
                                             "模型会自动切换到对应 inpainting 变体"}),
                "img2img_strength": ("FLOAT", {"default": 0.7, "min": 0.01, "max": 0.99, "step": 0.01,
                                               "tooltip": "重绘幅度：越小越接近原图；也影响积分消耗"}),
                "img2img_noise": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05}),
            },
            "hidden": {"prompt": "PROMPT", "unique_id": "UNIQUE_ID"},
        }

    def generate(self, token, proxy, model, custom_model_id, model_search,
                 positive_text, negative_text, uc_preset, quality_tags,
                 size_tier, aspect, steps, cfg_scale, sampler, cfg_rescale,
                 variety_plus, decrisp, smea, seed, n_samples,
                 positive=None, negative_cond=None, image=None, mask=None,
                 img2img_strength=0.7, img2img_noise=0.0,
                 prompt=None, unique_id=None):
        graph = prompt or {}

        # ---- 分辨率（官网预设，避免不支持的尺寸导致失败/白扣积分） ----
        size = models_catalog.resolve_size(size_tier, aspect)
        if size is None:
            raise NAIError(f"尺寸「{size_tier}」+ 比例「{aspect}」不支持："
                           f"壁纸档仅支持竖/横；方图请选 常规 或 大图")
        width, height = size

        # ---- 提示词解析：CONDITIONING 优先，取不到原文则明确报错 ----
        positive_prompt = positive_text or ""
        negative_prompt = negative_text or ""
        for cond_input, widget_text, kind in (
                (positive, positive_text, "正面"),
                (negative_cond, negative_text, "负面")):
            if cond_input is None:
                continue
            link = None
            if unique_id is not None and isinstance(graph.get(str(unique_id)), dict):
                link = graph[str(unique_id)]["inputs"].get(
                    "positive" if kind == "正面" else "negative_cond")
            text = _find_widget_text(graph, link[0]) if isinstance(link, list) and link else None
            if text is None:
                raise NAIError(f"{kind}提示词已接入 CONDITIONING，但无法从上游节点读到原文文本。"
                               f"请使用标准文本编码节点（其 text 输入为可编辑文本），"
                               f"或断开连接改用节点内置文本框")
            if kind == "正面":
                positive_prompt = text
            else:
                negative_prompt = text

        if not positive_prompt.strip():
            raise NAIError("正面提示词为空：请填写文本框，或接入文本编码器节点")

        # ---- 模型解析 / 重绘自动切换 ----
        model_id = models_catalog.resolve_model_id(model, custom_model_id)
        family = models_catalog.get_family(model_id)
        action = "generate"
        if mask is not None:
            inp_id = models_catalog.get_inpainting_id(model_id)
            if inp_id:
                _say(f"[NovelAI Cloud] 局部重绘：模型 {model_id} → {inp_id}")
                model_id, family = inp_id, models_catalog.get_family(inp_id)
            else:
                _say(f"[NovelAI Cloud] 警告：未知模型 {model_id} 的 inpainting 变体，按原模型尝试")
            action = "inpaint"
        elif image is not None:
            action = "img2img"

        # ---- 提示词拼装（质量词 + UC 预设） ----
        quality_suffix, quality_flag = models_catalog.get_quality_string(model_id, quality_tags)
        if quality_suffix:
            positive_prompt = positive_prompt.rstrip() + quality_suffix
        uc_base = models_catalog.get_uc_string(model_id, uc_preset)
        if uc_base and negative_prompt.strip():
            full_negative = uc_base + ", " + negative_prompt.strip()
        else:
            full_negative = uc_base or negative_prompt.strip()

        # ---- 图生图 / 重绘输入 ----
        image_b64 = mask_b64 = None
        if image is not None:
            src = _tensor_to_pil(image[0])
            if src.size != (width, height):
                _say(f"[NovelAI Cloud] {_RESIZE_HINT}: {src.size} → {(width, height)}")
                src = src.resize((width, height), Image.LANCZOS)
            image_b64 = image_to_b64_png(src)
        if mask is not None:
            if image is None:
                raise NAIError("接了 MASK（局部重绘）但没接 image：请同时接入原图")
            mk = _mask_to_pil(mask)
            if mk.size != (width, height):
                mk = mk.resize((width, height), Image.NEAREST)
            mask_b64 = image_to_b64_png(mk)

        strength = img2img_strength if action != "generate" else 1.0
        smea_on = smea in ("SMEA", "SMEA+DYN")
        smea_dyn_on = smea == "SMEA+DYN"

        # ---- 积分预估（控制台留档；界面上的实时预估由前端计算） ----
        est = anlas.estimate_cost(width, height, steps, n_samples, strength=strength,
                                  smea=smea_on, smea_dyn=smea_dyn_on)
        est_opus = anlas.estimate_cost(width, height, steps, n_samples,
                                       strength=strength, smea=smea_on,
                                       smea_dyn=smea_dyn_on, opus=True)
        _say(f"[NovelAI Cloud] 预计消耗: 非Opus {est['total']} Anlas | "
              f"Opus {est_opus['total']} Anlas{'（首张免费）' if est_opus['opus_free'] else ''}"
              f" | 单张 {est['per_image']}")

        # ---- 积分预检：仅在预计要消耗积分时查余额（免费档跳过，省一次请求） ----
        client = NovelAIClient(token, proxy)
        if est["total"] > 0:
            try:
                sub = client.subscription()
                if isinstance(sub.get("anlas"), int) and sub["anlas"] < est["total"]:
                    raise NAIError(f"Anlas 余额不足：预计需要 {est['total']}，当前仅 {sub['anlas']}。"
                                   f"已取消本次生成（未消耗积分）。可改用 V4.5 Full 普通尺寸免费档")
                _say(f"[NovelAI Cloud] 账户：{sub['tier_name']} 订阅"
                      f"{'' if sub['active'] else '（已过期）'}，"
                      f"Anlas 余额：{'无限（Opus）' if sub.get('anlas_unlimited') else sub.get('anlas')}")
            except NAIError as e:
                if e.status == 401 or "余额不足" in str(e):
                    raise
                _say(f"[NovelAI Cloud] 订阅查询跳过: {e}")

        # ---- 逐张串行生成（账号并发锁=1，串行 + 429 自动退避） ----
        try:
            from comfy.utils import ProgressBar
        except Exception:
            ProgressBar = None
        pbar = ProgressBar(n_samples) if ProgressBar else None

        images = []
        for i in range(n_samples):
            seed_i = (seed + i) % 4294967296
            payload = build_payload(
                model_id, family,
                prompt=positive_prompt, negative=full_negative,
                width=width, height=height, steps=steps, scale=cfg_scale,
                sampler=models_catalog.sampler_to_api(sampler), seed=seed_i, n_samples=1,
                uc_preset_id=models_catalog.UC_ID_V4PLUS.get(uc_preset, "none"),
                uc_preset_v3=models_catalog.UC_ID_V3.get(uc_preset, 4),
                quality_preset_id=quality_flag if family != "v3" else "none",
                quality_toggle=bool(quality_flag),
                cfg_rescale=cfg_rescale, noise_schedule="karras",
                smea=smea_on, smea_dyn=smea_dyn_on, variety_plus=variety_plus,
                decrisp=decrisp, action=action,
                image_b64=image_b64, mask_b64=mask_b64,
                strength=strength, noise=img2img_noise,
            )
            _say(f"[NovelAI Cloud] 生成 {i + 1}/{n_samples}: {model_id} "
                  f"{width}x{height} steps={steps} scale={cfg_scale} seed={seed_i} action={action}")
            for png in client.generate(payload):
                img = Image.open(io.BytesIO(png)).convert("RGB")
                images.append(np.array(img).astype(np.float32) / 255.0)
                _say(f"[NovelAI Cloud] 第 {i + 1} 张完成 seed={seed_i}，"
                      f"NovelAI 参数已含在原始 PNG 元数据中")
            if pbar:
                pbar.update(1)

        if not images:
            raise NAIError("没有生成任何图片")
        import torch  # 延迟导入：无 torch 的环境（纯测试）也能加载本模块
        out = torch.from_numpy(np.stack(images))
        return (out,)


def _cached_extra_models():
    """读取之前联网刷新得到的额外模型 ID（由 __init__.py 的路由写入）。"""
    try:
        from . import _extra_models
        return list(_extra_models)
    except Exception:
        return []
