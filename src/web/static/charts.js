/* Investmate ECharts Helper */

// Skeleton loading helper
function showSkeleton(domId) {
    var el = document.getElementById(domId);
    if (!el) return;
    el.classList.add('animate-pulse', 'bg-gray-200', 'dark:bg-gray-700', 'rounded-lg');
}

function hideSkeleton(domId) {
    var el = document.getElementById(domId);
    if (!el) return;
    el.classList.remove('animate-pulse', 'bg-gray-200', 'dark:bg-gray-700');
}

// Toast notification
function showToast(message, type) {
    type = type || 'info';
    var colors = {error: 'bg-red-500', success: 'bg-emerald-500', info: 'bg-gray-800'};
    var toast = document.createElement('div');
    toast.className = 'fixed bottom-4 right-4 px-5 py-3 rounded-lg shadow-lg text-sm text-white z-50 transition-all duration-300 transform translate-y-2 opacity-0 ' + (colors[type] || colors.info);
    toast.textContent = message;
    document.body.appendChild(toast);
    requestAnimationFrame(function() { toast.classList.remove('translate-y-2', 'opacity-0'); });
    setTimeout(function() { toast.classList.add('translate-y-2', 'opacity-0'); setTimeout(function() { toast.remove(); }, 300); }, 3000);
}

// Button loading state
function setButtonLoading(btn, loading) {
    if (loading) {
        btn.dataset.originalText = btn.textContent;
        btn.textContent = '처리 중...';
        btn.disabled = true;
        btn.classList.add('opacity-60', 'cursor-not-allowed');
    } else {
        btn.textContent = btn.dataset.originalText || btn.textContent;
        btn.disabled = false;
        btn.classList.remove('opacity-60', 'cursor-not-allowed');
    }
}

// 색상 팔레트
const COLORS = {
    primary: '#6366f1',
    blue: '#3b82f6',
    cyan: '#06b6d4',
    green: '#10b981',
    red: '#ef4444',
    orange: '#f59e0b',
    purple: '#8b5cf6',
    pink: '#ec4899',
    teal: '#14b8a6',
    indigo: '#4f46e5',
};
const PALETTE = [COLORS.primary, COLORS.blue, COLORS.cyan, COLORS.green, COLORS.orange, COLORS.red, COLORS.purple, COLORS.pink, COLORS.teal, COLORS.indigo];

// 차트 초기화 헬퍼
function initChart(domId) {
    const dom = document.getElementById(domId);
    if (!dom) return null;
    const isDark = document.documentElement.classList.contains('dark');
    const chart = echarts.init(dom, isDark ? 'dark' : null, { renderer: 'canvas' });
    window.addEventListener('resize', () => chart.resize());
    return chart;
}

// 그라데이션 area fill 생성
function areaGradient(chart, color, opacity1 = 0.25, opacity2 = 0.02) {
    return new echarts.graphic.LinearGradient(0, 0, 0, 1, [
        { offset: 0, color: color.replace(')', `,${opacity1})`).replace('rgb', 'rgba') },
        { offset: 1, color: color.replace(')', `,${opacity2})`).replace('rgb', 'rgba') },
    ]);
}

// 공통 라인 차트 옵션
function lineChartOption(labels, data, color, opts = {}) {
    return {
        tooltip: { trigger: 'axis', backgroundColor: 'rgba(15,23,42,0.92)', borderColor: 'rgba(99,102,241,0.3)', borderWidth: 1, textStyle: { color: '#e2e8f0', fontSize: 12 }, extraCssText: 'border-radius:8px; box-shadow:0 4px 12px rgba(0,0,0,0.2)' },
        grid: { left: '3%', right: '3%', top: '8%', bottom: opts.dataZoom ? '18%' : '3%', containLabel: true },
        xAxis: { type: 'category', data: labels, axisLabel: { show: opts.showXLabel !== false, fontSize: 10, rotate: 0 }, axisLine: { lineStyle: { color: '#ddd' }}, splitLine: { show: false } },
        yAxis: { type: 'value', splitLine: { lineStyle: { color: 'rgba(0,0,0,0.06)' }}, axisLabel: { fontSize: 10 } },
        series: [{
            type: 'line',
            data: data,
            smooth: 0.4,
            symbol: 'none',
            lineStyle: { color: color, width: 2.5, shadowColor: color + '40', shadowBlur: 8 },
            areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: color + '25' }, { offset: 0.7, color: color + '08' }, { offset: 1, color: color + '02' }] }},
            emphasis: { lineStyle: { width: 3 } },
            ...opts.seriesExtra,
        }],
        ...(opts.dataZoom ? { dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 2, borderColor: 'transparent', backgroundColor: 'rgba(0,0,0,0.03)', fillerColor: 'rgba(99,102,241,0.08)', handleStyle: { color: '#6366f1' }, start: data.length > 120 ? 100 - (120 / data.length * 100) : 0, end: 100 }] } : {}),
        animationDuration: 800,
        animationEasing: 'cubicOut',
    };
}

// 공통 바 차트 옵션
function barChartOption(labels, data, colors, opts = {}) {
    const colorArr = Array.isArray(colors) ? colors : data.map(() => colors);
    return {
        tooltip: { trigger: 'axis', backgroundColor: 'rgba(15,23,42,0.92)', borderColor: 'rgba(99,102,241,0.3)', borderWidth: 1, textStyle: { color: '#e2e8f0', fontSize: 12 }, extraCssText: 'border-radius:8px; box-shadow:0 4px 12px rgba(0,0,0,0.2)' },
        grid: { left: '3%', right: '3%', top: '8%', bottom: '3%', containLabel: true },
        xAxis: opts.horizontal ? { type: 'value', splitLine: { lineStyle: { color: 'rgba(0,0,0,0.06)' }} } : { type: 'category', data: labels, axisLabel: { fontSize: 10 } },
        yAxis: opts.horizontal ? { type: 'category', data: labels, axisLabel: { fontSize: 10 } } : { type: 'value', splitLine: { lineStyle: { color: 'rgba(0,0,0,0.06)' }}, ...(opts.yMax ? { max: opts.yMax } : {}) },
        series: [{
            type: 'bar',
            data: data.map((v, i) => ({ value: v, itemStyle: { color: colorArr[i % colorArr.length], borderRadius: opts.horizontal ? [0, 6, 6, 0] : [6, 6, 0, 0], shadowColor: 'rgba(0,0,0,0.06)', shadowBlur: 4, shadowOffsetY: 2 } })),
            barMaxWidth: 28,
            emphasis: { itemStyle: { shadowBlur: 8, shadowColor: 'rgba(0,0,0,0.12)' } },
        }],
        animationDuration: 600,
    };
}

// 레이더 차트 옵션
function radarChartOption(labels, data, color) {
    return {
        radar: {
            indicator: labels.map(l => ({ name: l, max: 10 })),
            shape: 'circle',
            splitArea: { areaStyle: { color: ['rgba(99,102,241,0.02)', 'rgba(99,102,241,0.04)'] } },
            splitLine: { lineStyle: { color: 'rgba(0,0,0,0.08)' } },
            axisName: { fontSize: 11, color: '#666' },
        },
        series: [{
            type: 'radar',
            data: [{ value: data, areaStyle: { color: color + '20' }, lineStyle: { color: color, width: 2 }, symbol: 'circle', symbolSize: 5, itemStyle: { color: color } }],
        }],
        animationDuration: 800,
    };
}

// 도넛/파이 차트 옵션
function pieChartOption(labels, data, opts = {}) {
    return {
        tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)', backgroundColor: 'rgba(0,0,0,0.75)', textStyle: { color: '#fff' } },
        legend: { orient: 'vertical', right: '5%', top: 'center', textStyle: { fontSize: 11 } },
        series: [{
            type: 'pie',
            radius: opts.ring ? ['45%', '70%'] : '65%',
            center: ['40%', '50%'],
            data: labels.map((l, i) => ({ name: l, value: data[i], itemStyle: { color: PALETTE[i % PALETTE.length] } })),
            emphasis: { itemStyle: { shadowBlur: 12, shadowColor: 'rgba(0,0,0,0.15)' }, scaleSize: 6 },
            label: { show: false },
            animationType: 'scale',
            animationDelay: (idx) => idx * 80,
        }],
        animationDuration: 800,
    };
}

// Debounced ECharts resize
var _resizeTimer;
window.addEventListener('resize', function() {
    clearTimeout(_resizeTimer);
    _resizeTimer = setTimeout(function() {
        document.querySelectorAll('[_echarts_instance_]').forEach(function(el) {
            var chart = echarts.getInstanceByDom(el);
            if (chart) chart.resize();
        });
    }, 150);
});
