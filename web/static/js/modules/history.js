/**
 * 选股历史查询功能模块
 */

/**
 * 查询选股历史
 */
export function searchSelectionHistory() {
    console.log('开始查询选股历史...');
    
    // 获取筛选条件
    const strategyFilter = document.getElementById('history-strategy-filter');
    const startDateInput = document.getElementById('history-start-date');
    const endDateInput = document.getElementById('history-end-date');
    
    console.log('筛选元素:', { strategyFilter, startDateInput, endDateInput });
    
    const strategyName = strategyFilter?.value?.trim() || '';
    const startDate = startDateInput?.value || '';
    let endDate = endDateInput?.value || '';
    
    console.log('筛选条件:', { strategyName, startDate, endDate });
    
    // 如果结束日期为空，默认设置为当天
    if (!endDate) {
        const today = new Date();
        endDate = today.toISOString().split('T')[0];
    }
    
    // 调用API（不传递股票代码）
    fetchSelectionHistory(strategyName, startDate, endDate, 1);
}

/**
 * 获取选股历史数据
 * @param {string} strategyName - 策略名称
 * @param {string} startDate - 开始日期
 * @param {string} endDate - 结束日期
 * @param {number} page - 页码
 */
export function fetchSelectionHistory(strategyName, startDate, endDate, page) {
    // 构建查询参数
    const params = new URLSearchParams();
    if (strategyName) params.append('strategy_name', strategyName);
    if (startDate) params.append('start_date', startDate);
    if (endDate) params.append('end_date', endDate);
    params.append('page', page);
    params.append('limit', 20);
    
    // 发送请求
    const url = `/api/selection-history?${params.toString()}`;
    console.log('请求URL:', url);
    
    fetch(url)
        .then(response => {
            console.log('API响应状态:', response.status);
            return response.json();
        })
        .then(data => {
            console.log('API返回数据:', data);
            if (data.success) {
                renderHistoryTable(data.data);
                updateHistoryStats(data.total, data.page, data.limit);
                renderHistoryPagination(data.total, data.page, data.limit);
            } else {
                showHistoryError(data.error || '查询失败');
            }
        })
        .catch(error => {
            console.error('API请求错误:', error);
            showHistoryError('网络错误: ' + error.message);
        });
}

/**
 * 渲染历史表格
 * @param {Array} data - 历史数据
 */
export function renderHistoryTable(data) {
    const tbody = document.getElementById('history-tbody');
    const table = document.getElementById('history-table');
    const emptyState = document.getElementById('history-empty');
    
    // 检查元素是否存在
    if (!tbody || !table || !emptyState) {
        console.error('历史记录表格元素不存在');
        return;
    }
    
    tbody.innerHTML = '';
    
    if (data.length === 0) {
        table.style.display = 'none';
        emptyState.style.display = 'block';
        return;
    }
    
    table.style.display = 'table';
    emptyState.style.display = 'none';
    
    // 遍历数据
    data.forEach(record => {
        const returnRate = record.return_rate || 0;
        let returnClass = 'return-neutral';
        if (returnRate > 0) {
            returnClass = 'return-positive';
        } else if (returnRate < 0) {
            returnClass = 'return-negative';
        }
        
        const row = document.createElement('tr');
        // 使用选入当日收盘价作为选入价格显示
        const selectionPrice = record.selection_day_price || record.selection_price || 0;
        row.innerHTML = `
            <td><span style="background: #dbeafe; color: #0c4a6e; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: 600;">${escapeHtml(record.strategy_name)}</span></td>
            <td><a href="javascript:void(0)" onclick="viewStockDetail('${escapeHtml(record.stock_code)}')" class="stock-link" style="color: #2563eb; text-decoration: none; cursor: pointer; font-weight: 600;">${escapeHtml(record.stock_code)}</a></td>
            <td>${escapeHtml(record.stock_name)}</td>
            <td>${formatDate(record.selection_date)}</td>
            <td>¥${formatPrice(selectionPrice)}</td>
            <td>¥${formatPrice(record.current_price)}</td>
            <td>
                <div style="font-size: 12px;">
                    <div>最高: ¥${formatPrice(record.highest_price)}</div>
                    <div>最低: ¥${formatPrice(record.lowest_price)}</div>
                </div>
            </td>
        `;
        tbody.appendChild(row);
    });
}

/**
 * 更新统计信息
 * @param {number} total - 总数
 * @param {number} page - 当前页码
 * @param {number} limit - 每页数量
 */
export function updateHistoryStats(total, page, limit) {
    const statsDiv = document.getElementById('history-stats');
    const totalElem = document.getElementById('history-total');
    const pageElem = document.getElementById('history-current-page');
    const totalPagesElem = document.getElementById('history-total-pages');
    
    // 检查元素是否存在
    if (!statsDiv || !totalElem || !pageElem) {
        console.error('统计信息元素不存在');
        return;
    }
    
    const totalPages = Math.ceil(total / limit);
    
    totalElem.textContent = total;
    pageElem.textContent = page;
    if (totalPagesElem) {
        totalPagesElem.textContent = totalPages;
    }
    statsDiv.style.display = 'block';
}

/**
 * 渲染分页
 * @param {number} total - 总数
 * @param {number} currentPage - 当前页码
 * @param {number} limit - 每页数量
 */
export function renderHistoryPagination(total, currentPage, limit) {
    const pagination = document.getElementById('history-pagination');
    const totalPages = Math.ceil(total / limit);
    
    if (totalPages <= 1) {
        pagination.style.display = 'none';
        return;
    }
    
    pagination.style.display = 'block';
    pagination.innerHTML = '';
    
    // 上一页
    const prevBtn = document.createElement('button');
    prevBtn.textContent = '← 上一页';
    prevBtn.disabled = currentPage === 1;
    prevBtn.onclick = () => goToHistoryPage(currentPage - 1);
    prevBtn.style.cssText = 'padding: 6px 12px; margin: 0 5px; border: 1px solid #d1d5db; background: white; border-radius: 4px; cursor: pointer; font-size: 12px;';
    pagination.appendChild(prevBtn);
    
    // 页码
    for (let i = Math.max(1, currentPage - 2); i <= Math.min(totalPages, currentPage + 2); i++) {
        const btn = document.createElement('button');
        btn.textContent = i;
        btn.style.cssText = `padding: 6px 10px; margin: 0 2px; border: 1px solid #d1d5db; background: ${i === currentPage ? '#2563eb' : 'white'}; color: ${i === currentPage ? 'white' : '#374151'}; border-radius: 4px; cursor: pointer; font-size: 12px;`;
        btn.onclick = () => goToHistoryPage(i);
        pagination.appendChild(btn);
    }
    
    // 下一页
    const nextBtn = document.createElement('button');
    nextBtn.textContent = '下一页 →';
    nextBtn.disabled = currentPage === totalPages;
    nextBtn.onclick = () => goToHistoryPage(currentPage + 1);
    nextBtn.style.cssText = 'padding: 6px 12px; margin: 0 5px; border: 1px solid #d1d5db; background: white; border-radius: 4px; cursor: pointer; font-size: 12px;';
    pagination.appendChild(nextBtn);
}

/**
 * 跳转到指定页
 * @param {number} page - 页码
 */
export function goToHistoryPage(page) {
    const strategyName = document.getElementById('history-strategy-filter')?.value.trim() || '';
    const startDate = document.getElementById('history-start-date')?.value || '';
    let endDate = document.getElementById('history-end-date')?.value || '';
    
    // 如果结束日期为空，默认设置为当天
    if (!endDate) {
        const today = new Date();
        endDate = today.toISOString().split('T')[0];
    }
    
    fetchSelectionHistory(strategyName, startDate, endDate, page);
}

/**
 * 重置筛选条件
 */
export function resetHistoryFilters() {
    const strategyFilter = document.getElementById('history-strategy-filter');
    const startDate = document.getElementById('history-start-date');
    const endDate = document.getElementById('history-end-date');
    
    // 检查元素是否存在
    if (strategyFilter) strategyFilter.value = '';
    if (startDate) startDate.value = '';
    if (endDate) endDate.value = '';
    
    // 重置后显示空状态，不自动查询
    showHistoryEmptyState('请点击"查询"按钮加载数据');
}

/**
 * 显示空状态提示
 * @param {string} message - 提示信息
 */
export function showHistoryEmptyState(message) {
    const emptyState = document.getElementById('history-empty');
    if (emptyState) {
        emptyState.innerHTML = `<p style="color: #6b7280;">📭 ${message}</p>`;
        emptyState.style.display = 'block';
    }
    const table = document.getElementById('history-table');
    if (table) table.style.display = 'none';
    const stats = document.getElementById('history-stats');
    if (stats) stats.style.display = 'none';
    const pagination = document.getElementById('history-pagination');
    if (pagination) pagination.style.display = 'none';
}

/**
 * 显示错误信息
 * @param {string} error - 错误信息
 */
export function showHistoryError(error) {
    const errorDiv = document.getElementById('history-error');
    if (errorDiv) {
        errorDiv.innerHTML = `<p style="color: #ef4444;">❌ ${error}</p>`;
        errorDiv.style.display = 'block';
    }
    showHistoryEmptyState('查询失败，请重试');
}

/**
 * 格式化日期
 * @param {string} dateStr - 日期字符串
 * @returns {string} 格式化后的日期
 */
export function formatDate(dateStr) {
    if (!dateStr) return '--';
    // 假设 dateStr 是 YYYYMMDD 格式
    if (dateStr.length === 8) {
        return `${dateStr.substring(0, 4)}-${dateStr.substring(4, 6)}-${dateStr.substring(6, 8)}`;
    }
    return dateStr;
}

/**
 * 格式化价格
 * @param {number} price - 价格
 * @returns {string} 格式化后的价格
 */
export function formatPrice(price) {
    if (price == null || isNaN(price)) return '--';
    return price.toFixed(2);
}

/**
 * 转义HTML字符
 * @param {string} text - 文本
 * @returns {string} 转义后的文本
 */
export function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * 加载连续选股统计
 * 统计最近 N 天选股的连续入选与每日差异
 */
export async function loadSelectionContinuity() {
    const daysInput = document.getElementById('continuity-days');
    const container = document.getElementById('continuity-result');
    if (!daysInput || !container) return;

    const days = parseInt(daysInput.value) || 5;
    container.innerHTML = '<p class="loading">正在统计连续选股，请稍候...</p>';

    try {
        const response = await fetch(`/api/selection-continuity?days=${days}`);
        const result = await response.json();

        if (!result.success) {
            container.innerHTML = `<p style="color: #ef4444;">❌ 统计失败: ${result.error || '未知错误'}</p>`;
            return;
        }
        renderContinuity(result, container);
    } catch (error) {
        console.error('统计连续选股异常:', error);
        container.innerHTML = `<p style="color: #ef4444;">❌ 统计异常: ${error.message}</p>`;
    }
}

/**
 * 渲染连续选股统计结果
 */
export function renderContinuity(data, container) {
    const dates = data.dates || [];
    const continuity = data.continuity || [];
    const diff = data.diff || [];

    if (dates.length === 0) {
        container.innerHTML = '<p style="color: #6b7280;">暂无选股数据</p>';
        return;
    }

    let html = '';

    // 连续入选重点提醒
    html += `
        <div style="margin-bottom: 12px; background: #fffbeb; border-left: 4px solid #f59e0b; padding: 12px; border-radius: 4px;">
            <strong style="color: #92400e;">🔥 连续入选重点提醒（连续 ≥ 2 天）</strong>
            <span style="color: #b45309; font-size: 12px; margin-left: 8px;">统计范围：${dates[0]} ~ ${dates[dates.length - 1]}，共 ${dates.length} 个选股日</span>
        </div>`;

    if (continuity.length === 0) {
        html += '<p style="color: #6b7280; font-size: 12px; padding: 8px 0;">该范围内没有连续 ≥ 2 天入选的股票</p>';
    } else {
        html += `<table class="data-table" style="width: 100%; font-size: 12px; margin-bottom: 20px;">
            <thead>
                <tr>
                    <th>排名</th>
                    <th>股票代码</th>
                    <th>股票名称</th>
                    <th>连续天数</th>
                    <th>累计入选</th>
                    <th>入选日期</th>
                </tr>
            </thead>
            <tbody>`;
        continuity.forEach((item, i) => {
            const highlight = item.consecutive_days >= 3;
            html += `
                <tr style="${highlight ? 'background: #fef3c7; font-weight: 600;' : ''}">
                    <td>${i + 1}</td>
                    <td><a href="javascript:void(0)" onclick="viewStockDetail('${escapeHtml(item.code)}')" class="stock-link">${escapeHtml(item.code)}</a></td>
                    <td>${escapeHtml(item.name)}</td>
                    <td><span style="background: #f59e0b; color: #fff; padding: 2px 8px; border-radius: 4px; font-weight: 600;">${item.consecutive_days}天</span></td>
                    <td>${item.appear_days}天</td>
                    <td>${(item.dates || []).join(', ')}</td>
                </tr>`;
        });
        html += '</tbody></table>';
    }

    // 每日选股差异
    html += `
        <div style="margin-bottom: 10px; font-size: 13px; font-weight: 600; color: #374151;">📅 每日选股差异（相对前一选股日）</div>
        <table class="data-table" style="width: 100%; font-size: 12px;">
            <thead>
                <tr>
                    <th>选股日期</th>
                    <th>当日数量</th>
                    <th>🟢 新增</th>
                    <th>🔴 去除</th>
                </tr>
            </thead>
            <tbody>`;
    diff.forEach(row => {
        const added = (row.added || []).length ? (row.added || []).join(', ') : '-';
        const removed = (row.removed || []).length ? (row.removed || []).join(', ') : '-';
        html += `
            <tr>
                <td>${row.date}</td>
                <td>${row.count}</td>
                <td style="color: #10b981;">${escapeHtml(added)}</td>
                <td style="color: #ef4444;">${escapeHtml(removed)}</td>
            </tr>`;
    });
    html += '</tbody></table>';

    container.innerHTML = html;
}
