/**
 * ECharts 图表生命周期 composable
 *
 * 统一图表组件的公共样板：
 *   容器 ref → nextTick 后 echarts.init → window resize 自适应 → 卸载时 dispose。
 *
 * 用法：
 *   const { chartRef, getChart } = useEChart(chart => loadData())
 *   // 模板: <div ref="chartRef" />
 *   // 数据更新后: getChart()?.setOption({...})
 */

import { nextTick, onMounted, onUnmounted, ref } from "vue";
import * as echarts from "echarts";

export function useEChart(onReady?: (chart: echarts.ECharts) => void) {
  const chartRef = ref<HTMLDivElement>();
  let chart: echarts.ECharts | null = null;

  const resizeChart = (): void => {
    chart?.resize();
  };

  onMounted(() => {
    nextTick(() => {
      if (chartRef.value) {
        chart = echarts.init(chartRef.value);
        onReady?.(chart);
      }
      window.addEventListener("resize", resizeChart);
    });
  });

  onUnmounted(() => {
    chart?.dispose();
    chart = null;
    window.removeEventListener("resize", resizeChart);
  });

  /** 获取当前实例（未挂载/已卸载时为 null），用于 setOption/getWidth 等 */
  const getChart = (): echarts.ECharts | null => chart;

  return { chartRef, getChart };
}
