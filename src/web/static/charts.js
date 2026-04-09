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

// ─── 게이지 차트 (Fear & Greed 등) ────────────────

function gaugeChartOption(value, rating, opts) {
    opts = opts || {};
    var isDark = document.documentElement.classList.contains('dark');
    return {
        series: [{
            type: 'gauge',
            startAngle: 180,
            endAngle: 0,
            min: 0,
            max: 100,
            center: ['50%', '70%'],
            radius: '100%',
            axisLine: {
                lineStyle: {
                    width: 20,
                    color: [
                        [0.25, '#ef4444'],
                        [0.45, '#f97316'],
                        [0.55, '#eab308'],
                        [0.75, '#22c55e'],
                        [1, '#06b6d4']
                    ]
                }
            },
            pointer: {
                icon: 'path://M12.8,0.7l12,40.1H0.7L12.8,0.7z',
                length: '55%',
                width: 8,
                offsetCenter: [0, '-10%'],
                itemStyle: { color: isDark ? '#e5e7eb' : '#374151' }
            },
            axisTick: { show: false },
            splitLine: { show: false },
            axisLabel: {
                distance: -30,
                fontSize: 10,
                color: isDark ? '#9ca3af' : '#6b7280',
                formatter: function(v) {
                    if (v === 0) return 'Extreme\nFear';
                    if (v === 50) return 'Neutral';
                    if (v === 100) return 'Extreme\nGreed';
                    return '';
                }
            },
            title: {
                offsetCenter: [0, '20%'],
                fontSize: 13,
                fontWeight: 600,
                color: isDark ? '#d1d5db' : '#4b5563'
            },
            detail: {
                fontSize: 32,
                fontWeight: 'bolder',
                offsetCenter: [0, '-5%'],
                valueAnimation: true,
                formatter: function(v) { return v != null ? v.toFixed(0) : '-'; },
                color: isDark ? '#f3f4f6' : '#1f2937'
            },
            data: [{ value: value, name: rating || '' }]
        }],
        animationDuration: 1200,
    };
}

// ─── 글로벌 디바운스 리사이즈 (단일 핸들러) ────────────────

// ─── Deep Dive 전용 차트 ────────────────────────────────

function layerRadarChart(domId, scores) {
    var chart = initChart(domId);
    if (!chart) return;
    var labels = ['펀더멘털', '밸류에이션', '기술적', '수급', '내러티브', '매크로'];
    var keys = ['fundamental', 'valuation', 'technical', 'flow', 'narrative', 'macro'];
    var values = keys.map(function(k) { return scores[k] || 5; });
    chart.setOption(radarChartOption(labels, values, COLORS.primary, 10));
}

function scenarioRangeChart(domId, data) {
    var chart = initChart(domId);
    if (!chart || !data || !data.horizons || data.horizons.length === 0) return;
    var horizons = data.horizons;
    var categories = horizons.map(function(h) { return h.label; });
    var colors = { bear: '#ef4444', base: '#6366f1', bull: '#22c55e' };

    var series = ['bear', 'base', 'bull'].map(function(sc) {
        return {
            name: sc.charAt(0).toUpperCase() + sc.slice(1),
            type: 'bar',
            barWidth: 18,
            itemStyle: { color: colorWithAlpha(colors[sc], 0.7), borderRadius: 3 },
            data: horizons.map(function(h) {
                var s = h[sc];
                if (!s) return [0, 0];
                return [s.low, s.high];
            }),
        };
    });

    chart.setOption({
        tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
        legend: { data: ['Bear', 'Base', 'Bull'], top: 5, textStyle: { fontSize: 11 } },
        grid: { left: 50, right: 30, top: 40, bottom: 30 },
        xAxis: { type: 'value', axisLabel: { formatter: function(v) { return '$' + fmtCompact(v); } } },
        yAxis: { type: 'category', data: categories, axisLabel: { fontSize: 12, fontWeight: 600 } },
        series: series,
        markLine: data.currentPrice ? {
            silent: true, symbol: 'none',
            data: [{ xAxis: data.currentPrice, label: { formatter: '현재가', fontSize: 10 }, lineStyle: { color: '#666', type: 'dashed' } }],
        } : undefined,
    });
}

// ─── Deep Dive Phase 3 차트 ─────────────────────────────────

/**
 * 액션 타임라인 차트 — conviction 추이 + 등급 변경 마커
 * @param {string} domId
 * @param {{dates: string[], convictions: number[], grades: string[]}} data
 */
function actionTimelineChart(domId, data) {
    var chart = initChart(domId);
    if (!chart || !data || !data.dates || !data.dates.length) return chart;

    var GRADE_COLORS = { ADD: '#10b981', HOLD: '#6b7280', TRIM: '#f59e0b', EXIT: '#ef4444' };

    // 등급 변경 시점 마커 데이터
    var markPoints = [];
    for (var i = 0; i < data.grades.length; i++) {
        if (i === 0 || data.grades[i] !== data.grades[i - 1]) {
            markPoints.push({
                coord: [data.dates[i], data.convictions[i]],
                value: data.grades[i],
                itemStyle: { color: GRADE_COLORS[data.grades[i]] || '#6b7280' },
                symbol: 'diamond', symbolSize: 14,
            });
        }
    }

    chart.setOption({
        tooltip: {
            trigger: 'axis',
            formatter: function(params) {
                var p = params[0];
                var idx = p.dataIndex;
                var grade = data.grades[idx] || '-';
                return fmtDate(p.axisValue) + '<br>'
                    + '<span style="color:' + (GRADE_COLORS[grade] || '#666') + '">● ' + grade + '</span>'
                    + ' | 확신도: ' + p.value;
            },
        },
        xAxis: { type: 'category', data: data.dates, axisLabel: { formatter: fmtDate } },
        yAxis: { type: 'value', min: 1, max: 10, name: '확신도' },
        series: [{
            type: 'line', data: data.convictions, smooth: true,
            lineStyle: { color: '#6366f1', width: 2 },
            itemStyle: { color: '#6366f1' },
            areaStyle: { color: colorWithAlpha('#6366f1', 0.08) },
            markPoint: { data: markPoints, label: { show: true, fontSize: 10, formatter: '{c}' } },
        }],
        dataZoom: data.dates.length > 60 ? [{ type: 'slider', start: 50, end: 100 }] : undefined,
    });
    return chart;
}

/**
 * 정확도 바 차트 — 종목별 hit_rate / direction / overall 비교
 * @param {string} domId
 * @param {Array<{ticker: string, hit_rate: number, direction: number, overall: number}>} data
 */
function accuracyBarChart(domId, data) {
    var chart = initChart(domId);
    if (!chart || !data || !data.length) return chart;

    var tickers = data.map(function(d) { return d.ticker; });

    chart.setOption({
        tooltip: {
            trigger: 'axis', axisPointer: { type: 'shadow' },
            formatter: function(params) {
                var label = params[0].axisValue;
                var lines = [label];
                params.forEach(function(p) {
                    lines.push(p.marker + ' ' + p.seriesName + ': ' + fmtNum(p.value, 1) + '%');
                });
                return lines.join('<br>');
            },
        },
        legend: { data: ['적중률', '방향 정확도', '종합 점수'] },
        yAxis: { type: 'category', data: tickers, inverse: true },
        xAxis: { type: 'value', min: 0, max: 100, axisLabel: { formatter: '{value}%' } },
        series: [
            { name: '적중률', type: 'bar', data: data.map(function(d) { return d.hit_rate; }), itemStyle: { color: '#3b82f6' } },
            { name: '방향 정확도', type: 'bar', data: data.map(function(d) { return d.direction; }), itemStyle: { color: '#10b981' } },
            { name: '종합 점수', type: 'bar', data: data.map(function(d) { return d.overall; }), itemStyle: { color: '#8b5cf6' } },
        ],
    });
    return chart;
}

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
