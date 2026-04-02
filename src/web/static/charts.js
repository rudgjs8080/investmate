/* Investmate ECharts Helper — v2 (Chart Core Overhaul) */

// ─── 숫자/날짜 포맷 유틸리티 ───────────────────────────────

function fmtNum(value, decimals) {
    if (value == null || isNaN(value)) return '-';
    if (decimals === undefined) decimals = 2;
    return value.toLocaleString('ko-KR', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
    });
}

function fmtPercent(value, decimals) {
    if (value == null || isNaN(value)) return '-';
    if (decimals === undefined) decimals = 1;
    return (value > 0 ? '+' : '') + value.toLocaleString('ko-KR', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
    }) + '%';
}

function fmtPrice(value) {
    if (value == null || isNaN(value)) return '-';
    return '$' + value.toLocaleString('ko-KR', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    });
}

function fmtCompact(value) {
    if (value == null || isNaN(value)) return '-';
    var abs = Math.abs(value);
    if (abs >= 1e12) return (value / 1e12).toFixed(1) + 'T';
    if (abs >= 1e9) return (value / 1e9).toFixed(1) + 'B';
    if (abs >= 1e6) return (value / 1e6).toFixed(1) + 'M';
    if (abs >= 1e3) return (value / 1e3).toFixed(1) + 'K';
    return value.toFixed(0);
}

function fmtDate(dateStr) {
    if (!dateStr) return '';
    var parts = String(dateStr).split('-');
    if (parts.length === 3) return parts[0] + '.' + parts[1] + '.' + parts[2];
    return String(dateStr);
}

// ─── 색상 유틸리티 ──────────────────────────────────────────

/** hex/rgb/rgba 어떤 포맷이든 안전하게 알파값 적용 */
function colorWithAlpha(color, alpha) {
    if (!color) return 'rgba(0,0,0,' + alpha + ')';
    // hex (#RGB, #RRGGBB, #RRGGBBAA)
    if (color.charAt(0) === '#') {
        var hex = color.slice(1);
        if (hex.length === 3) hex = hex[0]+hex[0]+hex[1]+hex[1]+hex[2]+hex[2];
        var r = parseInt(hex.substring(0, 2), 16);
        var g = parseInt(hex.substring(2, 4), 16);
        var b = parseInt(hex.substring(4, 6), 16);
        return 'rgba(' + r + ',' + g + ',' + b + ',' + alpha + ')';
    }
    // rgb(...) or rgba(...)
    var m = color.match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/);
    if (m) return 'rgba(' + m[1] + ',' + m[2] + ',' + m[3] + ',' + alpha + ')';
    return color;
}

// ─── Toast / Button / Skeleton ──────────────────────────────

function showToast(message, type) {
    type = type || 'info';
    var colors = { error: 'bg-red-500', success: 'bg-emerald-500', info: 'bg-gray-800' };
    var toast = document.createElement('div');
    toast.className = 'fixed bottom-4 right-4 px-5 py-3 rounded-lg shadow-lg text-sm text-white z-50 transition-all duration-300 transform translate-y-2 opacity-0 ' + (colors[type] || colors.info);
    toast.textContent = message;
    document.body.appendChild(toast);
    requestAnimationFrame(function() { toast.classList.remove('translate-y-2', 'opacity-0'); });
    setTimeout(function() { toast.classList.add('translate-y-2', 'opacity-0'); setTimeout(function() { toast.remove(); }, 300); }, 3000);
}

function setButtonLoading(btn, loading) {
    if (loading) {
        btn.dataset.originalText = btn.textContent;
        btn.textContent = '\uCC98\uB9AC \uC911...';
        btn.disabled = true;
        btn.classList.add('opacity-60', 'cursor-not-allowed');
    } else {
        btn.textContent = btn.dataset.originalText || btn.textContent;
        btn.disabled = false;
        btn.classList.remove('opacity-60', 'cursor-not-allowed');
    }
}

// ─── 색상 팔레트 ────────────────────────────────────────────

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

// ─── 차트 초기화 (자동 스켈레톤 + 다크모드) ────────────────

function initChart(domId) {
    var dom = document.getElementById(domId);
    if (!dom) return null;
    // 자동 스켈레톤 로딩
    dom.classList.add('chart-skeleton');
    var isDark = document.documentElement.classList.contains('dark');
    var chart = echarts.init(dom, isDark ? 'dark' : null, { renderer: 'canvas' });
    // setOption 래핑: 첫 데이터 세팅 시 스켈레톤 자동 제거
    var origSetOption = chart.setOption.bind(chart);
    chart.setOption = function(option, notMerge) {
        dom.classList.remove('chart-skeleton');
        return origSetOption(option, notMerge);
    };
    return chart;
}

// 다크모드 테마 전환 시 전체 차트 재초기화
function reinitAllCharts() {
    var isDark = document.documentElement.classList.contains('dark');
    document.querySelectorAll('[_echarts_instance_]').forEach(function(el) {
        var chart = echarts.getInstanceByDom(el);
        if (!chart) return;
        var option = chart.getOption();
        chart.dispose();
        var newChart = echarts.init(el, isDark ? 'dark' : null, { renderer: 'canvas' });
        // getOption 반환값은 배열 래핑됨, 그대로 setOption 가능
        newChart.setOption(option);
    });
}

// ─── 공통 툴팁 팩토리 ──────────────────────────────────────

function chartTooltip(opts) {
    opts = opts || {};
    var config = {
        trigger: opts.trigger || 'axis',
        backgroundColor: 'rgba(15,23,42,0.92)',
        borderColor: 'rgba(99,102,241,0.3)',
        borderWidth: 1,
        textStyle: { color: '#e2e8f0', fontSize: 12 },
        extraCssText: 'border-radius:8px; box-shadow:0 4px 12px rgba(0,0,0,0.2)',
    };
    if (opts.formatter) config.formatter = opts.formatter;
    if (opts.crosshair) {
        config.axisPointer = { type: 'cross', crossStyle: { color: '#94a3b8' } };
    } else if (opts.shadow) {
        config.axisPointer = { type: 'shadow' };
    }
    return config;
}

// ─── markLine 빌더 ──────────────────────────────────────────

function buildMarkLine(markLines) {
    if (!markLines || !markLines.length) return undefined;
    return {
        silent: true,
        symbol: 'none',
        data: markLines.map(function(ml) {
            return {
                yAxis: ml.value,
                name: ml.label || '',
                lineStyle: {
                    color: ml.color || '#94a3b8',
                    type: ml.type || 'dashed',
                    width: ml.width || 1,
                },
                label: {
                    formatter: ml.label || '{c}',
                    position: ml.position || 'end',
                    fontSize: 10,
                    color: ml.color || '#94a3b8',
                },
            };
        }),
    };
}

// ─── 공통 라인 차트 옵션 (단일/다중 시리즈) ────────────────

function lineChartOption(labels, dataOrSeries, colorOrNull, opts) {
    opts = opts || {};
    var unit = opts.unit || '';
    var decimals = opts.decimals !== undefined ? opts.decimals : 2;
    var isMulti = Array.isArray(dataOrSeries) && dataOrSeries.length > 0
        && typeof dataOrSeries[0] === 'object' && dataOrSeries[0] !== null && dataOrSeries[0].name;

    var series;
    var dataLen;

    if (isMulti) {
        // 다중 시리즈 모드
        series = dataOrSeries.map(function(s, i) {
            var c = s.color || PALETTE[i % PALETTE.length];
            var seriesObj = {
                name: s.name,
                type: 'line',
                data: s.data,
                smooth: s.smooth !== undefined ? s.smooth : 0.4,
                symbol: 'none',
                lineStyle: {
                    color: c,
                    width: s.width || 2.5,
                    type: s.lineType || 'solid',
                },
                emphasis: { lineStyle: { width: 3 } },
            };
            if (s.area !== false) {
                seriesObj.areaStyle = {
                    color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
                        colorStops: [
                            { offset: 0, color: colorWithAlpha(c, 0.15) },
                            { offset: 0.7, color: colorWithAlpha(c, 0.03) },
                            { offset: 1, color: colorWithAlpha(c, 0.005) },
                        ],
                    },
                };
            }
            if (i === 0 && opts.markLines) {
                seriesObj.markLine = buildMarkLine(opts.markLines);
            }
            return seriesObj;
        });
        dataLen = series[0].data.length;
    } else {
        // 단일 시리즈 모드 (후방 호환)
        var color = colorOrNull || COLORS.primary;
        var singleSeries = {
            type: 'line',
            data: dataOrSeries,
            smooth: 0.4,
            symbol: 'none',
            lineStyle: { color: color, width: 2.5, shadowColor: colorWithAlpha(color, 0.25), shadowBlur: 8 },
            areaStyle: {
                color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
                    colorStops: [
                        { offset: 0, color: colorWithAlpha(color, 0.15) },
                        { offset: 0.7, color: colorWithAlpha(color, 0.03) },
                        { offset: 1, color: colorWithAlpha(color, 0.005) },
                    ],
                },
            },
            emphasis: { lineStyle: { width: 3 } },
        };
        if (opts.seriesExtra) Object.assign(singleSeries, opts.seriesExtra);
        if (opts.markLines) singleSeries.markLine = buildMarkLine(opts.markLines);
        series = [singleSeries];
        dataLen = Array.isArray(dataOrSeries) ? dataOrSeries.length : 0;
    }

    // Smart DataZoom: slider는 120+ 데이터에서만 표시
    var dzThreshold = opts.dataZoomThreshold || 120;
    var needSlider = dataLen > dzThreshold;
    var dataZoomConfig = [];
    if (opts.dataZoom) {
        dataZoomConfig.push({ type: 'inside' });
        if (needSlider) {
            dataZoomConfig.push({
                type: 'slider', height: 18, bottom: 2,
                borderColor: 'transparent', backgroundColor: 'rgba(0,0,0,0.03)',
                fillerColor: 'rgba(99,102,241,0.08)',
                handleStyle: { color: '#6366f1' },
                start: 100 - (dzThreshold / dataLen * 100), end: 100,
            });
        }
    }

    // 툴팁 formatter
    var tooltipFormatter = function(params) {
        var html = '<div style="font-size:12px;font-weight:600;margin-bottom:4px">' + fmtDate(params[0].axisValue) + '</div>';
        params.forEach(function(p) {
            var val = (p.value != null && !isNaN(p.value)) ? fmtNum(p.value, decimals) + unit : '-';
            html += '<div style="margin-top:3px;display:flex;align-items:center;gap:6px">'
                + '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:' + p.color + '"></span>'
                + '<span style="flex:1">' + (p.seriesName || '') + '</span>'
                + '<b>' + val + '</b></div>';
        });
        return html;
    };

    // Y축 설정: auto-scale (데이터 범위에 맞게)
    var yAxisConfig = {
        type: 'value',
        min: opts.yMin !== undefined ? opts.yMin : 'dataMin',
        max: opts.yMax !== undefined ? opts.yMax : 'dataMax',
        splitLine: { lineStyle: { color: 'rgba(0,0,0,0.06)' } },
        axisLabel: { fontSize: 10 },
    };

    var option = {
        tooltip: chartTooltip({ crosshair: opts.crosshair !== false, formatter: tooltipFormatter }),
        grid: {
            left: '3%', right: '3%', top: isMulti ? '12%' : '8%',
            bottom: (opts.dataZoom && needSlider) ? '18%' : '3%',
            containLabel: true,
        },
        xAxis: {
            type: 'category', data: labels,
            axisLabel: { show: opts.showXLabel !== false, fontSize: 10, rotate: 0 },
            axisLine: { lineStyle: { color: '#ddd' } },
            splitLine: { show: false },
        },
        yAxis: yAxisConfig,
        series: series,
        animationDuration: 800,
        animationEasing: 'cubicOut',
    };

    // 범례 (다중 시리즈일 때만)
    if (isMulti) {
        option.legend = { bottom: (opts.dataZoom && needSlider) ? 22 : 0, textStyle: { fontSize: 11 } };
    }

    // DataZoom
    if (dataZoomConfig.length) option.dataZoom = dataZoomConfig;

    // Toolbox (차트 내보내기)
    if (opts.toolbox) {
        option.toolbox = {
            show: true,
            feature: { saveAsImage: { title: '\uC800\uC7A5', pixelRatio: 2 } },
            right: 10, top: 0,
            iconStyle: { borderColor: '#94a3b8' },
        };
    }

    return option;
}

// ─── 공통 바 차트 옵션 ──────────────────────────────────────

function barChartOption(labels, data, colors, opts) {
    opts = opts || {};
    var colorArr = Array.isArray(colors) ? colors : data.map(function() { return colors; });
    var unit = opts.unit || '';
    var decimals = opts.decimals !== undefined ? opts.decimals : 2;

    var tooltipFormatter = function(params) {
        var p = Array.isArray(params) ? params[0] : params;
        var val = (p.value != null && !isNaN(p.value)) ? fmtNum(p.value, decimals) + unit : '-';
        return '<div style="font-size:12px">'
            + '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:' + (p.color || '#666') + ';margin-right:6px"></span>'
            + p.name + ' <b>' + val + '</b></div>';
    };

    var yAxisConfig = opts.horizontal
        ? { type: 'category', data: labels, axisLabel: { fontSize: 10 } }
        : {
            type: 'value',
            min: opts.yMin !== undefined ? opts.yMin : undefined,
            max: opts.yMax !== undefined ? opts.yMax : undefined,
            splitLine: { lineStyle: { color: 'rgba(0,0,0,0.06)' } },
        };

    var xAxisConfig = opts.horizontal
        ? { type: 'value', splitLine: { lineStyle: { color: 'rgba(0,0,0,0.06)' } } }
        : { type: 'category', data: labels, axisLabel: { fontSize: 10 } };

    var seriesObj = {
        type: 'bar',
        data: data.map(function(v, i) {
            return {
                value: v,
                itemStyle: {
                    color: colorArr[i % colorArr.length],
                    borderRadius: opts.horizontal ? [0, 6, 6, 0] : [6, 6, 0, 0],
                    shadowColor: 'rgba(0,0,0,0.06)', shadowBlur: 4, shadowOffsetY: 2,
                },
            };
        }),
        barMaxWidth: 28,
        emphasis: { itemStyle: { shadowBlur: 8, shadowColor: 'rgba(0,0,0,0.12)' } },
    };

    if (opts.markLines) seriesObj.markLine = buildMarkLine(opts.markLines);

    return {
        tooltip: chartTooltip({ shadow: true, formatter: tooltipFormatter }),
        grid: { left: '3%', right: '3%', top: '8%', bottom: '3%', containLabel: true },
        xAxis: xAxisConfig,
        yAxis: yAxisConfig,
        series: [seriesObj],
        animationDuration: 600,
    };
}

// ─── 레이더 차트 옵션 ───────────────────────────────────────

function radarChartOption(labels, data, color, maxVal) {
    if (maxVal === undefined) maxVal = 10;
    return {
        tooltip: chartTooltip({
            trigger: 'item',
            formatter: function(params) {
                if (!params.value) return '';
                var html = '<div style="font-size:12px;font-weight:600;margin-bottom:4px">' + (params.name || '') + '</div>';
                labels.forEach(function(l, i) {
                    html += '<div style="margin-top:2px">' + l + ': <b>' + fmtNum(params.value[i], 1) + '</b></div>';
                });
                return html;
            },
        }),
        radar: {
            indicator: labels.map(function(l) { return { name: l, max: maxVal }; }),
            shape: 'circle',
            splitArea: { areaStyle: { color: ['rgba(99,102,241,0.02)', 'rgba(99,102,241,0.04)'] } },
            splitLine: { lineStyle: { color: 'rgba(0,0,0,0.08)' } },
            axisName: { fontSize: 11, color: '#666' },
        },
        series: [{
            type: 'radar',
            data: [{
                value: data,
                areaStyle: { color: colorWithAlpha(color, 0.12) },
                lineStyle: { color: color, width: 2 },
                symbol: 'circle', symbolSize: 5,
                itemStyle: { color: color },
            }],
        }],
        animationDuration: 800,
    };
}

// ─── 도넛/파이 차트 옵션 ────────────────────────────────────

function pieChartOption(labels, data, opts) {
    opts = opts || {};
    return {
        tooltip: {
            trigger: 'item',
            formatter: '{b}: {c} ({d}%)',
            backgroundColor: 'rgba(0,0,0,0.75)',
            textStyle: { color: '#fff' },
        },
        legend: { orient: 'vertical', right: '5%', top: 'center', textStyle: { fontSize: 11 } },
        series: [{
            type: 'pie',
            radius: opts.ring ? ['45%', '70%'] : '65%',
            center: ['40%', '50%'],
            data: labels.map(function(l, i) {
                return { name: l, value: data[i], itemStyle: { color: PALETTE[i % PALETTE.length] } };
            }),
            emphasis: { itemStyle: { shadowBlur: 12, shadowColor: 'rgba(0,0,0,0.15)' }, scaleSize: 6 },
            label: { show: false },
            animationType: 'scale',
            animationDelay: function(idx) { return idx * 80; },
        }],
        animationDuration: 800,
    };
}

// ─── 글로벌 디바운스 리사이즈 (단일 핸들러) ────────────────

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
