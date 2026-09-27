<template>
  <div class="login-page">
    <div class="login-bg" />
    <el-card
      class="login-card"
      shadow="never"
    >
      <div class="login-brand">
        <div class="login-logo">
          <el-icon :size="40">
            <SetUp />
          </el-icon>
        </div>
        <h1 class="login-title">
          {{ t('auth.loginTitle') }}
        </h1>
        <p class="login-subtitle">
          {{ t('auth.loginSubtitle') }}
        </p>
      </div>

      <div class="login-tabs">
        <button
          class="login-tab"
          :class="{ active: activeTab === 'login' }"
          :data-testid="activeTab === 'login' ? 'tab-login-active' : 'tab-login'"
          @click="switchTab('login')"
        >
          {{ t('auth.loginTab') }}
        </button>
        <button
          class="login-tab"
          :class="{ active: activeTab === 'register' }"
          :data-testid="activeTab === 'register' ? 'tab-register-active' : 'tab-register'"
          @click="switchTab('register')"
        >
          {{ t('auth.registerTab') }}
        </button>
      </div>

      <el-form
        v-if="activeTab === 'login'"
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        size="large"
        @submit.prevent="handleLogin"
      >
        <el-form-item prop="username">
          <el-input
            v-model="form.username"
            :placeholder="t('auth.usernamePlaceholder')"
            :prefix-icon="User"
            autocomplete="username"
            data-testid="login-username"
          />
        </el-form-item>
        <el-form-item prop="password">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            :placeholder="t('auth.passwordPlaceholder')"
            :prefix-icon="Lock"
            autocomplete="current-password"
            data-testid="login-password"
            @keyup.enter="handleLogin"
          />
        </el-form-item>
        <el-form-item>
          <el-button
            type="primary"
            class="login-submit"
            :loading="loading"
            native-type="submit"
            data-testid="login-submit"
          >
            {{ loading ? t('auth.loggingIn') : t('auth.login') }}
          </el-button>
        </el-form-item>
      </el-form>

      <el-form
        v-else
        ref="registerFormRef"
        :model="registerForm"
        :rules="registerRules"
        label-position="top"
        size="large"
        @submit.prevent="handleRegister"
      >
        <el-form-item prop="username">
          <el-input
            v-model="registerForm.username"
            :placeholder="t('auth.usernamePlaceholder')"
            :prefix-icon="User"
            autocomplete="username"
            data-testid="register-username"
          />
        </el-form-item>
        <el-form-item prop="password">
          <el-input
            v-model="registerForm.password"
            type="password"
            show-password
            :placeholder="t('auth.passwordPlaceholder')"
            :prefix-icon="Lock"
            autocomplete="new-password"
            data-testid="register-password"
          />
        </el-form-item>
        <el-form-item prop="confirmPassword">
          <el-input
            v-model="registerForm.confirmPassword"
            type="password"
            show-password
            :placeholder="t('auth.confirmPasswordPlaceholder')"
            :prefix-icon="Lock"
            autocomplete="new-password"
            data-testid="register-confirm"
            @keyup.enter="handleRegister"
          />
        </el-form-item>
        <el-form-item>
          <el-button
            type="primary"
            class="login-submit"
            :loading="registering"
            native-type="submit"
            data-testid="register-submit"
          >
            {{ registering ? t('auth.registering') : t('auth.register') }}
          </el-button>
        </el-form-item>
      </el-form>

      <el-divider class="guest-divider">
        <span class="guest-divider-text">{{ t('common.or') }}</span>
      </el-divider>

      <el-button
        class="guest-button"
        :loading="guestLoading"
        data-testid="guest-login"
        @click="handleGuestLogin"
      >
        <el-icon :size="16">
          <Avatar />
        </el-icon>
        {{ guestLoading ? t('auth.loggingIn') : t('auth.guestLogin') }}
      </el-button>

      <div class="login-footer">
        <span class="login-hint">{{ t('auth.guestLoginHint') }}</span>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { Avatar, Lock, SetUp, User } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()

type AuthTab = 'login' | 'register'

const activeTab = ref<AuthTab>('login')
const formRef = ref<FormInstance>()
const registerFormRef = ref<FormInstance>()
const loading = ref(false)
const registering = ref(false)
const guestLoading = ref(false)

const form = reactive({
  username: '',
  password: '',
})

const registerForm = reactive({
  username: '',
  password: '',
  confirmPassword: '',
})

const rules: FormRules = {
  username: [{ required: true, message: t('auth.usernameRequired'), trigger: 'blur' }],
  password: [{ required: true, message: t('auth.passwordRequired'), trigger: 'blur' }],
}

const registerRules: FormRules = {
  username: [
    { required: true, message: t('auth.usernameRequired'), trigger: 'blur' },
    { min: 3, max: 32, message: t('auth.usernameTooShort'), trigger: 'blur' },
  ],
  password: [
    { required: true, message: t('auth.passwordRequired'), trigger: 'blur' },
    { min: 8, message: t('auth.passwordTooShort'), trigger: 'blur' },
  ],
  confirmPassword: [
    { required: true, message: t('auth.confirmPasswordRequired'), trigger: 'blur' },
    {
      validator: (_rule, value: string, callback) => {
        if (value !== registerForm.password) {
          callback(new Error(t('auth.passwordMismatch')))
        } else {
          callback()
        }
      },
      trigger: 'blur',
    },
  ],
}

/** 仅允许站内相对路径作为登录后跳转目标，防止开放重定向。 */
function safeRedirect(target: unknown): string {
  if (typeof target === 'string' && target.startsWith('/') && !target.startsWith('//')) {
    return target
  }
  return '/'
}

function switchTab(tab: AuthTab): void {
  activeTab.value = tab
}

async function handleLogin(): Promise<void> {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  loading.value = true
  try {
    const result = await authStore.login(form.username, form.password)
    if (result.success) {
      ElMessage.success(t('auth.loginSuccess'))
      await router.replace(safeRedirect(route.query.redirect))
      return
    }
    ElMessage.error(result.error || t('auth.loginFailed'))
  } finally {
    loading.value = false
  }
}

async function handleRegister(): Promise<void> {
  if (!registerFormRef.value) return
  const valid = await registerFormRef.value.validate().catch(() => false)
  if (!valid) return

  registering.value = true
  try {
    const result = await authStore.register(registerForm.username, registerForm.password)
    if (result.success) {
      ElMessage.success(t('auth.registerSuccess'))
      await router.replace(safeRedirect(route.query.redirect))
      return
    }
    ElMessage.error(result.error || t('auth.registerFailed'))
  } finally {
    registering.value = false
  }
}

async function handleGuestLogin(): Promise<void> {
  guestLoading.value = true
  try {
    const result = await authStore.guestLogin()
    if (result.success) {
      ElMessage.success(t('auth.guestLogin'))
      await router.replace(safeRedirect(route.query.redirect))
      return
    }
    ElMessage.error(result.error || t('auth.loginFailed'))
  } finally {
    guestLoading.value = false
  }
}

onMounted(() => {
  // 已登录用户访问登录页时直接进入应用
  if (authStore.isAuthenticated) {
    router.replace(safeRedirect(route.query.redirect))
  }
})
</script>

<style scoped>
.login-page {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: 24px;
  overflow: hidden;
  background-color: var(--bg-primary);
  background-image:
    radial-gradient(1100px 520px at 12% -8%, rgba(0, 122, 255, 0.10), transparent 58%),
    radial-gradient(900px 520px at 100% 108%, rgba(255, 149, 0, 0.08), transparent 58%),
    radial-gradient(700px 400px at 88% -6%, rgba(52, 199, 89, 0.05), transparent 55%);
}

.login-bg {
  position: absolute;
  inset: 0;
  background:
    radial-gradient(1200px 600px at 15% -10%, var(--accent-tint), transparent 60%),
    radial-gradient(900px 500px at 100% 110%, rgba(0, 122, 255, 0.05), transparent 60%);
  pointer-events: none;
}

.login-card {
  position: relative;
  width: 100%;
  max-width: 420px;
  padding: 40px 36px 28px;
  border: 1px solid var(--bg-200);
  border-radius: var(--radius-xl);
  background-color: var(--bg-card);
  box-shadow: var(--shadow-xl);
  backdrop-filter: blur(20px);
}

.login-brand {
  display: flex;
  flex-direction: column;
  align-items: center;
  margin-bottom: 22px;
  text-align: center;
}

.login-logo {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 72px;
  height: 72px;
  margin-bottom: 18px;
  border-radius: 20px;
  color: #fff;
  background: linear-gradient(135deg, #007aff 0%, #4da8ff 100%);
  box-shadow: 0 10px 24px rgba(0, 122, 255, 0.28);
}

.login-title {
  margin: 0 0 8px;
  font-size: 26px;
  font-weight: 700;
  color: var(--text-primary);
  letter-spacing: -0.02em;
}

.login-subtitle {
  margin: 0;
  font-size: 14px;
  color: var(--text-tertiary);
}

.login-tabs {
  display: flex;
  gap: 4px;
  padding: 4px;
  margin-bottom: 22px;
  background-color: var(--bg-200);
  border-radius: var(--radius-lg);
}

.login-tab {
  flex: 1;
  height: 36px;
  border: none;
  border-radius: calc(var(--radius-lg) - 4px);
  background: transparent;
  font-size: 14px;
  font-weight: 500;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all var(--transition-fast);
}

.login-tab.active {
  background-color: var(--bg-card);
  color: var(--text-primary);
  box-shadow: var(--shadow-sm);
  font-weight: 600;
}

.login-submit {
  width: 100%;
  margin-top: 4px;
  height: 46px;
  font-size: 15px;
  font-weight: 600;
  border-radius: var(--radius-lg) !important;
}

.guest-divider {
  margin: 22px 0 16px;
}

.guest-divider-text {
  font-size: 12px;
  color: var(--text-400);
  padding: 0 8px;
}

.guest-button {
  width: 100%;
  height: 46px;
  font-size: 15px;
  font-weight: 600;
  border-radius: var(--radius-lg) !important;
  border: 1px solid var(--bg-200);
}

.login-footer {
  margin-top: 18px;
  text-align: center;
}

.login-hint {
  font-size: 12px;
  color: var(--text-tertiary);
}
</style>
