"""NovelAI 直连 API 客户端（仅标准库，无第三方依赖）。

端点（2026-08 实测）：
- POST https://image.novelai.net/ai/generate-image   JSON 载荷，响应为 ZIP（内含 DEFLATE 压缩的 PNG）
- GET  https://image.novelai.net/user/subscription   订阅档位与 Anlas 余额
- 备用生成端点 https://api.novelai.net/ai/generate-image（部分地区不可达）

代理：优先显式传入，其次环境变量 NAI_PROXY；支持 http:// 代理（CONNECT 隧道）。
令牌只通过参数传入，本模块不打印、不落盘。
"""

import base64
import io
import json
import os
import time
import urllib.error
import urllib.request
import zlib

IMAGE_HOST = "https://image.novelai.net"
API_HOST = "https://api.novelai.net"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
_TIER_NAMES = {0: "Paper", 1: "Tablet", 2: "Scroll", 3: "Opus"}


class NAIError(Exception):
    """带 HTTP 状态码的 NovelAI 错误；status=0 表示网络/代理层失败。"""

    def __init__(self, message, status=0):
        super().__init__(message)
        self.status = status


def _proxy_url(proxy=None):
    p = (proxy or "").strip() or os.environ.get("NAI_PROXY", "").strip()
    if p and not p.startswith("http"):
        raise NAIError(f"代理地址需要以 http:// 开头，当前为: {p}")
    return p or None


def _http(url, token=None, body=None, proxy=None, timeout=120, method=None):
    """返回 (status, body_bytes, content_type)。HTTP >=400 不抛异常，由调用方处理。"""
    handlers = []
    p = _proxy_url(proxy)
    if p:
        handlers.append(urllib.request.ProxyHandler({"http": p, "https": p}))
    opener = urllib.request.build_opener(*handlers)
    headers = {"User-Agent": _UA, "Accept": "*/*"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read(), resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers.get("Content-Type", "")
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        reason = getattr(e, "reason", e)
        hint = ("\n—— 本机无法直连 NovelAI：在节点代理框填 http://127.0.0.1:代理端口"
                "（Clash 常见 7890/7897），或在代理客户端开 TUN/系统代理模式") if not p else \
               (f"\n—— 代理 {p} 连接失败：确认代理客户端已启动、端口正确")
        raise NAIError(f"网络错误: {reason}{hint}", status=0) from e


def parse_subscription(data):
    """从 /user/subscription 响应中提取 (tier, tierName, active, opus, anlas, anlas_unlimited)。

    Anlas 余额位于 trainingStepsLeft.fixedTrainingStepsLeft；Opus 订阅为 -2（无限档哨兵）。
    """
    def find_int(obj, key):
        if isinstance(obj, dict):
            if key in obj and isinstance(obj[key], (int, float)):
                return int(obj[key])
            for v in obj.values():
                got = find_int(v, key)
                if got is not None:
                    return got
        elif isinstance(obj, list):
            for v in obj:
                got = find_int(v, key)
                if got is not None:
                    return got
        return None

    if not isinstance(data, dict):
        return {"tier": None, "tier_name": "unknown", "active": False, "opus": False,
                "anlas": None, "anlas_unlimited": False}
    tier = data.get("tier")
    active = bool(data.get("active"))
    anlas = find_int(data, "fixedTrainingStepsLeft")
    unlimited = anlas is not None and anlas < 0
    return {
        "tier": tier,
        "tier_name": _TIER_NAMES.get(tier, "unknown"),
        "active": active,
        "opus": tier == 3 and active,
        "anlas": None if unlimited else anlas,
        "anlas_unlimited": unlimited,
    }


class NovelAIClient:
    def __init__(self, token, proxy=None, timeout=300, attempts=5):
        if not token or not token.strip():
            raise NAIError("未填写 NovelAI Token（网页右上角 Account → Get Persistent API Token，pst- 开头）")
        self.token = token.strip()
        self.proxy = _proxy_url(proxy)
        self.timeout = timeout
        self.attempts = max(1, attempts)

    # ---- 订阅 / 连通性 ----
    def subscription(self):
        t0 = time.time()
        status, body, _ = _http(f"{IMAGE_HOST}/user/subscription", token=self.token,
                                proxy=self.proxy, timeout=30, method="GET")
        latency_ms = int((time.time() - t0) * 1000)
        if status == 401:
            raise NAIError("令牌无效（401）——确认完整复制 pst- 开头的一整行，且未被重新生成吊销", status=401)
        if status != 200:
            raise NAIError(f"查询订阅失败 HTTP {status}: {body[:200].decode('utf-8', 'replace')}", status=status)
        info = parse_subscription(json.loads(body.decode("utf-8")))
        info["latency_ms"] = latency_ms
        info["proxy"] = self.proxy
        return info

    # ---- 生成 ----
    def generate(self, payload):
        """执行一次生成请求，返回 [png_bytes, ...]。429 并发锁自动指数退避重试。"""
        url = f"{IMAGE_HOST}/ai/generate-image"
        body = json.dumps(payload).encode("utf-8")
        last_err = None
        for attempt in range(1, self.attempts + 1):
            status, resp, ctype = _http(url, token=self.token, body=body,
                                        proxy=self.proxy, timeout=self.timeout)
            if status == 200:
                return unzip_pngs(resp)
            text = resp[:500].decode("utf-8", "replace")
            if status == 401:
                raise NAIError("令牌失效（401）——请重新获取 Persistent API Token 并更新节点", status=401)
            if status == 402:
                raise NAIError("Anlas 不足（402）——改用 V4.5 Full 普通尺寸免费档，或充值/降低参数", status=402)
            if status == 403:
                raise NAIError(f"请求被拒绝（403，内容政策或权限）: {text}", status=403)
            if status == 429 or "concurrency" in text.lower():
                wait = min(8.0, 1.0 * (2 ** (attempt - 1)))
                last_err = NAIError(f"并发锁（429）: {text}", status=429)
                if attempt < self.attempts:
                    time.sleep(wait)
                continue
            last_err = NAIError(f"生成失败 HTTP {status}: {text}", status=status)
            if status in (400, 404, 422, 500, 502, 503):
                break
        raise last_err or NAIError("生成失败：未知错误")


# ---- 响应 ZIP 解包（PNG 为 DEFLATE 压缩，必须真解压） ----
def unzip_pngs(data):
    eocd = data.rfind(b"PK\x05\x06")
    pngs = []
    if eocd != -1:
        count = int.from_bytes(data[eocd + 10:eocd + 12], "little")
        ptr = int.from_bytes(data[eocd + 16:eocd + 20], "little")
        for _ in range(count):
            if ptr + 46 > len(data) or int.from_bytes(data[ptr:ptr + 4], "little") != 0x02014b50:
                break
            method = int.from_bytes(data[ptr + 10:ptr + 12], "little")
            csize = int.from_bytes(data[ptr + 20:ptr + 24], "little")
            usize = int.from_bytes(data[ptr + 24:ptr + 28], "little")
            name_len = int.from_bytes(data[ptr + 28:ptr + 30], "little")
            extra_len = int.from_bytes(data[ptr + 30:ptr + 32], "little")
            comment_len = int.from_bytes(data[ptr + 32:ptr + 34], "little")
            lho = int.from_bytes(data[ptr + 42:ptr + 46], "little")
            name = data[ptr + 46:ptr + 46 + name_len].decode("utf-8", "replace")
            ln_len = int.from_bytes(data[lho + 26:lho + 28], "little")
            le_len = int.from_bytes(data[lho + 28:lho + 30], "little")
            start = lho + 30 + ln_len + le_len
            if name.lower().endswith(".png"):
                if method == 8:
                    pngs.append(zlib.decompress(data[start:start + csize], -15))
                else:
                    pngs.append(data[start:start + usize])
            ptr += 46 + name_len + extra_len + comment_len
    if not pngs:  # 兜底：直接扫 PNG 签名（应对响应格式变化）
        sig = b"\x89PNG\r\n\x1a\n"
        pos = 0
        while True:
            s = data.find(sig, pos)
            if s == -1:
                break
            e = data.find(b"IEND", s)
            if e == -1:
                break
            pngs.append(data[s:e + 8])
            pos = e + 8
    if not pngs:
        head = data[:120].decode("latin1", "replace")
        raise NAIError(f"响应中没有 PNG 数据，head: {head!r}")
    return pngs


# ---- 载荷构造 ----
def build_payload(model_id, family, *, prompt, negative, width, height, steps, scale,
                  sampler, seed, n_samples=1, uc_preset_id="none", uc_preset_v3=4,
                  quality_preset_id="none", quality_toggle=True, cfg_rescale=0.0,
                  noise_schedule="karras", smea=False, smea_dyn=False, variety_plus=False,
                  decrisp=False, action="generate", image_b64=None, mask_b64=None,
                  strength=0.7, noise=0.0):
    """构造生成载荷。V4+ 结构按 2026-08-30 抓包实测，V3 按社区成熟实现。"""
    if family == "v3":
        if sampler == "ddim":
            sampler = "ddim_v3"
        params = {
            "width": width, "height": height,
            "steps": steps, "scale": scale, "sampler": sampler,
            "seed": seed, "n_samples": n_samples,
            "ucPreset": uc_preset_v3, "qualityToggle": quality_toggle,
            "autoSmea": False, "sm": bool(smea), "sm_dyn": bool(smea_dyn),
            "dynamic_thresholding": bool(decrisp),
            "legacy": False, "add_original_image": True,
            "cfg_rescale": cfg_rescale,
            "noise_schedule": noise_schedule if noise_schedule != "karras" else "native",
            "skip_cfg_above_sigma": 19 if variety_plus else None,
            "uc": negative,
        }
    else:
        params = {
            "params_version": 4,
            "width": width, "height": height,
            "scale": scale, "sampler": sampler, "steps": steps,
            "seed": seed, "n_samples": n_samples,
            "ucPresetId": uc_preset_id, "qualityPresetId": quality_preset_id,
            "autoSmea": False, "dynamic_thresholding": bool(decrisp),
            "controlnet_strength": 1, "legacy": False, "add_original_image": True,
            "cfg_rescale": cfg_rescale, "noise_schedule": noise_schedule,
            "legacy_v3_extend": False,
            "skip_cfg_above_sigma": 58 if variety_plus else None,
            "use_coords": False, "legacy_uc": False,
            "normalize_reference_strength_multiple": True,
            "inpaintImg2ImgStrength": strength if action == "inpaint" else 1,
            "characterPrompts": [],
            "tag_hint_qt": 1, "tag_hint_uc_preset": 2,
            "deliberate_euler_ancestral_bug": False, "prefer_brownian": True,
            "image_format": "png",
            "negative_prompt": negative,
            "v4_prompt": {"caption": {"base_caption": prompt, "char_captions": []},
                          "use_coords": False, "use_order": True},
            "v4_negative_prompt": {"caption": {"base_caption": negative, "char_captions": []},
                                   "legacy_uc": False},
        }
    if action in ("img2img", "inpaint"):
        params["image"] = image_b64
        params["strength"] = strength
        params["noise"] = noise
    if action == "inpaint":
        params["mask"] = mask_b64
        params["add_original_image"] = True
    return {"input": prompt, "model": model_id, "action": action, "parameters": params}


def image_to_b64_png(img):
    """PIL.Image → base64 PNG 字符串。"""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")
