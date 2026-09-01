// NovelAI 云端生图节点前端：令牌指示灯、实时 Anlas 预估、尺寸预设、模型搜索与联网刷新
// 公式与后端 anlas.py 完全一致（逆向自官方前端，属估算，实际扣费以后端为准）
import { app } from "../../scripts/app.js";

const NODE_NAME = "NovelAICloudGenerator";

// 字段名 → 中文显示标签（仅改显示层；widget.name / 工作流键名保持英文）
const LABELS = {
  token: "NovelAI 令牌",
  proxy: "代理地址",
  model: "模型",
  custom_model_id: "自定义模型ID",
  model_search: "模型搜索",
  positive_text: "正面提示词",
  negative_text: "负面提示词",
  uc_preset: "负面预设",
  quality_tags: "质量词",
  size_tier: "尺寸",
  aspect: "比例",
  steps: "步数",
  cfg_scale: "提示词引导度(CFG)",
  sampler: "采样器",
  cfg_rescale: "CFG 重缩放",
  variety_plus: "多样性增强(Variety+)",
  decrisp: "去糊(Decrisper)",
  smea: "SMEA 模式",
  seed: "随机种子",
  n_samples: "生成张数",
  img2img_strength: "重绘强度",
  img2img_noise: "噪声",
};
const INPUT_LABELS = {
  positive: "正向条件(接编码器)",
  negative_cond: "负向条件(接编码器)",
  image: "原图(图生图)",
  mask: "蒙版(局部重绘)",
};

// 尺寸预设（与后端 models_catalog.SIZE_PRESETS 一致）
const SIZE_PRESETS = {
  "常规|竖": [832, 1216], "常规|方": [1024, 1024], "常规|横": [1216, 832],
  "大图|竖": [1024, 1536], "大图|方": [1472, 1472], "大图|横": [1536, 1024],
  "壁纸|竖": [1088, 1920], "壁纸|横": [1920, 1088],
};

// ---- Anlas 预估（与后端 anlas.py 同公式） ----
function estimate(width, height, steps, n, smea, smeaDyn, strength, opus) {
  const w = Math.max(1, width | 0), h = Math.max(1, height | 0);
  const area = w * h;
  const st = Math.max(1, steps | 0);
  const ns = Math.max(1, n | 0);
  const sf = strength && strength > 0 ? strength : 1.0;
  const base = Math.ceil(2.951823174884865e-6 * area + 5.753298233447344e-7 * area * st);
  const mult = smea && smeaDyn ? 1.4 : smea ? 1.2 : 1.0;
  const raw = base * mult * sf;
  const per = Math.max(Math.ceil(raw), 2);
  const over = raw > 140;
  const free = !!opus && area <= 1048576 && st <= 28;
  const bill = free ? Math.max(0, ns - 1) : ns;
  return { total: per * bill, per, free, over };
}

function currentSize(node) {
  const t = wv(node, "size_tier")?.value;
  const a = wv(node, "aspect")?.value;
  return SIZE_PRESETS[`${t}|${a}`] || [832, 1216];
}

function fmtEst(e, size) {
  const sizeStr = size ? `尺寸 ${size[0]}×${size[1]} ｜ ` : "";
  if (e.over) return sizeStr + `⚠ 参数过大：单张预估超过官方 140 Anlas 上限，服务器会拒绝出图`;
  if (e.opus === null || e.opus === undefined)
    return sizeStr + `预计消耗：非Opus ${e.nonOpus} Anlas ｜ Opus ${e.total} Anlas${e.free ? "（首张免费）" : ""}`;
  return sizeStr + `预计消耗：${e.total} Anlas${e.free ? "（首张免费）" : ""} ｜ 单张 ${e.per}`;
}

// ---- 工具 ----
const wv = (node, name) => node.widgets?.find((w) => w.name === name);
const num = (node, name, dft) => {
  const w = wv(node, name);
  const v = w ? Number(w.value) : NaN;
  return Number.isFinite(v) ? v : dft;
};
const hasLink = (node, name) => {
  const i = node.inputs?.findIndex((x) => x.name === name);
  return i >= 0 && node.inputs[i].link != null;
};

async function postJSON(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return await r.json();
}

const LIGHT_COLORS = {
  gray: ["#888", "#9a9a9a"],
  yellow: ["#e6c34a", "#e6c34a"],
  green: ["#4caf50", "#7fd18a"],
  red: ["#e5484d", "#ff8080"],
};

function makePanel(node) {
  const wrap = document.createElement("div");
  wrap.style.cssText = "display:flex;flex-direction:column;gap:4px;padding:4px 0;min-width:300px";

  // 令牌指示灯行
  const lightRow = document.createElement("div");
  lightRow.style.cssText = "display:flex;align-items:center;gap:6px";
  const dot = document.createElement("span");
  dot.className = "nai-dot";
  dot.style.cssText = "width:10px;height:10px;border-radius:50%;background:#888;flex:none;display:inline-block";
  const lightText = document.createElement("span");
  lightText.className = "nai-light";
  lightText.style.cssText = "font:12px sans-serif;color:#9a9a9a;white-space:pre-line";
  lightText.textContent = "令牌：未检测";
  lightRow.append(dot, lightText);

  const est = document.createElement("div");
  est.className = "nai-est";
  est.style.cssText =
    "font:12px sans-serif;padding:6px 8px;border-radius:6px;background:rgba(80,160,255,.12);" +
    "border:1px solid rgba(80,160,255,.35);color:var(--input-text,#ddd);white-space:pre-line";

  const row = document.createElement("div");
  row.style.cssText = "display:flex;gap:6px";
  const btnCheck = document.createElement("button");
  btnCheck.textContent = "测试连接";
  const btnModels = document.createElement("button");
  btnModels.textContent = "刷新模型";
  for (const b of [btnCheck, btnModels]) {
    b.style.cssText =
      "flex:1;padding:4px 8px;cursor:pointer;border-radius:5px;font:12px sans-serif;" +
      "background:var(--comfy-input-bg,#2a2a2a);color:var(--input-text,#ddd);" +
      "border:1px solid rgba(255,255,255,.15)";
  }

  const status = document.createElement("div");
  status.className = "nai-status";
  status.style.cssText = "font:11px sans-serif;color:#9a9a9a;white-space:pre-line;min-height:14px";

  wrap.append(lightRow, est, row, status);
  return { wrap, dot, lightText, est, status, btnCheck, btnModels };
}

function setLight(panel, color, text) {
  const [bg, fg] = LIGHT_COLORS[color] || LIGHT_COLORS.gray;
  panel.dot.style.background = bg;
  panel.lightText.textContent = text;
  panel.lightText.style.color = fg;
}

function refreshEstimate(node, panel) {
  const [width, height] = currentSize(node);
  const strengthInput = hasLink(node, "image") || hasLink(node, "mask");
  const strength = strengthInput ? num(node, "img2img_strength", 0.7) : 1.0;
  const opus = node.nai_opus;
  const smeaVal = (wv(node, "smea")?.value) || "关";
  const sOn = smeaVal === "SMEA" || smeaVal === "SMEA+DYN";
  const sDyn = smeaVal === "SMEA+DYN";
  const common = [width, height, num(node, "steps", 23),
    num(node, "n_samples", 1), sOn, sDyn, strength];
  const eOpus = estimate(...common, true);
  const ePlain = estimate(...common, false);
  panel.est.textContent = fmtEst(
    opus === undefined ? { ...eOpus, nonOpus: ePlain.total } : (opus ? eOpus : ePlain),
    [width, height],
  );
  panel.est.style.background = (eOpus.over || ePlain.over)
    ? "rgba(255,80,80,.15)" : "rgba(80,160,255,.12)";
}

async function checkToken(node, panel, { immediate = false } = {}) {
  const token = wv(node, "token")?.value || "";
  if (!token) {
    node.nai_opus = undefined;
    setLight(panel, "gray", "令牌：未填写");
    panel.status.textContent = "";
    refreshEstimate(node, panel);
    return;
  }
  // 同一个令牌不重复自动检测（手动按钮强制）
  if (!immediate && node.nai_last_token === token) return;
  node.nai_last_token = token;
  setLight(panel, "yellow", "令牌：检测中…");
  try {
    const r = await postJSON("/nai_cloud/check", { token, proxy: wv(node, "proxy")?.value || "" });
    if (r.ok) {
      node.nai_opus = !!r.opus;
      const bal = r.anlas_unlimited ? "无限（Opus）"
        : typeof r.anlas === "number" ? r.anlas : "未知";
      setLight(panel, "green", `令牌：✓ ${r.tier_name}${r.active ? "" : "（已过期）"} ｜ ${bal}`);
      panel.status.textContent = `✓ ${r.tier_name} 订阅 ｜ 余额 ${bal} ｜ 延迟 ${r.latency_ms}ms`;
      panel.status.style.color = "#7fd18a";
    } else {
      node.nai_opus = undefined;
      setLight(panel, "red", `令牌：✗ ${r.error || "无效"}`);
      panel.status.textContent = "";
    }
  } catch (e) {
    node.nai_opus = undefined;
    setLight(panel, "red", `令牌：✗ ${e}`);
  }
  refreshEstimate(node, panel);
}

function applyModelFilter(node) {
  const combo = wv(node, "model");
  const search = wv(node, "model_search");
  if (!combo) return;
  if (!node.nai_all_models || !node.nai_all_models.length)
    node.nai_all_models = [...(combo.options.values || [])];
  const q = (search?.value || "").trim().toLowerCase();
  if (!q) {
    combo.options.values = [...node.nai_all_models];
  } else {
    combo.options.values = node.nai_all_models.filter((v) => v.toLowerCase().includes(q));
    if (combo.options.values.length && !combo.options.values.includes(combo.value))
      combo.value = combo.options.values[0];
  }
}

function setupNode(node) {
  if (node.nai_panel_done) return;
  node.nai_panel_done = true;

  // 字段名中文化（仅显示层）
  for (const w of node.widgets || []) {
    if (LABELS[w.name]) w.label = LABELS[w.name];
  }
  for (const inp of node.inputs || []) {
    if (INPUT_LABELS[inp.name]) inp.label = INPUT_LABELS[inp.name];
  }

  const panel = makePanel(node);
  if (typeof node.addDOMWidget === "function") {
    node.addDOMWidget("nai_cloud_panel", "nai_cloud_panel", panel.wrap, {
      getValue: () => "", setValue: () => {},
    });
  }

  node.nai_opus = undefined;
  node.nai_last_token = undefined;
  refreshEstimate(node, panel);

  // 按钮
  panel.btnCheck.addEventListener("click", () => checkToken(node, panel, { immediate: true }));
  panel.btnModels.addEventListener("click", async () => {
    panel.status.textContent = "正在联网刷新模型清单…";
    panel.btnModels.disabled = true;
    try {
      const r = await postJSON("/nai_cloud/refresh_models", { proxy: wv(node, "proxy")?.value || "" });
      if (r.ok) {
        const combo = wv(node, "model");
        if (combo) {
          const keep = combo.value;
          node.nai_all_models = [...r.models];
          combo.options.values = [...r.models];
          if (r.models.includes(keep)) combo.value = keep;
          else if (r.models.length) combo.value = r.models[0];
        }
        panel.status.textContent = `✓ 模型清单已更新${r.added?.length ? "，新增 " + r.added.join("、") : "（无新增）"}`;
        panel.status.style.color = "#7fd18a";
      } else {
        panel.status.textContent = `✗ ${r.error || "刷新失败"}`;
        panel.status.style.color = "#ff8080";
      }
    } catch (e) {
      panel.status.textContent = `✗ ${e}`;
      panel.status.style.color = "#ff8080";
    } finally {
      panel.btnModels.disabled = false;
    }
  });

  // 参数变化 → 立即重算预估；令牌变化 → 自动检测（防抖）
  let debounced = null;
  const origOnWidgetChanged = node.onWidgetChanged?.bind(node);
  node.onWidgetChanged = function (name, ...rest) {
    const r = origOnWidgetChanged ? origOnWidgetChanged(name, ...rest) : undefined;
    if (name === "model_search") applyModelFilter(this);
    if (name === "token") {
      clearTimeout(debounced);
      const self = this;
      debounced = setTimeout(() => checkToken(self, panel), 700);
    }
    if (["size_tier", "aspect", "steps", "n_samples", "smea",
         "img2img_strength"].includes(name)) refreshEstimate(this, panel);
    return r;
  };

  const origOnConnectionsChange = node.onConnectionsChange?.bind(node);
  node.onConnectionsChange = function (...args) {
    const r = origOnConnectionsChange ? origOnConnectionsChange.apply(this, args) : undefined;
    refreshEstimate(this, panel);
    return r;
  };

  applyModelFilter(node);
  // 节点载入时若已带令牌，自动检测一次（绿灯/红灯一眼可见）
  if ((wv(node, "token")?.value || "").trim()) {
    setTimeout(() => checkToken(node, panel), 600);
  }
}

app.registerExtension({
  name: "NovelAI.Cloud",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== NODE_NAME) return;
    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onNodeCreated ? onNodeCreated.apply(this, arguments) : undefined;
      setupNode(this);
      return r;
    };
  },
});
