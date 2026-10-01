/**
 * 选股参数配置模块 - 策略持有决策参数（观察期短/中/长 + 舍弃阈值 + 技术面负面指标）
 * 保存后写回 config/strategy_params.yaml，下次选股立即生效（无需重启）。
 */

// 负面指标输入框 id → 后端字段
const NEG_FIELDS = [
    ['neg-break-score', 'break_score'],
    ['neg-off5-pct', 'off5_pct'],
    ['neg-off5-score', 'off5_score'],
    ['neg-overboll-pct', 'overboll_pct'],
    ['neg-overboll-score', 'overboll_score'],
    ['neg-stagnation-pct', 'stagnation_pct'],
    ['neg-stagnation-score', 'stagnation_score'],
    ['neg-bigdrop-pct', 'bigdrop_pct'],
    ['neg-bigdrop-score', 'bigdrop_score'],
    ['neg-vol-mult', 'vol_mult'],
];

export async function loadSelectionConfig() {
    try {
        const resp = await fetch('/api/selection/config');
        const res = await resp.json();
        if (res && res.success) {
            const d = res.data || {};
            const opd = d.observe_period_days || {};
            if (document.getElementById('sel-short-days')) document.getElementById('sel-short-days').value = opd.short || '';
            if (document.getElementById('sel-mid-days')) document.getElementById('sel-mid-days').value = opd.mid || '';
            if (document.getElementById('sel-long-days')) document.getElementById('sel-long-days').value = opd.long || '';
            if (document.getElementById('sel-abandon-threshold')) document.getElementById('sel-abandon-threshold').value = d.abandon_score_threshold || '';
            // 负面指标参数
            const neg = d.negative_signals || {};
            NEG_FIELDS.forEach(([id, key]) => {
                const el = document.getElementById(id);
                if (el && neg[key] !== undefined) el.value = neg[key];
            });
        }
    } catch (e) {
        console.error('加载选股参数失败:', e);
    }
}

export async function saveSelectionConfig() {
    const msg = document.getElementById('sel-config-msg');
    if (msg) { msg.textContent = '保存中...'; msg.style.color = '#6b7280'; }
    const short = parseInt(document.getElementById('sel-short-days').value, 10);
    const mid = parseInt(document.getElementById('sel-mid-days').value, 10);
    const long = parseInt(document.getElementById('sel-long-days').value, 10);
    const thr = parseFloat(document.getElementById('sel-abandon-threshold').value);
    if (!short || short <= 0 || !mid || mid <= 0 || !long || long <= 0) {
        if (msg) msg.textContent = '观察期须为大于0的整数';
        return;
    }
    // 收集负面指标参数
    const payload = { short, mid, long, abandon_score_threshold: thr };
    let hasNeg = false;
    NEG_FIELDS.forEach(([id, key]) => {
        const el = document.getElementById(id);
        if (!el) return;
        const v = parseFloat(el.value);
        if (!isNaN(v)) { payload[key] = v; hasNeg = true; }
    });
    try {
        const resp = await fetch('/api/selection/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const res = await resp.json();
        if (msg) {
            if (res.success) {
                msg.textContent = '保存成功，下次选股立即生效';
                msg.style.color = '#16a34a';
            } else {
                msg.textContent = '保存失败: ' + (res.error || '未知错误');
                msg.style.color = '#dc2626';
            }
        }
        if (res.success) loadSelectionConfig();
    } catch (e) {
        if (msg) { msg.textContent = '保存失败: ' + e.message; msg.style.color = '#dc2626'; }
    }
}

export function initSelectionConfig() {
    const btn = document.getElementById('save-selection-config-btn');
    if (btn && !btn.dataset.bound) {
        btn.dataset.bound = '1';
        btn.addEventListener('click', saveSelectionConfig);
    }
    loadSelectionConfig();
}
