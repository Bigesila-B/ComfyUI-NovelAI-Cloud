"""ComfyUI-NovelAI-Cloud：NovelAI 云端生图节点。

- 节点：NovelAICloudGenerator（NovelAI 分类）
- 后端路由（供前端按钮调用，不消耗 Anlas）：
    POST /nai_cloud/check          测试连接/令牌：返回订阅档位与 Anlas 余额
    GET  /nai_cloud/models         当前模型下拉框列表（内置 + 联网缓存）
    POST /nai_cloud/refresh_models 联网刷新模型清单（默认 novelai-sdk 社区源，可用自定义 URL）

联网刷新的模型列表只保留在内存中（重启 ComfyUI 后点一次“刷新模型”即可恢复）；
要长期使用新模型，直接在节点的“自定义模型ID”框里填模型 ID 即可。
"""

import re
import urllib.request

_extra_models = []  # 联网刷新得到的额外模型 ID（仅内存；nodes.INPUT_TYPES 会读取）

from .nodes import NovelAICloudGenerator  # noqa: E402  (需在 _extra_models 定义之后)
from . import models_catalog  # noqa: E402
from .nai_api import NAIError, NovelAIClient  # noqa: E402

NODE_CLASS_MAPPINGS = {"NovelAICloudGenerator": NovelAICloudGenerator}
NODE_DISPLAY_NAME_MAPPINGS = {"NovelAICloudGenerator": "NovelAI 云端生图"}
WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]


def _register_routes():
    try:
        from server import PromptServer
        from aiohttp import web
    except Exception:
        return  # 非 ComfyUI 环境（纯测试）跳过路由

    routes = PromptServer.instance.routes

    @routes.post("/nai_cloud/check")
    async def nai_check(request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        token = (body.get("token") or "").strip()
        proxy = body.get("proxy") or ""
        if not token:
            return web.json_response({"ok": False, "error": "请先填写 Token"})
        try:
            client = NovelAIClient(token, proxy)
            info = client.subscription()
            return web.json_response({"ok": True, **info})
        except NAIError as e:
            return web.json_response({"ok": False, "error": str(e), "status": e.status})
        except Exception as e:  # noqa: BLE001
            return web.json_response({"ok": False, "error": f"未知错误: {e}"})

    @routes.get("/nai_cloud/models")
    async def nai_models(request):
        return web.json_response({
            "ok": True,
            "models": models_catalog.model_choices(_extra_models),
            "defaults": {m[0]: {"steps": m[4], "scale": m[5], "sampler": m[6]}
                         for m in models_catalog.BUILTIN_MODELS},
        })

    @routes.post("/nai_cloud/refresh_models")
    async def nai_refresh_models(request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        url = (body.get("url") or "").strip() or models_catalog.DEFAULT_MODELS_URL
        proxy = body.get("proxy") or ""
        if not re.match(r"^https://", url):
            return web.json_response({"ok": False, "error": "模型列表 URL 必须是 https:// 地址"})
        try:
            handlers = []
            p = (proxy or "").strip() or urllib.request.getproxies().get("https", "")
            if p and p.startswith("http"):
                handlers.append(urllib.request.ProxyHandler({"http": p, "https": p}))
            opener = urllib.request.build_opener(*handlers)
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with opener.open(req, timeout=25) as resp:
                text = resp.read().decode("utf-8", "replace")
            ids = models_catalog.extract_model_ids(text)
            if not ids:
                return web.json_response({"ok": False,
                                          "error": f"该地址里没找到 nai-diffusion-* 模型 ID: {url}"})
            merged = sorted(set(ids) | {m[0] for m in models_catalog.BUILTIN_MODELS})
            global _extra_models
            builtin = {m[0] for m in models_catalog.BUILTIN_MODELS}
            _extra_models = [i for i in merged if i not in builtin]
            return web.json_response({"ok": True, "models": models_catalog.model_choices(_extra_models),
                                      "added": _extra_models, "source": url})
        except Exception as e:  # noqa: BLE001
            hint = "（检查代理地址或网络）" if "timeout" in str(e).lower() else ""
            return web.json_response({"ok": False, "error": f"刷新失败: {e}{hint}"})


_register_routes()
