/**
 * 选股排名相关功能模块
 */

/**
 * 初始化选股排名页面
 */
export function initStockRankingPage() {
    console.log('初始化选股排名页面');
    // 加载可用日期
    loadRankingDates('stock-ranking-date');
    // 重置结果区域
    document.getElementById('stock-ranking-result').innerHTML = '';
}

/**
 * 初始化排名跟踪页面
 */
export function initRankingTrackPage() {
    console.log('初始化排名跟踪页面');
    // 加载可用日期
    loadRankingDates('ranking-track-date');
    // 重置结果区域
    document.getElementById('ranking-track-result').innerHTML = '';
}

/**
 * 加载排名可用日期
 * @param {string} dateInputId - 日期输入框ID
 */
export async function loadRankingDates(dateInputId) {
    try {
        const response = await fetch('/api/ranking/dates');
        const result = await response.json();
        
        if (result.success && result.data) {
            const dates = result.data;
            if (dates.length > 0) {
                // 设置默认日期为最新的日期
                const dateInput = document.getElementById(dateInputId);
                if (dateInput) {
                    dateInput.value = dates[0];
                }
            }
        }
    } catch (error) {
        console.error('加载排名日期失败:', error);
    }
}

/**
 * 生成选股排名
 */
export async function generateRanking() {
    const dateInput = document.getElementById('stock-ranking-date');
    const resultContainer = document.getElementById('stock-ranking-result');
    
    if (!dateInput || !resultContainer) return;
    
    const selectionDate = dateInput.value;
    if (!selectionDate) {
        alert('请选择选股日期');
        return;
    }
    
    // 显示加载状态
    resultContainer.innerHTML = '<p class="loading">正在生成排名，请稍候...</p>';
    
    try {
        const response = await fetch('/api/ranking/generate', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ selection_date: selectionDate })
        });
        
        const result = await response.json();
        
        if (result.success) {
            renderRankingResult(result.data, resultContainer, selectionDate);
        } else {
            resultContainer.innerHTML = `<p class="loading text-danger">生成排名失败: ${result.error}</p>`;
        }
    } catch (error) {
        console.error('生成排名异常:', error);
        resultContainer.innerHTML = `<p class="loading text-danger">生成排名失败: ${error.message}</p>`;
    }
}

/**
 * 跟踪排名
 */
export async function trackRanking() {
    const dateInput = document.getElementById('ranking-track-date');
    const topNSelect = document.getElementById('ranking-track-topn');
    const sortSelect = document.getElementById('ranking-track-sort');
    const resultContainer = document.getElementById('ranking-track-result');
    
    if (!dateInput || !topNSelect || !resultContainer) return;
    
    const selectionDate = dateInput.value;
    const topN = parseInt(topNSelect.value);
    
    if (!selectionDate) {
        alert('请选择选股日期');
        return;
    }
    
    // 显示加载状态
    resultContainer.innerHTML = '<p class="loading">正在跟踪排名，请稍候...</p>';
    
    try {
        const sortBy = sortSelect ? sortSelect.value : 'yield';
        const response = await fetch(`/api/ranking/track?selection_date=${selectionDate}&top_n=${topN}&sort_by=${sortBy}`);
        const result = await response.json();
        
        if (result.success) {
            renderTrackingResult(result.data, resultContainer, selectionDate);
        } else {
            resultContainer.innerHTML = `<p class="loading text-danger">跟踪排名失败: ${result.error}</p>`;
        }
    } catch (error) {
        console.error('跟踪排名异常:', error);
        resultContainer.innerHTML = `<p class="loading text-danger">跟踪排名失败: ${error.message}</p>`;
    }
}

/**
 * 渲染排名结果
 * @param {Array} data - 排名数据
 * @param {HTMLElement} container - 结果容器
 */
export function renderRankingResult(data, container, selectionDate) {
    if (!data || data.length === 0) {
        container.innerHTML = '<p class="text-muted">暂无排名数据</p>';
        return;
    }
    
    let html = `
        <div class="table-responsive">
            <table class="table table-striped">
                <thead>
                    <tr>
                        <th>排名</th>
                        <th>股票代码</th>
                        <th>股票名称</th>
                        <th>评分</th>
                        <th>行业</th>
                        <th>板块</th>
                        <th>选入价</th>
                        <th>止盈/止损建议</th>
                        <th>卖出原因</th>
                        <th>卖出后收益</th>
                    </tr>
                </thead>
                <tbody>
    `;
    
    data.forEach((item, index) => {
        // 防御性代码，处理可能的undefined值
        const score = item.score || 0;
        const selectionPrice = item.selection_price || 0;
        
        html += `
            <tr>
                <td>${index + 1}</td>
                <td><a href="javascript:void(0)" onclick="viewStockDetail('${item.stock_code}')" class="stock-link">${item.stock_code}</a></td>
                <td>${item.stock_name}</td>
                <td><a href="javascript:void(0)" onclick="showScoreDetail('${item.stock_code}', '${selectionDate}')" class="score-link">${score.toFixed(2)}</a></td>
                <td>${item.industry || '-'}</td>
                <td>${item.sector || '-'}</td>
                <td>¥${selectionPrice.toFixed(2)}</td>
                <td>
                    ${item.sell_status === '卖出' ? ('🔴 建议卖出' + (item.sell_price ? ' @ ¥' + Number(item.sell_price).toFixed(2) : '（次日开盘）')) : '🟢 持有'}
                </td>
                <td style="font-size: 12px; max-width: 180px;">${item.sell_status === '卖出' ? (item.sell_reason || '-') : '-'}</td>
                <td class="${item.sell_status === '卖出' && item.sell_yield !== null && item.sell_yield !== undefined && item.sell_yield >= 0 ? 'text-danger' : 'text-success'}">${item.sell_status === '卖出' ? (item.sell_yield !== null && item.sell_yield !== undefined ? Number(item.sell_yield).toFixed(2) + '%' : '待次日收盘') : '-'}</td>
            </tr>
        `;
    });
    
    html += `
                </tbody>
            </table>
        </div>
    `;
    
    container.innerHTML = html;
}

/**
 * 渲染排名跟踪结果
 * @param {Array} data - 排名数据
 * @param {HTMLElement} container - 结果容器
 */
export function renderTrackingResult(data, container, selectionDate) {
    if (!data || data.length === 0) {
        container.innerHTML = '<p class="text-muted">暂无排名数据</p>';
        return;
    }
    
    let html = `
        <div class="table-responsive">
            <table class="table table-striped">
                <thead>
                    <tr>
                        <th>排名</th>
                        <th>股票代码</th>
                        <th>股票名称</th>
                        <th>评分</th>
                        <th>评分排名</th>
                        <th>行业</th>
                        <th>选入价</th>
                        <th>当前价</th>
                        <th>收益率</th>
                        <th>最高价格</th>
                        <th>最高收益</th>
                        <th>入选策略及说明</th>
                    </tr>
                </thead>
                <tbody>
    `;
    
    // 计算统计数据
    let totalReturn = 0;
    let winCount = 0;
    let maxReturn = -Infinity;
    let minReturn = Infinity;
    let totalMaxReturn = 0;
    
    data.forEach(item => {
        // 防御性代码，处理可能的undefined值
        const score = item.score || 0;
        const selectionPrice = item.selection_price || 0;
        const currentPrice = item.current_price || 0;
        const currentReturn = item.current_yield || 0;
        const highestPrice = item.highest_price || 0;
        const highestReturn = item.highest_yield || 0;
        
        // 累计统计数据
        totalReturn += currentReturn;
        if (currentReturn > 0) {
            winCount++;
        }
        maxReturn = Math.max(maxReturn, currentReturn);
        minReturn = Math.min(minReturn, currentReturn);
        totalMaxReturn += highestReturn;
        
        html += `
            <tr>
                <td>${item.rank_position}</td>
                <td><a href="javascript:void(0)" onclick="viewStockDetail('${item.stock_code}')" class="stock-link">${item.stock_code}</a></td>
                <td>${item.stock_name}</td>
                <td><a href="javascript:void(0)" onclick="showScoreDetail('${item.stock_code}', '${selectionDate}')" class="score-link">${score.toFixed(2)}</a></td>
                <td>${item.score_rank || '-'}</td>
                <td>${item.industry || '-'}</td>
                <td>¥${selectionPrice.toFixed(2)}</td>
                <td>¥${currentPrice.toFixed(2)}</td>
                <td class="${currentReturn >= 0 ? 'text-danger' : 'text-success'}">${currentReturn.toFixed(2)}%</td>
                <td>¥${highestPrice.toFixed(2)}</td>
                <td class="${highestReturn >= 0 ? 'text-danger' : 'text-success'}">${highestReturn.toFixed(2)}%</td>
                <td style="max-width: 280px; font-size: 12px;">${item.strategies || '-'}</td>
            </tr>
        `;
    });
    
    // 计算平均值和胜率
    const avgReturn = (totalReturn / data.length).toFixed(2);
    const winRate = ((winCount / data.length) * 100).toFixed(2);
    const avgMaxReturn = (totalMaxReturn / data.length).toFixed(2);
    
    // 处理无穷大的情况
    const displayMaxReturn = maxReturn === -Infinity ? '-' : maxReturn.toFixed(2) + '%';
    const displayMinReturn = minReturn === Infinity ? '-' : minReturn.toFixed(2) + '%';
    
    html += `
                </tbody>
            </table>
        </div>
        
        <!-- 统计说明 -->
        <div style="margin-top: 20px; padding: 15px; background: #f9fafb; border-radius: 8px; border-left: 4px solid #3b82f6;">
            <div style="font-size: 14px; color: #374151; line-height: 1.8;">
                <strong>📊 Top${data.length}选入以来统计：</strong>
                平均收益 <span style="color: #3b82f6; font-weight: 600;">${avgReturn}%</span> | 
                胜率 <span style="color: #10b981; font-weight: 600;">${winRate}%</span> | 
                最高收益 <span style="color: #059669; font-weight: 600;">${displayMaxReturn}</span> | 
                最低收益 <span style="color: #dc2626; font-weight: 600;">${displayMinReturn}</span> | 
                最高涨幅平均 <span style="color: #f59e0b; font-weight: 600;">${avgMaxReturn}%</span>
            </div>
        </div>
    `;
    
    container.innerHTML = html;
}

/**
 * 重新生成排名 - 用于修复评分不完整或为0的情况
 */
export async function regenerateRanking() {
    const dateInput = document.getElementById('stock-ranking-date');
    const resultContainer = document.getElementById('stock-ranking-result');
    
    if (!dateInput || !resultContainer) return;
    
    const selectionDate = dateInput.value;
    if (!selectionDate) {
        alert('请选择选股日期');
        return;
    }
    
    // 确认操作
    if (!confirm('确定要重新生成排名吗？这将重新计算所有评分为0的股票。')) {
        return;
    }
    
    // 显示加载状态
    resultContainer.innerHTML = '<p class="loading">正在重新生成排名，请稍候...</p>';
    
    try {
        const response = await fetch('/api/ranking/regenerate', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                selection_date: selectionDate,
                force_recalculate: false  // 默认只重新计算评分为0的股票
            })
        });
        
        const result = await response.json();
        
        if (result.success) {
            // 显示重新生成结果
            const data = result.data || {};
            resultContainer.innerHTML = `
                <div style="padding: 15px; background: #d1fae5; border: 1px solid #6ee7b7; border-radius: 8px; margin-bottom: 20px;">
                    <h5 style="color: #065f46; margin-bottom: 10px;">✅ 排名重新生成成功</h5>
                    <p style="color: #047857; margin: 5px 0;">
                        <strong>总股票数：</strong> ${data.total || 0}
                    </p>
                    <p style="color: #047857; margin: 5px 0;">
                        <strong>重新计算：</strong> ${data.recalculated || 0}
                    </p>
                    <p style="color: #047857; margin: 5px 0;">
                        <strong>失败数量：</strong> ${data.failed || 0}
                    </p>
                    <p style="color: #047857; margin: 5px 0;">
                        <strong>消息：</strong> ${result.message || ''}
                    </p>
                </div>
                <p class="text-muted">请点击"生成排名"按钮查看最新的排名结果。</p>
            `;
        } else {
            resultContainer.innerHTML = `
                <div style="padding: 15px; background: #fee2e2; border: 1px solid #fca5a5; border-radius: 8px;">
                    <h5 style="color: #7f1d1d; margin-bottom: 10px;">❌ 重新生成排名失败</h5>
                    <p style="color: #991b1b;">${result.message || '未知错误'}</p>
                </div>
            `;
        }
    } catch (error) {
        console.error('重新生成排名异常:', error);
        resultContainer.innerHTML = `
            <div style="padding: 15px; background: #fee2e2; border: 1px solid #fca5a5; border-radius: 8px;">
                <h5 style="color: #7f1d1d; margin-bottom: 10px;">❌ 重新生成排名失败</h5>
                <p style="color: #991b1b;">${error.message || '网络错误'}</p>
            </div>
        `;
    }
}

/**
 * 强制重新生成排名 - 重新计算所有股票的评分
 */
export async function forceRegenerateRanking() {
    const dateInput = document.getElementById('stock-ranking-date');
    const resultContainer = document.getElementById('stock-ranking-result');
    
    if (!dateInput || !resultContainer) return;
    
    const selectionDate = dateInput.value;
    if (!selectionDate) {
        alert('请选择选股日期');
        return;
    }
    
    // 确认操作
    if (!confirm('确定要强制重新生成排名吗？这将重新计算所有股票的评分，可能需要较长时间。')) {
        return;
    }
    
    // 显示加载状态
    resultContainer.innerHTML = '<p class="loading">正在强制重新生成排名，请稍候...</p>';
    
    try {
        const response = await fetch('/api/ranking/regenerate', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                selection_date: selectionDate,
                force_recalculate: true  // 强制重新计算所有股票
            })
        });
        
        const result = await response.json();
        
        if (result.success) {
            // 显示重新生成结果
            const data = result.data || {};
            resultContainer.innerHTML = `
                <div style="padding: 15px; background: #d1fae5; border: 1px solid #6ee7b7; border-radius: 8px; margin-bottom: 20px;">
                    <h5 style="color: #065f46; margin-bottom: 10px;">✅ 排名强制重新生成成功</h5>
                    <p style="color: #047857; margin: 5px 0;">
                        <strong>总股票数：</strong> ${data.total || 0}
                    </p>
                    <p style="color: #047857; margin: 5px 0;">
                        <strong>重新计算：</strong> ${data.recalculated || 0}
                    </p>
                    <p style="color: #047857; margin: 5px 0;">
                        <strong>失败数量：</strong> ${data.failed || 0}
                    </p>
                    <p style="color: #047857; margin: 5px 0;">
                        <strong>消息：</strong> ${result.message || ''}
                    </p>
                </div>
                <p class="text-muted">请点击"生成排名"按钮查看最新的排名结果。</p>
            `;
        } else {
            resultContainer.innerHTML = `
                <div style="padding: 15px; background: #fee2e2; border: 1px solid #fca5a5; border-radius: 8px;">
                    <h5 style="color: #7f1d1d; margin-bottom: 10px;">❌ 强制重新生成排名失败</h5>
                    <p style="color: #991b1b;">${result.message || '未知错误'}</p>
                </div>
            `;
        }
    } catch (error) {
        console.error('强制重新生成排名异常:', error);
        resultContainer.innerHTML = `
            <div style="padding: 15px; background: #fee2e2; border: 1px solid #fca5a5; border-radius: 8px;">
                <h5 style="color: #7f1d1d; margin-bottom: 10px;">❌ 强制重新生成排名失败</h5>
                <p style="color: #991b1b;">${error.message || '网络错误'}</p>
            </div>
        `;
    }
}

/**
 * 初始化选股跟踪页面（统一选股跟踪：合并原选股排名 + 排名跟踪）
 */
export function initSelectionTrackPage() {
    console.log('初始化选股跟踪页面');
    loadTrackStrategyOptions();
    querySelectionTrack();
    // 绑定查询按钮（覆盖式赋值，避免重复叠加）
    const qb = document.getElementById('track-query-btn');
    if (qb) qb.onclick = () => querySelectionTrack(1);
    // 绑定重新生成按钮
    const rb = document.getElementById('track-regenerate-btn');
    if (rb) rb.onclick = () => regenerateSelectionTrack();
    // 股票名输入框回车触发查询
    const sn = document.getElementById('track-stock-name');
    if (sn) sn.onkeydown = (e) => { if (e.key === 'Enter') querySelectionTrack(1); };
}

/**
 * 加载选股跟踪策略下拉（value=类名，文本=中文名，与 strategy_hold_record.strategy_name 一致）
 */
export async function loadTrackStrategyOptions() {
    const select = document.getElementById('track-strategy-filter');
    if (!select) return;
    try {
        const response = await fetch('/api/strategies');
        const result = await response.json();
        if (result.success && Array.isArray(result.data)) {
            select.innerHTML = '<option value="">全部策略</option>';
            result.data.forEach(st => {
                const opt = document.createElement('option');
                opt.value = st.name || '';          // 类名（存库标识）
                opt.textContent = st.display_name || st.name || '';
                select.appendChild(opt);
            });
        }
    } catch (error) {
        console.error('加载策略列表失败:', error);
    }
}

/**
 * 查询统一选股跟踪
 */
export async function querySelectionTrack(page = 1) {
    const btn = document.getElementById('track-query-btn');
    const btnText = document.getElementById('track-query-btn-text');
    const loading = document.getElementById('track-query-loading');
    const resultContainer = document.getElementById('selection-track-result');
    if (!resultContainer) return;

    const holdStatus = document.getElementById('track-status-filter')?.value || '';
    const strategyName = document.getElementById('track-strategy-filter')?.value || '';
    const stockName = document.getElementById('track-stock-name')?.value?.trim() || '';
    const sortBy = document.getElementById('track-sort-by')?.value || 'yield';
    const sortOrder = document.getElementById('track-sort-order')?.value || 'desc';

    if (btn) { btn.disabled = true; if (btnText) btnText.textContent = '查询中...'; }
    if (loading) loading.style.display = 'inline';
    resultContainer.innerHTML = '<p class="loading">正在查询选股跟踪，请稍候...</p>';

    const params = new URLSearchParams();
    params.append('page', page);
    params.append('limit', 20);
    if (holdStatus) params.append('hold_status', holdStatus);
    if (strategyName) params.append('strategy_name', strategyName);
    if (stockName) params.append('stock_name', stockName);
    params.append('sort_by', sortBy);
    params.append('sort_order', sortOrder);

    try {
        const response = await fetch(`/api/selection-track?${params.toString()}`);
        const result = await response.json();
        if (result.success) {
            renderSelectionTrackTable(result.data);
            renderSelectionTrackStats(result.total, result.page, result.limit);
            renderSelectionTrackPagination(result.total, result.page, result.limit);
        } else {
            resultContainer.innerHTML = `<p class="loading text-danger">查询选股跟踪失败: ${result.error}</p>`;
        }
    } catch (error) {
        console.error('查询选股跟踪异常:', error);
        resultContainer.innerHTML = `<p class="loading text-danger">查询选股跟踪失败: ${error.message}</p>`;
    } finally {
        if (btn) { btn.disabled = false; if (btnText) btnText.textContent = '查询'; }
        if (loading) loading.style.display = 'none';
    }
}

/**
 * 重新生成选股跟踪：重算当前持有标的的评分、强弱标志与卖出信号并回写
 */
export async function regenerateSelectionTrack() {
    if (!confirm('确定要重新生成选股跟踪吗？将重新计算当前所有持有标的的评分、强弱标志与卖出信号（可能需要较长时间）。')) {
        return;
    }
    const btn = document.getElementById('track-regenerate-btn');
    const btnText = document.getElementById('track-regenerate-btn-text');
    const loading = document.getElementById('track-regenerate-loading');
    const resultContainer = document.getElementById('selection-track-result');

    if (btn) { btn.disabled = true; if (btnText) btnText.textContent = '重新生成中...'; }
    if (loading) loading.style.display = 'inline';
    if (resultContainer) resultContainer.innerHTML = '<p class="loading">正在重新计算评分 / 强弱标志 / 卖出信号，请稍候...</p>';

    try {
        const response = await fetch('/api/selection-track/regenerate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({})
        });
        const result = await response.json();
        if (result.success) {
            const soldN = result.sold_count || 0;
            const tip = result.total > 0
                ? `✅ 重新生成完成：共重算 ${result.total} 条持有标的，其中 <span style="color:#dc2626;font-weight:600;">${soldN} 条命中卖出信号</span>（评分日期 ${result.score_date}）`
                : '暂无持有标的可重新生成（先执行选股产生持有记录）';
            if (resultContainer) resultContainer.innerHTML = `<div style="padding:15px; background:#d1fae5; border:1px solid #6ee7b7; border-radius:8px; margin-bottom:20px; color:#065f46;">${tip}</div>`;
            // 刷新列表
            querySelectionTrack(1);
        } else {
            if (resultContainer) resultContainer.innerHTML = `<p class="loading text-danger">重新生成失败: ${result.error}</p>`;
        }
    } catch (error) {
        console.error('重新生成选股跟踪异常:', error);
        if (resultContainer) resultContainer.innerHTML = `<p class="loading text-danger">重新生成失败: ${error.message}</p>`;
    } finally {
        if (btn) { btn.disabled = false; if (btnText) btnText.textContent = '重新生成'; }
        if (loading) loading.style.display = 'none';
    }
}

/**
 * 渲染选股跟踪表格
 */
export function renderSelectionTrackTable(data) {
    const container = document.getElementById('selection-track-result');
    if (!container) return;
    if (!data || data.length === 0) {
        container.innerHTML = '<p class="text-muted">暂无选股跟踪数据</p>';
        return;
    }

    const headers = ['股票名称', '选入日期', '退出日期', '评分', '买入价', '当前价格', '卖出价', '累计收益率', '选入策略', '强弱标志', '当前状态', '操作'];
    const rows = data.map(item => {
        const ret = item.cum_return;
        const retColor = ret >= 0 ? '#dc2626' : '#16a34a';
        const status = item.status || '--';
        const statusColor = status.indexOf('清仓') >= 0 ? '#6b7280' : '#2563eb';
        const entryDate = item.entry_date || '--';
        const exitDate = item.exit_date || '--';
        const sellPrice = item.sell_price != null ? '¥' + Number(item.sell_price).toFixed(2) : '--';
        return `
            <tr>
                <td><a href="javascript:void(0)" onclick="viewStockDetail('${escapeHtml(item.stock_code)}')" class="stock-link" style="color:#2563eb; text-decoration:none; cursor:pointer; font-weight:600;">${escapeHtml(item.stock_name || '--')}</a></td>
                <td>${escapeHtml(entryDate)}</td>
                <td>${escapeHtml(exitDate)}</td>
                <td><a href="javascript:void(0)" onclick="showScoreDetail('${escapeHtml(item.stock_code)}', '${escapeHtml(item.entry_date || '')}')" class="score-link" style="color:#2563eb; cursor:pointer;">${item.score != null ? Number(item.score).toFixed(2) : '--'}</a></td>
                <td>¥${item.buy_price != null ? Number(item.buy_price).toFixed(2) : '--'}</td>
                <td>¥${item.current_price != null ? Number(item.current_price).toFixed(2) : '--'}</td>
                <td>${sellPrice}</td>
                <td style="color:${retColor}; font-weight:600;">${ret != null ? Number(ret).toFixed(2) + '%' : '--'}</td>
                <td>${(item.strategy_name || '--').split(' + ').map(n => `<span style="background:#dbeafe; color:#0c4a6e; padding:3px 8px; border-radius:4px; font-size:12px; font-weight:600; display:inline-block; margin:2px 3px;">${escapeHtml(n)}</span>`).join('')}</td>
                <td>${strengthLabelCell(item.strength_label)}</td>
                <td><span style="color:${statusColor}; font-weight:600;">${escapeHtml(status)}</span></td>
                <td style="display:flex; gap:6px; align-items:center;">
                    <button class="btn btn-sm" style="padding:4px 10px; font-size:12px; background:#ecfeff; color:#0f766e; border:1px solid #99f6e4; border-radius:4px; cursor:pointer;" onclick="openTrackEditModal('${escapeHtml(item.stock_code)}','${escapeHtml(item.strategy_key || '')}','${escapeHtml(item.stock_name || '')}','${escapeHtml(item.exit_date || '')}','${item.sell_price != null ? item.sell_price : ''}','${encodeURIComponent(item.strategy_name || '')}','${item.current_price != null ? item.current_price : ''}')">✏️ 卖出</button>
                    ${item.exit_reason ? `<button class="btn btn-sm" style="padding:4px 10px; font-size:12px; background:#ecfeff; color:#0f766e; border:1px solid #99f6e4; border-radius:4px; cursor:pointer;" onclick="openExitReasonModal('${escapeHtml(item.stock_name || '--')}','${encodeURIComponent(item.exit_reason)}')">📋 说明</button>` : ''}
                </td>
            </tr>`;
    }).join('');

    container.innerHTML = `
        <div class="table-responsive">
            <table class="table table-striped" style="font-size:12px;">
                <thead><tr>${headers.map(h => `<th>${h}</th>`).join('')}</tr></thead>
                <tbody>${rows}</tbody>
            </table>
        </div>`;
}
/**
 * 强弱标志单元格（真/假标签：真走强/假走强/真走弱/假走弱/--）
 * @param {string} label - 后端 strength_label
 */
function strengthLabelCell(label) {
    const map = { '真走强': '#ef4444', '假走强': '#f59e0b', '真走弱': '#16a34a', '假走弱': '#f59e0b' };
    if (!label) return '<span style="color:#9ca3af;">--</span>';
    return `<span style="color:${map[label] || '#0ea5e9'}; font-weight:600;">${escapeHtml(label)}</span>`;
}


/**
 * 渲染统计信息
 */
export function renderSelectionTrackStats(total, page, limit) {
    const stats = document.getElementById('selection-track-stats');
    if (!stats) return;
    const totalPages = Math.ceil(total / limit);
    stats.style.display = 'block';
    stats.innerHTML = `📊 共 <strong>${total}</strong> 只被选入股票（当前页 ${page}/${totalPages}）`;
}

/**
 * 渲染分页
 */
export function renderSelectionTrackPagination(total, currentPage, limit) {
    const pagination = document.getElementById('selection-track-pagination');
    if (!pagination) return;
    const totalPages = Math.ceil(total / limit);
    if (totalPages <= 1) { pagination.style.display = 'none'; return; }
    pagination.style.display = 'block';
    pagination.innerHTML = '';

    const mkBtn = (text, pg, disabled, primary) => {
        const b = document.createElement('button');
        b.textContent = text;
        b.disabled = !!disabled;
        b.onclick = () => querySelectionTrack(pg);
        b.style.cssText = `padding:6px 12px; margin:0 5px; border:1px solid ${primary ? '#2563eb' : '#d1d5db'}; background:${primary ? '#2563eb' : 'white'}; color:${primary ? 'white' : '#374151'}; border-radius:4px; cursor:pointer; font-size:12px;`;
        if (disabled) b.style.opacity = '0.5';
        return b;
    };
    pagination.appendChild(mkBtn('← 上一页', currentPage - 1, currentPage === 1, false));
    for (let i = Math.max(1, currentPage - 2); i <= Math.min(totalPages, currentPage + 2); i++) {
        pagination.appendChild(mkBtn(String(i), i, false, i === currentPage));
    }
    pagination.appendChild(mkBtn('下一页 →', currentPage + 1, currentPage === totalPages, false));
}

/** HTML转义（本模块内） */
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * 设置排名相关事件监听
 */
export function setupRankingEvents() {
    // 绑定生成排名按钮
    const generateBtn = document.getElementById('generate-ranking-btn');
    if (generateBtn) {
        generateBtn.addEventListener('click', generateRanking);
    }
    
    // 绑定跟踪排名按钮
    const trackBtn = document.getElementById('track-ranking-btn');
    if (trackBtn) {
        trackBtn.addEventListener('click', trackRanking);
    }
    
    // 绑定强制重新生成排名按钮（改名为"重新生成"）
    const forceRegenerateBtn = document.getElementById('force-regenerate-ranking-btn');
    if (forceRegenerateBtn) {
        forceRegenerateBtn.addEventListener('click', forceRegenerateRanking);
    }
}


/**
 * 打开人工修改选股跟踪弹窗（操作列）
 * @param {string} stock_code - 股票代码
 * @param {string} strategy_key - 策略类名（用于后端定位记录）
 * @param {string} stock_name - 股票名称
 * @param {string} exit_date - 已退出时的退出日期（可空）
 * @param {string|number} sell_price - 已存在的卖出价（可空）
 */
export function openTrackEditModal(stock_code, strategy_key, stock_name, exit_date, sell_price, strategy_name_enc, current_price) {
    closeTrackModal();
    const overlay = document.createElement('div');
    overlay.id = 'track-edit-modal-overlay';
    overlay.style.cssText = 'position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(0,0,0,0.45); z-index:9999; display:flex; align-items:center; justify-content:center;';
    const box = document.createElement('div');
    box.style.cssText = 'background:#fff; border-radius:10px; padding:20px 24px; width:360px; box-shadow:0 10px 30px rgba(0,0,0,0.2); font-size:13px; color:#1f2937;';
    const today = new Date().toISOString().slice(0, 10);
    const defDate = exit_date || today;
    const defPrice = (sell_price != null && sell_price !== '') ? sell_price : '';
    box.innerHTML = `
        <h3 style="margin:0 0 12px; font-size:15px; font-weight:600;">人工修改选股跟踪</h3>
        <p style="margin:0 0 4px; color:#374151;"><strong>${escapeHtml(stock_name || '--')}</strong>（${escapeHtml(stock_code)}）</p>
        <p style="margin:0 0 4px; font-size:12px; color:#6b7280;">策略：${escapeHtml(decodeURIComponent(strategy_name_enc || '') || strategy_key || '--')}</p>
        <p style="margin:0 0 12px; font-size:12px; color:#6b7280;">当前价：¥${current_price != null && current_price !== '' ? Number(current_price).toFixed(2) : '--'}</p>
        <label style="font-size:12px; font-weight:600; display:block; margin-bottom:4px;">卖出时间</label>
        <input type="date" id="track-edit-sell-date" value="${escapeHtml(defDate)}" style="width:100%; padding:6px; border:1px solid #d1d5db; border-radius:4px; margin-bottom:12px; font-size:13px;">
        <label style="font-size:12px; font-weight:600; display:block; margin-bottom:4px;">卖出价格（留空则用当日收盘价）</label>
        <input type="number" step="0.01" id="track-edit-sell-price" value="${escapeHtml(defPrice)}" placeholder="如 13.50" style="width:100%; padding:6px; border:1px solid #d1d5db; border-radius:4px; margin-bottom:16px; font-size:13px;">
        <div style="display:flex; gap:10px; justify-content:flex-end;">
            <button onclick="closeTrackModal()" style="padding:6px 14px; border:1px solid #d1d5db; background:#fff; border-radius:4px; cursor:pointer; font-size:12px;">取消</button>
            <button onclick="submitTrackManualEdit('${escapeHtml(strategy_key)}','${escapeHtml(stock_code)}')" style="padding:6px 14px; border:none; background:#2563eb; color:#fff; border-radius:4px; cursor:pointer; font-size:12px;">确认保存</button>
        </div>
    `;
    overlay.appendChild(box);
    overlay.onclick = (e) => { if (e.target === overlay) closeTrackModal(); };
    document.body.appendChild(overlay);
}

export function closeTrackModal() {
    const el = document.getElementById('track-edit-modal-overlay');
    if (el) el.remove();
}

export async function submitTrackManualEdit(strategy_key, stock_code) {
    const sellDate = document.getElementById('track-edit-sell-date')?.value || '';
    const sellPriceRaw = document.getElementById('track-edit-sell-price')?.value || '';
    if (!confirm('确认保存对该记录的人工修改？')) return;
    try {
        const resp = await fetch('/api/selection-track/manual-edit', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ strategy_key, stock_code, sell_date: sellDate, sell_price: sellPriceRaw })
        });
        const data = await resp.json();
        if (data.success) {
            alert('✅ ' + (data.msg || '保存成功'));
            closeTrackModal();
            querySelectionTrack(1);
        } else {
            alert('❌ ' + (data.error || '保存失败'));
        }
    } catch (e) {
        alert('❌ 保存异常: ' + e.message);
    }
}

export function openExitReasonModal(stock_name, exit_reason_enc) {
    closeExitReasonModal();
    const reason = exit_reason_enc ? decodeURIComponent(exit_reason_enc) : '--';
    const overlay = document.createElement('div');
    overlay.id = 'track-exit-reason-modal-overlay';
    overlay.style.cssText = 'position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(0,0,0,0.45); z-index:9999; display:flex; align-items:center; justify-content:center;';
    const box = document.createElement('div');
    box.style.cssText = 'background:#fff; border-radius:10px; padding:20px 24px; width:400px; box-shadow:0 10px 30px rgba(0,0,0,0.2); font-size:13px; color:#1f2937;';
    box.innerHTML = `
        <h3 style="margin:0 0 12px; font-size:15px; font-weight:600;">退出说明</h3>
        <p style="margin:0 0 4px; color:#374151;"><strong>${escapeHtml(stock_name || '--')}</strong></p>
        <div style="margin-top:10px; padding:12px; background:#f9fafb; border:1px solid #e5e7eb; border-radius:6px; color:#374151; max-height:300px; overflow-y:auto; white-space:pre-wrap; word-break:break-word;">${escapeHtml(reason)}</div>
        <div style="display:flex; justify-content:flex-end; margin-top:16px;">
            <button onclick="closeExitReasonModal()" style="padding:6px 14px; border:1px solid #d1d5db; background:#fff; border-radius:4px; cursor:pointer; font-size:12px;">关闭</button>
        </div>
    `;
    overlay.appendChild(box);
    overlay.onclick = (e) => { if (e.target === overlay) closeExitReasonModal(); };
    document.body.appendChild(overlay);
}

export function closeExitReasonModal() {
    const el = document.getElementById('track-exit-reason-modal-overlay');
    if (el) el.remove();
}

// 供页面内联 onclick 调用（ES module 导出不会自动进全局，需显式挂 window）
window.openTrackEditModal = openTrackEditModal;
window.closeTrackModal = closeTrackModal;
window.submitTrackManualEdit = submitTrackManualEdit;
window.openExitReasonModal = openExitReasonModal;
window.closeExitReasonModal = closeExitReasonModal;
