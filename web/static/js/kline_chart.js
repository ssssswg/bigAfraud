/**
 * kline_chart.js - 使用 ECharts 渲染可交互K线图
 * 依赖：web/static/js/lib/echarts.min.js
 * 功能：日K线 + MA5/MA10/MA20 + 成交量副图，支持缩放/拖拽/十字准线/悬浮数值
 */

let klineChartInstance = null;

/**
 * 计算简单移动平均线
 * @param {Array<number>} closes 收盘价数组
 * @param {number} n 周期
 */
function calcMA(closes, n) {
    const arr = [];
    for (let i = 0; i < closes.length; i++) {
        if (i < n - 1) {
            arr.push('-');
        } else {
            let s = 0;
            for (let j = i - n + 1; j <= i; j++) s += closes[j];
            arr.push(+(s / n).toFixed(3));
        }
    }
    return arr;
}

/**
 * 初始化K线图表（全局函数，被 stocks.js 调用）
 * @param {string} containerId - 容器元素ID
 * @param {Array} rawData - K线数据（含 date/open/high/low/close/volume）
 */
function initKlineChart(containerId, rawData) {
    const container = document.getElementById(containerId);
    if (!container) {
        console.error(`容器 ${containerId} 不存在`);
        return;
    }
    if (klineChartInstance) {
        klineChartInstance.dispose();
        klineChartInstance = null;
    }
    container.innerHTML = '';

    const data = (Array.isArray(rawData) ? rawData : []).filter(d => d && d.open && d.close);
    if (!data.length) {
        container.innerHTML = '<div style="padding:20px;color:#ef4444;">没有有效的K线数据</div>';
        return;
    }

    const dates = data.map(d => String(d.date || '').slice(0, 10));
    // ECharts candlestick 顺序: [open, close, low, high]
    const ohlc = data.map(d => [Number(d.open), Number(d.close), Number(d.low), Number(d.high)]);
    const vols = data.map(d => Number(d.volume) || 0);
    const closes = data.map(d => Number(d.close));
    // 成交量红涨绿跌（按当日收盘较昨收）
    const volColors = data.map((d, i) => {
        const prev = i > 0 ? Number(data[i - 1].close) : Number(d.open);
        return Number(d.close) >= prev ? '#ef4444' : '#10b981';
    });

    // 数据范围近2年（data.length），默认展示最近约120个交易日（近半年）
    const DEFAULT_SHOW = 120;
    const zoomStart = Math.max(0, 100 - (DEFAULT_SHOW / data.length) * 100);

    const chart = echarts.init(container);
    const option = {
        animation: false,
        backgroundColor: '#ffffff',
        axisPointer: {
            link: [{ xAxisIndex: 'all' }],
            label: { backgroundColor: '#777' }
        },
        tooltip: {
            trigger: 'axis',
            axisPointer: { type: 'cross' },
            backgroundColor: 'rgba(255,255,255,0.96)',
            borderColor: '#e5e7eb',
            textStyle: { color: '#333', fontSize: 12 },
            formatter: function (params) {
                if (!params || !params.length) return '';
                const idx = params[0].dataIndex;
                const d = data[idx];
                const prev = idx > 0 ? Number(data[idx - 1].close) : Number(d.open);
                const chg = prev ? (Number(d.close) - prev) / prev * 100 : 0;
                const sign = chg > 0 ? '+' : '';
                const chgColor = chg >= 0 ? '#ef4444' : '#10b981';
                let html = `<div style="font-weight:700;margin-bottom:6px;font-size:13px;">${dates[idx]}</div>`;
                html += `<div style="line-height:1.7;">开盘: <b>${Number(d.open)}</b>　收盘: <b>${Number(d.close)}</b></div>`;
                html += `<div style="line-height:1.7;">最高: <b>${Number(d.high)}</b>　最低: <b>${Number(d.low)}</b></div>`;
                const vol = Number(d.volume) || 0;
                const volStr = vol >= 10000 ? (vol / 10000).toFixed(2) + '万手' : vol.toFixed(0) + '手';
                html += `<div style="line-height:1.7;">成交量: <b>${volStr}</b></div>`;
                html += `<div style="line-height:1.7;">涨跌幅: <span style="color:${chgColor};font-weight:700;">${sign}${chg.toFixed(2)}%</span></div>`;
                return html;
            }
        },
        legend: {
            data: ['MA5', 'MA10', 'MA20'],
            top: 6, left: 64,
            itemWidth: 16, itemHeight: 8,
            textStyle: { fontSize: 11, color: '#666' }
        },
        grid: [
            { left: 64, right: 16, top: 28, height: '56%' },
            { left: 64, right: 16, top: '69%', height: '18%' }
        ],
        xAxis: [
            {
                type: 'category', data: dates, gridIndex: 0, boundaryGap: true,
                axisLine: { onZero: false, lineStyle: { color: '#ccc' } },
                axisLabel: { show: true, fontSize: 11, color: '#888', interval: 'auto' },
                splitLine: { show: false },
                min: 'dataMin', max: 'dataMax'
            },
            {
                type: 'category', data: dates, gridIndex: 1,
                axisLine: { lineStyle: { color: '#ccc' } },
                axisLabel: { show: false },
                splitLine: { show: false }
            }
        ],
        yAxis: [
            {
                scale: true, gridIndex: 0,
                splitLine: { show: true, lineStyle: { color: '#f0f0f0' } },
                axisLabel: { fontSize: 11, color: '#888' }
            },
            {
                scale: true, gridIndex: 1, splitNumber: 2,
                splitLine: { show: false },
                axisLabel: { fontSize: 10, color: '#888' }
            }
        ],
        dataZoom: [
            { type: 'inside', xAxisIndex: [0, 1], start: zoomStart, end: 100 },
            { type: 'slider', xAxisIndex: [0, 1], top: '91%', height: 18, start: zoomStart, end: 100,
              borderColor: '#ccc', fillerColor: 'rgba(59,130,246,0.12)' }
        ],
        series: [
            {
                name: 'K线', type: 'candlestick', data: ohlc, xAxisIndex: 0, yAxisIndex: 0,
                itemStyle: {
                    color: '#ef4444', color0: '#10b981',
                    borderColor: '#ef4444', borderColor0: '#10b981'
                }
            },
            { name: 'MA5', type: 'line', data: calcMA(closes, 5), xAxisIndex: 0, yAxisIndex: 0, symbol: 'none', lineStyle: { width: 1 }, itemStyle: { color: '#3b82f6' } },
            { name: 'MA10', type: 'line', data: calcMA(closes, 10), xAxisIndex: 0, yAxisIndex: 0, symbol: 'none', lineStyle: { width: 1 }, itemStyle: { color: '#f97316' } },
            { name: 'MA20', type: 'line', data: calcMA(closes, 20), xAxisIndex: 0, yAxisIndex: 0, symbol: 'none', lineStyle: { width: 1 }, itemStyle: { color: '#eab308' } },
            {
                name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1,
                data: vols.map((v, i) => ({ value: v, itemStyle: { color: volColors[i] } }))
            }
        ]
    };
    chart.setOption(option);
    klineChartInstance = chart;

    // 容器尺寸变化时自适应
    const onResize = function () {
        if (klineChartInstance) klineChartInstance.resize();
    };
    window.addEventListener('resize', onResize);
}
