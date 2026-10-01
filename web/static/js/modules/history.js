/**
 * 选股历史查询功能模块
 * 数据源：stock_selection_record（选股记录表）—— 每天选股明细
 * 展示：选股日期、股票名称、命中的策略、选入价、评分、命中策略数
 */

/**
 * 查询选股历史
 */
export function searchSelectionHistory() {
    console.log('开始查询选股历史...');
    const strategyFilter = document.getElementById('history-strategy-filter');
    const strategyName = strategyFilter?.value?.trim() || '';
    const startDate = document.getElementById('history-start-date')?.value?.trim() || '';
    const endDate = document.getElementById('history-end-date')?.value?.trim() || '';
    fetchSelectionHistory(strategyName, startDate, endDate, 1);
}

/**
 * 获取选股记录（每天选股明细）
 * @param {string} strategyName - 策略名称
 * @param {string} startDate - 开始日期
 * @param {string} endDate - 结束日期
 * @param {number} page - 页码
 */
export function fetchSelectionHistory(strategyName, startDate, endDate, page) {
    const params = new URLSearchParams();
    if (strategyName) params.append('strategy_name', strategyName);
    if (startDate) params.append('start_date', startDate);
    if (endDate) params.append('end_date', endDate);
    params.append('page', page);
    params.append('limit', 20);

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
 * 渲染选股记录表（每天选股明细）
 * @param {Array} data - 选股记录数据
 */
export function renderHistoryTable(data) {
    const tbody = document.getElementById('history-tbody');
    const table = document.getElementById('history-table');
    const emptyState = document.getElementById('history-empty');
    const thead = document.getElementById('history-thead');

    if (!tbody || !table || !emptyState || !thead) {
        console.error('历史记录表格元素不存在');
        return;
    }

    tbody.innerHTML = '';

    if (!data || data.length === 0) {
        table.style.display = 'none';
        emptyState.style.display = 'block';
        return;
    }

    table.style.display = 'table';
    emptyState.style.display = 'none';

    // 列头：选股记录明细
    const headers = ['选股日期', '股票名称', '命中的策略', '选入价', '评分', '命中策略数'];
    const theadRow = thead.querySelector('tr');
    theadRow.innerHTML = headers.map(h => `<th>${h}</th>`).join('');

    data.forEach(record => {
        const row = document.createElement('tr');
        const scoreTxt = record.score != null && !isNaN(record.score) ? Number(record.score).toFixed(2) : '--';
        row.innerHTML = `
            <td>${formatDate(record.selection_date)}</td>
            <td>${escapeHtml(record.stock_name)}</td>
            <td><span style="background: #dbeafe; color: #0c4a6e; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: 600;">${escapeHtml(record.strategy_name)}</span></td>
            <td>¥${formatPrice(record.selection_price)}</td>
            <td>${scoreTxt}</td>
            <td>${record.strategy_count != null ? Number(record.strategy_count) : 1}</td>
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

    const prevBtn = document.createElement('button');
    prevBtn.textContent = '← 上一页';
    prevBtn.disabled = currentPage === 1;
    prevBtn.onclick = () => goToHistoryPage(currentPage - 1);
    prevBtn.style.cssText = 'padding: 6px 12px; margin: 0 5px; border: 1px solid #d1d5db; background: white; border-radius: 4px; cursor: pointer; font-size: 12px;';
    pagination.appendChild(prevBtn);

    for (let i = Math.max(1, currentPage - 2); i <= Math.min(totalPages, currentPage + 2); i++) {
        const btn = document.createElement('button');
        btn.textContent = i;
        btn.style.cssText = `padding: 6px 10px; margin: 0 2px; border: 1px solid #d1d5db; background: ${i === currentPage ? '#2563eb' : 'white'}; color: ${i === currentPage ? 'white' : '#374151'}; border-radius: 4px; cursor: pointer; font-size: 12px;`;
        btn.onclick = () => goToHistoryPage(i);
        pagination.appendChild(btn);
    }

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
    const startDate = document.getElementById('history-start-date')?.value?.trim() || '';
    const endDate = document.getElementById('history-end-date')?.value?.trim() || '';
    fetchSelectionHistory(strategyName, startDate, endDate, page);
}

/**
 * 重置筛选条件
 */
export function resetHistoryFilters() {
    const strategyFilter = document.getElementById('history-strategy-filter');
    const startDate = document.getElementById('history-start-date');
    const endDate = document.getElementById('history-end-date');
    if (strategyFilter) strategyFilter.value = '';
    if (startDate) startDate.value = '';
    if (endDate) endDate.value = '';
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
