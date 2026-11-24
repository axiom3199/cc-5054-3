"""Self-contained HTML report for the workbench.

Embeds the summary JSON and references generated frame/visual images so the
user can box the small-text ROI, set the error cap and bit budget, and see
for every candidate: used block modes, rejection reasons, ROI zoom and
failed glyphs — all under the same output budget.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

PAGE_TEMPLATE = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>屏幕内容编码工作台</title>
<style>
  :root { --bg:#14171d; --panel:#1d222b; --line:#2c333f; --fg:#d8dee8;
          --mut:#8b95a5; --ok:#5fd08a; --bad:#ef6464; --warn:#e8c85a;
          --pal:#e8c85a; --ibc:#e86ec8; --intra:#5a8ce6; --inter:#78c878; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:13px/1.5 ui-monospace,Menlo,Consolas,monospace; }
  header { padding:12px 18px; border-bottom:1px solid var(--line);
           display:flex; gap:18px; align-items:center; flex-wrap:wrap; }
  header h1 { font-size:15px; margin:0; }
  .wrap { display:grid; grid-template-columns:minmax(420px,1.2fr) 1fr;
          gap:14px; padding:14px 18px; }
  .panel { background:var(--panel); border:1px solid var(--line);
           border-radius:8px; padding:12px; }
  .panel h2 { font-size:13px; margin:0 0 10px; color:var(--warn); }
  canvas { width:100%; image-rendering:pixelated; background:#000;
           border:1px solid var(--line); border-radius:6px; cursor:crosshair; }
  .controls { display:grid; grid-template-columns:repeat(2,1fr); gap:8px 14px;
              margin-top:10px; }
  label { color:var(--mut); display:flex; flex-direction:column; gap:3px; }
  input, select { background:#10141a; color:var(--fg); border:1px solid var(--line);
                  border-radius:5px; padding:5px 7px; font:inherit; }
  button { background:#2a3342; color:var(--fg); border:1px solid var(--line);
           border-radius:5px; padding:6px 12px; cursor:pointer; font:inherit; }
  button:hover { border-color:var(--warn); }
  table { width:100%; border-collapse:collapse; font-size:12px; }
  th,td { text-align:left; padding:5px 7px; border-bottom:1px solid var(--line); }
  th { color:var(--mut); font-weight:normal; }
  tr.chosen { background:rgba(95,208,138,.12); }
  tr.rejected td { color:var(--mut); }
  .tag { display:inline-block; padding:1px 6px; border-radius:4px; font-size:11px; }
  .tag.ok { background:rgba(95,208,138,.18); color:var(--ok); }
  .tag.bad { background:rgba(239,100,100,.18); color:var(--bad); }
  .modes span { display:inline-block; margin:2px 4px 2px 0; padding:1px 6px;
                border-radius:4px; font-size:11px; }
  .m-INTRA { background:rgba(90,140,230,.25); color:var(--intra); }
  .m-INTER { background:rgba(120,200,120,.22); color:var(--inter); }
  .m-PALETTE { background:rgba(232,200,90,.22); color:var(--pal); }
  .m-IBC { background:rgba(232,110,200,.22); color:var(--ibc); }
  .zoom { image-rendering:pixelated; width:100%; border:1px solid var(--line);
          border-radius:6px; margin-top:8px; }
  .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
  .small { font-size:11px; color:var(--mut); }
  .pill { font-size:11px; padding:2px 8px; border-radius:10px;
          background:#2a3342; }
  .row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  .reason { color:var(--bad); font-size:11px; }
  img.vis { width:100%; image-rendering:pixelated; border:1px solid var(--line);
            border-radius:6px; margin-top:8px; }
  .tabs { display:flex; gap:6px; margin-bottom:8px; flex-wrap:wrap; }
  .tabs button.active { border-color:var(--warn); color:var(--warn); }
</style>
</head>
<body>
<header>
  <h1>屏幕内容编码工作台</h1>
  <span class="pill" id="hist-pill"></span>
  <span class="pill" id="frame-pill"></span>
  <span class="small">AV1 / libaom 3.9.1 · 块模式来自插桩编码器 AOM_BLOCK_LOG · 码流证据来自 dump_obu</span>
</header>
<div class="wrap">
  <div>
    <div class="panel">
      <h2>1 · 原始桌面帧（拖拽框选小字号彩字 ROI）</h2>
      <canvas id="frame" width="__W__" height="__H__"></canvas>
      <div class="controls">
        <label>变体
          <select id="variant">
            <option value="text">text（细笔画字形）</option>
            <option value="video">video（运动色块）</option>
          </select>
        </label>
        <label>评估帧
          <input id="frame-no" type="number" min="0" value="0">
        </label>
        <label>ROI 误差上限 PSNR (dB)
          <input id="cap-psnr" type="number" step="0.5" value="__CAP_PSNR__">
        </label>
        <label>笔画缺口上限 (0-1)
          <input id="cap-gap" type="number" step="0.01" value="__CAP_GAP__">
        </label>
        <label>总码量预算 (bytes)
          <input id="cap-bits" type="number" step="100" value="__BUDGET__">
        </label>
        <label style="justify-content:end">
          <span style="height:8px"></span>
          <button id="apply">应用并重新判定</button>
        </label>
      </div>
      <p class="small" id="roi-readout"></p>
    </div>
    <div class="panel" style="margin-top:14px">
      <h2>4 · 逐块放大回读（相同输出预算）</h2>
      <div class="tabs" id="cand-tabs"></div>
      <div id="vis-slot"></div>
    </div>
  </div>
  <div>
    <div class="panel">
      <h2>2 · 候选版本与否决原因（预算/误差双约束）</h2>
      <table id="cand-table"><thead><tr>
        <th>候选</th><th>bytes</th><th>全画面</th><th>ROI</th><th>缺口</th>
        <th>判定</th></tr></thead><tbody></tbody></table>
      <p class="small" id="pick-readout"></p>
    </div>
    <div class="panel" style="margin-top:14px">
      <h2>3 · 实际使用的块模式（ROI 内面积，px）</h2>
      <div class="modes" id="mode-readout"></div>
      <h2 style="margin-top:14px">色度采样对照</h2>
      <table id="sample-table"><thead><tr>
        <th>配置</th><th>ΔROI(dB)</th><th>Δ全画面</th><th>归因</th></tr></thead>
        <tbody></tbody></table>
    </div>
    <div class="panel" style="margin-top:14px">
      <h2>码流证据（OBU / 帧字节）</h2>
      <div id="bitstream" class="small"></div>
    </div>
  </div>
</div>
<script>
const SUMMARY = __SUMMARY__;
const IMG = __IMG_INDEX__;
const W = __W__, H = __H__;
let roi = SUMMARY.roi_box.slice();
let drag = null;

const cv = document.getElementById('frame');
const ctx = cv.getContext ? cv.getContext('2d') : null;
function loadFrame() {
  if (!ctx) { drawRoi(); return; }
  const v = document.getElementById('variant').value;
  const f = Number(document.getElementById('frame-no').value) || 0;
  const key = `scene_${v}/frames/f${String(f).padStart(3,'0')}.png`;
  const im = new Image();
  im.onload = () => { ctx.drawImage(im,0,0); drawRoi(); };
  im.src = IMG[key] || IMG[Object.keys(IMG)[0]];
}
function drawRoi() {
  if (!ctx) {
    document.getElementById('roi-readout').textContent =
      `ROI = [${roi.join(', ')}]  (x0,y0,x1,y1)`;
    return;
  }
  ctx.save();
  ctx.strokeStyle = '#e8c85a'; ctx.lineWidth = 2;
  ctx.setLineDash([6,4]);
  ctx.strokeRect(roi[0],roi[1],roi[2]-roi[0],roi[3]-roi[1]);
  ctx.restore();
  document.getElementById('roi-readout').textContent =
    `ROI = [${roi.join(', ')}]  (x0,y0,x1,y1)`;
}
cv.addEventListener('pointerdown', e => {
  const r = cv.getBoundingClientRect();
  drag = [(e.clientX-r.left)*W/r.width, (e.clientY-r.top)*H/r.height];
});
cv.addEventListener('pointerup', e => {
  if (!drag) return;
  const r = cv.getBoundingClientRect();
  const x = (e.clientX-r.left)*W/r.width, y = (e.clientY-r.top)*H/r.height;
  roi = [Math.round(Math.min(drag[0],x)), Math.round(Math.min(drag[1],y)),
         Math.round(Math.max(drag[0],x)), Math.round(Math.max(drag[1],y))];
  drag = null; drawRoi();
});
['variant','frame-no'].forEach(id =>
  document.getElementById(id).addEventListener('change', loadFrame));

function cands() {
  const v = document.getElementById('variant').value;
  return SUMMARY.candidates.filter(c => c.variant === v);
}
function judge(c, cap) {
  const reasons = [];
  if (c.bitstream_bytes > cap.bits) reasons.push('over_budget');
  if (c.metrics.psnr_roi < cap.psnr) reasons.push('roi_psnr_below_cap');
  if (c.metrics.stroke_gap_ratio > cap.gap) reasons.push('stroke_gap_over_cap');
  return reasons;
}
function render() {
  const cap = {
    psnr: Number(document.getElementById('cap-psnr').value),
    gap: Number(document.getElementById('cap-gap').value),
    bits: Number(document.getElementById('cap-bits').value),
  };
  const list = cands().map(c => ({...c, reasons: judge(c, cap)}));
  const feasible = list.filter(c => c.reasons.length === 0);
  const chosen = feasible.length
    ? feasible.sort((a,b) => a.bitstream_bytes - b.bitstream_bytes)[0]
    : list.sort((a,b) => a.reasons.length - b.reasons.length)[0];
  const tb = document.querySelector('#cand-table tbody');
  tb.innerHTML = '';
  list.forEach(c => {
    const ok = c.reasons.length === 0;
    const tr = document.createElement('tr');
    tr.dataset.tag = c.tag;
    if (c.tag === chosen.tag) tr.className = 'chosen';
    if (!ok) tr.classList.add('rejected');
    tr.style.cursor = 'pointer';
    tr.innerHTML = `<td>${c.pix_fmt}/${c.config}</td>
      <td>${c.bitstream_bytes}</td>
      <td>${c.metrics.psnr_full.toFixed(1)}</td>
      <td>${c.metrics.psnr_roi.toFixed(1)}</td>
      <td>${c.metrics.stroke_gap_ratio.toFixed(3)}</td>
      <td><span class="tag ${ok?'ok':'bad'}">${ok?'FEASIBLE':'REJECTED'}</span>
          <div class="reason">${c.reasons.join(', ')}</div></td>`;
    tr.onclick = () => showVis(c.tag);
    tb.appendChild(tr);
  });
  document.getElementById('pick-readout').textContent =
    feasible.length
      ? `选定 ${chosen.tag}：预算内 ROI 达标的最小码量版本`
      : `无可行候选；展示违约最少的 ${chosen.tag}（所有候选至少违反一项约束）`;
  // mode area in ROI
  const mr = document.getElementById('mode-readout');
  mr.innerHTML = '';
  Object.entries(chosen.roi_mode_area_px).sort().forEach(([k,val]) => {
    const s = document.createElement('span');
    s.className = `m-${k}`; s.textContent = `${k}: ${val}px`;
    mr.appendChild(s);
  });
  // sampling table
  const v = document.getElementById('variant').value;
  const st = document.querySelector('#sample-table tbody');
  st.innerHTML = '';
  SUMMARY.sampling_attribution.filter(a => a.variant === v).forEach(a => {
    st.innerHTML += `<tr><td>${a.config}</td>
      <td>${a.delta_roi_db.toFixed(1)}</td><td>${a.delta_full_db.toFixed(1)}</td>
      <td>${a.attribution.replace('_',' ')}</td></tr>`;
  });
  // bitstream evidence
  const f = Number(document.getElementById('frame-no').value) || 0;
  const fs = chosen.frame_status || {};
  document.getElementById('bitstream').innerHTML =
    `帧 ${f}：${fs.bytes ?? '-'} 字节 · 编码器 PSNR Y/U/V = ` +
    `${fs.psnr_y ?? '-'} / ${fs.psnr_u ?? '-'} / ${fs.psnr_v ?? '-'}<br>` +
    `总码流 ${chosen.bitstream_bytes} 字节，bpp=${chosen.bits_per_pixel.toFixed(4)}；` +
    `逐块/OBU 日志见 runs/${chosen.tag}/（blocks.log, obu.txt, aomenc.log）`;
  renderTabs(list, chosen.tag);
  showVis(chosen.tag);
}
function renderTabs(list, active) {
  const tabs = document.getElementById('cand-tabs');
  tabs.innerHTML = '';
  list.forEach(c => {
    const b = document.createElement('button');
    b.textContent = `${c.pix_fmt}/${c.config}`;
    b.dataset.tag = c.tag;
    if (c.tag === active) b.className = 'active';
    b.onclick = () => showVis(c.tag);
    tabs.appendChild(b);
  });
}
function showVis(tag) {
  document.querySelectorAll('#cand-tabs button').forEach(b =>
    b.classList.toggle('active', b.dataset.tag === tag));
  const slot = document.getElementById('vis-slot');
  const c = SUMMARY.candidates.find(x => x.tag === tag);
  const keys = ['block_overlay_roi','roi_zoom','damage_map','glyph_panel','block_overlay'];
  const titles = ['ROI 块模式','ROI 放大','损伤热图','还原失败字形（红框）','全帧块模式'];
  slot.innerHTML = '<div class="grid2">' + keys.map((k,i) =>
    `<div><div class="small">${titles[i]}</div>` +
    (c.visuals && IMG[c.visuals[k]]
      ? `<img class="vis" src="${IMG[c.visuals[k]]}">`
      : '<div class="small">（该候选无此视图）</div>') + `</div>`).join('') + '</div>';
}
document.getElementById('apply').onclick = render;
document.getElementById('hist-pill').textContent =
  '两变体色块分布 ' + (SUMMARY.histogram_match_between_variants ? '精确一致' : '不一致');
document.getElementById('frame-pill').textContent =
  `${SUMMARY.n_frames} 帧 · 评估帧 ${SUMMARY.eval_frame}`;
loadFrame(); render();
</script>
</body>
</html>
"""


def _data_uri(path: Path) -> str:
    mime = "image/png"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def build_report(work_root: Path) -> Path:
    work_root = Path(work_root)
    summary = json.loads((work_root / "summary.json").read_text())

    img_index: dict[str, str] = {}
    for p in work_root.rglob("*.png"):
        rel = str(p.relative_to(work_root))
        # Skip per-run vis here only if too large; embed everything for
        # self-contained portability.
        img_index[rel] = _data_uri(p)

    html = (PAGE_TEMPLATE
            .replace("__SUMMARY__", json.dumps(summary))
            .replace("__IMG_INDEX__", json.dumps(img_index))
            .replace("__W__", str(summary.get("width", 640)))
            .replace("__H__", str(summary.get("height", 480)))
            .replace("__CAP_PSNR__", str(summary["cap"]["roi_psnr_min"]))
            .replace("__CAP_GAP__", str(summary["cap"]["stroke_gap_max"]))
            .replace("__BUDGET__", str(summary["cap"]["bit_budget_bytes"])))
    out = work_root / "report.html"
    out.write_text(html)
    return out
