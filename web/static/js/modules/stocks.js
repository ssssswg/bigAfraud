/**
 * 股票相关功能模块
 */

/**
 * 加载统计信息
 */
export async function loadStats() {
    try {
        const response = await fetch('/api/stats');
        const result = await response.json();
        
        if (result.success) {
            document.getElementById('stat-stocks').textContent = result.data.total_stocks;
            document.getElementById('stat-date').textContent = result.data.latest_date;
            document.getElementById('stat-strategies').textContent = result.data.strategies;
        }
    } catch (error) {
        console.error('加载统计信息失败:', error);
    }
}

/**
 * 加载我的金股数据
 */
export async function loadMyGoldenStocks() {
    const container = document.getElementById('my-golden-stocks-content');
    try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 5000); // 5秒超时
        
        const response = await fetch('/api/dashboard/my-golden-stocks', { signal: controller.signal });
        clearTimeout(timeoutId);
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        
        const result = await response.json();
        
        // 检查result是否为空或没有success字段
        if (!result || (result.success === false)) {
            container.innerHTML = '<p class="text-muted">暂无金股数据</p>';
            return;
        }
        
        // 如果success为true或result中有stocks数据
        if (result.stocks && result.stocks.length > 0) {
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
                            </tr>
                        </thead>
                        <tbody>
            `;
            
            result.stocks.forEach((stock, index) => {
                html += `
                    <tr>
                        <td>${index + 1}</td>
                        <td><a href="javascript:void(0)" onclick="viewStockDetail('${stock.stock_code}')" class="stock-link">${stock.stock_code}</a></td>
                        <td>${stock.stock_name}</td>
                        <td><a href="javascript:void(0)" onclick="showScoreDetail('${stock.stock_code}', '${result.date}')" class="score-link">${(stock.total_score || 0).toFixed(2)}</a></td>
                        <td>${stock.industry || '-'}</td>
                        <td>${stock.area || '-'}</td>
                    </tr>
                `;
            });
            
            html += `
                        </tbody>
                    </table>
                </div>
                <p class="text-muted" style="margin-top: 10px; font-size: 12px;">数据日期: ${result.date}</p>
            `;
            
            container.innerHTML = html;
        } else {
            container.innerHTML = '<p class="text-muted">暂无金股数据</p>';
        }
    } catch (error) {
        console.error('加载我的金股失败:', error);
        if (error.name === 'AbortError') {
            container.innerHTML = '<p class="text-muted">暂无金股数据</p>';
        } else {
            container.innerHTML = '<p class="text-muted">暂无金股数据</p>';
        }
    }
}

/**
 * 加载最热行业数据
 */
export async function loadHotIndustries() {
    const container = document.getElementById('hot-industries-content');
    try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 5000); // 5秒超时
        
        const response = await fetch('/api/dashboard/hot-industries', { signal: controller.signal });
        clearTimeout(timeoutId);
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        
        const result = await response.json();
        
        // 检查result是否为空或没有success字段
        if (!result || (result.success === false)) {
            container.innerHTML = '<p class="text-muted">暂无行业数据</p>';
            return;
        }
        
        // 如果success为true或result中有industries数据
        if (result.industries && result.industries.length > 0) {
            let html = `
                <div class="table-responsive">
                    <table class="table table-striped">
                        <thead>
                            <tr>
                                <th>排名</th>
                                <th>行业</th>
                                <th>股票数量</th>
                                <th>占比</th>
                            </tr>
                        </thead>
                        <tbody>
            `;
            
            // 只显示前5个行业
            const top5Industries = result.industries.slice(0, 5);
            top5Industries.forEach((industry, index) => {
                html += `
                    <tr>
                        <td>${index + 1}</td>
                        <td>${industry.industry}</td>
                        <td><a href="javascript:void(0)" onclick="showIndustryStocks('${industry.industry}', ${industry.count})" class="stock-link">${industry.count}</a></td>
                        <td>${industry.percentage}%</td>
                    </tr>
                `;
            });
            
            html += `
                        </tbody>
                    </table>
                </div>
                <p class="text-muted" style="margin-top: 10px; font-size: 12px;">数据日期: ${result.date}</p>
            `;
            
            container.innerHTML = html;
        } else {
            container.innerHTML = '<p class="text-muted">暂无行业数据</p>';
        }
    } catch (error) {
        console.error('加载最热行业失败:', error);
        if (error.name === 'AbortError') {
            container.innerHTML = '<p class="text-muted">暂无行业数据</p>';
        } else {
            container.innerHTML = '<p class="text-muted">暂无行业数据</p>';
        }
    }
}

/**
 * 加载最热板块数据
 */
export async function loadHotAreas() {
    const container = document.getElementById('hot-areas-content');
    try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 5000); // 5秒超时
        
        const response = await fetch('/api/dashboard/hot-areas', { signal: controller.signal });
        clearTimeout(timeoutId);
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        
        const result = await response.json();
        
        // 检查result是否为空或没有success字段
        if (!result || (result.success === false)) {
            container.innerHTML = '<p class="text-muted">暂无板块数据</p>';
            return;
        }
        
        // 如果success为true或result中有areas数据
        if (result.areas && result.areas.length > 0) {
            let html = `
                <div class="table-responsive">
                    <table class="table table-striped">
                        <thead>
                            <tr>
                                <th>排名</th>
                                <th>板块</th>
                                <th>股票数量</th>
                                <th>占比</th>
                            </tr>
                        </thead>
                        <tbody>
            `;
            
            // 只显示前5个板块
            const top5Areas = result.areas.slice(0, 5);
            top5Areas.forEach((area, index) => {
                html += `
                    <tr>
                        <td>${index + 1}</td>
                        <td>${area.area}</td>
                        <td><a href="javascript:void(0)" onclick="showAreaStocks('${area.area}', ${area.count})" class="stock-link">${area.count}</a></td>
                        <td>${area.percentage}%</td>
                    </tr>
                `;
            });
            
            html += `
                        </tbody>
                    </table>
                </div>
                <p class="text-muted" style="margin-top: 10px; font-size: 12px;">数据日期: ${result.date}</p>
            `;
            
            container.innerHTML = html;
        } else {
            container.innerHTML = '<p class="text-muted">暂无板块数据</p>';
        }
    } catch (error) {
        console.error('加载最热板块失败:', error);
        if (error.name === 'AbortError') {
            container.innerHTML = '<p class="text-muted">暂无板块数据</p>';
        } else {
            container.innerHTML = '<p class="text-muted">暂无板块数据</p>';
        }
    }
}

/**
 * 股票列表分页状态
 */
let stocksState = { page: 1, per_page: 100, total: 0, total_pages: 1, keyword: '' };

/**
 * 加载股票列表（服务端分页 + 关键词模糊查询）
 * @param {number} page - 页码
 * @param {string} keyword - 搜索关键词（代码/名称）
 */
export async function loadStocks(page = 1, keyword = '') {
    const tbody = document.getElementById('stocks-tbody');
    tbody.innerHTML = '<tr><td colspan="7" class="loading">正在加载股票列表...</td></tr>';

    stocksState.page = Math.max(1, page);
    stocksState.keyword = keyword || '';
    bindStockSearch();

    try {
        const params = new URLSearchParams({
            page: stocksState.page,
            per_page: stocksState.per_page,
            keyword: stocksState.keyword
        });
        const response = await fetch(`/api/stocks?${params.toString()}`);
        const result = await response.json();

        if (!result.success) {
            tbody.innerHTML = `<tr><td colspan="7" class="loading">加载失败: ${result.error || ''}</td></tr>`;
            return;
        }

        stocksState.total = result.total || 0;
        stocksState.total_pages = result.total_pages || 1;
        renderStocks(result.data || []);
        renderStocksPagination();
    } catch (error) {
        tbody.innerHTML = `<tr><td colspan="7" class="loading">加载失败: ${error.message}</td></tr>`;
    }
}

/**
 * 渲染股票列表
 * @param {Array} stocks - 股票列表数据
 */
export function renderStocks(stocks) {
    const tbody = document.getElementById('stocks-tbody');
    
    if (stocks.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="loading">暂无数据</td></tr>';
        return;
    }
    
    tbody.innerHTML = stocks.map(stock => `
        <tr>
            <td><strong>${stock.code}</strong></td>
            <td>${stock.name}</td>
            <td>¥${stock.latest_price}</td>
            <td>${stock.latest_date}</td>
            <td>${stock.market_cap}</td>
            <td>${stock.data_count}</td>
            <td>
                <button class="btn btn-secondary" onclick="viewStockDetail('${stock.code}')">
                    查看
                </button>
            </td>
        </tr>
    `).join('');
}

/**
 * 渲染分页控件
 */
function renderStocksPagination() {
    const container = document.getElementById('stocks-pagination');
    if (!container) return;

    const { page, per_page, total, total_pages } = stocksState;
    if (total_pages <= 1) {
        container.style.display = 'none';
        return;
    }
    container.style.display = 'flex';

    const btn = (label, pg, disabled, active) =>
        `<button class="btn ${active ? 'btn-primary' : 'btn-secondary'}" ${disabled ? 'disabled' : ''} onclick="window.goStocksPage(${pg})">${label}</button>`;

    const maxShown = 5;
    let start = Math.max(1, page - Math.floor(maxShown / 2));
    let end = Math.min(total_pages, start + maxShown - 1);
    start = Math.max(1, end - maxShown + 1);
    let pages = '';
    for (let i = start; i <= end; i++) {
        pages += btn(i, i, false, i === page);
    }

    container.innerHTML = `
        <span style="font-size:12px; color:#6b7280;">共 ${total} 只 · 第 ${page}/${total_pages} 页</span>
        ${btn('上一页', page - 1, page <= 1, false)}
        ${pages}
        ${btn('下一页', page + 1, page >= total_pages, false)}
        <select id="stocks-per-page" onchange="window.goStocksPerPage(this.value)" style="font-size:12px; padding:2px 4px; margin-left:8px;">
            ${[50, 100, 200, 500].map(n => `<option value="${n}" ${n === per_page ? 'selected' : ''}>每页${n}条</option>`).join('')}
        </select>
    `;
}

// 全局翻页 / 改每页条数（供 index.html onclick 调用）
window.goStocksPage = (pg) => loadStocks(pg, stocksState.keyword);
window.goStocksPerPage = (per) => {
    stocksState.per_page = parseInt(per, 10) || 100;
    loadStocks(1, stocksState.keyword);
};

/**
 * 绑定搜索框（防抖，幂等——只绑定一次）
 */
function bindStockSearch() {
    const input = document.getElementById('stock-search');
    if (!input || input.dataset.stockSearchBound) return;
    input.dataset.stockSearchBound = '1';
    let timer = null;
    input.addEventListener('input', (e) => {
        clearTimeout(timer);
        timer = setTimeout(() => loadStocks(1, e.target.value.trim()), 300);
    });
}

/**
 * 查看股票详情
 * @param {string} code - 股票代码
 */
export async function viewStockDetail(code) {
    try {
        const response = await fetch(`/api/stock/${code}`);
        const result = await response.json();
        
        if (result.success) {
            showStockModal(code, result.data);
        } else {
            alert('加载股票详情失败: ' + result.error);
        }
    } catch (error) {
        alert('加载股票详情失败: ' + error.message);
    }
}

/**
 * 显示股票详情弹窗
 * @param {string} code - 股票代码
 * @param {Object} data - 股票数据
 */
export function showStockModal(code, data) {
    const modal = document.getElementById('stock-modal');
    document.getElementById('stock-detail-modal-title').textContent = `股票详情: ${code}`;
    
    // 显示K线图表容器
    const chartContainer = document.getElementById('stock-chart-container');
    chartContainer.style.display = 'block';
    
    // 清空并隐藏股票信息区域，只显示K线图表（避免空容器抢占K线宽度）
    const infoEl = document.getElementById('stock-info');
    if (infoEl) {
        infoEl.innerHTML = '';
        infoEl.style.display = 'none';
    }
    
    // 显示涨跌幅列表
    const changeList = document.getElementById('stock-change-list');
    if (changeList) changeList.style.display = 'block';
    
    // 先显示模态框，让容器获得正确的尺寸
    modal.classList.add('active');
    
    // 使用requestAnimationFrame确保DOM已更新，容器有正确的宽度
    requestAnimationFrame(() => {
        // 初始化K线图表（默认展示最近约2个月，约40个交易日，更聚焦近期）
        // 注意：使用stock-chart-container而不是stock-chart（canvas元素）
        const recent = (Array.isArray(data) && data.length > 480) ? data.slice(-480) : data;
        initKlineChart('stock-chart-container', recent);
        // 填充每日涨跌幅列表（与K线一致，近2个月）
        fillStockChangeList(recent);
    });
}

/**
 * 关闭弹窗
 */
export function closeModal() {
    document.getElementById('stock-modal').classList.remove('active');
}

/**
 * 加载策略列表到历史记录下拉框
 * 与策略回测页面使用统一的数据源 /api/trading/backtest/strategies
 */
export async function loadHistoryStrategyOptions() {
    const strategySelect = document.getElementById('history-strategy-filter');
    if (!strategySelect) return;
    
    try {
        // 使用与策略回测一致的API端点
        const response = await fetch('/api/trading/backtest/strategies');
        const data = await response.json();
        
        if (data.success && data.data && data.data.strategies) {
            // 保留第一个选项（全部策略）
            strategySelect.innerHTML = '<option value="">全部策略</option>';
            
            data.data.strategies.forEach(strategy => {
                const option = document.createElement('option');
                // 使用中文名称作为value和显示文本，与策略回测页面保持一致
                const chineseName = strategy.display_name || strategy.name;
                option.value = chineseName;
                option.textContent = chineseName;
                strategySelect.appendChild(option);
            });
        }
    } catch (error) {
        console.error('加载策略列表失败:', error);
    }
}

/**
 * 显示行业股票列表
 * @param {string} industry - 行业名称
 * @param {number} limit - 显示数量
 */
export async function showIndustryStocks(industry, limit = 50) {
    try {
        const response = await fetch(`/api/dashboard/industry-stocks?industry=${encodeURIComponent(industry)}&limit=${limit}`);
        const result = await response.json();
        
        if (result.success) {
            showStocksModal(`${industry}行业股票列表`, result.stocks, result.date || '');
        } else {
            alert('加载行业股票失败: ' + result.error);
        }
    } catch (error) {
        alert('加载行业股票失败: ' + error.message);
    }
}

/**
 * 显示板块股票列表
 * @param {string} area - 板块名称
 * @param {number} limit - 显示数量
 */
export async function showAreaStocks(area, limit = 50) {
    try {
        const response = await fetch(`/api/dashboard/area-stocks?area=${encodeURIComponent(area)}&limit=${limit}`);
        const result = await response.json();
        
        if (result.success) {
            showStocksModal(`${area}板块股票列表`, result.stocks, result.date || '');
        } else {
            alert('加载板块股票失败: ' + result.error);
        }
    } catch (error) {
        alert('加载板块股票失败: ' + error.message);
    }
}

/**
 * 显示股票列表模态框
 * @param {string} title - 模态框标题
 * @param {Array} stocks - 股票列表数据
 * @param {string} date - 评分日期
 */
export function fillStockChangeList(data) {
    const el = document.getElementById('stock-change-list');
    if (!el || !data) return;
    const arr = (Array.isArray(data) ? data : []).filter(d => d && d.close);
    let rows = '';
    for (let i = arr.length - 1; i >= 0; i--) {
        const d = arr[i];
        const prevC = i > 0 ? arr[i - 1].close : null;
        const chg = prevC ? (Number(d.close) - Number(prevC)) / Number(prevC) * 100 : null;
        const color = (chg !== null && chg >= 0) ? '#ef4444' : '#10b981';
        const date = String(d.date || '').slice(0, 10);
        const close = Number(d.close).toFixed(2);
        const chgTxt = (chg === null || chg === undefined)
            ? '--'
            : (chg > 0 ? '+' : '') + chg.toFixed(2) + '%';
        rows += `<tr>
            <td>${date}</td>
            <td>${close}</td>
            <td style="color:${color};font-weight:600;">${chgTxt}</td>
        </tr>`;
    }
    el.innerHTML = `<div class="scl-title">每日涨跌幅</div>
        <table class="scl-table">
            <thead><tr><th>日期</th><th>收盘</th><th>涨跌幅</th></tr></thead>
            <tbody>${rows}</tbody>
        </table>`;
}

export function showStocksModal(title, stocks, date) {
    const modal = document.getElementById('stock-modal');
    document.getElementById('stock-detail-modal-title').textContent = title;
    
    // 隐藏K线图表容器，只显示股票列表
    const chartContainer = document.getElementById('stock-chart-container');
    chartContainer.style.display = 'none';
    
    // 隐藏涨跌幅列表
    const changeList = document.getElementById('stock-change-list');
    if (changeList) changeList.style.display = 'none';
    
    // 恢复并清空股票信息区域（供股票列表弹窗使用）
    const stockInfo = document.getElementById('stock-info');
    stockInfo.style.display = 'block';
    stockInfo.innerHTML = '';
    
    if (stocks.length === 0) {
        stockInfo.innerHTML = '<p class="text-muted">暂无股票数据</p>';
        modal.classList.add('active');
        return;
    }
    
    // 构建表格
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
                        <th>当前价</th>
                        <th>收益率</th>
                        <th>最高价格</th>
                        <th>最高收益</th>
                    </tr>
                </thead>
                <tbody>
    `;
    
    stocks.forEach((item, index) => {
        // 防御性代码，处理可能的undefined值
        const score = item.score || 0;
        const selectionPrice = item.selection_price || 0;
        const currentPrice = item.current_price || 0;
        const currentReturn = item.current_yield || 0;
        const highestPrice = item.highest_price || 0;
        const highestReturn = item.highest_yield || 0;
        
        html += `
            <tr>
                <td>${index + 1}</td>
                <td><a href="javascript:void(0)" onclick="viewStockDetail('${item.stock_code}')" class="stock-link">${item.stock_code}</a></td>
                <td>${item.stock_name}</td>
                <td><a href="javascript:void(0)" onclick="showScoreDetail('${item.stock_code}', '${date}')" class="score-link">${score.toFixed(2)}</a></td>
                <td>${item.industry || '-'}</td>
                <td>${item.sector || '-'}</td>
                <td>¥${selectionPrice.toFixed(2)}</td>
                <td>¥${currentPrice.toFixed(2)}</td>
                <td class="${currentReturn >= 0 ? 'text-danger' : 'text-success'}">${currentReturn.toFixed(2)}%</td>
                <td>¥${highestPrice.toFixed(2)}</td>
                <td class="${highestReturn >= 0 ? 'text-danger' : 'text-success'}">${highestReturn.toFixed(2)}%</td>
            </tr>
        `;
    });
    
    html += `
                </tbody>
            </table>
        </div>
    `;
    
    stockInfo.innerHTML = html;
    modal.classList.add('active');
}

/**
 * 初始化K线图表
 * @param {string} containerId - 容器ID
 * @param {Object} data - K线数据
 */
function initKlineChart(containerId, data) {
    // 调用全局的initKlineChart函数
    if (window.initKlineChart) {
        window.initKlineChart(containerId, data);
    } else {
        console.error('全局initKlineChart函数不存在');
    }
}
