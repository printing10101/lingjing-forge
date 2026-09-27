<template>
  <div id="app">
    <ErrorBoundary>
      <SplashScreen v-if="showSplash" @complete="showSplash = false" />
      <el-config-provider :locale="elLocale">
        <div v-if="!appReady" class="app-initializing">
          <el-icon class="is-loading" :size="28">
            <Loading />
          </el-icon>
          <span>{{ $t("splashScreen.statusInit") }}</span>
        </div>
        <AppLayout
          v-else-if="!isStandaloneRoute"
          :project-name="projectStore.projectName"
          :is-modified="projectStore.isModified"
          @file-command="fileDialogsRef?.handleFileCommand"
          @refresh="fileDialogsRef?.handleRefresh"
        />
        <!-- 独立全屏页面（登录页等）：不套 AppLayout 侧边栏/头部 -->
        <router-view v-else />

        <!-- 工程文件对话框（新建/打开/另存为/未保存确认） -->
        <AppFileDialogs ref="fileDialogsRef" />

        <StepImportDialog />
        <DxfImportDialog />
        <ErrorConflictDialog />
        <BackendStartupDialog
          v-if="showStartupDialog"
          v-model="showStartupDialog"
        />

        <!-- [U-P0-1] 首次启动引导：通过 localStorage 标记控制，仅首次启动展示 -->
        <Tour
          ref="tourRef"
          :steps="tourSteps"
          storage-key="tour_progress_v1"
          @finish="handleTourFinish"
          @skip="handleTourSkip"
        />

        <!-- [W10.2] 制造流程引导：图纸→建模→工艺→仿真→NC 全流程 walkthrough，
             全局引导完成/跳过 后链式触发（独立完成标记，首体验即传达物理校验卖点） -->
        <Tour
          ref="flowTourRef"
          :steps="flowTourSteps"
          storage-key="flow_tour_progress_v1"
          @finish="handleFlowTourFinish"
          @skip="handleFlowTourSkip"
        />
      </el-config-provider>
    </ErrorBoundary>
  </div>
</template>

<script setup lang="ts">
import {
  computed,
  onMounted,
  onBeforeUnmount,
  inject,
  ref,
  watch,
  type Ref,
} from "vue";
import { useRoute } from "vue-router";
import { useI18n } from "vue-i18n";
import { useVersionStore } from "@/stores/version";
import { useProjectStore } from "@/stores/project";
import { useAuthStore } from "@/stores/auth";
import StepImportDialog from "@/components/step_import/StepImportDialog.vue";
import DxfImportDialog from "@/components/dxf_import/DxfImportDialog.vue";
import ErrorConflictDialog from "@/components/ErrorConflictDialog.vue";
import BackendStartupDialog from "@/components/BackendStartupDialog.vue";
import SplashScreen from "@/components/SplashScreen.vue";
import AppLayout from "@/components/AppLayout.vue";
import Tour from "@/components/Onboarding/Tour.vue";
import type { TourStep } from "@/components/Onboarding/Tour.vue";
import AppFileDialogs from "@/components/AppFileDialogs.vue";
import { useBackendStatus } from "@/composables/useBackendStatus";
import { Loading } from "@element-plus/icons-vue";
import zhCn from "element-plus/es/locale/lang/zh-cn";

const authStore = useAuthStore();
const { t } = useI18n();
const route = useRoute();

// 独立全屏页面（不套 AppLayout 侧边栏/头部）：登录页
const isStandaloneRoute = computed(() => route.name === "login");

const elLocaleRef = inject<Ref<typeof zhCn>>("locale", ref(zhCn));
const elLocale = computed(() => elLocaleRef.value);

// [U-P0-1] 首次启动引导：localStorage 标记键名。版本号升级时可强制重新引导。
const TOUR_COMPLETED_KEY = "tour_completed_v1";
// [W10.2] 制造流程引导标记
const FLOW_TOUR_COMPLETED_KEY = "flow_tour_completed_v1";
// Tour 组件实例引用
const tourRef = ref<InstanceType<typeof Tour> | null>(null);
const flowTourRef = ref<InstanceType<typeof Tour> | null>(null);
// 引导步骤配置（响应式：语言切换时自动更新文案）
const tourSteps = computed<TourStep[]>(() => [
  {
    title: t("onboardingTour.step1Title"),
    description: t("onboardingTour.step1Desc"),
  },
  {
    title: t("onboardingTour.step2Title"),
    description: t("onboardingTour.step2Desc"),
    target: ".sidebar-nav",
    placement: "right",
  },
  {
    title: t("onboardingTour.step3Title"),
    description: t("onboardingTour.step3Desc"),
    target: ".header-actions",
    placement: "bottom",
  },
  {
    title: t("onboardingTour.step4Title"),
    description: t("onboardingTour.step4Desc"),
    target: ".header-search",
    placement: "bottom",
  },
  {
    title: t("onboardingTour.step5Title"),
    description: t("onboardingTour.step5Desc"),
  },
  {
    title: t("onboardingTour.step6Title"),
    description: t("onboardingTour.step6Desc"),
  },
]);

// [W10.2] 制造流程引导步骤：图纸/语言 → 建模 → 工艺 → 仿真 → 刀轨 NC，
// 目标选择器为侧边栏 router-link 渲染出的 a[href]（稳定可定位）
const flowTourSteps = computed<TourStep[]>(() => [
  {
    title: t("onboardingTour.flowStep1Title"),
    description: t("onboardingTour.flowStep1Desc"),
  },
  {
    title: t("onboardingTour.flowStep2Title"),
    description: t("onboardingTour.flowStep2Desc"),
    target: '.sidebar-nav a[href="/nl-modeling"]',
    placement: "right",
  },
  {
    title: t("onboardingTour.flowStep3Title"),
    description: t("onboardingTour.flowStep3Desc"),
    target: '.sidebar-nav a[href="/process-planning"]',
    placement: "right",
  },
  {
    title: t("onboardingTour.flowStep4Title"),
    description: t("onboardingTour.flowStep4Desc"),
    target: '.sidebar-nav a[href="/simulation"]',
    placement: "right",
  },
  {
    title: t("onboardingTour.flowStep5Title"),
    description: t("onboardingTour.flowStep5Desc"),
    target: '.sidebar-nav a[href="/toolpath-editor"]',
    placement: "right",
  },
  {
    title: t("onboardingTour.flowStep6Title"),
    description: t("onboardingTour.flowStep6Desc"),
    target: '.sidebar-nav a[href="/flywheel-dashboard"]',
    placement: "right",
  },
  {
    title: t("onboardingTour.flowStep7Title"),
    description: t("onboardingTour.flowStep7Desc"),
    target: '.sidebar-nav a[href="/approval-dashboard"]',
    placement: "right",
  },
  {
    title: t("onboardingTour.flowStep8Title"),
    description: t("onboardingTour.flowStep8Desc"),
  },
]);

function handleTourFinish() {
  // 引导完成：写入 localStorage 标记，后续启动不再自动展示
  try {
    localStorage.setItem(TOUR_COMPLETED_KEY, "true");
  } catch {
    // localStorage 不可用（隐私模式等）时静默忽略，不影响功能
  }
  // [W10.2] 全局引导完成 → 链式触发制造流程引导
  maybeStartFlowTour();
}

function handleTourSkip() {
  // 跳过引导：同样写入标记，避免每次启动都弹出
  try {
    localStorage.setItem(TOUR_COMPLETED_KEY, "true");
  } catch {
    // 静默忽略
  }
}

// [W10.2] 制造流程引导：完成/跳过均写标记；全局引导结束后链式触发
function handleFlowTourFinish() {
  try {
    localStorage.setItem(FLOW_TOUR_COMPLETED_KEY, "true");
  } catch {
    // 静默忽略
  }
}

function handleFlowTourSkip() {
  try {
    localStorage.setItem(FLOW_TOUR_COMPLETED_KEY, "true");
  } catch {
    // 静默忽略
  }
}

function maybeStartFlowTour(delayMs = 600) {
  // 已登录且流程引导未完成时启动；localStorage 不可用视为未完成（每次启动可重看）
  try {
    if (!authStore.isAuthenticated) return;
    if (localStorage.getItem(FLOW_TOUR_COMPLETED_KEY) === "true") return;
    setTimeout(() => {
      flowTourRef.value?.start();
    }, delayMs);
  } catch {
    // 静默忽略
  }
}

// 重新引导事件监听：供「帮助 → 重新引导」菜单通过 window.dispatchEvent(new Event('replay-tour')) 触发
function handleReplayTour() {
  try {
    localStorage.removeItem(TOUR_COMPLETED_KEY);
  } catch {
    // 静默忽略
  }
  // 延迟一帧确保 DOM 已就绪（菜单关闭动画）
  setTimeout(() => {
    tourRef.value?.start();
  }, 100);
}

// [W10.2] 制造流程引导重放：帮助菜单 replay-flow-tour 事件
function handleReplayFlowTour() {
  try {
    localStorage.removeItem(FLOW_TOUR_COMPLETED_KEY);
  } catch {
    // 静默忽略
  }
  setTimeout(() => {
    flowTourRef.value?.start();
  }, 100);
}

const versionStore = useVersionStore();
const projectStore = useProjectStore();

// 工程文件对话框子组件引用（转发 file-command / refresh 命令）
const fileDialogsRef = ref<InstanceType<typeof AppFileDialogs> | null>(null);

// 启动动画
// Tauri 模式下使用原生 splashscreen 窗口（splashscreen.html）覆盖预处理白屏阶段，
// Vue 内部 SplashScreen 不再触发，避免双重启动动画；
// Web 模式下仍使用 Vue 内部 SplashScreen 作为启动动画。
const isTauriEnv =
  typeof window !== "undefined" &&
  Boolean(
    (window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__,
  );
const showSplash = ref(!isTauriEnv);
// 应用初始化完成标志（auto-login 完成后才渲染路由页面）
const appReady = ref(false);

// 后端进程状态监听
const { state: backendState, tauriMode } = useBackendStatus();
const showStartupDialog = ref(false);

watch(
  () => backendState.status,
  (status) => {
    if (!tauriMode.value) return;
    if (status === "starting" || status === "failed" || status === "crashed") {
      showStartupDialog.value = true;
    } else if (status === "running" || status === "stopped") {
      showStartupDialog.value = false;
    }
  },
  { immediate: true },
);

onMounted(async () => {
  // 安全修复 [B7]：删除硬编码凭据 admin/admin123 自动登录。
  // 改为检查已有 token；未登录时由路由守卫引导至登录页，用户手动完成认证。
  // 桌面应用可通过 Tauri sidecar 预置 token，Web 端需用户手动登录。
  try {
    // 安全修复 [P3-FE-2]：移除 console.info 残留
    // 登录态提示应通过 UI 展示（由路由守卫引导至登录页），不应在控制台输出
    if (!authStore.isAuthenticated) {
      // 未登录时由路由守卫引导至登录页，无需在此输出控制台日志
    }
  } catch (e: unknown) {
    // 检查登录态失败不阻塞应用启动，仅记录便于排查
    console.warn("[App] login state check failed:", e);
  }
  appReady.value = true;
  // 版本检查不阻塞 UI 渲染，后台静默执行；失败时记录便于排查版本不一致问题
  versionStore.fetchVersionInfo().catch((e: unknown) => {
    console.warn("[App] fetchVersionInfo failed:", e);
  });
  versionStore.checkConsistency();

  // [U-P0-1] 首次启动引导：检查 localStorage 标记，未完成且已登录时延迟启动 Tour
  // 延迟 800ms 确保主布局 DOM 渲染完成，Tour 才能正确定位目标元素
  // 未登录时不启动（避免引导遮罩覆盖登录页）
  try {
    const completed = localStorage.getItem(TOUR_COMPLETED_KEY);
    if (authStore.isAuthenticated && completed !== "true") {
      setTimeout(() => {
        tourRef.value?.start();
      }, 800);
    } else if (authStore.isAuthenticated && completed === "true") {
      // [W10.2] 全局引导已完成的老用户：制造流程引导未完成时补放一次
      // （延迟更久，避开启动期的后端连接与首屏渲染）
      maybeStartFlowTour(1500);
    }
  } catch {
    // localStorage 不可用时静默忽略，不阻塞应用启动
  }

  // 注册重新引导事件监听（供帮助菜单触发）
  window.addEventListener("replay-tour", handleReplayTour);
  window.addEventListener("replay-flow-tour", handleReplayFlowTour);
});

onBeforeUnmount(() => {
  // 清理事件监听，防止内存泄漏
  window.removeEventListener("replay-tour", handleReplayTour);
  window.removeEventListener("replay-flow-tour", handleReplayFlowTour);
});
</script>

<style>
#app {
  font-family: inherit;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  color: var(--text-primary);
  background-color: var(--bg-primary);
}

.app-initializing {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 300px;
  gap: 16px;
  color: var(--text-tertiary);
  font-size: 14px;
}
</style>
